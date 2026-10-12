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
    RestoreNumber,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, ESS_SETPOINT_LIMIT
from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import Mode, Setting, VeBusError
from .vebus.models import BatteryLimits, ConfigInfo, VeBusState

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
    ess_addresses = sorted(coordinator.client.ess_records)
    for address in ess_addresses:
        entities.append(
            VeBusEssSetpointNumber(coordinator, address, len(ess_addresses) > 1)
        )
    if coordinator.battery_limits_enabled:
        limits = VeBusBatteryLimits(coordinator)
        entities.extend(
            VeBusBatteryLimitNumber(coordinator, description, limits)
            for description in BATTERY_LIMIT_NUMBERS
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


ESS_SETPOINT = NumberEntityDescription(
    key="ess_grid_setpoint",
    translation_key="ess_grid_setpoint",
    device_class=NumberDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.WATT,
    native_min_value=-ESS_SETPOINT_LIMIT,
    native_max_value=ESS_SETPOINT_LIMIT,
    native_step=1,
    mode=NumberMode.BOX,
)


class VeBusEssSetpointNumber(VeBusEntity, RestoreNumber):
    """ESS grid setpoint of one phase master (positive = take from the grid).

    The value is only sent while "ESS external control" is on. It is
    restored after a restart.
    """

    def __init__(
        self, coordinator: VeBusCoordinator, address: int, per_phase: bool
    ) -> None:
        """Initialize the number for the phase master at ``address``."""
        if per_phase:
            description = NumberEntityDescription(
                key=f"{ESS_SETPOINT.key}_l{address + 1}",
                translation_key=f"{ESS_SETPOINT.translation_key}_phase",
                translation_placeholders={"phase": f"L{address + 1}"},
                device_class=ESS_SETPOINT.device_class,
                native_unit_of_measurement=ESS_SETPOINT.native_unit_of_measurement,
                native_min_value=ESS_SETPOINT.native_min_value,
                native_max_value=ESS_SETPOINT.native_max_value,
                native_step=ESS_SETPOINT.native_step,
                mode=ESS_SETPOINT.mode,
            )
        else:
            description = ESS_SETPOINT
        super().__init__(coordinator, description)
        self._address = address

    async def async_added_to_hass(self) -> None:
        """Restore the last setpoint."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.ess_setpoints[self._address] = last.native_value

    @property
    def native_value(self) -> float:
        """Return the setpoint Home Assistant asks for."""
        return self.coordinator.ess_setpoints.get(self._address, 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Store the setpoint and write it if external control is on."""
        await self.async_device_call(
            self.coordinator.async_set_ess_setpoint(self._address, value)
        )


@dataclass(frozen=True, kw_only=True)
class VeBusBatteryLimitNumberEntityDescription(NumberEntityDescription):
    """Describes one of the four Battery Operational Limits."""

    field: str  # attribute of BatteryLimits
    default_fn: Callable[[VeBusState], float | None]


BATTERY_LIMIT_NUMBERS: tuple[VeBusBatteryLimitNumberEntityDescription, ...] = (
    VeBusBatteryLimitNumberEntityDescription(
        key="bms_charge_voltage",
        translation_key="bms_charge_voltage",
        field="charge_voltage",
        device_class=NumberDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.01,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default_fn=lambda state: state.setting_value(Setting.U_BAT_ABSORPTION),
    ),
    VeBusBatteryLimitNumberEntityDescription(
        key="bms_charge_current",
        translation_key="bms_charge_current",
        field="charge_current",
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=0,
        native_max_value=1000,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default_fn=lambda state: state.setting_value(Setting.I_BAT_BULK),
    ),
    VeBusBatteryLimitNumberEntityDescription(
        key="bms_discharge_voltage",
        translation_key="bms_discharge_voltage",
        field="discharge_voltage",
        device_class=NumberDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.01,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default_fn=lambda _: 0.0,
    ),
    VeBusBatteryLimitNumberEntityDescription(
        key="bms_discharge_current",
        translation_key="bms_discharge_current",
        field="discharge_current",
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=0,
        native_max_value=1000,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default_fn=lambda _: 1000.0,
    ),
)


class VeBusBatteryLimits:
    """Collects the four limit values; sends them once all are known."""

    def __init__(self, coordinator: VeBusCoordinator) -> None:
        """Initialize."""
        self._coordinator = coordinator
        self.values: dict[str, float] = {}

    def update(self, field: str, value: float) -> BatteryLimits | None:
        """Store one value and return the complete limits, if complete."""
        self.values[field] = value
        if len(self.values) < len(BATTERY_LIMIT_NUMBERS):
            return None
        limits = BatteryLimits(
            charge_voltage=self.values["charge_voltage"],
            charge_current=self.values["charge_current"],
            discharge_voltage=self.values["discharge_voltage"],
            discharge_current=self.values["discharge_current"],
        )
        self._coordinator.battery_limits = limits
        return limits


class VeBusBatteryLimitNumber(VeBusEntity, RestoreNumber):
    """One Battery Operational Limit (BMS function).

    Limits are sent on every poll once all four have a value. Warning: once
    sent, the Multi stops charging and inverting if they are not refreshed
    within 5 minutes, until it is restarted.
    """

    entity_description: VeBusBatteryLimitNumberEntityDescription

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: VeBusBatteryLimitNumberEntityDescription,
        limits: VeBusBatteryLimits,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, description)
        self._limits = limits

    async def async_added_to_hass(self) -> None:
        """Restore the last value, or start from the device's own setting."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        value = last.native_value if last is not None else None
        if value is None:
            value = self.entity_description.default_fn(self.coordinator.data)
        if value is not None:
            self._limits.update(self.entity_description.field, value)

    @property
    def native_value(self) -> float | None:
        """Return the limit that is sent."""
        return self._limits.values.get(self.entity_description.field)

    async def async_set_native_value(self, value: float) -> None:
        """Change the limit and send the limits right away."""
        limits = self._limits.update(self.entity_description.field, value)
        if limits is not None:
            await self.async_device_call(
                self.coordinator.async_set_battery_limits(limits)
            )
        else:
            self.async_write_ha_state()
