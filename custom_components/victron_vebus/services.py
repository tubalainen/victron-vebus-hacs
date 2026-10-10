"""Services of the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import service
import voluptuous as vol

from .const import (
    ATTR_CURRENT_LIMIT,
    ATTR_MODE,
    DOMAIN,
    SERVICE_SET_REMOTE_PANEL_STATE,
)
from .vebus import Mode


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_REMOTE_PANEL_STATE,
        entity_domain=SELECT_DOMAIN,
        schema={
            vol.Required(ATTR_MODE): vol.In([mode.value for mode in Mode]),
            vol.Optional(ATTR_CURRENT_LIMIT): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=3276.7)
            ),
        },
        func="async_set_remote_panel_state",
    )
