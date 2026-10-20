"""The mode select of the Victron VE.Bus integration (a critical control)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import actions
from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity
from .vebus import Mode

PARALLEL_UPDATES = 1

MODE = SelectEntityDescription(
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
    async_add_entities([VeBusModeSelect(entry.runtime_data.coordinator, MODE)])


class VeBusModeSelect(VeBusEntity, SelectEntity):
    """The remote panel mode: on, off, charger only or inverter only.

    Changing it is critical (it can cut the power on the AC outputs), so it
    needs "Allow critical changes".
    """

    @property
    def current_option(self) -> str | None:
        """Return the mode requested through the remote panel interface.

        The mode in effect also depends on the front switch; the Mode sensor
        shows it.
        """
        config = self.coordinator.data.config
        if config is None or config.remote_mode is None:
            return None
        return config.remote_mode.value

    async def async_select_option(self, option: str) -> None:
        """Change the mode, keeping the current limit."""
        mode = Mode(option)
        self.coordinator.require_critical_allowed(
            f"Mode {option.replace('_', ' ')}", actions.MODE_RISKS[mode]
        )
        await self.async_device_call(actions.async_set_mode(self.coordinator, mode))
