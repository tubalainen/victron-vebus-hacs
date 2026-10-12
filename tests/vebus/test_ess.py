"""Tests for the ESS assistant and Battery Operational Limits."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import VeBusClient, VeBusNotSupportedError
from custom_components.victron_vebus.vebus.models import BatteryLimits

from .simulator import FakeMK3, SimulatedMultiPlus


async def _client(device: SimulatedMultiPlus) -> tuple[VeBusClient, FakeMK3]:
    fake = FakeMK3(device)
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client, fake


def _ess_device(**kwargs: object) -> SimulatedMultiPlus:
    device = SimulatedMultiPlus(**kwargs)  # type: ignore[arg-type]
    device.enable_ess((0, *device.units))
    return device


async def test_no_ess_assistant() -> None:
    """Without an assistant the scan stops at the end marker."""
    client, _ = await _client(SimulatedMultiPlus())
    assert client.ess_records == {}
    assert client.state.ess == {}
    with pytest.raises(VeBusNotSupportedError):
        await client.write_ess_setpoint(0, 100)
    await client.close()


async def test_find_ess_record_after_other_assistant() -> None:
    """The ESS record is found behind another assistant's record."""
    client, _ = await _client(_ess_device())
    assert client.ess_records == {0: 130}
    assert client.state.ess[0].setpoint == 0
    assert client.state.ess[0].disable_charge is False
    await client.close()


async def test_write_setpoint_with_write_ram_var_and_data() -> None:
    """The setpoint is written with WriteRAMVar followed by WriteData."""
    client, fake = await _client(_ess_device())
    sent = len(fake.device.received)
    await client.write_ess_setpoint(0, -200)
    frames = fake.device.received[sent:]
    assert frames[0][3:6] == bytes([0x32, 131, 0])
    assert frames[1][3:6] == bytes([0x34, 0x38, 0xFF])  # -200 little endian
    assert frames[0][2] == frames[1][2]  # same tag letter
    assert fake.device.ram_writes == [(0, 131, -200)]
    assert (await client.read_ess(0)).setpoint == -200
    await client.close()


async def test_write_ess_flags() -> None:
    """Disable charge and feed-in are bits of the second ESS word."""
    client, fake = await _client(_ess_device())
    state = await client.write_ess_flags(0, disable_feed_in=True)
    assert state.disable_feed_in is True
    assert state.disable_charge is False
    assert fake.device.assistant_ram[0][132] == 0x02
    await client.write_ess_flags(0, disable_charge=True, disable_feed_in=False)
    assert fake.device.assistant_ram[0][132] == 0x01
    assert client.state.ess[0].disable_charge is True
    await client.close()


async def test_ess_per_phase_master() -> None:
    """Each phase master has its own ESS record; slaves are not searched."""
    units = {address: {16: 0} for address in (1, 2, 3)}
    device = SimulatedMultiPlus(phases=(1, 2, 3), units=units)
    device.enable_ess((0, 1, 2, 3))
    client, fake = await _client(device)
    assert client.ess_records == {0: 130, 1: 130, 2: 130}
    await client.write_ess_setpoint(2, 300)
    assert fake.device.ram_writes[-1] == (2, 131, 300)
    assert fake.device.address == 0
    await client.close()


async def test_battery_limits() -> None:
    """BOL frames carry cV and dA values; the reply is decoded."""
    client, fake = await _client(SimulatedMultiPlus())
    reply = await client.send_battery_limits(
        BatteryLimits(
            charge_voltage=28.4,
            charge_current=50,
            discharge_voltage=24.0,
            discharge_current=100,
        )
    )
    assert fake.device.bol_frames[-1] == bytes(
        [0x0F, 0x18, 0x0B, 0xF4, 0x01, 0x00, 0x60, 0x09, 0xE8, 0x03, 0x00]
    )
    assert fake.device.received[-1][1:4] == b"\xffF\x09"
    assert reply.charge_voltage == pytest.approx(28.4)
    assert reply.discharge_current == pytest.approx(100)
    assert not reply.timeout
    assert client.state.battery_limits == reply
    await client.close()


async def test_battery_limits_need_firmware() -> None:
    """BOL needs device firmware 415 and MK3 firmware 203."""
    client, fake = await _client(SimulatedMultiPlus(firmware=2623414))
    assert not client.supports_battery_limits
    with pytest.raises(VeBusNotSupportedError):
        await client.send_battery_limits(BatteryLimits(28, 10, 24, 10))
    assert fake.device.bol_frames == []
    await client.close()

    client, _ = await _client(SimulatedMultiPlus(mk3_version=1170202))
    assert not client.supports_battery_limits
    await client.close()
