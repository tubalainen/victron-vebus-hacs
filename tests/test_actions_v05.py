"""Tests for the v0.5 guided device actions and their risk confirmations."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .vebus.simulator import FakeMK3


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


async def _action(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    action: str,
    user_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open the options, go to the device actions and pick one action."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "actions"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "actions"}
    )
    assert result["type"] is FlowResultType.MENU
    assert action in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": action}
    )
    if user_input is not None:
        assert result["step_id"] == action
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input
        )
    return result


async def _confirm(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"acknowledge": True}
    )


def _remote_frames(fake_mk3: FakeMK3) -> list[bytes]:
    return [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]


async def test_monitor_only_has_no_actions(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A monitoring entry goes straight to the settings form."""
    entry = _entry(controls=False)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"


async def test_actions_menu(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """All actions are offered on a MultiPlus-II with firmware 506."""
    entry = _entry()
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "actions"}
    )
    assert result["menu_options"] == [
        "action_mode",
        "action_passthru",
        "action_setting",
        "action_flag",
        "action_equalise",
        "action_restart",
    ]


async def test_restart_needs_confirmation(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The restart warns about the AC outputs and is only sent once confirmed."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(hass, entry, "action_restart")
    assert result["step_id"] == "confirm"
    warning = result["description_placeholders"]["warning"]
    assert "AC out 1 and AC out 2 lose power" in warning

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"acknowledge": False}
    )
    assert result["errors"] == {"base": "acknowledge_required"}
    assert fake_mk3.device.resets == 0

    result = await _confirm(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "action_done"
    assert fake_mk3.device.resets == 1
    # The options are not changed by an action.
    assert entry.options[CONF_ENABLE_CONTROLS] is True


async def test_mode_off(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Switching off warns that the outputs lose power, and keeps the limit."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(hass, entry, "action_mode", {"mode": "off"})
    warning = result["description_placeholders"]["warning"]
    assert "AC out 1 and AC out 2 lose power now" in warning
    assert not _remote_frames(fake_mk3)

    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    frame = _remote_frames(fake_mk3)[-1]
    assert frame[3] == 4  # off
    assert int.from_bytes(frame[4:6], "little") == 160  # 16.0 A kept
    assert hass.states.get("sensor.victron_ve_bus_mode").state == "off"


async def test_passthru(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Entering passthru with PowerAssist on warns that the Multi inverts."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(
        hass, entry, "action_passthru", {"passthru": "enter", "current_limit": 16}
    )
    warning = result["description_placeholders"]["warning"]
    assert "PowerAssist is on" in warning
    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    frame = _remote_frames(fake_mk3)[-1]
    assert frame[3] == 3  # mode stays on
    assert int.from_bytes(frame[4:6], "little") == 0

    result = await _action(
        hass, entry, "action_passthru", {"passthru": "leave", "current_limit": 10}
    )
    assert "10 A" in result["description_placeholders"]["warning"]
    result = await _confirm(hass, result)
    assert int.from_bytes(_remote_frames(fake_mk3)[-1][4:6], "little") == 100


async def test_setting_validation(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Invalid values are refused before the risk dialog."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(
        hass,
        entry,
        "action_setting",
        {"setting": "float_voltage", "value": 29.0, "persistent": False},
    )
    assert result["step_id"] == "action_setting"
    assert result["errors"] == {"base": "invalid_value"}
    assert "absorption" in result["description_placeholders"]["error"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"setting": "absorption_voltage", "value": 40.0, "persistent": False},
    )
    assert result["errors"] == {"base": "invalid_value"}
    assert not fake_mk3.device.writes


async def test_setting_in_ram(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A RAM write warns that it is lost on restart and leaves the EEPROM alone."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(
        hass,
        entry,
        "action_setting",
        {"setting": "float_voltage", "value": 27.0, "persistent": False},
    )
    warning = result["description_placeholders"]["warning"]
    assert "lost when the Multi restarts" in warning
    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    assert fake_mk3.device.writes == [(0x03, 3, 2700)]
    assert fake_mk3.device.eeprom[3] == 2760
    assert hass.states.get("sensor.victron_ve_bus_float_voltage").state == "27.0"


async def test_persistent_setting(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A permanent write updates the EEPROM and is rate limited."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(
        hass,
        entry,
        "action_setting",
        {"setting": "absorption_voltage", "value": 28.4, "persistent": True},
    )
    assert "stored permanently" in result["description_placeholders"]["warning"]
    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    assert fake_mk3.device.writes == [(0x01, 2, 2840)]
    assert fake_mk3.device.eeprom[2] == 2840

    # A second permanent write of the same setting within 5 minutes is refused.
    result = await _action(
        hass,
        entry,
        "action_setting",
        {"setting": "absorption_voltage", "value": 28.6, "persistent": True},
    )
    result = await _confirm(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "command_failed"}
    assert "5 minutes" in result["description_placeholders"]["error"]
    assert len(fake_mk3.device.writes) == 1

    # Restarting reverts RAM changes but keeps the permanent value.
    result = await _action(hass, entry, "action_restart")
    await _confirm(hass, result)
    assert fake_mk3.device.settings[2] == 2840


async def test_flag_ups_off(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Switching UPS off sets the wave check bit in RAM, with a clear warning."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(
        hass,
        entry,
        "action_flag",
        {"flag": "ups_function", "enabled": False, "persistent": False},
    )
    warning = result["description_placeholders"]["warning"]
    assert "short interruption" in warning
    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    assert fake_mk3.device.settings[0] == 0x89B0 | 0x0008
    assert fake_mk3.device.writes[-1][0] == 0x03
    assert hass.states.get("binary_sensor.victron_ve_bus_ups_function").state == "off"


async def test_equalise(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Equalisation warns about lithium batteries."""
    entry = _entry()
    await _setup(hass, entry)
    result = await _action(hass, entry, "action_equalise")
    assert "lithium" in result["description_placeholders"]["warning"]
    result = await _confirm(hass, result)
    assert result["reason"] == "action_done"
    assert fake_mk3.device.forced == [1]


async def test_earlier_entities_return(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Entities removed in v0.5 come back in 1.1 with their old entity IDs."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = [
        registry.async_get_or_create(
            domain, DOMAIN, f"HQ123_0_{key}", config_entry=entry
        ).entity_id
        for domain, key in (
            ("select", "remote_mode"),
            ("switch", "power_assist"),
            ("number", "float_voltage"),
            ("button", "force_equalise"),
        )
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in old:
        assert registry.async_get(entity_id) is not None, entity_id
        assert hass.states.get(entity_id) is not None, entity_id
