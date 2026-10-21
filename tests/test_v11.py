"""Tests for 1.1: critical controls as entities, the lock, saving and actions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.coordinator import VeBusCoordinator

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"
LOCK = f"switch.{PREFIX}_allow_critical_changes"
MODE = f"select.{PREFIX}_mode"


def _entry(controls: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_ENABLE_CONTROLS: controls, CONF_SCAN_INTERVAL: 30},
    )


async def _setup(
    hass: HomeAssistant, controls: bool = True
) -> tuple[MockConfigEntry, VeBusCoordinator]:
    entry = _entry(controls)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entry.runtime_data.coordinator


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


def _remote_frames(fake_mk3: FakeMK3) -> list[bytes]:
    return [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]


async def _call(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any]
) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def _unlock(hass: HomeAssistant) -> None:
    await _call(hass, "switch", "turn_on", {"entity_id": LOCK})


async def _refused(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any]
) -> str:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, domain, service, data)
    assert err.value.translation_key == "critical_locked"
    return str(err.value.translation_placeholders)


# ---------------------------------------------------------------------------
# The lock


async def test_lock_turns_itself_off(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Allow critical changes starts off and turns off after 10 minutes."""
    await _setup(hass)
    assert _state(hass, LOCK) == "off"
    await _unlock(hass)
    state = hass.states.get(LOCK)
    assert state is not None
    assert state.state == "on"
    assert "turns_off_at" in state.attributes

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=599))
    await hass.async_block_till_done()
    assert _state(hass, LOCK) == "on"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=601))
    await hass.async_block_till_done()
    assert _state(hass, LOCK) == "off"


async def test_no_critical_entities_when_monitoring(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Monitoring only has no lock and no critical entities."""
    await _setup(hass, controls=False)
    for entity_id in (
        LOCK,
        MODE,
        f"switch.{PREFIX}_stop_grid_charging_0_a",
        f"switch.{PREFIX}_powerassist",
        f"number.{PREFIX}_absorption_voltage",
        f"button.{PREFIX}_restart_the_multi",
        f"button.{PREFIX}_save_settings_permanently",
        f"sensor.{PREFIX}_unsaved_setting_changes",
    ):
        assert hass.states.get(entity_id) is None, entity_id


# ---------------------------------------------------------------------------
# Critical entities


async def test_mode_select(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """The mode needs the lock open and keeps the current limit."""
    await _setup(hass)
    assert _state(hass, MODE) == "on"
    risk = await _refused(
        hass, "select", "select_option", {"entity_id": MODE, "option": "inverter_only"}
    )
    assert "AC out 2 loses power" in risk
    assert not _remote_frames(fake_mk3)

    await _unlock(hass)
    await _call(
        hass, "select", "select_option", {"entity_id": MODE, "option": "charger_only"}
    )
    frame = _remote_frames(fake_mk3)[-1]
    assert frame[3] == 1  # charger only
    assert int.from_bytes(frame[4:6], "little") == 160
    assert _state(hass, MODE) == "charger_only"


async def test_passthru_switch(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Passthru sets 0 A and turning it off restores the last limit."""
    await _setup(hass)
    entity_id = f"switch.{PREFIX}_stop_grid_charging_0_a"
    assert _state(hass, entity_id) == "off"
    await _refused(hass, "switch", "turn_on", {"entity_id": entity_id})

    await _unlock(hass)
    await _call(hass, "switch", "turn_on", {"entity_id": entity_id})
    assert int.from_bytes(_remote_frames(fake_mk3)[-1][4:6], "little") == 0
    assert _state(hass, entity_id) == "on"
    # The device reports its minimum limit; a mode change keeps passthru.
    assert fake_mk3.device.current_limit == 30
    await _call(
        hass, "select", "select_option", {"entity_id": MODE, "option": "charger_only"}
    )
    assert int.from_bytes(_remote_frames(fake_mk3)[-1][4:6], "little") == 0
    assert _state(hass, entity_id) == "on"
    await _call(hass, "switch", "turn_off", {"entity_id": entity_id})
    assert int.from_bytes(_remote_frames(fake_mk3)[-1][4:6], "little") == 160
    assert _state(hass, entity_id) == "off"


async def test_flag_switches(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """PowerAssist, weak AC and UPS need the lock; changes are RAM only."""
    await _setup(hass)
    ups = f"switch.{PREFIX}_ups_function"
    risk = await _refused(hass, "switch", "turn_off", {"entity_id": ups})
    assert "short interruption" in risk
    assert fake_mk3.device.writes == []

    await _unlock(hass)
    await _call(hass, "switch", "turn_off", {"entity_id": ups})
    assert fake_mk3.device.settings[0] == 0x89B0 | 0x0008
    assert fake_mk3.device.writes[-1][0] == 0x03  # RAM only
    assert fake_mk3.device.eeprom[0] == 0x89B0
    assert _state(hass, ups) == "off"
    unsaved = hass.states.get(f"sensor.{PREFIX}_unsaved_setting_changes")
    assert unsaved is not None
    assert unsaved.state == "1"
    assert unsaved.attributes["settings"] == ["flags (power assist, ...)"]


async def test_voltage_numbers(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Charge voltages need the lock and are checked against each other."""
    await _setup(hass)
    entity_id = f"number.{PREFIX}_float_voltage"
    await _refused(hass, "number", "set_value", {"entity_id": entity_id, "value": 27.0})
    await _unlock(hass)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "number", "set_value", {"entity_id": entity_id, "value": 29.0}
        )
    assert err.value.translation_key == "invalid_value"
    await _call(hass, "number", "set_value", {"entity_id": entity_id, "value": 27.0})
    assert fake_mk3.device.writes == [(0x03, 3, 2700)]
    assert _state(hass, entity_id) == "27.0"


async def test_routine_controls_need_no_lock(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Current limits and force absorption work without the lock."""
    await _setup(hass)
    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_charge_current_limit", "value": 50},
    )
    assert fake_mk3.device.settings[4] == 50
    await _call(
        hass, "button", "press", {"entity_id": f"button.{PREFIX}_force_absorption"}
    )
    assert fake_mk3.device.forced == [2]


async def test_ram_value_shown_over_stored_read_back(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A device whose ReadSetting reports the EEPROM value (1.1.4).

    The number shows the RAM value with the stored value as an attribute,
    and saving stores the RAM value.
    """
    _, coordinator = await _setup(hass)
    fake_mk3.device.ram_only_mode = "readback_eeprom"
    entity_id = f"number.{PREFIX}_charge_current_limit"
    attributes = hass.states.get(entity_id).attributes
    assert attributes["active_value_source"] == "unverified"
    assert "stored_value" not in attributes
    await _call(hass, "number", "set_value", {"entity_id": entity_id, "value": 39})
    await coordinator.async_refresh()
    state = hass.states.get(entity_id)
    assert state.state == "39.0"
    assert state.attributes["active_value_source"] == "acknowledged"
    assert state.attributes["stored_value"] == 70.0

    await _unlock(hass)
    await _call(
        hass,
        "button",
        "press",
        {"entity_id": f"button.{PREFIX}_save_settings_permanently"},
    )
    assert fake_mk3.device.eeprom[4] == 39
    await coordinator.async_refresh()
    state = hass.states.get(entity_id)
    assert state.state == "39.0"
    assert state.attributes["active_value_source"] == "stored"
    assert state.attributes["stored_value"] == 39.0


async def test_configured_and_remote_limits(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The configured limit shows what the Multi acknowledged (1.1.10)."""
    _, coordinator = await _setup(hass)
    fake_mk3.device.ram_only_mode = "readback_eeprom"
    fake_mk3.device.limit_follows_setting = True
    configured = f"number.{PREFIX}_configured_ac_input_current_limit"
    remote = f"number.{PREFIX}_remote_ac_input_current_limit"
    assert _state(hass, remote) == "unknown"
    await _call(hass, "number", "set_value", {"entity_id": remote, "value": 16})
    await _call(hass, "number", "set_value", {"entity_id": configured, "value": 7})
    await coordinator.client.poll(full=True)
    await coordinator.async_refresh()
    state = hass.states.get(configured)
    assert state.state == "7.0"
    assert state.attributes["active_value_source"] == "acknowledged"
    assert state.attributes["stored_value"] == 16.0
    # The remote panel limit is what the interface reports, not the 7 A
    # in effect.
    assert _state(hass, remote) == "16.0"


async def test_buttons(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Equalise, restart and save need the lock."""
    await _setup(hass)
    equalise = f"button.{PREFIX}_start_equalisation"
    restart = f"button.{PREFIX}_restart_the_multi"
    save = f"button.{PREFIX}_save_settings_permanently"
    assert hass.states.get(restart).attributes["device_class"] == "restart"
    risk = await _refused(hass, "button", "press", {"entity_id": restart})
    assert "lose power" in risk
    await _refused(hass, "button", "press", {"entity_id": equalise})
    assert fake_mk3.device.resets == 0

    await _unlock(hass)
    # Nothing changed yet: nothing to save.
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "button", "press", {"entity_id": save})
    assert err.value.translation_key == "nothing_to_save"

    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_absorption_voltage", "value": 28.4},
    )
    assert fake_mk3.device.eeprom[2] == 2880
    await _call(hass, "button", "press", {"entity_id": save})
    assert fake_mk3.device.writes[-1] == (0x01, 2, 2840)
    assert fake_mk3.device.eeprom[2] == 2840
    assert _state(hass, f"sensor.{PREFIX}_unsaved_setting_changes") == "0"

    await _call(hass, "button", "press", {"entity_id": equalise})
    assert fake_mk3.device.forced == [1]
    await _call(hass, "button", "press", {"entity_id": restart})
    assert fake_mk3.device.resets == 1


async def test_restart_forgets_unsaved_changes(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """After a restart RAM changes are gone, so there is nothing to save."""
    _, coordinator = await _setup(hass)
    await _unlock(hass)
    await _call(
        hass,
        "number",
        "set_value",
        {"entity_id": f"number.{PREFIX}_float_voltage", "value": 27.0},
    )
    assert coordinator.client.unsaved_settings == {3}
    await _call(
        hass, "button", "press", {"entity_id": f"button.{PREFIX}_restart_the_multi"}
    )
    assert coordinator.client.unsaved_settings == set()


# ---------------------------------------------------------------------------
# Actions for automations


async def test_actions(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Every action needs acknowledge_risk; the lock does not apply."""
    entry, _ = await _setup(hass)
    base = {"config_entry_id": entry.entry_id, "acknowledge_risk": True}

    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            DOMAIN,
            "set_mode",
            {**base, "acknowledge_risk": False, "mode": "off"},
        )
    assert err.value.translation_key == "risk_not_acknowledged"

    await _call(hass, DOMAIN, "set_mode", {**base, "mode": "inverter_only"})
    assert _remote_frames(fake_mk3)[-1][3] == 2

    await _call(
        hass,
        DOMAIN,
        "set_setting",
        {**base, "setting": "float_voltage", "value": 27.2, "persistent": True},
    )
    assert fake_mk3.device.writes[-1] == (0x01, 3, 2720)
    assert fake_mk3.device.eeprom[3] == 2720
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            DOMAIN,
            "set_setting",
            {**base, "setting": "float_voltage", "value": 29.5},
        )
    assert err.value.translation_key == "invalid_value"

    await _call(
        hass,
        DOMAIN,
        "set_feature",
        {**base, "feature": "weak_ac_input", "enabled": True},
    )
    assert fake_mk3.device.settings[0] & 0x4000

    await _call(hass, DOMAIN, "save_settings", base)
    assert fake_mk3.device.eeprom[0] & 0x4000
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, DOMAIN, "save_settings", base)
    assert err.value.translation_key == "nothing_to_save"

    await _call(hass, DOMAIN, "equalise", base)
    assert fake_mk3.device.forced == [1]
    await _call(hass, DOMAIN, "restart", base)
    assert fake_mk3.device.resets == 1


async def test_actions_need_control(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A monitoring-only entry refuses the new actions."""
    entry, _ = await _setup(hass, controls=False)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            DOMAIN,
            "restart",
            {"config_entry_id": entry.entry_id, "acknowledge_risk": True},
        )
    assert err.value.translation_key == "controls_disabled"
    assert fake_mk3.device.resets == 0


async def test_renamed_switch_keeps_entity_id(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Existing installs keep switch.*_passthru after the rename (1.1.2)."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "HQ123_0_passthru",
        config_entry=entry,
        suggested_object_id=f"{PREFIX}_passthru",
    )
    assert old.entity_id == f"switch.{PREFIX}_passthru"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(f"switch.{PREFIX}_passthru")
    assert state is not None
    assert (
        state.attributes["friendly_name"] == "Victron VE.Bus Stop grid charging (0 A)"
    )
    assert hass.states.get(f"switch.{PREFIX}_stop_grid_charging_0_a") is None


async def test_numbers_added_once_ranges_are_read(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A setting whose range is missing at start-up gets its number later."""
    fake_mk3.device.no_range_settings = {2}
    _, coordinator = await _setup(hass)
    absorption = f"number.{PREFIX}_absorption_voltage"
    assert hass.states.get(absorption) is None
    assert coordinator.client.missing_ranges([2]) == [2]

    # The device answers again; the background retry reads the range.
    fake_mk3.device.no_range_settings = set()
    coordinator._ranges_retry_at = 0
    await coordinator._async_retry_ranges()
    await hass.async_block_till_done()
    assert _state(hass, absorption) == "28.8"
    assert coordinator.client.missing_ranges([2]) == []
