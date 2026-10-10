"""Switches for the Victron VE.Bus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity

PARALLEL_UPDATES = 1

KEEP_AWAKE = SwitchEntityDescription(
    key="keep_awake",
    translation_key="keep_awake",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VE.Bus switches."""
    async_add_entities(
        [VeBusKeepAwakeSwitch(entry.runtime_data.coordinator, KEEP_AWAKE)]
    )


class VeBusKeepAwakeSwitch(VeBusEntity, SwitchEntity, RestoreEntity):
    """Keeps the VE.Bus device awake while it is switched off.

    A switched off device goes to sleep after a while and stops powering the
    MK3, which then cannot switch it back on. The MK3 'standby' flag prevents
    this. The flag is lost whenever the device loses power, so it is sent again
    on every poll. The setting is stored in Home Assistant and restored on start.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the previous state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self.coordinator.keep_awake = (
            last_state is None or last_state.state != STATE_OFF
        )

    @property
    def available(self) -> bool:
        """The setting can be changed even while the device is unreachable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the device is kept awake."""
        return self.coordinator.keep_awake

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Report the flags last acknowledged by the interface."""
        flags = self.coordinator.data.interface_flags
        if flags is None:
            return None
        return {"interface_flags": f"0x{flags:02x}"}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Keep the device awake."""
        self.coordinator.keep_awake = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Allow the device to sleep."""
        self.coordinator.keep_awake = False
        self.async_write_ha_state()
