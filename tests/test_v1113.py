"""Tests for 1.1.13: battery temperature, the full 'L' reply, RAM 11 and 12."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ALARM_ISSUES,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.coordinator import VeBusCoordinator
from custom_components.victron_vebus.vebus import RamVar, VeBusClient

from .vebus.simulator import FakeMK3, SimulatedMultiPlus

PREFIX = "victron_ve_bus"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_SCAN_INTERVAL: 30},
    )


async def _setup(hass: HomeAssistant) -> VeBusCoordinator:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    return coordinator


async def _refresh(hass: HomeAssistant, coordinator: VeBusCoordinator) -> None:
    coordinator._async_on_state(await coordinator.client.poll(full=True))
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def _client(device: SimulatedMultiPlus) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=FakeMK3(device), request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


# ---------------------------------------------------------------------------
# Battery temperature (RAM 21)


async def test_battery_temperature(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """RAM 21 is a temperature sensor in °C, below zero too."""
    coordinator = await _setup(hass)
    sensor = f"sensor.{PREFIX}_battery_temperature"
    assert _state(hass, sensor) == "21.5"
    assert hass.states.get(sensor).attributes["unit_of_measurement"] == "°C"
    fake_mk3.device.ram[21] = 535  # half kelvin
    await _refresh(hass, coordinator)
    assert _state(hass, sensor) == "-5.5"


async def test_no_battery_temperature_when_not_described(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A device that doesn't know RAM 21 gets no sensor and no request for it."""
    fake_mk3.device.unsupported_ram_vars = {21}
    coordinator = await _setup(hass)
    assert hass.states.get(f"sensor.{PREFIX}_battery_temperature") is None
    assert all(21 not in ids for _, ids in coordinator.client._slow_queue)


async def test_start_without_reply_for_battery_temperature() -> None:
    """RAM 21 is optional: no reply to its info doesn't stop the start."""
    device = SimulatedMultiPlus()
    device.unanswered_ram_var_info = {21}
    client = await _client(device)
    assert client.ram_var_info[RamVar.BATTERY_TEMPERATURE] is None
    await client.close()


# ---------------------------------------------------------------------------
# RAM 11 and 12 without RAM 10


async def test_relay_read_without_virtual_switch(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """As on a MultiPlus-II (fw 556): RAM 10 unknown, 11 and 12 still read."""
    fake_mk3.device.unsupported_ram_vars = {10}
    fake_mk3.device.ram[12] = 0x20  # relay closed
    coordinator = await _setup(hass)
    await _refresh(hass, coordinator)
    assert hass.states.get(f"binary_sensor.{PREFIX}_virtual_switch") is None
    assert _state(hass, f"binary_sensor.{PREFIX}_relay") == "on"
    assert _state(hass, f"binary_sensor.{PREFIX}_ignore_ac_input") == "off"


# ---------------------------------------------------------------------------
# The full 'L' reply


async def test_vebus_error_code_and_ripple_alarm(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The alarm byte gives the ripple alarm, the status byte the error code."""
    coordinator = await _setup(hass)
    error = f"sensor.{PREFIX}_ve_bus_error_code"
    ripple = f"sensor.{PREFIX}_ripple_alarm"
    assert _state(hass, error) == "0"
    assert _state(hass, ripple) == "ok"
    fake_mk3.device.led_status_flags = 0x03
    fake_mk3.device.led_alarm_flags = 0x08
    await _refresh(hass, coordinator)
    assert _state(hass, error) == "3"
    assert _state(hass, ripple) == "warning"
    fake_mk3.device.led_alarm_flags = 0x80
    await _refresh(hass, coordinator)
    assert _state(hass, ripple) == "alarm"


async def test_short_led_reply_has_no_extra_sensors(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """An 'L' reply with only the LED bytes works as before."""
    fake_mk3.device.short_led_reply = True
    await _setup(hass)
    assert hass.states.get(f"sensor.{PREFIX}_ve_bus_error_code") is None
    assert hass.states.get(f"sensor.{PREFIX}_ripple_alarm") is None
    assert _state(hass, f"sensor.{PREFIX}_overload_alarm") == "ok"


@pytest.mark.parametrize(
    ("alarm_flags", "expected"),
    [
        (0x00, ["ok", "ok", "ok", "ok"]),
        (0x01, ["warning", "ok", "ok", "ok"]),
        (0x22, ["ok", "alarm", "ok", "ok"]),
        (0x44, ["ok", "ok", "alarm", "ok"]),
    ],
)
async def test_led_conditions_and_block_mode(
    alarm_flags: int, expected: list[str]
) -> None:
    """Warning bits 0..3, alarm bits 4..7 (an alarm wins); block mode bit 7."""
    device = SimulatedMultiPlus(led_alarm_flags=alarm_flags, led_status_flags=0x80)
    client = await _client(device)
    leds = await client.read_led()
    assert [leds.condition(i).value for i in range(4)] == expected
    assert leds.block_mode is True
    assert leds.vebus_error == 0
    await client.close()


# ---------------------------------------------------------------------------
# Ripple and VE.Bus error alarms (1.1.14)


async def _alarm_setup(
    hass: HomeAssistant, **options: object
) -> tuple[VeBusCoordinator, MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_SCAN_INTERVAL: 30, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    await _refresh(hass, coordinator)
    return coordinator, entry


def _event(hass: HomeAssistant) -> tuple[str | None, dict[str, object]]:
    state = hass.states.get(f"event.{PREFIX}_alarm")
    assert state is not None
    return state.attributes.get("event_type"), dict(state.attributes)


async def test_ripple_alarm_event_and_issue(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A ripple warning fires an event; a ripple alarm also raises an issue."""
    coordinator, entry = await _alarm_setup(hass)
    issue_id = f"ripple_alarm_{entry.entry_id}"
    issues = ir.async_get(hass)
    assert coordinator.alarms["ripple"] == "ok"
    assert _event(hass)[0] is None

    fake_mk3.device.led_alarm_flags = 0x08
    await _refresh(hass, coordinator)
    event_type, attributes = _event(hass)
    assert event_type == "ripple_warning"
    assert attributes["alarm"] == "ripple"
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    fake_mk3.device.led_alarm_flags = 0x80
    await _refresh(hass, coordinator)
    assert _event(hass)[0] == "ripple_alarm"
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "ripple_alarm"

    fake_mk3.device.led_alarm_flags = 0
    await _refresh(hass, coordinator)
    assert _event(hass)[0] == "ripple_cleared"
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_vebus_error_event_and_issue(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A VE.Bus error code raises an issue naming it; a new code fires again."""
    coordinator, entry = await _alarm_setup(hass)
    issue_id = f"vebus_error_alarm_{entry.entry_id}"
    issues = ir.async_get(hass)
    assert coordinator.alarms["vebus_error"] == "ok"

    fake_mk3.device.led_status_flags = 0x03
    await _refresh(hass, coordinator)
    event_type, attributes = _event(hass)
    assert event_type == "vebus_error_alarm"
    assert attributes["error_code"] == 3
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"name": "Victron VE.Bus", "code": "3"}

    # Block mode (bit 7) is not an error, a different code is.
    fake_mk3.device.led_status_flags = 0x80 | 0x08
    await _refresh(hass, coordinator)
    assert _event(hass)[1]["error_code"] == 8
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["code"] == "8"

    fake_mk3.device.led_status_flags = 0x80
    await _refresh(hass, coordinator)
    assert _event(hass)[0] == "vebus_error_cleared"
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_l_alarms_without_issues_option_and_on_unload(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The issue option covers the new alarms; unloading removes their issues."""
    fake_mk3.device.led_alarm_flags = 0x80
    fake_mk3.device.led_status_flags = 0x05
    coordinator, entry = await _alarm_setup(hass, **{CONF_ALARM_ISSUES: False})
    issues = ir.async_get(hass)
    assert coordinator.alarms["ripple"] == "alarm"
    assert issues.async_get_issue(DOMAIN, f"ripple_alarm_{entry.entry_id}") is None

    hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 30})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    await _refresh(hass, coordinator)
    assert issues.async_get_issue(DOMAIN, f"ripple_alarm_{entry.entry_id}")
    assert issues.async_get_issue(DOMAIN, f"vebus_error_alarm_{entry.entry_id}")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"ripple_alarm_{entry.entry_id}") is None
    assert issues.async_get_issue(DOMAIN, f"vebus_error_alarm_{entry.entry_id}") is None


async def test_short_led_reply_raises_no_l_alarms(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Without the alarm and status bytes there is nothing to report."""
    fake_mk3.device.short_led_reply = True
    coordinator, _ = await _alarm_setup(hass)
    assert "ripple" not in coordinator.alarms
    assert "vebus_error" not in coordinator.alarms
