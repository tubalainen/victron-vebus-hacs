"""Config flow for the Victron VE.Bus integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import usb
from homeassistant.config_entries import (
    ConfigEntry,
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
    SerialPortSelector,
    TextSelector,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo
import voluptuous as vol

from .const import (
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ADDRESS,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    PROBE_TIMEOUT,
)
from .vebus import (
    VeBusClient,
    VeBusConnectionError,
    VeBusError,
    VeBusNotSupportedError,
    VeBusTimeoutError,
)

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
        return self.async_create_entry(title=DEFAULT_NAME, data={CONF_DEVICE: device})

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
                return self.async_create_entry(
                    title=DEFAULT_NAME, data={CONF_DEVICE: self._device}
                )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="usb_confirm",
            description_placeholders={"device": self._device},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device path of the interface."""
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
                await async_probe(device)
            except ProbeError as err:
                errors["base"] = err.args[0]
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=entry.unique_id
                    if not _is_path_unique_id(entry.unique_id)
                    else unique_id,
                    data_updates={CONF_DEVICE: device},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_DEVICE): TextSelector()}),
                user_input or {CONF_DEVICE: entry.data[CONF_DEVICE]},
            ),
            errors=errors,
        )


class VeBusOptionsFlow(OptionsFlowWithReload):
    """Options of the integration."""

    _pending: dict[str, Any]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            was_enabled = self.config_entry.options.get(
                CONF_ENABLE_BATTERY_LIMITS, False
            )
            if was_enabled and not user_input.get(CONF_ENABLE_BATTERY_LIMITS, False):
                self._pending = user_input
                return await self.async_step_disable_battery_limits()
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
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
                vol.Required(CONF_ENABLE_CONTROLS, default=False): BooleanSelector(),
                vol.Required(
                    CONF_ENABLE_BATTERY_LIMITS, default=False
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
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
