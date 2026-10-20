"""Sensors for the Victron VE.Bus integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, ENERGY_MIN_GAP
from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .energy import EnergyCounter
from .entity import VeBusEntity
from .vebus import (
    AlarmState,
    ChargeState,
    DeviceState,
    Led,
    LedState,
    Mode,
    RamVar,
    Setting,
)
from .vebus.models import AcInfo, UnitState, VeBusState

PARALLEL_UPDATES = 0

NOT_CHARGING = "not_charging"
AC_INPUTS = ("ac_in_1", "ac_in_2", "ac_in_3", "ac_in_4")
# Values of setting 10 (charge characteristic), as named by VEConfigure.
CHARGE_CHARACTERISTICS = ("variable", "fixed", "fixed_storage")


@dataclass(frozen=True, kw_only=True)
class VeBusSensorEntityDescription(SensorEntityDescription):
    """Describes a VE.Bus sensor."""

    value_fn: Callable[[VeBusState], StateType]
    exists_fn: Callable[[VeBusCoordinator], bool] = lambda _: True


def _ram(var: RamVar, factor: float = 1.0) -> Callable[[VeBusState], StateType]:
    def value(state: VeBusState) -> StateType:
        raw = state.ram_value(var)
        return None if raw is None else raw * factor

    return value


def _setting(setting: Setting) -> Callable[[VeBusState], StateType]:
    return lambda state: state.setting_value(setting)


def _has_setting(setting: Setting) -> Callable[[VeBusCoordinator], bool]:
    return lambda coordinator: coordinator.client.setting_info.get(setting) is not None


def _has_ram(var: RamVar) -> Callable[[VeBusCoordinator], bool]:
    return lambda coordinator: coordinator.client.ram_var_info.get(var) is not None


def _device_state(state: VeBusState) -> StateType:
    if state.device_state is not None and state.device_state.state is not None:
        return state.device_state.state.value
    ac = state.ac.get(1)
    if ac is not None and ac.device_state is not None:
        return ac.device_state.value
    return None


def _charge_state(state: VeBusState) -> StateType:
    info = state.device_state
    if info is None or info.state is None:
        return None
    if info.state is not DeviceState.CHARGE:
        return NOT_CHARGING
    return info.charge_state.value if info.charge_state else None


# Front panel LEDs, each shown as off, on or blinking.
LED_NAMES: dict[str, Led] = {
    "mains": Led.MAINS,
    "absorption": Led.ABSORPTION,
    "bulk": Led.BULK,
    "float": Led.FLOAT,
    "inverter": Led.INVERTER,
    "overload": Led.OVERLOAD,
    "low_battery": Led.LOW_BATTERY,
    "temperature": Led.TEMPERATURE,
}


def _led(led: Led) -> Callable[[VeBusState], StateType]:
    def value(state: VeBusState) -> StateType:
        if state.leds is None or not state.leds.known:
            return None
        return state.leds.state(led).value

    return value


def _alarm(led: Led) -> Callable[[VeBusState], StateType]:
    return lambda state: state.leds.alarm(led).value if state.leds else None


def _mode(attr: str) -> Callable[[VeBusState], StateType]:
    def value(state: VeBusState) -> StateType:
        if state.config is None:
            return None
        mode: Mode | None = getattr(state.config, attr)
        return mode.value if mode else None

    return value


def _config(attr: str) -> Callable[[VeBusState], StateType]:
    def value(state: VeBusState) -> StateType:
        if state.config is None or state.config.dmc_dedicated:
            return None
        result: float = getattr(state.config, attr)
        return result

    return value


def _state_of_charge(state: VeBusState) -> StateType:
    soc = state.ram_value(RamVar.CHARGE_STATE)
    capacity = state.setting_value(Setting.BATTERY_CAPACITY)
    if soc is None or not capacity:
        return None
    return round(soc * 100, 1)


def _has_battery_monitor(coordinator: VeBusCoordinator) -> bool:
    capacity = coordinator.client.state.setting_value(Setting.BATTERY_CAPACITY)
    return bool(capacity) and _has_ram(RamVar.CHARGE_STATE)(coordinator)


def _active_input(state: VeBusState) -> StateType:
    if state.config is None:
        return None
    return AC_INPUTS[state.config.last_active_ac_input]


def _charge_characteristic(state: VeBusState) -> StateType:
    value = state.setting_value(Setting.CHARGE_CHARACTERISTIC)
    if value is None:
        return None
    index = int(value)
    if not 0 <= index < len(CHARGE_CHARACTERISTICS):
        return None
    return CHARGE_CHARACTERISTICS[index]


def _low_battery_restart_voltage(state: VeBusState) -> StateType:
    # The inverter restarts at the shutdown voltage plus the hysteresis.
    limit = state.setting_value(Setting.U_BAT_LOW_LIMIT)
    hysteresis = state.setting_value(Setting.U_BAT_LOW_HYSTERESIS)
    if limit is None or hysteresis is None:
        return None
    return round(limit + hysteresis, 2)


def _total(var: RamVar, sign: float = 1.0) -> Callable[[VeBusState], StateType]:
    """Return a RAM variable summed over all units (just one for most systems)."""

    def value(state: VeBusState) -> StateType:
        total = state.total_ram_value(var)
        return None if total is None else sign * total

    return value


DEVICE_SENSORS: tuple[VeBusSensorEntityDescription, ...] = (
    VeBusSensorEntityDescription(
        key="ac_input_power",
        translation_key="ac_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_total(RamVar.INVERTER_POWER_2, -1),
    ),
    VeBusSensorEntityDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_total(RamVar.OUTPUT_POWER),
    ),
    VeBusSensorEntityDescription(
        key="ac_output_frequency",
        translation_key="ac_output_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=1,
        value_fn=lambda s: s.dc.inverter_frequency if s.dc else None,
    ),
    VeBusSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        value_fn=lambda s: s.dc.voltage if s.dc else None,
    ),
    VeBusSensorEntityDescription(
        key="battery_current",
        translation_key="battery_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda s: s.dc.battery_current if s.dc else None,
    ),
    VeBusSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_total(RamVar.INVERTER_POWER_1),
    ),
    VeBusSensorEntityDescription(
        key="charger_current",
        translation_key="charger_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda s: s.dc.charger_current if s.dc else None,
    ),
    VeBusSensorEntityDescription(
        key="inverter_current",
        translation_key="inverter_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda s: s.dc.inverter_current if s.dc else None,
    ),
    VeBusSensorEntityDescription(
        key="battery_ripple",
        translation_key="battery_ripple",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_ram(RamVar.U_BAT_RMS),
        exists_fn=_has_ram(RamVar.U_BAT_RMS),
    ),
    VeBusSensorEntityDescription(
        key="state_of_charge",
        translation_key="state_of_charge",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=_state_of_charge,
        exists_fn=_has_battery_monitor,
    ),
    VeBusSensorEntityDescription(
        key="device_state",
        translation_key="device_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in DeviceState],
        value_fn=_device_state,
    ),
    VeBusSensorEntityDescription(
        key="charge_state",
        translation_key="charge_state",
        device_class=SensorDeviceClass.ENUM,
        options=[NOT_CHARGING, *(state.value for state in ChargeState)],
        value_fn=_charge_state,
    ),
    VeBusSensorEntityDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=[mode.value for mode in Mode],
        value_fn=_mode("mode"),
    ),
    VeBusSensorEntityDescription(
        key="front_switch_mode",
        translation_key="front_switch_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[Mode.ON.value, Mode.OFF.value, Mode.CHARGER_ONLY.value],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mode("front_switch_mode"),
    ),
    VeBusSensorEntityDescription(
        key="overload_alarm",
        translation_key="overload_alarm",
        device_class=SensorDeviceClass.ENUM,
        options=[alarm.value for alarm in AlarmState],
        value_fn=_alarm(Led.OVERLOAD),
    ),
    VeBusSensorEntityDescription(
        key="low_battery_alarm",
        translation_key="low_battery_alarm",
        device_class=SensorDeviceClass.ENUM,
        options=[alarm.value for alarm in AlarmState],
        value_fn=_alarm(Led.LOW_BATTERY),
    ),
    VeBusSensorEntityDescription(
        key="temperature_alarm",
        translation_key="temperature_alarm",
        device_class=SensorDeviceClass.ENUM,
        options=[alarm.value for alarm in AlarmState],
        value_fn=_alarm(Led.TEMPERATURE),
    ),
    *(
        VeBusSensorEntityDescription(
            key=f"led_{name}",
            translation_key=f"led_{name}",
            device_class=SensorDeviceClass.ENUM,
            options=[led_state.value for led_state in LedState],
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_led(led),
        )
        for name, led in LED_NAMES.items()
    ),
    VeBusSensorEntityDescription(
        key="active_ac_input",
        translation_key="active_ac_input",
        device_class=SensorDeviceClass.ENUM,
        options=list(AC_INPUTS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_active_input,
        exists_fn=lambda c: (
            c.client.state.config is not None
            and c.client.state.config.num_ac_inputs > 1
        ),
    ),
    VeBusSensorEntityDescription(
        key="ac_input_current_limit",
        translation_key="ac_input_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_config("actual_current_limit"),
    ),
    VeBusSensorEntityDescription(
        key="ac_input_current_limit_minimum",
        translation_key="ac_input_current_limit_minimum",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_config("minimum_current_limit"),
    ),
    VeBusSensorEntityDescription(
        key="ac_input_current_limit_maximum",
        translation_key="ac_input_current_limit_maximum",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_config("maximum_current_limit"),
    ),
    VeBusSensorEntityDescription(
        key="absorption_voltage",
        translation_key="absorption_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.U_BAT_ABSORPTION),
        exists_fn=_has_setting(Setting.U_BAT_ABSORPTION),
    ),
    VeBusSensorEntityDescription(
        key="float_voltage",
        translation_key="float_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.U_BAT_FLOAT),
        exists_fn=_has_setting(Setting.U_BAT_FLOAT),
    ),
    VeBusSensorEntityDescription(
        key="charge_current_limit",
        translation_key="charge_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.I_BAT_BULK),
        exists_fn=_has_setting(Setting.I_BAT_BULK),
    ),
    VeBusSensorEntityDescription(
        key="configured_ac_input_current_limit",
        translation_key="configured_ac_input_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_setting(Setting.I_MAINS_LIMIT_AC1),
        exists_fn=_has_setting(Setting.I_MAINS_LIMIT_AC1),
    ),
    VeBusSensorEntityDescription(
        key="solar_wind_priority_voltage",
        translation_key="solar_wind_priority_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.SOLAR_WIND_PRIORITY_VOLTAGE),
        exists_fn=lambda c: (
            c.supports_solar_wind_priority()
            and _has_setting(Setting.SOLAR_WIND_PRIORITY_VOLTAGE)(c)
        ),
    ),
    VeBusSensorEntityDescription(
        key="configured_ac_input_2_current_limit",
        translation_key="configured_ac_input_2_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.I_MAINS_LIMIT_AC2),
        exists_fn=_has_setting(Setting.I_MAINS_LIMIT_AC2),
    ),
    VeBusSensorEntityDescription(
        key="battery_capacity",
        translation_key="battery_capacity",
        native_unit_of_measurement="Ah",
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_setting(Setting.BATTERY_CAPACITY),
        exists_fn=_has_setting(Setting.BATTERY_CAPACITY),
    ),
    VeBusSensorEntityDescription(
        key="inverter_output_voltage",
        translation_key="inverter_output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.U_INV_SETPOINT),
        exists_fn=_has_setting(Setting.U_INV_SETPOINT),
    ),
    VeBusSensorEntityDescription(
        key="low_battery_shutdown_voltage",
        translation_key="low_battery_shutdown_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.U_BAT_LOW_LIMIT),
        exists_fn=_has_setting(Setting.U_BAT_LOW_LIMIT),
    ),
    VeBusSensorEntityDescription(
        key="low_battery_restart_voltage",
        translation_key="low_battery_restart_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_low_battery_restart_voltage,
        exists_fn=lambda c: (
            _has_setting(Setting.U_BAT_LOW_LIMIT)(c)
            and _has_setting(Setting.U_BAT_LOW_HYSTERESIS)(c)
        ),
    ),
    VeBusSensorEntityDescription(
        key="charge_characteristic",
        translation_key="charge_characteristic",
        device_class=SensorDeviceClass.ENUM,
        options=list(CHARGE_CHARACTERISTICS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_charge_characteristic,
        exists_fn=_has_setting(Setting.CHARGE_CHARACTERISTIC),
    ),
    VeBusSensorEntityDescription(
        key="maximum_absorption_time",
        translation_key="maximum_absorption_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setting(Setting.ABSORPTION_DURATION),
        exists_fn=_has_setting(Setting.ABSORPTION_DURATION),
    ),
    VeBusSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: str(s.firmware) if s.firmware else None,
    ),
    VeBusSensorEntityDescription(
        key="interface_version",
        translation_key="interface_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: str(s.mk3_version) if s.mk3_version else None,
    ),
)


@dataclass(frozen=True, kw_only=True)
class VeBusEnergySensorEntityDescription(SensorEntityDescription):
    """Describes an energy counter integrated from a power reading."""

    power_fn: Callable[[VeBusState], float | None]
    # +1 counts positive power, -1 counts negative power.
    direction: int


def _power(var: RamVar, sign: float = 1.0) -> Callable[[VeBusState], float | None]:
    """Return a power RAM variable summed over all units."""

    def value(state: VeBusState) -> float | None:
        total = state.total_ram_value(var)
        return None if total is None else sign * total

    return value


def _energy(
    key: str, power_fn: Callable[[VeBusState], float | None], direction: int
) -> VeBusEnergySensorEntityDescription:
    return VeBusEnergySensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        power_fn=power_fn,
        direction=direction,
    )


# AC input power is positive while taking power from the grid; battery
# (DC side) power is positive while charging.
ENERGY_SENSORS: tuple[VeBusEnergySensorEntityDescription, ...] = (
    _energy("ac_input_energy_imported", _power(RamVar.INVERTER_POWER_2, -1), 1),
    _energy("ac_input_energy_exported", _power(RamVar.INVERTER_POWER_2, -1), -1),
    _energy("ac_output_energy", _power(RamVar.OUTPUT_POWER), 1),
    _energy("battery_energy_charged", _power(RamVar.INVERTER_POWER_1), 1),
    _energy("battery_energy_discharged", _power(RamVar.INVERTER_POWER_1), -1),
)


@dataclass(frozen=True, kw_only=True)
class VeBusPhaseSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor with one entity per AC phase."""

    phase_value_fn: Callable[[AcInfo], StateType]


PHASE_SENSORS: tuple[VeBusPhaseSensorEntityDescription, ...] = (
    VeBusPhaseSensorEntityDescription(
        key="ac_input_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        phase_value_fn=lambda ac: ac.mains_voltage,
    ),
    VeBusPhaseSensorEntityDescription(
        key="ac_input_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        phase_value_fn=lambda ac: ac.mains_current,
    ),
    VeBusPhaseSensorEntityDescription(
        key="ac_input_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=1,
        phase_value_fn=lambda ac: ac.mains_frequency,
    ),
    VeBusPhaseSensorEntityDescription(
        key="ac_output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        phase_value_fn=lambda ac: ac.inverter_voltage,
    ),
    VeBusPhaseSensorEntityDescription(
        key="ac_output_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        phase_value_fn=lambda ac: ac.inverter_current,
    ),
)


# Apparent power per phase, only for multi-phase systems.
PHASE_POWER_SENSORS: tuple[VeBusPhaseSensorEntityDescription, ...] = (
    VeBusPhaseSensorEntityDescription(
        key="ac_input_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        phase_value_fn=lambda ac: abs(ac.mains_voltage * ac.mains_current),
    ),
    VeBusPhaseSensorEntityDescription(
        key="ac_output_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        phase_value_fn=lambda ac: abs(ac.inverter_voltage * ac.inverter_current),
    ),
)


@dataclass(frozen=True, kw_only=True)
class VeBusUnitSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor of one unit in a multi-unit system."""

    unit_value_fn: Callable[[UnitState], StateType]


def _unit_ram(var: RamVar, sign: float = 1.0) -> Callable[[UnitState], StateType]:
    def value(unit: UnitState) -> StateType:
        raw = unit.ram_value(var)
        return None if raw is None else sign * raw

    return value


def _unit_device_state(unit: UnitState) -> StateType:
    info = unit.device_state
    return info.state.value if info is not None and info.state is not None else None


UNIT_SENSORS: tuple[VeBusUnitSensorEntityDescription, ...] = (
    VeBusUnitSensorEntityDescription(
        key="ac_input_voltage",
        translation_key="ac_input_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        unit_value_fn=_unit_ram(RamVar.U_MAINS_RMS),
    ),
    VeBusUnitSensorEntityDescription(
        key="ac_input_current",
        translation_key="ac_input_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        unit_value_fn=_unit_ram(RamVar.I_MAINS_RMS),
    ),
    VeBusUnitSensorEntityDescription(
        key="ac_input_power",
        translation_key="ac_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        unit_value_fn=_unit_ram(RamVar.INVERTER_POWER_2, -1),
    ),
    VeBusUnitSensorEntityDescription(
        key="ac_output_voltage",
        translation_key="ac_output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        unit_value_fn=_unit_ram(RamVar.U_INVERTER_RMS),
    ),
    VeBusUnitSensorEntityDescription(
        key="ac_output_current",
        translation_key="ac_output_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        unit_value_fn=_unit_ram(RamVar.I_INVERTER_RMS),
    ),
    VeBusUnitSensorEntityDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        unit_value_fn=_unit_ram(RamVar.OUTPUT_POWER),
    ),
    VeBusUnitSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        unit_value_fn=_unit_ram(RamVar.U_BAT),
    ),
    VeBusUnitSensorEntityDescription(
        key="battery_current",
        translation_key="battery_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        unit_value_fn=_unit_ram(RamVar.I_BAT),
    ),
    VeBusUnitSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        unit_value_fn=_unit_ram(RamVar.INVERTER_POWER_1),
    ),
    VeBusUnitSensorEntityDescription(
        key="device_state",
        translation_key="device_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in DeviceState],
        unit_value_fn=_unit_device_state,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VE.Bus sensors."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        VeBusSensor(coordinator, description)
        for description in DEVICE_SENSORS
        if description.exists_fn(coordinator)
    ]
    entities.extend(
        VeBusEnergySensor(coordinator, description) for description in ENERGY_SENSORS
    )
    phases = coordinator.identity.phases
    multi_phase = len(phases) > 1
    phase_descriptions = (
        (*PHASE_SENSORS, *PHASE_POWER_SENSORS) if multi_phase else PHASE_SENSORS
    )
    for phase in phases:
        for description in phase_descriptions:
            entities.append(
                VeBusPhaseSensor(coordinator, description, phase, multi_phase)
            )
    ess_addresses = sorted(coordinator.client.ess_records)
    for address in ess_addresses:
        entities.append(
            VeBusEssSetpointSensor(coordinator, address, len(ess_addresses) > 1)
        )
    units = coordinator.identity.units
    if len(units) > 1:
        for address in units:
            for unit_description in UNIT_SENSORS:
                entities.append(VeBusUnitSensor(coordinator, unit_description, address))
    async_add_entities(entities)


class VeBusSensor(VeBusEntity, SensorEntity):
    """A sensor of a VE.Bus device."""

    entity_description: VeBusSensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class VeBusPhaseSensor(VeBusEntity, SensorEntity):
    """A sensor for one AC phase of a VE.Bus device."""

    entity_description: VeBusPhaseSensorEntityDescription

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: VeBusPhaseSensorEntityDescription,
        phase: int,
        multi_phase: bool,
    ) -> None:
        """Initialize the sensor."""
        key = description.key if phase == 1 else f"{description.key}_l{phase}"
        if multi_phase:
            translation_key = f"{description.key}_phase"
            placeholders = {"phase": f"L{phase}"}
        else:
            translation_key = description.key
            placeholders = {}
        super().__init__(
            coordinator,
            replace(
                description,
                key=key,
                translation_key=translation_key,
                translation_placeholders=placeholders,
            ),
        )
        self._phase = phase

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        ac = self.coordinator.data.ac.get(self._phase)
        return None if ac is None else self.entity_description.phase_value_fn(ac)


class VeBusUnitSensor(VeBusEntity, SensorEntity):
    """A sensor of one unit of a multi-unit system."""

    entity_description: VeBusUnitSensorEntityDescription

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: VeBusUnitSensorEntityDescription,
        address: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description, unit=address)
        self._address = address

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        unit = self.coordinator.data.units.get(self._address)
        return None if unit is None else self.entity_description.unit_value_fn(unit)


class VeBusEssSetpointSensor(VeBusEntity, SensorEntity):
    """The ESS setpoint the device currently uses (positive = from the grid)."""

    def __init__(
        self, coordinator: VeBusCoordinator, address: int, per_phase: bool
    ) -> None:
        """Initialize the sensor for the phase master at ``address``."""
        description = SensorEntityDescription(
            key=f"ess_active_setpoint_l{address + 1}"
            if per_phase
            else "ess_active_setpoint",
            translation_key="ess_active_setpoint_phase"
            if per_phase
            else "ess_active_setpoint",
            translation_placeholders={"phase": f"L{address + 1}"} if per_phase else {},
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfPower.WATT,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        super().__init__(coordinator, description)
        self._address = address

    @property
    def native_value(self) -> StateType:
        """Return the setpoint read from the ESS assistant."""
        ess = self.coordinator.data.ess.get(self._address)
        if ess is None or ess.setpoint is None:
            return None
        # MK3 sign: negative = import; Home Assistant/GX sign is the inverse.
        return -ess.setpoint


class VeBusEnergySensor(VeBusEntity, RestoreSensor):
    """Energy in kWh, integrated from a power reading and kept across restarts."""

    entity_description: VeBusEnergySensorEntityDescription

    def __init__(
        self,
        coordinator: VeBusCoordinator,
        description: VeBusEnergySensorEntityDescription,
    ) -> None:
        """Initialize the counter."""
        super().__init__(coordinator, description)
        interval = float(
            coordinator.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
        )
        self._counter = EnergyCounter(
            description.direction, max_gap=max(5 * interval, ENERGY_MIN_GAP)
        )

    async def async_added_to_hass(self) -> None:
        """Restore the total and take the first sample."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, (int, float, Decimal)):
            self._counter.total = float(last.native_value)
        self._add_sample()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._add_sample()
        super()._handle_coordinator_update()

    def _add_sample(self) -> None:
        if not self.coordinator.last_update_success:
            # Nothing is counted for the time the device is unreachable.
            self._counter.reset()
            return
        state = self.coordinator.data
        self._counter.add(state.timestamp, self.entity_description.power_fn(state))

    @property
    def native_value(self) -> float:
        """Return the energy in kWh."""
        return round(self._counter.total, 4)
