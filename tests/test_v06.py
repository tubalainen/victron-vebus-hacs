"""Tests for v0.6: energy sensors, alarm notifications and status sensors."""

from __future__ import annotations

from dataclasses import replace

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.victron_vebus.const import (
    CONF_ALARM_ISSUES,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.coordinator import VeBusCoordinator
from custom_components.victron_vebus.energy import EnergyCounter

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry(**options: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_SCAN_INTERVAL: 30, **options},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> VeBusCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    return coordinator


def _value(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return float(state.state)


# ---------------------------------------------------------------------------
# Energy counter


def test_energy_counter_trapezoid() -> None:
    """Power is integrated with the trapezoidal rule, one direction only."""
    counter = EnergyCounter(direction=1, max_gap=60)
    assert not counter.add(0.0, 1000)
    assert counter.add(36.0, 1000)  # 1 kW for 36 s = 0.01 kWh
    assert counter.total == pytest.approx(0.01)
    assert counter.add(72.0, 0)  # ramp down: half of it
    assert counter.total == pytest.approx(0.015)
    assert not counter.add(108.0, -500)  # other direction counts as zero
    assert counter.total == pytest.approx(0.015)

    exported = EnergyCounter(direction=-1, max_gap=60)
    exported.add(0.0, -1000)
    exported.add(36.0, -1000)
    assert exported.total == pytest.approx(0.01)


def test_energy_counter_gaps() -> None:
    """Gaps, missing values and repeated snapshots add nothing."""
    counter = EnergyCounter(direction=1, max_gap=60)
    counter.add(0.0, 1000)
    assert not counter.add(0.0, 1000)  # the same snapshot again
    assert not counter.add(100.0, 1000)  # longer than max_gap
    assert counter.total == 0
    assert counter.add(110.0, 1000)
    assert counter.total == pytest.approx(1000 * 10 / 3_600_000)
    total = counter.total
    assert not counter.add(120.0, None)
    assert not counter.add(130.0, 1000)  # first sample after a missing value
    counter.reset()
    assert not counter.add(140.0, 1000)
    assert counter.total == total


# ---------------------------------------------------------------------------
# Energy sensors


async def test_energy_sensors(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """The energy sensors integrate the power readings of the device."""
    coordinator = await _setup(hass, _entry())
    ids = {
        key: f"sensor.{PREFIX}_{key}"
        for key in (
            "ac_input_energy_imported",
            "ac_input_energy_exported",
            "ac_output_energy",
            "battery_energy_charged",
            "battery_energy_discharged",
        )
    }
    state = hass.states.get(ids["battery_energy_charged"])
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["device_class"] == "energy"
    before = {key: _value(hass, entity_id) for key, entity_id in ids.items()}

    # 36 s later with the same readings: AC in 350 W, AC out 50 W, battery 300 W.
    data = coordinator.data
    coordinator.async_set_updated_data(replace(data, timestamp=data.timestamp + 36))
    await hass.async_block_till_done()
    delta = {key: _value(hass, ids[key]) - before[key] for key in ids}
    assert delta["ac_input_energy_imported"] == pytest.approx(0.0035, abs=1e-4)
    assert delta["ac_output_energy"] == pytest.approx(0.0005, abs=1e-4)
    assert delta["battery_energy_charged"] == pytest.approx(0.003, abs=1e-4)
    assert delta["ac_input_energy_exported"] == 0
    assert delta["battery_energy_discharged"] == 0


async def test_energy_not_counted_while_unavailable(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The time the device is unreachable adds no energy."""
    coordinator = await _setup(hass, _entry())
    entity_id = f"sensor.{PREFIX}_battery_energy_charged"
    data = coordinator.data
    coordinator.async_set_updated_data(replace(data, timestamp=data.timestamp + 36))
    await hass.async_block_till_done()
    first = _value(hass, entity_id)

    coordinator.async_set_update_error(TimeoutError("unplugged"))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    coordinator.async_set_updated_data(replace(data, timestamp=data.timestamp + 72))
    await hass.async_block_till_done()
    assert _value(hass, entity_id) == pytest.approx(first)
    coordinator.async_set_updated_data(replace(data, timestamp=data.timestamp + 108))
    await hass.async_block_till_done()
    assert _value(hass, entity_id) == pytest.approx(first + 0.003, abs=1e-4)


async def test_energy_restored(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Totals continue from the last value after a restart."""
    entity_id = f"sensor.{PREFIX}_ac_output_energy"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "12.5"),
                {"native_value": 12.5, "native_unit_of_measurement": "kWh"},
            )
        ],
    )
    await _setup(hass, _entry())
    assert _value(hass, entity_id) >= 12.5


# ---------------------------------------------------------------------------
# Alarms


async def _poll(coordinator: VeBusCoordinator, hass: HomeAssistant) -> None:
    state = await coordinator.client.poll()
    coordinator._async_on_state(state)
    await hass.async_block_till_done()


def _event(hass: HomeAssistant) -> tuple[str | None, dict[str, object]]:
    state = hass.states.get(f"event.{PREFIX}_alarm")
    assert state is not None
    return state.attributes.get("event_type"), dict(state.attributes)


async def test_alarm_events_and_issues(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A pre-alarm fires an event; an alarm also raises a repair issue."""
    entry = _entry()
    coordinator = await _setup(hass, entry)
    issues = ir.async_get(hass)
    issue_id = f"low_battery_alarm_{entry.entry_id}"
    await _poll(coordinator, hass)
    assert _event(hass)[0] is None
    assert coordinator.alarms["low_battery"] == "ok"

    fake_mk3.device.leds_blink = 0x40  # low battery LED blinking: pre-alarm
    await _poll(coordinator, hass)
    event_type, attributes = _event(hass)
    assert event_type == "low_battery_warning"
    assert attributes["alarm"] == "low_battery"
    assert attributes["level"] == "warning"
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    fake_mk3.device.leds_blink = 0
    fake_mk3.device.leds_on |= 0x40  # on: alarm
    await _poll(coordinator, hass)
    assert _event(hass)[0] == "low_battery_alarm"
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {"name": "Victron VE.Bus"}
    assert hass.states.get(f"sensor.{PREFIX}_low_battery_alarm").state == "alarm"

    fake_mk3.device.leds_on &= ~0x40
    await _poll(coordinator, hass)
    assert _event(hass)[0] == "low_battery_cleared"
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_alarm_issue_option_and_unload(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Issues can be turned off; unloading removes them."""
    fake_mk3.device.leds_on |= 0x20  # overload alarm from the start
    entry = _entry(**{CONF_ALARM_ISSUES: False})
    coordinator = await _setup(hass, entry)
    await _poll(coordinator, hass)
    assert _event(hass)[0] == "overload_alarm"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"overload_alarm_{entry.entry_id}") is None

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ALARM_ISSUES: True}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    await _poll(coordinator, hass)
    assert issues.async_get_issue(DOMAIN, f"overload_alarm_{entry.entry_id}")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"overload_alarm_{entry.entry_id}") is None


# ---------------------------------------------------------------------------
# Status sensors


async def test_status_sensors(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Read-only settings and the virtual switch are shown."""
    await _setup(hass, _entry())

    def state(entity_id: str) -> str:
        current = hass.states.get(entity_id)
        assert current is not None, entity_id
        return current.state

    assert float(state(f"sensor.{PREFIX}_inverter_output_voltage")) == 230
    assert float(state(f"sensor.{PREFIX}_low_battery_shutdown_voltage")) == 22.0
    assert float(state(f"sensor.{PREFIX}_low_battery_restart_voltage")) == 24.0
    assert state(f"sensor.{PREFIX}_charge_characteristic") == "fixed_storage"
    # Setting 9 is scaled to minutes (8 x 60) and shown in hours.
    absorption = hass.states.get(f"sensor.{PREFIX}_maximum_absorption_time")
    assert absorption is not None
    assert float(absorption.state) == 8
    assert absorption.attributes["unit_of_measurement"] == "h"
    assert state(f"binary_sensor.{PREFIX}_virtual_switch") == "off"


# ---------------------------------------------------------------------------
# LED sensors (0.6.0-beta2)


async def test_led_sensors(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Each LED is a sensor showing off, on or blinking."""
    coordinator = await _setup(hass, _entry())
    entity_id = f"sensor.{PREFIX}_low_battery_led"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["options"] == ["off", "on", "blink"]
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is None
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC

    fake_mk3.device.leds_blink = 0x40
    await _poll(coordinator, hass)
    assert hass.states.get(entity_id).state == "blink"
    assert hass.states.get(f"sensor.{PREFIX}_mains_led").state == "on"

    # 0x1F/0x1F: the interface could not read the LEDs.
    fake_mk3.device.leds_on = fake_mk3.device.leds_blink = 0x1F
    await _poll(coordinator, hass)
    assert hass.states.get(f"sensor.{PREFIX}_mains_led").state == "unknown"
    assert hass.states.get(entity_id).state == "unknown"
    assert (
        hass.states.get(f"binary_sensor.{PREFIX}_ac_input_connected").state == "unknown"
    )


async def test_led_binary_sensors_removed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The LED binary sensors of earlier versions are removed."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "HQ123_0_led_mains", config_entry=entry
    ).entity_id
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(old) is None
    assert registry.async_get(f"sensor.{PREFIX}_mains_led") is not None


async def test_charge_characteristic_fixed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Charge characteristic 1 is a fixed absorption time without storage."""
    fake_mk3.device.settings[10] = 1
    await _setup(hass, _entry())
    state = hass.states.get(f"sensor.{PREFIX}_charge_characteristic")
    assert state is not None
    assert state.state == "fixed"
