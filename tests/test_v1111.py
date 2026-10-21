"""Tests for 1.1.11: safety fixes from the architecture review."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.victron_vebus import actions
from custom_components.victron_vebus.const import (
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_ENABLE_CONTROLS,
    CONF_ENABLE_ESS_CONTROL,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.coordinator import VeBusCoordinator
from custom_components.victron_vebus.vebus import (
    Setting,
    VeBusClient,
    VeBusTimeoutError,
)

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"
LOCK = f"switch.{PREFIX}_allow_critical_changes"


def _entry(**options: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_ENABLE_CONTROLS: True, CONF_SCAN_INTERVAL: 30, **options},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> VeBusCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    return coordinator


async def _call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def _poll(coordinator: VeBusCoordinator, hass: HomeAssistant) -> None:
    state = await coordinator.client.poll()
    coordinator._async_on_state(state)
    await hass.async_block_till_done()


def _remote_frames(fake_mk3: FakeMK3) -> list[bytes]:
    return [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]


# ---------------------------------------------------------------------------
# Experimental functions are opt-in


async def test_ess_control_needs_its_option(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """With the ESS assistant but without the option, no ESS control."""
    fake_mk3.device.enable_ess()
    await _setup(hass, _entry())
    assert hass.states.get(f"switch.{PREFIX}_ess_external_control") is None
    assert hass.states.get(f"switch.{PREFIX}_ess_disable_charge") is None
    assert hass.states.get(f"number.{PREFIX}_ess_grid_setpoint") is None


async def test_ess_control_is_not_resumed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A restart of Home Assistant never resumes ESS control by itself."""
    mock_restore_cache(hass, [State(f"switch.{PREFIX}_ess_external_control", "on")])
    fake_mk3.device.enable_ess()
    coordinator = await _setup(hass, _entry(**{CONF_ENABLE_ESS_CONTROL: True}))
    assert coordinator.ess_control is False
    state = hass.states.get(f"switch.{PREFIX}_ess_external_control")
    assert state is not None
    assert state.state == "off"


async def test_ess_released_when_home_assistant_stops(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Stopping Home Assistant hands the setpoint back (0 W)."""
    fake_mk3.device.enable_ess()
    await _setup(hass, _entry(**{CONF_ENABLE_ESS_CONTROL: True}))
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
    assert fake_mk3.device.ram_writes[-1] == (0, 131, -100)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert fake_mk3.device.ram_writes[-1] == (0, 131, 0)


async def test_enabling_experimental_needs_acknowledgement(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Turning on ESS control or battery limits shows a warning first."""
    entry = _entry()
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            "alarm_issues": True,
            CONF_ENABLE_BATTERY_LIMITS: False,
            CONF_ENABLE_ESS_CONTROL: True,
        },
    )
    assert result["step_id"] == "enable_experimental"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"acknowledge": False}
    )
    assert result["errors"] == {"base": "acknowledge_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"acknowledge": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENABLE_ESS_CONTROL] is True


# ---------------------------------------------------------------------------
# Battery limits


async def test_battery_limits_survive_an_ess_failure(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A failing ESS write doesn't stop the battery limits."""
    fake_mk3.device.enable_ess()
    entry = _entry(**{CONF_ENABLE_BATTERY_LIMITS: True, CONF_ENABLE_ESS_CONTROL: True})
    coordinator = await _setup(hass, entry)
    coordinator.ess_control = True
    sent = len(fake_mk3.device.bol_frames)
    with patch.object(
        coordinator,
        "_async_write_ess_setpoints",
        side_effect=VeBusTimeoutError("no reply"),
    ):
        await coordinator._async_periodic_writes()
    assert len(fake_mk3.device.bol_frames) == sent + 1


async def test_stale_battery_limits_raise_an_issue(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Limits that stop getting through raise a critical repair issue."""
    entry = _entry(**{CONF_ENABLE_BATTERY_LIMITS: True})
    coordinator = await _setup(hass, entry)
    issues = ir.async_get(hass)
    issue_id = f"battery_limits_stale_{entry.entry_id}"
    await coordinator._async_periodic_writes()
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    coordinator._battery_limits_sent_at = time.monotonic() - 200
    with patch.object(
        coordinator.client,
        "send_battery_limits",
        side_effect=VeBusTimeoutError("no reply"),
    ):
        await coordinator._async_periodic_writes()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.CRITICAL

    await coordinator._async_periodic_writes()
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_battery_limit_voltage_is_bounded(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A charge voltage outside the absorption range is refused."""
    await _setup(hass, _entry(**{CONF_ENABLE_BATTERY_LIMITS: True}))
    entity_id = f"number.{PREFIX}_battery_limit_charge_voltage"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "number", "set_value", {"entity_id": entity_id, "value": 28})
    assert err.value.translation_key == "critical_locked"
    await _call(hass, "switch", "turn_on", {"entity_id": LOCK})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "number", "set_value", {"entity_id": entity_id, "value": 60})
    assert err.value.translation_key == "invalid_value"
    await _call(hass, "number", "set_value", {"entity_id": entity_id, "value": 28})
    assert hass.states.get(entity_id).state == "28.0"


# ---------------------------------------------------------------------------
# Passthru and the remote panel


async def test_passthru_follows_the_device(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Passthru is what the interface reports, and a restart clears it."""
    coordinator = await _setup(hass, _entry())
    passthru = f"switch.{PREFIX}_stop_grid_charging_0_a"
    # Set elsewhere (e.g. before a restart of Home Assistant).
    fake_mk3.device.panel_state = (3, 0)
    await _poll(coordinator, hass)
    assert coordinator.passthru is True
    fake_mk3.device.panel_state = (3, 100)
    await _poll(coordinator, hass)
    assert coordinator.passthru is False
    assert coordinator.last_remote_limit == 10.0

    coordinator.passthru = True
    coordinator.client.stats.boot_frames += 1  # the device restarted
    fake_mk3.device.panel_state = (4, 0)  # and the panel state is unknown
    await _poll(coordinator, hass)
    assert coordinator.passthru is False
    assert hass.states.get(passthru).state == "off"


async def test_passthru_keeps_the_remote_mode(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The remote mode is sent, not the mode in effect.

    With the front switch on "charger only" and the remote on, the mode in
    effect is charger only; sending that would lock the remote to it.
    """
    fake_mk3.device.switch_register = 0x10 | 0x03 | 0x04  # remote on, charging only
    coordinator = await _setup(hass, _entry())
    assert coordinator.data.config is not None
    assert coordinator.data.config.mode.value == "charger_only"
    assert coordinator.data.config.remote_mode.value == "on"
    await _call(hass, "switch", "turn_on", {"entity_id": LOCK})
    await _call(
        hass,
        "switch",
        "turn_on",
        {"entity_id": f"switch.{PREFIX}_stop_grid_charging_0_a"},
    )
    assert _remote_frames(fake_mk3)[-1][3] == 3  # on


def test_resume_limit_has_no_fixed_default() -> None:
    """Without any known limit there is nothing to resume with."""
    state = SimpleNamespace(config=None, setting_value=lambda setting: None)
    coordinator = SimpleNamespace(
        last_remote_limit=None, last_current_limit=None, data=state
    )
    assert actions.resume_limit(coordinator) is None  # type: ignore[arg-type]
    coordinator.last_current_limit = 8.0
    assert actions.resume_limit(coordinator) == 8.0  # type: ignore[arg-type]
    coordinator.last_remote_limit = 10.0
    assert actions.resume_limit(coordinator) == 10.0  # type: ignore[arg-type]


async def test_leaving_passthru_without_a_known_limit_is_refused(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """No guessed limit: the user gives one."""
    coordinator = await _setup(hass, _entry())
    coordinator.last_remote_limit = None
    coordinator.last_current_limit = None
    with (
        patch.object(actions, "resume_limit", return_value=None),
        pytest.raises(ServiceValidationError) as err,
    ):
        await actions.async_set_passthru(coordinator, False, None)
    assert err.value.translation_key == "limit_unknown"


# ---------------------------------------------------------------------------
# Alarms with unknown LEDs


async def test_unknown_leds_keep_the_alarm(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """0x1F/0x1F says nothing about the alarms: no "cleared", issue kept."""
    entry = _entry()
    coordinator = await _setup(hass, entry)
    issues = ir.async_get(hass)
    issue_id = f"low_battery_alarm_{entry.entry_id}"
    fake_mk3.device.leds_on |= 0x40
    await _poll(coordinator, hass)
    assert issues.async_get_issue(DOMAIN, issue_id) is not None

    fake_mk3.device.leds_on = fake_mk3.device.leds_blink = 0x1F
    await _poll(coordinator, hass)
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    assert coordinator.alarms["low_battery"].value == "alarm"
    state = hass.states.get(f"sensor.{PREFIX}_low_battery_alarm")
    assert state is not None
    assert state.state == "unknown"


# ---------------------------------------------------------------------------
# Client


async def _client(fake: FakeMK3) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


async def test_reconnect_forgets_unsaved_settings() -> None:
    """Changes made before a new connection can't be saved afterwards."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert client.unsaved_settings == {4}
    await client.close()
    await client.connect()
    assert client.unsaved_settings == set()
    await client.close()


async def test_address_is_selected_again_after_a_lost_reply() -> None:
    """A lost reply to 'A' doesn't count as the address being selected."""
    fake = FakeMK3()
    client = await _client(fake)
    client._readdress = True
    fake.device.drop_replies["A"] = 1
    with pytest.raises(VeBusTimeoutError):
        await client.read_led()
    assert client._readdress is True
    before = len(fake.device.received)
    await client.read_led()
    assert any(frame[2] == ord("A") for frame in fake.device.received[before:])
    assert client._readdress is False
    await client.close()


async def test_long_frames_never_send_off() -> None:
    """Entering long frames doesn't send back an "off" panel state."""
    fake = FakeMK3()
    client = await _client(fake)
    fake.device.panel_state = (4, 90)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    long_frames = [
        frame
        for frame in fake.device.received
        if frame[2] == ord("S") and len(frame) > 9 and frame[7] == 0xD0
    ]
    assert long_frames[-1][3:6] == bytes([0, 0, 0])
    await client.close()
