"""Tests for the v0.2 controls: force charge buttons, setting numbers, flag switches."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry(controls: bool = True) -> MockConfigEntry:
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


async def test_no_setting_controls_without_option(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Without the controls option only read-only entities exist."""
    await _setup(hass, _entry(controls=False))
    assert hass.states.get(f"button.{PREFIX}_force_absorption") is None
    assert hass.states.get(f"number.{PREFIX}_float_voltage") is None
    assert hass.states.get(f"switch.{PREFIX}_powerassist") is None
    # The read-only solar & wind priority voltage sensor is always there.
    assert _state(hass, f"sensor.{PREFIX}_solar_wind_priority_voltage") == "27.6"


async def test_force_charge_buttons(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Pressing a button forces the charge state."""
    await _setup(hass, _entry())
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": f"button.{PREFIX}_force_absorption"},
        blocking=True,
    )
    assert fake_mk3.device.forced == [2]
    assert _state(hass, f"sensor.{PREFIX}_charge_state") == "forced_absorption"


async def test_setting_numbers(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Numbers write settings in RAM only and validate against each other."""
    await _setup(hass, _entry())
    entity_id = f"number.{PREFIX}_float_voltage"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "27.6"
    assert state.attributes["min"] == 24.0
    assert state.attributes["max"] == 32.0
    assert state.attributes["step"] == pytest.approx(0.01)

    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 27.0}, blocking=True
    )
    assert fake_mk3.device.writes == [(0x03, 3, 2700)]
    assert _state(hass, entity_id) == "27.0"
    assert _state(hass, f"sensor.{PREFIX}_float_voltage") == "27.0"

    # Float above absorption (28.8 V) is refused before anything is sent.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 29.0},
            blocking=True,
        )
    assert len(fake_mk3.device.writes) == 1

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_charge_current_limit", "value": 50},
        blocking=True,
    )
    assert fake_mk3.device.settings[4] == 50


async def test_flag_switches(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Flag switches change one bit each; UPS is the inverse of its bit."""
    await _setup(hass, _entry())
    assert _state(hass, f"switch.{PREFIX}_powerassist") == "on"
    assert _state(hass, f"switch.{PREFIX}_weak_ac_input") == "off"
    assert _state(hass, f"switch.{PREFIX}_ups_function") == "on"

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": f"switch.{PREFIX}_weak_ac_input"},
        blocking=True,
    )
    assert fake_mk3.device.settings[0] == 0x89B0 | 0x4000
    assert _state(hass, f"switch.{PREFIX}_weak_ac_input") == "on"

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": f"switch.{PREFIX}_ups_function"},
        blocking=True,
    )
    assert fake_mk3.device.settings[0] == 0x89B0 | 0x4000 | 0x0008
    assert _state(hass, f"switch.{PREFIX}_ups_function") == "off"
    assert all(flags == 0x03 for flags, _, _ in fake_mk3.device.writes)


async def test_solar_wind_priority(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """The priority switch clears bit 9 of setting 60; the voltage is capped."""
    await _setup(hass, _entry())
    switch = f"switch.{PREFIX}_solar_wind_priority"
    assert _state(hass, switch) == "on"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    assert fake_mk3.device.settings[60] == 0x0010
    assert _state(hass, switch) == "off"

    number = f"number.{PREFIX}_solar_wind_priority_voltage"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": 29.0}, blocking=True
        )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 27.2}, blocking=True
    )
    assert fake_mk3.device.settings[88] == 2720


async def test_old_firmware_hides_writes(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Firmware before 412 gets no setting entities, before 506 no priority."""
    fake_mk3.device.firmware = 2623411
    await _setup(hass, _entry())
    assert hass.states.get(f"button.{PREFIX}_force_float") is not None
    assert hass.states.get(f"number.{PREFIX}_float_voltage") is None
    assert hass.states.get(f"switch.{PREFIX}_powerassist") is None
    assert hass.states.get(f"switch.{PREFIX}_solar_wind_priority") is None
    assert hass.states.get(f"sensor.{PREFIX}_solar_wind_priority_voltage") is None


async def test_truncated_setting_info_completed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Missing ranges are read in long-frame mode during setup."""
    fake_mk3.device.truncate_setting_info = True
    await _setup(hass, _entry())
    state = hass.states.get(f"number.{PREFIX}_absorption_voltage")
    assert state is not None
    assert state.attributes["max"] == 32.0
    assert not fake_mk3.device.long_frames
