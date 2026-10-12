"""Base entity for the Victron VE.Bus integration."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import VeBusCoordinator
from .vebus import VeBusError, VeBusNotSupportedError


def device_identifier(coordinator: VeBusCoordinator) -> str:
    """Return the identifier of the VE.Bus device of a coordinator."""
    entry = coordinator.config_entry
    return f"{entry.unique_id or entry.entry_id}_{coordinator.client.address}"


def system_device_info(coordinator: VeBusCoordinator) -> DeviceInfo:
    """Return the device info of the VE.Bus system (the unit at the home address)."""
    firmware = coordinator.identity.firmware
    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier(coordinator))},
        manufacturer=MANUFACTURER,
        model="VE.Bus inverter/charger",
        model_id=firmware.product_id if firmware else None,
        name=coordinator.config_entry.title,
        sw_version=str(firmware.version) if firmware else None,
    )


def unit_identifier(coordinator: VeBusCoordinator, address: int) -> str:
    """Return the identifier of one unit of a multi-unit system."""
    entry = coordinator.config_entry
    return f"{entry.unique_id or entry.entry_id}_unit{address}"


def unit_device_info(coordinator: VeBusCoordinator, address: int) -> DeviceInfo:
    """Return the device info of one unit, linked to the system device."""
    firmware = coordinator.client.unit_firmware.get(address)
    info = DeviceInfo(
        identifiers={(DOMAIN, unit_identifier(coordinator, address))},
        manufacturer=MANUFACTURER,
        model="VE.Bus inverter/charger",
        model_id=firmware.product_id if firmware else None,
        name=f"{coordinator.config_entry.title} unit {address}",
        sw_version=str(firmware.version) if firmware else None,
    )
    if coordinator.system_device_id is not None:
        info["via_device_id"] = coordinator.system_device_id
    return info


class VeBusEntity(CoordinatorEntity[VeBusCoordinator]):
    """An entity belonging to the VE.Bus system or to one of its units."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: EntityDescription,
        unit: int | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        if unit is None:
            identifier = device_identifier(coordinator)
            self._attr_device_info = system_device_info(coordinator)
        else:
            identifier = unit_identifier(coordinator, unit)
            self._attr_device_info = unit_device_info(coordinator, unit)
        self._attr_unique_id = f"{identifier}_{description.key}"

    async def async_device_call[T](self, call: Awaitable[T]) -> T:
        """Run a command on the device and publish the resulting state.

        Errors become translated Home Assistant exceptions.
        """
        try:
            result = await call
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
        self.coordinator.async_set_updated_data(self.coordinator.client.state)
        return result
