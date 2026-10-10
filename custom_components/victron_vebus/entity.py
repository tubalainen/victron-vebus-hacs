"""Base entity for the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import VeBusCoordinator


def device_identifier(coordinator: VeBusCoordinator) -> str:
    """Return the identifier of the VE.Bus device of a coordinator."""
    entry = coordinator.config_entry
    return f"{entry.unique_id or entry.entry_id}_{coordinator.client.address}"


class VeBusEntity(CoordinatorEntity[VeBusCoordinator]):
    """An entity belonging to a VE.Bus device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: VeBusCoordinator, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        identifier = device_identifier(coordinator)
        self._attr_unique_id = f"{identifier}_{description.key}"
        firmware = coordinator.identity.firmware
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            model="VE.Bus inverter/charger",
            model_id=firmware.product_id if firmware else None,
            name=coordinator.config_entry.title,
            sw_version=str(firmware.version) if firmware else None,
        )
