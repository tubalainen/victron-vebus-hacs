"""Binary sensors for the Victron VE.Bus integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import Led, LedState, RamVar, Setting, SettingFlag
from .vebus.models import VeBusState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VeBusBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a VE.Bus binary sensor."""

    value_fn: Callable[[VeBusState], bool | None]
    exists_fn: Callable[[VeBusCoordinator], bool] = lambda _: True
    led: Led | None = None


def _ram_bit(var: RamVar) -> Callable[[VeBusState], bool | None]:
    def value(state: VeBusState) -> bool | None:
        raw = state.ram_value(var)
        return None if raw is None else bool(raw)

    return value


def _has_ram(var: RamVar) -> Callable[[VeBusCoordinator], bool]:
    return lambda coordinator: coordinator.client.ram_var_info.get(var) is not None


def _led(led: Led) -> Callable[[VeBusState], bool | None]:
    def value(state: VeBusState) -> bool | None:
        if state.leds is None:
            return None
        return state.leds.state(led) is not LedState.OFF

    return value


def _ac_input_connected(state: VeBusState) -> bool | None:
    # The mains LED is lit while the AC input relay is closed and blinks while
    # AC is present but the device has not connected to it (yet).
    if state.leds is None:
        return None
    return state.leds.state(Led.MAINS) is LedState.ON


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


def _ups_function(state: VeBusState) -> bool | None:
    # UPS is on while the "disable wave check" bit is clear.
    disabled = state.setting_flag(Setting.FLAGS_0, SettingFlag.DISABLE_WAVE_CHECK)
    return None if disabled is None else not disabled


BINARY_SENSORS: tuple[VeBusBinarySensorEntityDescription, ...] = (
    VeBusBinarySensorEntityDescription(
        key="ac_input_connected",
        translation_key="ac_input_connected",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=_ac_input_connected,
    ),
    VeBusBinarySensorEntityDescription(
        key="relay",
        translation_key="relay",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ram_bit(RamVar.MULTI_FUNCTIONAL_RELAY_STATE),
        exists_fn=_has_ram(RamVar.MULTI_FUNCTIONAL_RELAY_STATE),
    ),
    VeBusBinarySensorEntityDescription(
        key="virtual_switch",
        translation_key="virtual_switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ram_bit(RamVar.VIRTUAL_SWITCH_POSITION),
        exists_fn=_has_ram(RamVar.VIRTUAL_SWITCH_POSITION),
    ),
    VeBusBinarySensorEntityDescription(
        key="ignore_ac_input",
        translation_key="ignore_ac_input",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ram_bit(RamVar.IGNORE_AC_INPUT_STATE),
        exists_fn=_has_ram(RamVar.IGNORE_AC_INPUT_STATE),
    ),
    VeBusBinarySensorEntityDescription(
        key="power_assist",
        translation_key="power_assist",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.setting_flag(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED),
        exists_fn=lambda c: (
            c.client.state.settings_raw.get(Setting.FLAGS_0) is not None
        ),
    ),
    VeBusBinarySensorEntityDescription(
        key="weak_ac_input",
        translation_key="weak_ac_input",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.setting_flag(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT),
        exists_fn=lambda c: (
            c.client.state.settings_raw.get(Setting.FLAGS_0) is not None
        ),
    ),
    VeBusBinarySensorEntityDescription(
        key="ups_function",
        translation_key="ups_function",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_ups_function,
        exists_fn=lambda c: (
            c.client.state.settings_raw.get(Setting.FLAGS_0) is not None
        ),
    ),
    VeBusBinarySensorEntityDescription(
        key="remote_panel_detected",
        translation_key="remote_panel_detected",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.config.remote_panel_detected if s.config else None,
    ),
    *(
        VeBusBinarySensorEntityDescription(
            key=f"led_{name}",
            translation_key=f"led_{name}",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=_led(led),
            led=led,
        )
        for name, led in LED_NAMES.items()
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VE.Bus binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        VeBusBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator)
    )


class VeBusBinarySensor(VeBusEntity, BinarySensorEntity):
    """A binary sensor of a VE.Bus device."""

    entity_description: VeBusBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Report whether an LED is blinking."""
        led = self.entity_description.led
        leds = self.coordinator.data.leds
        if led is None or leds is None:
            return None
        return {"blinking": leds.state(led) is LedState.BLINK}
