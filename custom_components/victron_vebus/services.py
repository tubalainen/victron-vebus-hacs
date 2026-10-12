"""Services of the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_ACKNOWLEDGE_RISK,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_CURRENT_LIMIT,
    ATTR_MODE,
    CONF_ENABLE_CONTROLS,
    DOMAIN,
    SERVICE_SET_REMOTE_PANEL_STATE,
)
from .coordinator import VeBusConfigEntry
from .vebus import Mode, VeBusError

SET_REMOTE_PANEL_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_MODE): vol.In([mode.value for mode in Mode]),
        vol.Optional(ATTR_CURRENT_LIMIT): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=3276.7)
        ),
        vol.Required(ATTR_ACKNOWLEDGE_RISK): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""

    async def set_remote_panel_state(call: ServiceCall) -> None:
        """Set mode and current limit, like a remote panel.

        Modes other than "on" and a limit of 0 can cut the AC outputs, so the
        caller has to acknowledge the risk explicitly.
        """
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
        coordinator = entry.runtime_data.coordinator
        current_limit = call.data.get(ATTR_CURRENT_LIMIT)
        if current_limit is None:
            config = coordinator.data.config
            if config is not None and not config.dmc_dedicated:
                current_limit = config.actual_current_limit
        try:
            await coordinator.client.set_remote_panel_state(
                Mode(call.data[ATTR_MODE]), current_limit
            )
            await coordinator.async_refresh_config()
        except VeBusError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_REMOTE_PANEL_STATE,
        set_remote_panel_state,
        schema=SET_REMOTE_PANEL_STATE_SCHEMA,
    )
