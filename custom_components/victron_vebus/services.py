"""Services (actions) of the Victron VE.Bus integration.

Every action that changes how the Multi operates needs a config entry set up
for "Monitor and control" and ``acknowledge_risk: true``. Automations and
scripts use these instead of the "Allow critical changes" switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from . import actions
from .const import (
    ATTR_ACKNOWLEDGE_RISK,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_CURRENT_LIMIT,
    ATTR_ENABLED,
    ATTR_FEATURE,
    ATTR_MODE,
    ATTR_PERSISTENT,
    ATTR_SETTING,
    ATTR_VALUE,
    CONF_ENABLE_CONTROLS,
    DOMAIN,
    SERVICE_EQUALISE,
    SERVICE_RESTART,
    SERVICE_SAVE_SETTINGS,
    SERVICE_SET_FEATURE,
    SERVICE_SET_MODE,
    SERVICE_SET_REMOTE_PANEL_STATE,
    SERVICE_SET_SETTING,
)
from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .vebus import Mode, VeBusError, VeBusNotSupportedError
from .vebus.const import MIN_FIRMWARE_FORCE_CHARGE, MIN_FIRMWARE_RAM_ONLY_WRITE

BASE_SCHEMA: dict[vol.Marker, object] = {
    vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Required(ATTR_ACKNOWLEDGE_RISK): cv.boolean,
}
MODES = [mode.value for mode in Mode]

SET_REMOTE_PANEL_STATE_SCHEMA = vol.Schema(
    {
        **BASE_SCHEMA,
        vol.Required(ATTR_MODE): vol.In(MODES),
        vol.Optional(ATTR_CURRENT_LIMIT): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=3276.7)
        ),
    }
)
SET_MODE_SCHEMA = vol.Schema({**BASE_SCHEMA, vol.Required(ATTR_MODE): vol.In(MODES)})
SET_SETTING_SCHEMA = vol.Schema(
    {
        **BASE_SCHEMA,
        vol.Required(ATTR_SETTING): vol.In(list(actions.SETTING_CHOICES)),
        vol.Required(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_PERSISTENT, default=False): cv.boolean,
    }
)
SET_FEATURE_SCHEMA = vol.Schema(
    {
        **BASE_SCHEMA,
        vol.Required(ATTR_FEATURE): vol.In(list(actions.FLAG_CHOICES)),
        vol.Required(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_PERSISTENT, default=False): cv.boolean,
    }
)
DEVICE_SCHEMA = vol.Schema(BASE_SCHEMA)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> VeBusCoordinator:
    """Return the coordinator of a loaded entry that may control the Multi."""
    if not call.data[ATTR_ACKNOWLEDGE_RISK]:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="risk_not_acknowledged"
        )
    entry: VeBusConfigEntry | None = hass.config_entries.async_get_entry(
        call.data[ATTR_CONFIG_ENTRY_ID]
    )
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    if not entry.options.get(CONF_ENABLE_CONTROLS, False):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="controls_disabled"
        )
    return entry.runtime_data.coordinator


def _not_supported(feature: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_supported",
        translation_placeholders={
            "error": f"{feature} is not supported by this device"
        },
    )


async def _run(call: Awaitable[object]) -> None:
    """Run a device command and turn its errors into Home Assistant errors."""
    try:
        await call
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_value",
            translation_placeholders={"error": str(err)},
        ) from err
    except VeBusNotSupportedError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_supported",
            translation_placeholders={"error": str(err)},
        ) from err
    except VeBusError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""

    async def set_remote_panel_state(call: ServiceCall) -> None:
        """Set mode and current limit, like a remote panel."""
        coordinator = _coordinator(hass, call)
        current_limit = call.data.get(ATTR_CURRENT_LIMIT)
        if current_limit is None:
            current_limit = actions.panel_limit(coordinator)
        else:
            # 0 A (passthru) is allowed: the call acknowledged the risk.
            actions.raise_invalid(
                actions.check_panel_limit(coordinator, current_limit, allow_zero=True)
            )

        async def command() -> None:
            await coordinator.client.set_remote_panel_state(
                Mode(call.data[ATTR_MODE]), current_limit
            )
            if current_limit is not None:
                coordinator.passthru = current_limit == 0
            await coordinator.async_refresh_config()

        await _run(command())

    async def set_mode(call: ServiceCall) -> None:
        """Change the mode, keeping the current limit."""
        coordinator = _coordinator(hass, call)
        await _run(actions.async_set_mode(coordinator, Mode(call.data[ATTR_MODE])))

    async def set_setting(call: ServiceCall) -> None:
        """Change a charger setting, in RAM or permanently."""
        coordinator = _coordinator(hass, call)
        key = call.data[ATTR_SETTING]
        setting = actions.SETTING_CHOICES[key]
        if not coordinator.can_write_setting(setting):
            raise _not_supported(key)
        value = call.data[ATTR_VALUE]
        error = actions.check_setting(coordinator, setting, value)
        if error is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"error": error},
            )
        await _run(
            actions.async_write_setting(
                coordinator, setting, value, call.data[ATTR_PERSISTENT]
            )
        )

    async def set_feature(call: ServiceCall) -> None:
        """Switch PowerAssist, weak AC input, UPS or solar & wind priority."""
        coordinator = _coordinator(hass, call)
        key = call.data[ATTR_FEATURE]
        choice = actions.FLAG_CHOICES[key]
        if not coordinator.can_write_flag(choice.setting, choice.flag):
            raise _not_supported(key)
        await _run(
            actions.async_write_flag(
                coordinator,
                choice,
                call.data[ATTR_ENABLED],
                call.data[ATTR_PERSISTENT],
            )
        )

    async def equalise(call: ServiceCall) -> None:
        """Start an equalisation charge."""
        coordinator = _coordinator(hass, call)
        if not coordinator.firmware_at_least(MIN_FIRMWARE_FORCE_CHARGE):
            raise _not_supported("Equalisation")
        await _run(actions.async_equalise(coordinator))

    async def restart(call: ServiceCall) -> None:
        """Restart the Multi."""
        await _run(actions.async_restart(_coordinator(hass, call)))

    async def save_settings(call: ServiceCall) -> None:
        """Store the settings changed in RAM permanently."""
        coordinator = _coordinator(hass, call)
        if not coordinator.firmware_at_least(MIN_FIRMWARE_RAM_ONLY_WRITE):
            raise _not_supported("Saving settings")
        if not coordinator.client.unsaved_settings:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_save"
            )
        await _run(actions.async_save_settings(coordinator))

    services: tuple[
        tuple[str, Callable[[ServiceCall], Awaitable[None]], vol.Schema], ...
    ] = (
        (
            SERVICE_SET_REMOTE_PANEL_STATE,
            set_remote_panel_state,
            SET_REMOTE_PANEL_STATE_SCHEMA,
        ),
        (SERVICE_SET_MODE, set_mode, SET_MODE_SCHEMA),
        (SERVICE_SET_SETTING, set_setting, SET_SETTING_SCHEMA),
        (SERVICE_SET_FEATURE, set_feature, SET_FEATURE_SCHEMA),
        (SERVICE_EQUALISE, equalise, DEVICE_SCHEMA),
        (SERVICE_RESTART, restart, DEVICE_SCHEMA),
        (SERVICE_SAVE_SETTINGS, save_settings, DEVICE_SCHEMA),
    )
    for name, handler, schema in services:
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
