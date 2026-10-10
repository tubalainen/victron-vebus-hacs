"""Sensors for the Victron VE.Bus integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import AlarmState, ChargeState, DeviceState, Led, Mode, RamVar, Setting
from .vebus.models import AcInfo, VeBusState

PARALLEL_UPDATES = 0

NOT_CHARGING = "not_charging"
AC_INPUTS = ("ac_in_1", "ac_in_2", "ac_in_3", "ac_in_4")


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


def _negated(var: RamVar) -> Callable[[VeBusState], StateType]:
    def value(state: VeBusState) -> StateType:
        raw = state.ram_value(var)
        return None if raw is None else -raw

    return value


DEVICE_SENSORS: tuple[VeBusSensorEntityDescription, ...] = (
    VeBusSensorEntityDescription(
        key="ac_input_power",
        translation_key="ac_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_negated(RamVar.INVERTER_POWER_2),
    ),
    VeBusSensorEntityDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=_ram(RamVar.OUTPUT_POWER),
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
        value_fn=_ram(RamVar.INVERTER_POWER_1),
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
    phases = coordinator.identity.phases
    multi_phase = len(phases) > 1
    for phase in phases:
        for description in PHASE_SENSORS:
            entities.append(
                VeBusPhaseSensor(coordinator, description, phase, multi_phase)
            )
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
