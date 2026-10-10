"""Tests for the VE.Bus client against the simulated MK3."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.victron_vebus.vebus import (
    ChargeState,
    DeviceState,
    InterfaceFlag,
    Mode,
    RamVar,
    Setting,
    VeBusClient,
    VeBusConnectionError,
    VeBusTimeoutError,
)
from custom_components.victron_vebus.vebus.frame import encode_frame

from .simulator import FakeMK3, SimulatedMultiPlus

TIMEOUT = 0.05


def _client(fake: FakeMK3) -> VeBusClient:
    return VeBusClient("/dev/fake", opener=fake, request_timeout=TIMEOUT)


async def test_initialize_and_poll() -> None:
    """The client reads and decodes everything from a MultiPlus-II."""
    fake = FakeMK3()
    client = _client(fake)
    await client.connect()
    identity = await client.initialize()

    assert identity.mk3_version == 1170216
    assert identity.firmware is not None and identity.firmware.version == 506
    assert identity.phases == (1,)

    state = client.state
    assert state.dc is not None
    assert state.dc.voltage == pytest.approx(26.41)
    assert state.dc.charger_current == pytest.approx(12.3)
    assert state.dc.battery_current == pytest.approx(12.3)
    ac = state.ac[1]
    assert ac.mains_voltage == pytest.approx(230.12)
    assert ac.mains_current == pytest.approx(5.5)
    assert ac.inverter_current == pytest.approx(-1.2)  # forced signed
    assert ac.device_state is DeviceState.CHARGE
    assert state.device_state is not None
    assert state.device_state.charge_state is ChargeState.FLOAT
    assert state.config is not None
    assert state.config.actual_current_limit == pytest.approx(16.0)
    assert state.config.mode is Mode.ON
    assert state.config.num_ac_inputs == 1
    assert state.ram[RamVar.INVERTER_POWER_1] == 300
    assert state.ram[RamVar.INVERTER_POWER_2] == -350
    assert state.ram[RamVar.OUTPUT_POWER] == 50
    assert state.ram[RamVar.CHARGE_STATE] == pytest.approx(0.85)
    assert state.ram[RamVar.U_BAT_RMS] == pytest.approx(0.05)
    assert state.ram[RamVar.MULTI_FUNCTIONAL_RELAY_STATE] == 1.0
    assert state.ram[RamVar.IGNORE_AC_INPUT_STATE] == 0.0
    assert state.settings[Setting.U_BAT_ABSORPTION] == pytest.approx(28.8)
    assert state.settings[Setting.I_MAINS_LIMIT_AC1] == pytest.approx(16.0)
    assert state.settings[Setting.BATTERY_CAPACITY] == 200
    # The Winmon mode 1 reset frame is the 9 byte form without EEPROM flags.
    assert bytes.fromhex("09 FF 53 00 00 00 01 90 00 01") in [
        frame[:-1] for frame in fake.device.received
    ]
    await client.close()


async def test_three_phase_detection() -> None:
    """Phases are detected by probing F2..F4."""
    fake = FakeMK3(SimulatedMultiPlus(phases=(1, 2, 3)))
    client = _client(fake)
    await client.connect()
    identity = await client.initialize()
    assert identity.phases == (1, 2, 3)
    assert set(client.state.ac) == {1, 2, 3}
    assert client.state.ac[1].phase_count == 3
    await client.close()


async def test_concurrent_requests_do_not_cross() -> None:
    """Requests issued concurrently each get their own reply."""
    fake = FakeMK3()
    client = _client(fake)
    await client.connect()
    await client.initialize()
    results = await asyncio.gather(
        client.read_led(),
        client.read_config(),
        client.read_ram_vars([RamVar.OUTPUT_POWER]),
        client.read_device_state(),
        client.poll(),
        client.read_ram_vars([RamVar.INVERTER_POWER_1, RamVar.INVERTER_POWER_2]),
    )
    assert results[2] == {RamVar.OUTPUT_POWER: 50}
    assert results[3].state is DeviceState.CHARGE
    assert results[5] == {RamVar.INVERTER_POWER_1: 300, RamVar.INVERTER_POWER_2: -350}
    await client.close()


async def test_remote_panel_state() -> None:
    """Mode and current limit are sent with the 'S' command."""
    fake = FakeMK3()
    client = _client(fake)
    await client.connect()
    await client.initialize()
    await client.set_remote_panel_state(Mode.CHARGER_ONLY, 12.5)
    assert fake.device.received[-1] == encode_frame(ord("S"), [1, 125, 0, 1, 0x80])
    config = await client.read_config()
    assert config.mode is Mode.CHARGER_ONLY
    assert config.remote_mode is Mode.CHARGER_ONLY
    assert config.actual_current_limit == pytest.approx(12.5)

    await client.set_remote_panel_state(Mode.ON)
    assert fake.device.received[-1] == encode_frame(ord("S"), [3, 0x00, 0x80, 1, 0x80])
    config = await client.read_config()
    assert config.mode is Mode.ON
    assert config.actual_current_limit == pytest.approx(12.5)
    await client.close()


async def test_interface_flags() -> None:
    """The keep awake flag is sent on every poll."""
    fake = FakeMK3()
    client = _client(fake)
    await client.connect()
    await client.initialize()
    flags = InterfaceFlag.PANEL_DETECT | InterfaceFlag.STANDBY
    state = await client.poll(interface_flags=flags)
    assert state.interface_flags == 0x03
    assert fake.device.interface_flags == 0x03
    await client.close()


async def test_interface_flags_unsupported() -> None:
    """An MK2 without the 'H' command is detected and no longer asked."""
    fake = FakeMK3(SimulatedMultiPlus(supports_h=False))
    client = _client(fake)
    await client.connect()
    await client.initialize()
    for _ in range(3):
        await client.poll(interface_flags=InterfaceFlag.STANDBY)
    assert not client.interface_flags_supported
    await client.close()


async def test_asleep_device_times_out() -> None:
    """A sleeping device makes initialisation fail with a timeout."""
    fake = FakeMK3(SimulatedMultiPlus(asleep=True))
    client = _client(fake)
    await client.connect()
    with pytest.raises(VeBusTimeoutError):
        await client.initialize()
    await client.close()


async def test_open_failure() -> None:
    """Errors opening the port are reported as connection errors."""
    fake = FakeMK3()
    fake.fail_open = True
    client = _client(fake)
    with pytest.raises(VeBusConnectionError):
        await client.connect()


async def test_boot_frame_triggers_readdress() -> None:
    """After a VE.Bus device restart the address is selected again."""
    fake = FakeMK3()
    client = _client(fake)
    await client.connect()
    await client.initialize()
    fake.writers[-1]._reader.feed_data(bytes.fromhex("02 00 00 FE"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert client.stats.boot_frames == 1
    await client.read_led()
    assert encode_frame(ord("A"), [1, 0]) in fake.device.received[-2:]
    await client.close()


async def test_run_reconnects_after_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run loop reports unavailability and recovers after a disconnect."""
    monkeypatch.setattr(
        "custom_components.victron_vebus.vebus.client.RECONNECT_MIN_DELAY", 0.01
    )
    fake = FakeMK3()
    client = _client(fake)
    states: list[object] = []
    errors: list[Exception] = []
    task = asyncio.create_task(client.run(states.append, errors.append, interval=0.01))
    try:
        async with asyncio.timeout(5):
            while not states:
                await asyncio.sleep(0.01)
            fake.writers[-1].disconnect()
            while not errors:
                await asyncio.sleep(0.01)
            count = len(states)
            while len(states) <= count:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert isinstance(errors[0], VeBusConnectionError)
    assert len(fake.writers) == 2
