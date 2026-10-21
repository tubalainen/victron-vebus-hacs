"""Tests for setting up the integration and its entities."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .vebus.simulator import FakeMK3


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_entities(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Sensors reflect the simulated MultiPlus-II."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    def state(entity_id: str) -> str:
        current = hass.states.get(entity_id)
        assert current is not None, entity_id
        return current.state

    assert state("sensor.victron_ve_bus_battery_voltage") == "26.41"
    assert float(state("sensor.victron_ve_bus_battery_current")) == pytest.approx(12.3)
    assert state("sensor.victron_ve_bus_battery_power") == "300.0"
    assert state("sensor.victron_ve_bus_ac_input_power") == "350.0"
    assert state("sensor.victron_ve_bus_ac_output_power") == "50.0"
    assert state("sensor.victron_ve_bus_ac_input_voltage") == "230.12"
    assert state("sensor.victron_ve_bus_ac_output_current") == "-1.2"
    assert state("sensor.victron_ve_bus_device_state") == "charge"
    assert state("sensor.victron_ve_bus_charge_state") == "float"
    assert state("sensor.victron_ve_bus_mode") == "on"
    assert state("sensor.victron_ve_bus_state_of_charge") == "85.0"
    assert state("sensor.victron_ve_bus_overload_alarm") == "ok"
    assert state("sensor.victron_ve_bus_absorption_voltage") == "28.8"
    assert state("sensor.victron_ve_bus_firmware_version") == "506 (2623506)"
    assert state("binary_sensor.victron_ve_bus_ac_input_connected") == "on"
    assert state("binary_sensor.victron_ve_bus_relay") == "on"
    assert state("switch.victron_ve_bus_keep_awake") == "on"
    # Single phase: no per phase entities and no controls by default.
    assert hass.states.get("sensor.victron_ve_bus_ac_input_voltage_l2") is None
    assert hass.states.get("select.victron_ve_bus_remote_mode") is None
    assert (
        hass.states.get("number.victron_ve_bus_remote_ac_input_current_limit") is None
    )

    registry = er.async_get(hass)
    entity = registry.async_get("sensor.victron_ve_bus_battery_voltage")
    assert entity is not None and entity.unique_id == "HQ123_0_battery_voltage"
    # LEDs: mains and float on (0x09), shown as diagnostic enum sensors.
    assert state("sensor.victron_ve_bus_mains_led") == "on"
    assert state("sensor.victron_ve_bus_float_led") == "on"
    assert state("sensor.victron_ve_bus_bulk_led") == "off"
    assert hass.states.get("binary_sensor.victron_ve_bus_mains_led") is None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_asleep(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Setup is retried while the device does not respond."""
    fake_mk3.device.asleep = True
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_three_phase_entities(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Every detected phase gets its own entities."""
    fake_mk3.device.phases = (1, 2, 3)
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.victron_ve_bus_ac_input_voltage_l1") is not None
    assert hass.states.get("sensor.victron_ve_bus_ac_input_voltage_l3") is not None
    registry = er.async_get(hass)
    entity = registry.async_get("sensor.victron_ve_bus_ac_input_voltage_l3")
    assert entity is not None and entity.unique_id == "HQ123_0_ac_input_voltage_l3"


async def test_controls(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """The current limit number and the service drive the 'S' command."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_ENABLE_CONTROLS: True, CONF_SCAN_INTERVAL: 30},
    )
    await _setup(hass, entry)
    number = "number.victron_ve_bus_remote_ac_input_current_limit"
    # The mode is a guided device action, not a dashboard select.
    assert hass.states.get("select.victron_ve_bus_remote_mode") is None
    assert hass.states.get(number).state == "16.0"

    def sent() -> list[bytes]:
        return [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 10.5}, blocking=True
    )
    assert hass.states.get(number).state == "10.5"
    assert sent()[-1][3] == 3  # the mode stays on

    await hass.services.async_call(
        DOMAIN,
        "set_remote_panel_state",
        {
            "config_entry_id": entry.entry_id,
            "mode": "charger_only",
            "acknowledge_risk": True,
        },
        blocking=True,
    )
    assert sent()[-1][3] == 1  # charger only
    assert hass.states.get("sensor.victron_ve_bus_mode").state == "charger_only"
    # Without a limit the actual limit is kept.
    assert hass.states.get(number).state == "10.5"

    await hass.services.async_call(
        DOMAIN,
        "set_remote_panel_state",
        {
            "config_entry_id": entry.entry_id,
            "mode": "on",
            "current_limit": 12,
            "acknowledge_risk": True,
        },
        blocking=True,
    )
    assert sent()[-1][3] == 3
    assert hass.states.get(number).state == "12.0"

    count = len(sent())
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_remote_panel_state",
            {
                "config_entry_id": entry.entry_id,
                "mode": "off",
                "acknowledge_risk": False,
            },
            blocking=True,
        )
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_remote_panel_state",
            {"config_entry_id": entry.entry_id, "mode": "off"},
            blocking=True,
        )
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_remote_panel_state",
            {
                "config_entry_id": entry.entry_id,
                "mode": "turbo",
                "acknowledge_risk": True,
            },
            blocking=True,
        )
    assert len(sent()) == count


async def test_service_needs_control(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """A monitoring-only entry refuses the remote panel action."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_remote_panel_state",
            {
                "config_entry_id": config_entry.entry_id,
                "mode": "off",
                "acknowledge_risk": True,
            },
            blocking=True,
        )
    assert err.value.translation_key == "controls_disabled"
    assert not [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]


async def test_keep_awake_switch(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """The keep awake switch controls the MK3 standby flag."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data.coordinator
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.victron_ve_bus_keep_awake"},
        blocking=True,
    )
    assert not coordinator.keep_awake
    assert hass.states.get("switch.victron_ve_bus_keep_awake").state == "off"
    await coordinator.client.poll(interface_flags=coordinator._interface_flags())
    assert fake_mk3.device.interface_flags == 0x01


async def test_unavailable_and_recovery(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Entities become unavailable when the interface is unplugged and recover."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_SCAN_INTERVAL: 1},
    )
    await _setup(hass, entry)
    entity_id = "sensor.victron_ve_bus_battery_voltage"
    assert hass.states.get(entity_id).state == "26.41"

    fake_mk3.writers[-1].disconnect()
    async with asyncio.timeout(5):
        while hass.states.get(entity_id).state != STATE_UNAVAILABLE:
            await asyncio.sleep(0.05)
    fake_mk3.device.ram[4] = 2700
    async with asyncio.timeout(10):
        while hass.states.get(entity_id).state != "27.0":
            await asyncio.sleep(0.05)
    assert len(fake_mk3.writers) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Diagnostics contain the state and communication statistics."""
    await _setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["firmware"] == 2623506
    assert diagnostics["phases"] == [1]
    assert diagnostics["state"]["dc"]["voltage"] == pytest.approx(26.41)
    assert diagnostics["statistics"]["frames_received"] > 0
    # No setting has been written yet.
    assert diagnostics["last_write"] is None
