"""Tests for 1.1.12: checks before writes and values that expire."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import (
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.victron_vebus.coordinator import VeBusCoordinator
from custom_components.victron_vebus.vebus import Setting

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_ENABLE_CONTROLS: True, CONF_SCAN_INTERVAL: 30},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> VeBusCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    return coordinator


async def _set_setting(
    hass: HomeAssistant, entry: MockConfigEntry, **data: Any
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "set_setting",
        {"config_entry_id": entry.entry_id, "acknowledge_risk": True, **data},
        blocking=True,
    )


async def _panel(hass: HomeAssistant, entry: MockConfigEntry, **data: Any) -> None:
    await hass.services.async_call(
        DOMAIN,
        "set_remote_panel_state",
        {"config_entry_id": entry.entry_id, "acknowledge_risk": True, **data},
        blocking=True,
    )


def _remote_frames(fake_mk3: FakeMK3) -> list[bytes]:
    return [f for f in fake_mk3.device.received if f[2] == ord("S") and f[0] == 7]


async def test_charge_voltage_refused_while_the_other_is_unknown(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Float and absorption are checked against each other, or not written."""
    fake_mk3.device.unanswered_settings = {Setting.U_BAT_ABSORPTION}
    entry = _entry()
    await _setup(hass, entry)
    with pytest.raises(ServiceValidationError, match="absorption voltage is unknown"):
        await _set_setting(hass, entry, setting="float_voltage", value=27.0)
    with pytest.raises(ServiceValidationError, match="absorption voltage is unknown"):
        await _set_setting(
            hass, entry, setting="solar_wind_priority_voltage", value=27.0
        )
    assert fake_mk3.device.writes == []


async def test_absorption_refused_while_float_is_unknown(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The absorption voltage needs the float voltage to be checked."""
    fake_mk3.device.unanswered_settings = {Setting.U_BAT_FLOAT}
    entry = _entry()
    await _setup(hass, entry)
    with pytest.raises(ServiceValidationError, match="float voltage is unknown"):
        await _set_setting(hass, entry, setting="absorption_voltage", value=28.4)
    assert fake_mk3.device.writes == []


async def test_panel_limit_checked_against_the_device_range(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The service only sends limits within the range the Multi reports."""
    entry = _entry()
    await _setup(hass, entry)
    count = len(_remote_frames(fake_mk3))
    for limit in (2.0, 60.0):  # the simulator reports 3..50 A
        with pytest.raises(ServiceValidationError, match=r"outside 3\.\.50 A"):
            await _panel(hass, entry, mode="on", current_limit=limit)
    assert len(_remote_frames(fake_mk3)) == count
    # 0 A (passthru) is allowed: the call acknowledges the risk.
    await _panel(hass, entry, mode="on", current_limit=0)
    await _panel(hass, entry, mode="on", current_limit=12)
    assert int.from_bytes(_remote_frames(fake_mk3)[-1][4:6], "little") == 120


async def test_passthru_dialog_refuses_a_limit_outside_the_range(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Resuming grid charging with a limit the Multi doesn't accept is refused."""
    entry = _entry()
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "actions"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "action_passthru"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"passthru": "leave", "current_limit": 80}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "action_passthru"
    assert result["errors"] == {"base": "invalid_value"}
    assert "outside 3..50 A" in result["description_placeholders"]["error"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"passthru": "leave", "current_limit": 10}
    )
    assert result["step_id"] == "confirm"


async def test_background_range_retry_is_short(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The background retry tries each setting once, holding the bus briefly."""
    fake_mk3.device.no_range_settings = {Setting.I_BAT_BULK}
    entry = _entry()
    coordinator = await _setup(hass, entry)
    coordinator._ranges_retry_at = 0
    with patch.object(
        coordinator.client,
        "complete_setting_info",
        wraps=coordinator.client.complete_setting_info,
    ) as complete:
        await coordinator._async_retry_ranges()
    assert complete.call_args.kwargs["attempts"] == 1


async def test_stale_setting_is_unknown(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A setting that can no longer be read is shown as unknown."""
    entry = _entry()
    coordinator = await _setup(hass, entry)
    sensor = f"sensor.{PREFIX}_solar_wind_priority_voltage"
    assert hass.states.get(sensor).state == "27.6"
    fake_mk3.device.unanswered_settings = {Setting.SOLAR_WIND_PRIORITY_VOLTAGE}
    for _ in range(3):
        coordinator._async_on_state(await coordinator.client.poll(full=True))
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "unknown"
