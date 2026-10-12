"""Tests for multi-unit systems, the RAM fallback and the Quattro AC input 2."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import RamVar, Setting, VeBusClient
from custom_components.victron_vebus.vebus.frame import encode_frame

from .simulator import FakeMK3, SimulatedMultiPlus


def _unit_ram(output_power: int, mains_power: int) -> dict[int, int]:
    return {
        0: 23012,
        1: 400,
        2: 23012,
        3: 100,
        4: 2641,
        5: 50,
        14: 200,
        15: mains_power & 0xFFFF,
        16: output_power,
    }


async def _client(device: SimulatedMultiPlus) -> tuple[VeBusClient, FakeMK3]:
    fake = FakeMK3(device)
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client, fake


def _addresses_selected(fake: FakeMK3) -> list[int]:
    return [frame[4] for frame in fake.device.received if frame[2] == ord("A")]


async def test_single_unit_scan() -> None:
    """A single unit is found; the scan stops after three empty addresses."""
    client, fake = await _client(SimulatedMultiPlus())
    assert client.units == (0,)
    assert client.state.units == {}
    selected = _addresses_selected(fake)
    assert selected[-6:] == [1, 0, 2, 0, 3, 0]
    assert fake.device.address == 0
    await client.close()


async def test_multi_unit_scan_and_poll() -> None:
    """Every unit is read; totals add up the units."""
    device = SimulatedMultiPlus(
        units={1: _unit_ram(100, -150), 3: _unit_ram(200, -250)}
    )
    client, fake = await _client(device)
    assert client.units == (0, 1, 3)  # address 2 is empty, the scan goes on
    state = client.state
    assert set(state.units) == {0, 1, 3}
    assert state.units[1].ram_value(RamVar.OUTPUT_POWER) == 100
    assert state.units[3].ram_value(RamVar.U_BAT) == pytest.approx(26.41)
    assert state.units[1].firmware is not None
    assert state.total_ram_value(RamVar.OUTPUT_POWER) == 50 + 100 + 200
    assert state.total_ram_value(RamVar.INVERTER_POWER_2) == -350 - 150 - 250
    assert fake.device.address == 0

    # Regular polls read one unit each, round-robin.
    device.units[1][16] = 500
    for _ in range(3):
        await client.poll()
    assert client.state.units[1].ram_value(RamVar.OUTPUT_POWER) == 500
    assert fake.device.address == 0
    assert fake.device.received[-1] != encode_frame(ord("A"), [1, 1])
    await client.close()


async def test_device_without_info_frames() -> None:
    """Compact/Phoenix inverters: values come from RAM variables."""
    client, _ = await _client(SimulatedMultiPlus(info_frames=False))
    state = client.state
    assert not state.info_frames
    assert state.phases == (1,)
    assert state.dc is not None
    assert state.dc.voltage == pytest.approx(26.41)
    assert state.dc.inverter_current == 0
    assert state.dc.inverter_frequency == pytest.approx(50.0, abs=0.1)
    ac = state.ac[1]
    assert ac.mains_voltage == pytest.approx(230.12)
    assert ac.inverter_current == pytest.approx(-1.2)
    assert ac.mains_frequency == pytest.approx(50.1, abs=0.1)
    # Polling keeps working without info frames.
    await client.poll()
    assert client.state.dc is not None
    await client.close()


async def test_quattro_second_input() -> None:
    """A Quattro reports two AC inputs and the AC input 2 limit."""
    client, _ = await _client(SimulatedMultiPlus(quattro=True))
    assert client.state.config is not None
    assert client.state.config.num_ac_inputs == 2
    assert client.state.setting_value(Setting.I_MAINS_LIMIT_AC2) == pytest.approx(12.0)
    await client.close()


async def test_multiplus_has_no_second_input() -> None:
    """Setting 49 is not supported on a MultiPlus-II."""
    client, _ = await _client(SimulatedMultiPlus())
    assert client.setting_info[Setting.I_MAINS_LIMIT_AC2] is None
    assert Setting.I_MAINS_LIMIT_AC2 not in client.state.settings
    await client.close()
