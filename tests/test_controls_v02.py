"""Tests for the dashboard controls: force charge buttons, setting numbers."""

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
    assert hass.states.get(f"number.{PREFIX}_charge_current_limit") is None
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
    """Numbers write settings in RAM only, within the device's range."""
    await _setup(hass, _entry())
    # Charge voltages are numbers too (since 1.1), behind the critical lock.
    assert hass.states.get(f"number.{PREFIX}_float_voltage") is not None
    entity_id = f"number.{PREFIX}_charge_current_limit"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "70.0"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 70

    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 50}, blocking=True
    )
    assert fake_mk3.device.writes == [(0x03, 4, 50)]
    assert fake_mk3.device.settings[4] == 50
    assert _state(hass, entity_id) == "50.0"

    # Above the device maximum is refused before anything is sent.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 80},
            blocking=True,
        )
    assert len(fake_mk3.device.writes) == 1


async def test_flag_binary_sensors(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """PowerAssist, weak AC and UPS are read-only; UPS is the inverse of its bit."""
    await _setup(hass, _entry())
    assert _state(hass, f"binary_sensor.{PREFIX}_powerassist") == "on"
    assert _state(hass, f"binary_sensor.{PREFIX}_weak_ac_input") == "off"
    assert _state(hass, f"binary_sensor.{PREFIX}_ups_function") == "on"
    # Since 1.1 they can also be switched, behind the critical lock.
    assert _state(hass, f"switch.{PREFIX}_powerassist") == "on"
    assert _state(hass, f"switch.{PREFIX}_ups_function") == "on"


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


async def test_solar_wind_priority_without_mask(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Without the mask of supported bits, the priority voltage decides (1.1.8).

    As on a MultiPlus-II (firmware 556): priority off, settings 0 and 60 have
    no range, setting 88 has one.
    """
    fake_mk3.device.settings[60] = 0x0010
    fake_mk3.device.no_range_settings = {0, 60}
    await _setup(hass, _entry())
    assert _state(hass, f"switch.{PREFIX}_solar_wind_priority") == "off"
    assert _state(hass, f"number.{PREFIX}_solar_wind_priority_voltage") == "27.6"


async def test_no_solar_wind_priority_without_its_voltage(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Without mask, flag or priority voltage, nothing is offered."""
    fake_mk3.device.settings[60] = 0x0010
    fake_mk3.device.no_range_settings = {0, 60}
    fake_mk3.device.missing_setting_info = {88}
    await _setup(hass, _entry())
    assert hass.states.get(f"switch.{PREFIX}_solar_wind_priority") is None
    assert hass.states.get(f"number.{PREFIX}_solar_wind_priority_voltage") is None


async def test_old_firmware_hides_writes(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Firmware before 412 gets no setting entities, before 506 no priority."""
    fake_mk3.device.firmware = 2623411
    await _setup(hass, _entry())
    assert hass.states.get(f"button.{PREFIX}_force_float") is not None
    assert hass.states.get(f"number.{PREFIX}_charge_current_limit") is None
    assert hass.states.get(f"switch.{PREFIX}_solar_wind_priority") is None
    assert hass.states.get(f"sensor.{PREFIX}_solar_wind_priority_voltage") is None


async def test_truncated_setting_info_completed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Missing ranges are read in long-frame mode during setup."""
    fake_mk3.device.truncate_setting_info = True
    await _setup(hass, _entry())
    state = hass.states.get(f"number.{PREFIX}_charge_current_limit")
    assert state is not None
    assert state.attributes["max"] == 70
    assert not fake_mk3.device.long_frames
