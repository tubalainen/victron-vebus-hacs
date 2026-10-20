"""Tests for v0.7: link quality, per-unit energy and tolerant refreshes."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import CONF_SCAN_INTERVAL, DOMAIN
from custom_components.victron_vebus.coordinator import VeBusCoordinator
from custom_components.victron_vebus.health import MIN_REQUESTS, LinkQuality
from custom_components.victron_vebus.vebus import VeBusTimeoutError

from .vebus.simulator import FakeMK3

PREFIX = "victron_ve_bus"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HQ123",
        title="Victron VE.Bus",
        data={"device": "/dev/fake"},
        options={CONF_SCAN_INTERVAL: 30},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> VeBusCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: VeBusCoordinator = entry.runtime_data.coordinator
    return coordinator


# ---------------------------------------------------------------------------
# Link quality


def test_link_quality_window() -> None:
    """The rate covers the requests of the window only."""
    link = LinkQuality(window=300)
    link.add(0, 0, 0)
    link.add(10, MIN_REQUESTS - 1, 0)
    assert link.success_rate is None  # too few requests
    link.add(20, 100, 10)
    assert link.requests == 100
    assert link.success_rate == 90.0
    # 400 s later the first samples have left the window.
    link.add(400, 300, 10)
    link.add(420, 400, 10)
    assert link.requests == 100
    assert link.success_rate == 100.0
    # Counters of a new client start from zero again.
    link.add(430, 5, 0)
    assert link.requests == 0
    assert link.success_rate is None


async def test_link_sensors_and_issue(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Poor links raise an issue that clears once the link recovers."""
    entry = _entry()
    coordinator = await _setup(hass, entry)
    issues = ir.async_get(hass)
    issue_id = f"poor_link_{entry.entry_id}"
    coordinator.link = LinkQuality(window=300)
    stats = SimpleNamespace(requests=0, timeouts=0)
    coordinator.client.stats = stats  # type: ignore[assignment]

    coordinator._async_check_link()
    stats.requests, stats.timeouts = 1000, 200
    coordinator._async_check_link()
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{PREFIX}_link_quality").state == "80.0"
    assert hass.states.get(f"sensor.{PREFIX}_request_timeouts").state == "200"
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["rate"] == "80"

    # Still below the clearing level: the issue stays.
    stats.requests = 3000
    coordinator._async_check_link()
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    stats.requests = 10000
    coordinator._async_check_link()
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    # The link sensors stay available while the device is unreachable.
    coordinator.async_set_update_error(TimeoutError("unplugged"))
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{PREFIX}_battery_voltage").state == "unavailable"
    assert hass.states.get(f"sensor.{PREFIX}_link_quality").state != "unavailable"


async def test_unload_removes_link_issue(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Unloading the entry removes the link issue."""
    entry = _entry()
    coordinator = await _setup(hass, entry)
    coordinator.link = LinkQuality(window=300)
    stats = SimpleNamespace(requests=0, timeouts=0)
    coordinator.client.stats = stats  # type: ignore[assignment]
    coordinator._async_check_link()
    stats.requests, stats.timeouts = 1000, 500
    coordinator._async_check_link()
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"poor_link_{entry.entry_id}")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"poor_link_{entry.entry_id}") is None


# ---------------------------------------------------------------------------
# Tolerant refresh after a command


async def test_refresh_timeout_is_not_an_error(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A command succeeds even if re-reading the mode afterwards times out."""
    coordinator = await _setup(hass, _entry())
    coordinator.client.refresh_config = AsyncMock(  # type: ignore[method-assign]
        side_effect=VeBusTimeoutError("busy")
    )
    await coordinator.async_refresh_config()
    assert hass.states.get(f"sensor.{PREFIX}_mode").state == "on"


# ---------------------------------------------------------------------------
# Per-unit energy


def _unit_ram(output_power: int) -> dict[int, int]:
    return {
        0: 23100,
        1: 300,
        2: 23100,
        3: 150,
        4: 2645,
        5: 40,
        14: 100,
        15: (-120) & 0xFFFF,
        16: output_power,
    }


async def test_unit_energy(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Every unit of a multi-unit system gets its own energy sensors."""
    fake_mk3.device.phases = (1, 2, 3)
    fake_mk3.device.units = {1: _unit_ram(200), 2: _unit_ram(300)}
    coordinator = await _setup(hass, _entry())
    unit = f"sensor.{PREFIX}_unit_1_ac_output_energy"
    system = f"sensor.{PREFIX}_ac_output_energy"
    before_unit = float(hass.states.get(unit).state)
    before_system = float(hass.states.get(system).state)
    assert hass.states.get(f"sensor.{PREFIX}_unit_2_battery_energy_charged")

    data = coordinator.data
    coordinator.async_set_updated_data(replace(data, timestamp=data.timestamp + 36))
    await hass.async_block_till_done()
    # 200 W for 36 s, and 550 W for the system (50 + 200 + 300 W).
    assert float(hass.states.get(unit).state) - before_unit == pytest.approx(
        0.002, abs=1e-4
    )
    assert float(hass.states.get(system).state) - before_system == pytest.approx(
        0.0055, abs=1e-4
    )
