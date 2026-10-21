"""Alarm events for the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ALARMS, VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import AlarmState

PARALLEL_UPDATES = 0

# "<alarm>_warning" (pre-alarm), "<alarm>_alarm" and "<alarm>_cleared". A
# VE.Bus error has no warning.
ALARM_EVENT_TYPES: list[str] = [
    f"{alarm}_{suffix}"
    for alarm in ALARMS
    for suffix in ("warning", "alarm", "cleared")
    if not (alarm == "vebus_error" and suffix == "warning")
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alarm event entity."""
    async_add_entities([VeBusAlarmEvent(entry.runtime_data.coordinator)])


class VeBusAlarmEvent(VeBusEntity, EventEntity):
    """Fires when an alarm of the device starts, escalates or clears."""

    _attr_event_types = ALARM_EVENT_TYPES

    def __init__(self, coordinator: VeBusCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(
            coordinator, EventEntityDescription(key="alarm", translation_key="alarm")
        )

    async def async_added_to_hass(self) -> None:
        """Listen for alarm changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_alarm_listener(self._async_handle_alarm)
        )

    @callback
    def _async_handle_alarm(self, alarm: str, level: AlarmState) -> None:
        suffix = "cleared" if level is AlarmState.OK else level.value
        data: dict[str, str | int] = {"alarm": alarm, "level": level.value}
        if alarm == "vebus_error" and self.coordinator.vebus_error_code is not None:
            data["error_code"] = self.coordinator.vebus_error_code
        self._trigger_event(f"{alarm}_{suffix}", data)
        self.async_write_ha_state()
