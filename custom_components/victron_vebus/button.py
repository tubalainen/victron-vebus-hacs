"""Buttons for the Victron VE.Bus integration.

Force absorption and float are routine. Equalisation, restarting the Multi
and saving settings permanently are critical: they need "Allow critical
changes".
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import actions
from .const import DOMAIN
from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity
from .vebus import ForceChargeState
from .vebus.const import MIN_FIRMWARE_FORCE_CHARGE, MIN_FIRMWARE_RAM_ONLY_WRITE

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

EQUALISE = ButtonEntityDescription(
    key="force_equalise",
    translation_key="force_equalise",
)
RESTART = ButtonEntityDescription(
    key="restart",
    translation_key="restart",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
)
SAVE_SETTINGS = ButtonEntityDescription(
    key="save_settings",
    translation_key="save_settings",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons (only when controls are enabled)."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [VeBusRestartButton(coordinator, RESTART)]
    if coordinator.firmware_at_least(MIN_FIRMWARE_FORCE_CHARGE):
        entities.extend(
            VeBusForceChargeButton(coordinator, description) for description in BUTTONS
        )
        entities.append(VeBusEqualiseButton(coordinator, EQUALISE))
    if coordinator.firmware_at_least(MIN_FIRMWARE_RAM_ONLY_WRITE):
        entities.append(VeBusSaveSettingsButton(coordinator, SAVE_SETTINGS))
    async_add_entities(entities)


class VeBusForceChargeButton(VeBusEntity, ButtonEntity):
    """Forces the charger into absorption or float."""

    entity_description: VeBusButtonEntityDescription

    async def async_press(self) -> None:
        """Send the forced charge state."""
        await self.async_device_call(
            self.coordinator.client.force_charge_state(
                self.entity_description.charge_state
            )
        )


class VeBusEqualiseButton(VeBusEntity, ButtonEntity):
    """Starts an equalisation charge (critical)."""

    async def async_press(self) -> None:
        """Force the equalise charge state."""
        self.coordinator.require_critical_allowed("Equalisation", actions.EQUALISE_RISK)
        await self.async_device_call(actions.async_equalise(self.coordinator))


class VeBusRestartButton(VeBusEntity, ButtonEntity):
    """Restarts the Multi (critical): the AC outputs lose power."""

    async def async_press(self) -> None:
        """Send the restart command."""
        self.coordinator.require_critical_allowed(
            "Restarting the Multi", actions.RESTART_RISK
        )
        await self.async_device_call(actions.async_restart(self.coordinator))


class VeBusSaveSettingsButton(VeBusEntity, ButtonEntity):
    """Stores the settings changed in RAM permanently (critical)."""

    async def async_press(self) -> None:
        """Write the changed settings to the EEPROM."""
        if not self.coordinator.client.unsaved_settings:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_save"
            )
        self.coordinator.require_critical_allowed(
            "Saving settings permanently", actions.SAVE_RISK
        )
        await self.async_device_call(actions.async_save_settings(self.coordinator))
