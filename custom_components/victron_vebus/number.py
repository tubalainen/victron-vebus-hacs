"""Numbers for the Victron VE.Bus integration.

The remote panel current limit, and charger settings changed in RAM only (the
device's stored configuration is not changed; a restart restores it).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import Mode, Setting, VeBusError
from .vebus.models import ConfigInfo, VeBusState

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


def _check_not_above_absorption(state: VeBusState, value: float) -> str | None:
    absorption = state.setting_value(Setting.U_BAT_ABSORPTION)
    if absorption is not None and value > absorption:
        return f"{value} V is above the absorption voltage of {absorption} V"
    return None


def _check_not_below_float(state: VeBusState, value: float) -> str | None:
    float_voltage = state.setting_value(Setting.U_BAT_FLOAT)
    if float_voltage is not None and value < float_voltage:
        return f"{value} V is below the float voltage of {float_voltage} V"
    return None


@dataclass(frozen=True, kw_only=True)
class VeBusSettingNumberEntityDescription(NumberEntityDescription):
    """Describes a number backed by a device setting."""

    setting: Setting
    exists_fn: Callable[[VeBusCoordinator], bool] = lambda _: True
    # Returns an error message if the value is not allowed in the current state.
    check_fn: Callable[[VeBusState, float], str | None] = lambda _s, _v: None


SETTING_NUMBERS: tuple[VeBusSettingNumberEntityDescription, ...] = (
    VeBusSettingNumberEntityDescription(
        key="absorption_voltage",
        translation_key="absorption_voltage",
        setting=Setting.U_BAT_ABSORPTION,
        device_class=NumberDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        check_fn=_check_not_below_float,
    ),
    VeBusSettingNumberEntityDescription(
        key="float_voltage",
        translation_key="float_voltage",
        setting=Setting.U_BAT_FLOAT,
        device_class=NumberDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        check_fn=_check_not_above_absorption,
    ),
    VeBusSettingNumberEntityDescription(
        key="charge_current_limit",
        translation_key="charge_current_limit",
        setting=Setting.I_BAT_BULK,
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    VeBusSettingNumberEntityDescription(
        key="configured_ac_input_current_limit",
        translation_key="configured_ac_input_current_limit",
        setting=Setting.I_MAINS_LIMIT_AC1,
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    VeBusSettingNumberEntityDescription(
        key="configured_ac_input_2_current_limit",
        translation_key="configured_ac_input_2_current_limit",
        setting=Setting.I_MAINS_LIMIT_AC2,
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    VeBusSettingNumberEntityDescription(
        key="solar_wind_priority_voltage",
        translation_key="solar_wind_priority_voltage",
        setting=Setting.SOLAR_WIND_PRIORITY_VOLTAGE,
        device_class=NumberDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        exists_fn=lambda coordinator: coordinator.supports_solar_wind_priority(),
        check_fn=_check_not_above_absorption,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers (only when controls are enabled)."""
    coordinator = entry.runtime_data.coordinator
    entities: list[NumberEntity] = [VeBusCurrentLimitNumber(coordinator, CURRENT_LIMIT)]
    entities.extend(
        VeBusSettingNumber(coordinator, description)
        for description in SETTING_NUMBERS
        if coordinator.can_write_setting(description.setting)
        and description.exists_fn(coordinator)
    )
    async_add_entities(entities)


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


class VeBusSettingNumber(VeBusEntity, NumberEntity):
    """A charger setting, changed in RAM only."""

    entity_description: VeBusSettingNumberEntityDescription

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: VeBusSettingNumberEntityDescription,
    ) -> None:
        """Initialize the number from the range the device reports."""
        super().__init__(coordinator, description)
        info = coordinator.client.setting_info[description.setting]
        assert info is not None
        assert info.minimum is not None
        assert info.maximum is not None
        self._attr_native_min_value = info.decode(info.minimum)
        self._attr_native_max_value = info.decode(info.maximum)
        self._attr_native_step = info.step

    @property
    def native_value(self) -> float | None:
        """Return the value currently in effect."""
        return self.coordinator.data.setting_value(self.entity_description.setting)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting to the device's RAM."""
        error = self.entity_description.check_fn(self.coordinator.data, value)
        if error is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"error": error},
            )
        await self.async_device_call(
            self.coordinator.client.write_setting(
                self.entity_description.setting, value
            )
        )
