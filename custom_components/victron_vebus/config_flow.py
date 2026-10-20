"""Config flow for the Victron VE.Bus integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import usb
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SerialPortSelector,
    TextSelector,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo
import voluptuous as vol

from . import actions
from .actions import (
    EQUALISE_WARNING,
    FLAG_CHOICES,
    MODE_WARNINGS,
    PASSTHRU_LEAVE_WARNING,
    RESTART_WARNING,
    SETTING_CHOICES,
    PendingAction,
    check_setting,
    flag_warning,
    passthru_warning,
    setting_warning,
)
from .const import (
    CONF_ACKNOWLEDGE,
    CONF_ALARM_ISSUES,
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    CONF_USAGE,
    DEFAULT_ADDRESS,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    PROBE_TIMEOUT,
    USAGE_CONTROL,
    USAGE_MONITOR,
)
from .coordinator import VeBusCoordinator
from .vebus import (
    Mode,
    Setting,
    VeBusClient,
    VeBusConnectionError,
    VeBusError,
    VeBusNotSupportedError,
    VeBusTimeoutError,
)
from .vebus.const import MIN_FIRMWARE_FORCE_CHARGE

_LOGGER = logging.getLogger(__name__)


class ProbeError(Exception):
    """Probing the interface failed; ``args[0]`` is the error key."""


async def async_probe(device: str) -> str | None:
    """Check that a VE.Bus device answers through the interface.

    Returns the firmware version string of the device, if available.
    """
    client = VeBusClient(device, address=DEFAULT_ADDRESS)
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            await client.connect()
            await client.get_version()
            await client.set_winmon_mode1()
            await client.set_address(DEFAULT_ADDRESS)
            # Winmon commands are answered by the VE.Bus device itself, so this
            # also verifies that a device is connected and awake.
            try:
                firmware = await client.read_firmware()
            except VeBusNotSupportedError:
                return None
            return str(firmware)
    except VeBusConnectionError as err:
        raise ProbeError("cannot_connect") from err
    except (VeBusTimeoutError, TimeoutError) as err:
        raise ProbeError("no_response") from err
    except VeBusError as err:
        raise ProbeError("unknown") from err
    finally:
        await client.close()


def _is_local_path(device: str) -> bool:
    return device.startswith("/") or "://" not in device


async def _async_unique_id(hass: HomeAssistant, device: str) -> tuple[str, str]:
    """Return (stable device path, unique id) for a device path or URL."""
    if not _is_local_path(device):
        return device, device
    by_id = await hass.async_add_executor_job(usb.get_serial_by_id, device)
    usb_device = await hass.async_add_executor_job(usb.usb_device_from_path, by_id)
    if usb_device is not None and usb_device.serial_number:
        return by_id, usb_device.serial_number
    return by_id, by_id


def _is_path_unique_id(unique_id: str | None) -> bool:
    return unique_id is None or "/" in unique_id


class VeBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Victron VE.Bus."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._device: str | None = None
        self._title = DEFAULT_NAME
        self._reconfigure_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> VeBusOptionsFlow:
        """Return the options flow."""
        return VeBusOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how to specify the interface."""
        return self.async_show_menu(step_id="user", menu_options=["serial", "manual"])

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the MK3 from the list of serial ports."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create_from_device(
                user_input[CONF_DEVICE], errors
            )
            if result is not None:
                return result
        return self.async_show_form(
            step_id="serial",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): SerialPortSelector()}),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a device path or URL, e.g. socket://host:port for ser2net."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create_from_device(
                user_input[CONF_DEVICE], errors
            )
            if result is not None:
                return result
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_DEVICE): TextSelector()}), user_input
            ),
            errors=errors,
        )

    async def _async_create_from_device(
        self, device: str, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        device = device.strip()
        device, unique_id = await _async_unique_id(self.hass, device)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_DEVICE: device})
        try:
            await async_probe(device)
        except ProbeError as err:
            errors["base"] = err.args[0]
            return None
        self._device = device
        return await self.async_step_usage()

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle a discovered MK3-USB interface."""
        device = await self.hass.async_add_executor_job(
            usb.get_serial_by_id, discovery_info.device
        )
        await self.async_set_unique_id(discovery_info.serial_number or device)
        self._abort_if_unique_id_configured(updates={CONF_DEVICE: device})
        self._device = device
        self.context["title_placeholders"] = {
            "name": discovery_info.description or "MK3-USB"
        }
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up a discovered interface."""
        assert self._device is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_probe(self._device)
            except ProbeError as err:
                errors["base"] = err.args[0]
            else:
                return await self.async_step_usage()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="usb_confirm",
            description_placeholders={"device": self._device},
            errors=errors,
        )

    async def async_step_usage(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose monitoring only, or monitoring and control."""
        if user_input is not None:
            if user_input[CONF_USAGE] == USAGE_CONTROL:
                return await self.async_step_control_warning()
            return self._async_create(controls=False)
        return self.async_show_form(step_id="usage", data_schema=_usage_schema())

    async def async_step_control_warning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Make sure the user understands what control means."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_ACKNOWLEDGE):
                if self.source == SOURCE_RECONFIGURE:
                    return self._async_finish_reconfigure(controls=True)
                return self._async_create(controls=True)
            errors["base"] = "acknowledge_required"
        return self.async_show_form(
            step_id="control_warning",
            data_schema=vol.Schema(
                {vol.Required(CONF_ACKNOWLEDGE, default=False): BooleanSelector()}
            ),
            errors=errors,
        )

    def _async_create(self, *, controls: bool) -> ConfigFlowResult:
        assert self._device is not None
        return self.async_create_entry(
            title=DEFAULT_NAME,
            data={CONF_DEVICE: self._device},
            options={CONF_ENABLE_CONTROLS: controls},
        )

    def _async_finish_reconfigure(self, *, controls: bool) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        options = {**entry.options, CONF_ENABLE_CONTROLS: controls}
        if not controls:
            # Battery limits need control; they are switched off with it.
            options[CONF_ENABLE_BATTERY_LIMITS] = False
        return self.async_update_reload_and_abort(
            entry, options=options, **self._reconfigure_data
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device path of the interface or how it may be used."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            device, unique_id = await _async_unique_id(
                self.hass, user_input[CONF_DEVICE].strip()
            )
            if not _is_path_unique_id(entry.unique_id):
                # Identified by the USB serial number: must be the same interface.
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch()
            try:
                # The running integration holds the port; only probe a new one.
                if device != entry.data[CONF_DEVICE]:
                    await async_probe(device)
            except ProbeError as err:
                errors["base"] = err.args[0]
            else:
                self._reconfigure_data = {
                    "unique_id": entry.unique_id
                    if not _is_path_unique_id(entry.unique_id)
                    else unique_id,
                    "data_updates": {CONF_DEVICE: device},
                }
                controls = user_input[CONF_USAGE] == USAGE_CONTROL
                if controls and not entry.options.get(CONF_ENABLE_CONTROLS, False):
                    return await self.async_step_control_warning()
                return self._async_finish_reconfigure(controls=controls)
        usage = (
            USAGE_CONTROL
            if entry.options.get(CONF_ENABLE_CONTROLS, False)
            else USAGE_MONITOR
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_DEVICE): TextSelector()}).extend(
                    _usage_schema().schema
                ),
                user_input or {CONF_DEVICE: entry.data[CONF_DEVICE], CONF_USAGE: usage},
            ),
            errors=errors,
        )


class VeBusOptionsFlow(OptionsFlowWithReload):
    """Options of the integration and guided device actions.

    Critical actions (restart, mode, passthru, settings, equalise) are only
    available here: each one shows its risks and needs to be confirmed.
    """

    _pending: dict[str, Any]
    _action: PendingAction

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between the integration settings and the device actions."""
        if self.config_entry.state is not ConfigEntryState.LOADED or not (
            self.config_entry.options.get(CONF_ENABLE_CONTROLS, False)
        ):
            # Monitoring only: there are no device actions.
            return await self.async_step_settings()
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "actions"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            was_enabled = self.config_entry.options.get(
                CONF_ENABLE_BATTERY_LIMITS, False
            )
            options = {**self.config_entry.options, **user_input}
            if was_enabled and not options.get(CONF_ENABLE_BATTERY_LIMITS, False):
                self._pending = options
                return await self.async_step_disable_battery_limits()
            return self.async_create_entry(data=options)
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_ALARM_ISSUES, default=True): BooleanSelector(),
        }
        if self.config_entry.options.get(CONF_ENABLE_CONTROLS, False):
            fields[vol.Required(CONF_ENABLE_BATTERY_LIMITS, default=False)] = (
                BooleanSelector()
            )
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )

    async def async_step_disable_battery_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn that the Multi keeps expecting battery limits until restarted."""
        if user_input is not None:
            return self.async_create_entry(data=self._pending)
        return self.async_show_form(step_id="disable_battery_limits")

    # ------------------------------------------------------------------
    # Guided device actions

    @property
    def _coordinator(self) -> VeBusCoordinator:
        coordinator: VeBusCoordinator = self.config_entry.runtime_data.coordinator
        return coordinator

    async def async_step_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a device action."""
        coordinator = self._coordinator
        options = ["action_mode", "action_passthru"]
        if any(
            coordinator.can_write_setting(setting)
            for setting in SETTING_CHOICES.values()
        ):
            options.append("action_setting")
        if any(
            coordinator.can_write_flag(choice.setting, choice.flag)
            for choice in FLAG_CHOICES.values()
        ):
            options.append("action_flag")
        if coordinator.firmware_at_least(MIN_FIRMWARE_FORCE_CHARGE):
            options.append("action_equalise")
        options.append("action_restart")
        return self.async_show_menu(step_id="actions", menu_options=options)

    async def async_step_action_restart(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Restart the Multi."""
        coordinator = self._coordinator
        return await self._async_confirm(
            PendingAction(
                "Restart the Multi",
                RESTART_WARNING,
                lambda: actions.async_restart(coordinator),
            )
        )

    async def async_step_action_equalise(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Force an equalisation charge."""
        coordinator = self._coordinator
        return await self._async_confirm(
            PendingAction(
                "Start equalisation",
                EQUALISE_WARNING,
                lambda: actions.async_equalise(coordinator),
            )
        )

    async def async_step_action_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the remote panel mode."""
        coordinator = self._coordinator
        if user_input is not None:
            mode = Mode(user_input["mode"])
            return await self._async_confirm(
                PendingAction(
                    f"Switch the mode to {mode.value}",
                    MODE_WARNINGS[mode],
                    lambda: actions.async_set_mode(coordinator, mode),
                )
            )
        config = coordinator.data.config
        current = config.remote_mode if config is not None else None
        schema = vol.Schema(
            {
                vol.Required(
                    "mode", default=(current or Mode.ON).value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[mode.value for mode in Mode],
                        translation_key="mode",
                    )
                )
            }
        )
        return self.async_show_form(step_id="action_mode", data_schema=schema)

    async def async_step_action_passthru(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter or leave passthru."""
        coordinator = self._coordinator
        if user_input is not None:
            if user_input["passthru"] == "enter":
                return await self._async_confirm(
                    PendingAction(
                        "Enter passthru",
                        passthru_warning(coordinator),
                        lambda: actions.async_set_passthru(coordinator, True, None),
                    )
                )
            limit = float(user_input["current_limit"])
            return await self._async_confirm(
                PendingAction(
                    "Leave passthru",
                    PASSTHRU_LEAVE_WARNING.format(limit=f"{limit:g}"),
                    lambda: actions.async_set_passthru(coordinator, False, limit),
                )
            )
        default_limit = coordinator.data.setting_value(Setting.I_MAINS_LIMIT_AC1)
        schema = vol.Schema(
            {
                vol.Required("passthru", default="enter"): SelectSelector(
                    SelectSelectorConfig(
                        options=["enter", "leave"], translation_key="passthru"
                    )
                ),
                vol.Required(
                    "current_limit", default=default_limit or 16.0
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=200,
                        step=0.1,
                        unit_of_measurement="A",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="action_passthru", data_schema=schema)

    async def async_step_action_setting(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change a charger setting, in RAM or permanently."""
        coordinator = self._coordinator
        choices = {
            key: setting
            for key, setting in SETTING_CHOICES.items()
            if coordinator.can_write_setting(setting)
        }
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            setting = choices[user_input["setting"]]
            value = float(user_input["value"])
            persistent = bool(user_input["persistent"])
            error = check_setting(coordinator, setting, value)
            if error is None:
                name = user_input["setting"].replace("_", " ")
                return await self._async_confirm(
                    PendingAction(
                        f"Change {name} to {value:g}",
                        setting_warning(setting, persistent),
                        lambda: actions.async_write_setting(
                            coordinator, setting, value, persistent
                        ),
                    )
                )
            errors["base"] = "invalid_value"
            placeholders["error"] = error
        schema = vol.Schema(
            {
                vol.Required("setting"): SelectSelector(
                    SelectSelectorConfig(
                        options=list(choices), translation_key="setting"
                    )
                ),
                vol.Required("value"): NumberSelector(
                    NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
                ),
                vol.Required("persistent", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="action_setting",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={
                **placeholders,
                "ranges": _setting_ranges(coordinator, choices),
            },
        )

    async def async_step_action_flag(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Switch PowerAssist, weak AC input, UPS or solar & wind priority."""
        coordinator = self._coordinator
        choices = {
            key: choice
            for key, choice in FLAG_CHOICES.items()
            if coordinator.can_write_flag(choice.setting, choice.flag)
        }
        if user_input is not None:
            key = user_input["flag"]
            choice = choices[key]
            enabled = bool(user_input["enabled"])
            persistent = bool(user_input["persistent"])
            state = "on" if enabled else "off"
            return await self._async_confirm(
                PendingAction(
                    f"Switch {key.replace('_', ' ')} {state}",
                    flag_warning(choice, enabled, persistent),
                    lambda: actions.async_write_flag(
                        coordinator, choice, enabled, persistent
                    ),
                )
            )
        schema = vol.Schema(
            {
                vol.Required("flag"): SelectSelector(
                    SelectSelectorConfig(options=list(choices), translation_key="flag")
                ),
                vol.Required("enabled", default=True): BooleanSelector(),
                vol.Required("persistent", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="action_flag", data_schema=schema)

    async def _async_confirm(self, action: PendingAction) -> ConfigFlowResult:
        self._action = action
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the risks of the pending action and run it once confirmed."""
        action = self._action
        errors: dict[str, str] = {}
        placeholders = {
            "action": action.title,
            "warning": action.warning,
            "error": "",
        }
        if user_input is not None:
            if not user_input.get("acknowledge"):
                errors["base"] = "acknowledge_required"
            else:
                try:
                    result = await action.run()
                except ValueError as err:
                    errors["base"] = "invalid_value"
                    placeholders["error"] = str(err)
                except VeBusError as err:
                    errors["base"] = "command_failed"
                    placeholders["error"] = str(err)
                else:
                    return self.async_abort(
                        reason="action_done",
                        description_placeholders={"result": result},
                    )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required("acknowledge", default=False): BooleanSelector()}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )


def _usage_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USAGE, default=USAGE_MONITOR): SelectSelector(
                SelectSelectorConfig(
                    options=[USAGE_MONITOR, USAGE_CONTROL], translation_key="usage"
                )
            )
        }
    )


def _setting_ranges(coordinator: VeBusCoordinator, choices: dict[str, Setting]) -> str:
    """Return a Markdown list of the current values and allowed ranges."""
    lines = []
    for key, setting in choices.items():
        info = coordinator.client.setting_info.get(setting)
        if info is None or info.minimum is None or info.maximum is None:
            continue
        current = coordinator.data.setting_value(setting)
        now = "unknown" if current is None else f"{current:g}"
        lines.append(
            f"- {key.replace('_', ' ')}: now {now}, allowed"
            f" {info.decode(info.minimum):g}..{info.decode(info.maximum):g}"
        )
    return "\n".join(lines)
