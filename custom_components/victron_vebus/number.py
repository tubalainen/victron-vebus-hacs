"""AC input current limit for the Victron VE.Bus integration."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import VeBusConfigEntry
from .entity import VeBusEntity
from .vebus import Mode, VeBusError
from .vebus.models import ConfigInfo

PARALLEL_UPDATES = 1

CURRENT_LIMIT = NumberEntityDescription(
    key="remote_current_limit",
    translation_key="remote_current_limit",
    device_class=NumberDeviceClass.CURRENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    native_step=0.1,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the current limit (only when controls are enabled)."""
    async_add_entities(
        [VeBusCurrentLimitNumber(entry.runtime_data.coordinator, CURRENT_LIMIT)]
    )


class VeBusCurrentLimitNumber(VeBusEntity, NumberEntity):
    """The AC input current limit set through the remote panel interface."""

    @property
    def _config(self) -> ConfigInfo | None:
        config = self.coordinator.data.config
        if config is None or config.dmc_dedicated:
            return None
        return config

    @property
    def available(self) -> bool:
        """The limit cannot be set while a dedicated panel overrides it."""
        return super().available and self._config is not None

    @property
    def native_value(self) -> float | None:
        """Return the current limit in effect."""
        config = self._config
        return config.actual_current_limit if config else None

    @property
    def native_min_value(self) -> float:
        """Return the lowest limit accepted by the device."""
        config = self._config
        return config.minimum_current_limit if config else 0.0

    @property
    def native_max_value(self) -> float:
        """Return the highest limit accepted by the device."""
        config = self._config
        return config.maximum_current_limit if config else 100.0

    async def async_set_native_value(self, value: float) -> None:
        """Set the limit without changing the mode currently in effect."""
        config = self._config
        mode = config.mode if config and config.mode else Mode.ON
        try:
            await self.coordinator.client.set_remote_panel_state(mode, value)
            await self.coordinator.async_refresh_config()
        except VeBusError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
