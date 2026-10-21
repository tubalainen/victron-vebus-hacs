"""Tests for v0.3: multi-unit systems, phase totals, Quattro and Compact devices."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry(controls: bool = False) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_ENABLE_CONTROLS: controls, CONF_SCAN_INTERVAL: 30},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


def _unit_ram(output_power: int) -> dict[int, int]:
    return {
        0: 23100,
        1: 300,
        2: 23100,
        3: 150,
        4: 2645,
        5: 40,
        14: 100,
        15: (-120) & 0xFFFF,
        16: output_power,
    }


async def test_single_unit_has_one_device(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """A single unit keeps exactly one device and no per-unit entities."""
    await _setup(hass, config_entry)
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    assert len(devices) == 1
    assert hass.states.get(f"sensor.{PREFIX}_unit_1_ac_output_power") is None
    assert hass.states.get(f"sensor.{PREFIX}_ac_input_apparent_power_l1") is None
    assert (
        hass.states.get(f"sensor.{PREFIX}_configured_ac_input_2_current_limit") is None
    )


async def test_three_phase_system(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Three units on three phases: unit devices, totals and phase power."""
    fake_mk3.device.phases = (1, 2, 3)
    fake_mk3.device.units = {1: _unit_ram(200), 2: _unit_ram(300)}
    entry = _entry()
    await _setup(hass, entry)

    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _, identifier in device.identifiers
    }
    system = devices["HQ123_0"]
    assert len(devices) == 4
    for address in (0, 1, 2):
        unit = devices[f"HQ123_unit{address}"]
        assert unit.via_device_id == system.id
        assert unit.name == f"Victron VE.Bus unit {address}"

    assert _state(hass, f"sensor.{PREFIX}_unit_1_ac_output_power") == "200.0"
    assert _state(hass, f"sensor.{PREFIX}_unit_2_battery_voltage") == "26.45"
    assert _state(hass, f"sensor.{PREFIX}_unit_0_device_state") == "charge"
    # System totals add up the three units (50 + 200 + 300 W).
    assert _state(hass, f"sensor.{PREFIX}_ac_output_power") == "550.0"
    assert _state(hass, f"sensor.{PREFIX}_ac_input_power") == "590.0"
    # Apparent power per phase from the info frames: 230.12 V * 5.5 A.
    assert float(_state(hass, f"sensor.{PREFIX}_ac_input_apparent_power_l2")) == round(
        230.12 * 5.5, 3
    )

    entity = er.async_get(hass).async_get(f"sensor.{PREFIX}_unit_1_ac_output_power")
    assert entity is not None
    assert entity.unique_id == "HQ123_unit1_ac_output_power"


async def test_quattro_ac_input_2(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A Quattro gets the AC input 2 limit and the active input."""
    fake_mk3.device.quattro = True
    fake_mk3.device.settings[49] = 120
    await _setup(hass, _entry(controls=True))
    assert (
        _state(hass, f"sensor.{PREFIX}_configured_ac_input_2_current_limit") == "12.0"
    )
    assert _state(hass, f"sensor.{PREFIX}_active_ac_input") == "ac_in_1"
    number = f"number.{PREFIX}_configured_ac_input_2_current_limit"
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": f"switch.{PREFIX}_allow_critical_changes"},
        blocking=True,
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 20}, blocking=True
    )
    assert fake_mk3.device.settings[49] == 200
    assert _state(hass, number) == "20.0"


async def test_device_without_info_frames(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Compact/Phoenix inverters get values from RAM variables."""
    fake_mk3.device.info_frames = False
    await _setup(hass, config_entry)
    assert _state(hass, f"sensor.{PREFIX}_battery_voltage") == "26.41"
    assert _state(hass, f"sensor.{PREFIX}_ac_output_voltage") == "230.12"
    assert _state(hass, f"sensor.{PREFIX}_device_state") == "charge"
