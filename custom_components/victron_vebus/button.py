"""Buttons forcing a charge state for the Victron VE.Bus integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity
from .vebus import ForceChargeState
from .vebus.const import MIN_FIRMWARE_FORCE_CHARGE

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class VeBusButtonEntityDescription(ButtonEntityDescription):
    """Describes a button forcing a charge state."""

    charge_state: ForceChargeState


BUTTONS: tuple[VeBusButtonEntityDescription, ...] = (
    VeBusButtonEntityDescription(
        key="force_absorption",
        translation_key="force_absorption",
        charge_state=ForceChargeState.ABSORPTION,
    ),
    VeBusButtonEntityDescription(
        key="force_float",
        translation_key="force_float",
        charge_state=ForceChargeState.FLOAT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons (only when controls are enabled)."""
    coordinator = entry.runtime_data.coordinator
    if not coordinator.firmware_at_least(MIN_FIRMWARE_FORCE_CHARGE):
        return
    async_add_entities(
        VeBusForceChargeButton(coordinator, description) for description in BUTTONS
    )


class VeBusForceChargeButton(VeBusEntity, ButtonEntity):
    """Forces the charger into equalise, absorption or float."""

    entity_description: VeBusButtonEntityDescription

    async def async_press(self) -> None:
        """Send the forced charge state."""
        await self.async_device_call(
            self.coordinator.client.force_charge_state(
                self.entity_description.charge_state
            )
        )
