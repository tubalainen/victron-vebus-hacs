"""Tests for v0.4: ESS external control and Battery Operational Limits."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry(controls: bool = True, battery_limits: bool = False) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={
            CONF_ENABLE_CONTROLS: controls,
            CONF_ENABLE_BATTERY_LIMITS: battery_limits,
            CONF_SCAN_INTERVAL: 30,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def _call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def test_no_ess_without_assistant(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Without the ESS assistant no ESS entities are created."""
    await _setup(hass, _entry())
    assert hass.states.get(f"switch.{PREFIX}_ess_external_control") is None
    assert hass.states.get(f"number.{PREFIX}_ess_grid_setpoint") is None
    assert hass.states.get(f"sensor.{PREFIX}_ess_active_setpoint") is None


async def test_ess_external_control(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """The setpoint is only written while external control is on."""
    fake_mk3.device.enable_ess()
    entry = _entry()
    await _setup(hass, entry)
    control = f"switch.{PREFIX}_ess_external_control"
    setpoint = f"number.{PREFIX}_ess_grid_setpoint"
    assert _state(hass, control) == "off"
    assert _state(hass, setpoint) == "0.0"
    assert _state(hass, f"sensor.{PREFIX}_ess_active_setpoint") == "0"

    await _call(hass, "number", "set_value", {"entity_id": setpoint, "value": 500})
    assert fake_mk3.device.ram_writes == []

    # Home Assistant sign: +500 W = import. MK3 sign: -500.
    await _call(hass, "switch", "turn_on", {"entity_id": control})
    assert fake_mk3.device.ram_writes == [(0, 131, -500)]
    await _call(hass, "number", "set_value", {"entity_id": setpoint, "value": -300})
    assert fake_mk3.device.ram_writes[-1] == (0, 131, 300)

    # Refreshed periodically, not on every poll.
    coordinator = entry.runtime_data.coordinator
    count = len(fake_mk3.device.ram_writes)
    await coordinator._async_periodic_writes()
    assert len(fake_mk3.device.ram_writes) == count
    with patch("custom_components.victron_vebus.coordinator.ESS_REFRESH_INTERVAL", 0):
        await coordinator._async_periodic_writes()
    assert len(fake_mk3.device.ram_writes) == count + 1

    # Turning control off returns the assistant to 0 W.
    await _call(hass, "switch", "turn_off", {"entity_id": control})
    assert fake_mk3.device.ram_writes[-1] == (0, 131, 0)
    await coordinator._async_periodic_writes()
    assert fake_mk3.device.ram_writes[-1] == (0, 131, 0)


async def test_ess_flags(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Disable charge and disable feed-in switches."""
    fake_mk3.device.enable_ess()
    await _setup(hass, _entry())
    switch = f"switch.{PREFIX}_ess_disable_feed_in"
    assert _state(hass, switch) == "off"
    await _call(hass, "switch", "turn_on", {"entity_id": switch})
    assert fake_mk3.device.assistant_ram[0][132] == 0x02
    assert _state(hass, switch) == "on"
    await _call(
        hass, "switch", "turn_on", {"entity_id": f"switch.{PREFIX}_ess_disable_charge"}
    )
    assert fake_mk3.device.assistant_ram[0][132] == 0x03


async def test_ess_per_phase(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Three-phase systems get a setpoint per phase master."""
    device = fake_mk3.device
    device.phases = (1, 2, 3)
    device.units = {1: {16: 0}, 2: {16: 0}}
    device.enable_ess((0, 1, 2))
    await _setup(hass, _entry())
    await _call(
        hass,
        "switch",
        "turn_on",
        {"entity_id": f"switch.{PREFIX}_ess_external_control"},
    )
    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_ess_grid_setpoint_l2", "value": 250},
    )
    assert (1, 131, -250) in device.ram_writes
    assert hass.states.get(f"number.{PREFIX}_ess_grid_setpoint_l3") is not None
    assert device.address == 0


async def test_unload_releases_ess(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Unloading while in control writes 0 W."""
    fake_mk3.device.enable_ess()
    entry = _entry()
    await _setup(hass, entry)
    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_ess_grid_setpoint", "value": 100},
    )
    await _call(
        hass,
        "switch",
        "turn_on",
        {"entity_id": f"switch.{PREFIX}_ess_external_control"},
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake_mk3.device.ram_writes[-1] == (0, 131, 0)


async def test_battery_limits_disabled_by_default(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Without the option nothing is ever sent."""
    entry = _entry()
    await _setup(hass, entry)
    assert hass.states.get(f"number.{PREFIX}_battery_limit_charge_voltage") is None
    await entry.runtime_data.coordinator._async_periodic_writes()
    assert fake_mk3.device.bol_frames == []


async def test_battery_limits(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Limits start from the device settings and are sent on every poll."""
    entry = _entry(battery_limits=True)
    await _setup(hass, entry)
    assert _state(hass, f"number.{PREFIX}_battery_limit_charge_voltage") == "28.8"
    assert _state(hass, f"number.{PREFIX}_battery_limit_charge_current") == "70.0"
    assert _state(hass, f"number.{PREFIX}_battery_limit_discharge_current") == "1000.0"

    coordinator = entry.runtime_data.coordinator
    sent = len(fake_mk3.device.bol_frames)
    await coordinator._async_periodic_writes()
    assert len(fake_mk3.device.bol_frames) == sent + 1
    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_battery_limit_charge_current", "value": 25},
    )
    frame = fake_mk3.device.bol_frames[-1]
    assert int.from_bytes(frame[3:6], "little") == 250  # dA
    assert coordinator.data.battery_limits is not None
    assert coordinator.data.battery_limits.charge_current == pytest.approx(25)


async def test_battery_limits_need_firmware(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Firmware before 415 gets no battery limit entities."""
    fake_mk3.device.firmware = 2623414
    await _setup(hass, _entry(battery_limits=True))
    assert hass.states.get(f"number.{PREFIX}_battery_limit_charge_voltage") is None


async def test_disabling_battery_limits_asks_for_confirmation(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Turning the option off warns that the Multi must be restarted."""
    entry = _entry(battery_limits=True)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 30, CONF_ENABLE_BATTERY_LIMITS: False},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "disable_battery_limits"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENABLE_BATTERY_LIMITS] is False
