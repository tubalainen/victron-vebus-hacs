"""Remote panel mode select for the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity
from .vebus import Mode, VeBusError

PARALLEL_UPDATES = 1

MODE_SELECT = SelectEntityDescription(
    key="remote_mode",
    translation_key="remote_mode",
    options=[mode.value for mode in Mode],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mode select (only when controls are enabled)."""
    async_add_entities([VeBusModeSelect(entry.runtime_data.coordinator, MODE_SELECT)])


class VeBusModeSelect(VeBusEntity, SelectEntity):
    """Selects the remote panel mode (on, off, charger only, inverter only)."""

    @property
    def current_option(self) -> str | None:
        """Return the mode requested by the remote panel."""
        config = self.coordinator.data.config
        if config is None or config.remote_mode is None:
            return None
        return config.remote_mode.value

    async def async_select_option(self, option: str) -> None:
        """Change the mode, keeping the current input current limit."""
        await self.async_set_remote_panel_state(Mode(option))

    async def async_set_remote_panel_state(
        self, mode: Mode | str, current_limit: float | None = None
    ) -> None:
        """Set mode and optionally the input current limit (also a service)."""
        if current_limit is None:
            config = self.coordinator.data.config
            if config is not None and not config.dmc_dedicated:
                current_limit = config.actual_current_limit
        try:
            await self.coordinator.client.set_remote_panel_state(
                Mode(mode), current_limit
            )
            await self.coordinator.async_refresh_config()
        except VeBusError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
