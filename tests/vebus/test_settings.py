"""Tests for RAM-only setting writes, flag changes and forced charge states."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import (
    ChargeState,
    ForceChargeState,
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusNotSupportedError,
    VeBusWriteError,
)
from custom_components.victron_vebus.vebus.decode import decode_setting_info
from custom_components.victron_vebus.vebus.frame import encode_frame

from .simulator import FakeMK3, SimulatedMultiPlus

MODE1 = encode_frame(ord("S"), [0, 0, 0, 1, 0x90, 0, 1])
# The panel state read with 'D' is sent back: 04 00 00 until one was set.
LONG = encode_frame(ord("S"), [4, 0, 0, 0x01, 0xD0, 0x3E, 0x06])


async def _client(
    device: SimulatedMultiPlus | None = None,
) -> tuple[VeBusClient, FakeMK3]:
    fake = FakeMK3(device)
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client, fake


def test_decode_long_setting_info() -> None:
    """The mode 2 GetSettingInfo reply has no per-field reply codes."""
    reply = bytes.fromhex("89 9C FF 00 00 40 0B 60 09 80 0C 00")
    info = decode_setting_info(reply, long_form=True)
    assert info is not None
    assert info.scale == pytest.approx(0.01)
    assert info.default == 2880
    assert info.minimum == 2400
    assert info.maximum == 3200
    assert info.encode(28.8) == 2880


async def test_initial_flag_settings_are_read() -> None:
    """Flag settings are polled as raw values."""
    client, _ = await _client()
    state = client.state
    assert state.setting_flag(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED) is True
    assert state.setting_flag(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT) is False
    assert state.setting_flag(Setting.FLAGS_2, SettingFlag.SOLAR_WIND_PRIORITY) is True
    assert state.setting_value(Setting.SOLAR_WIND_PRIORITY_VOLTAGE) == pytest.approx(
        27.6
    )
    await client.close()


async def test_write_setting_ram_only_in_long_frame_session() -> None:
    """A write switches to long frames, writes RAM only and switches back."""
    client, fake = await _client()
    sent_before = len(fake.device.received)
    value = await client.write_setting(Setting.U_BAT_FLOAT, 27.0)
    assert value == pytest.approx(27.0)
    assert fake.device.writes == [(0x03, 3, 2700)]  # setting, RAM only
    assert client.state.setting_value(Setting.U_BAT_FLOAT) == pytest.approx(27.0)
    frames = fake.device.received[sent_before:]
    assert frames[0] == encode_frame(ord("D"))
    assert frames[1] == LONG
    assert frames[-1] == MODE1
    assert not fake.device.long_frames
    await client.close()


async def test_write_setting_validates_range() -> None:
    """Values outside the reported range are refused before writing."""
    client, fake = await _client()
    with pytest.raises(ValueError, match="outside"):
        await client.write_setting(Setting.U_BAT_ABSORPTION, 40.0)
    assert fake.device.writes == []
    await client.close()


async def test_write_without_ack_verified_by_readback() -> None:
    """Missing acknowledgements are fine when the value reads back."""
    client, fake = await _client(SimulatedMultiPlus(ack_writes=False))
    assert await client.write_setting(Setting.I_BAT_BULK, 50) == 50
    assert fake.device.settings[4] == 50
    await client.close()


async def test_write_access_level_refused() -> None:
    """An access level error is reported and short frames are restored."""
    device = SimulatedMultiPlus(access_level_settings={2})
    client, fake = await _client(device)
    with pytest.raises(VeBusWriteError, match="access level"):
        await client.write_setting(Setting.U_BAT_ABSORPTION, 28.0)
    assert not fake.device.long_frames
    assert await client.read_setting(Setting.U_BAT_ABSORPTION) == pytest.approx(28.8)
    await client.close()


async def test_firmware_too_old_for_writes() -> None:
    """RAM-only writes need firmware 412."""
    client, fake = await _client(SimulatedMultiPlus(firmware=2623411))
    with pytest.raises(VeBusNotSupportedError, match="412"):
        await client.write_setting(Setting.U_BAT_FLOAT, 27.0)
    assert fake.device.writes == []
    await client.close()


async def test_set_flag_read_modify_write() -> None:
    """Only the requested bit changes."""
    client, fake = await _client()
    raw = await client.set_setting_flag(
        Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT, True
    )
    assert raw == 0x89B0 | 0x4000
    assert fake.device.writes == [(0x03, 0, 0xC9B0)]
    assert client.state.setting_flag(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT)

    # Setting a bit that is already set writes nothing.
    await client.set_setting_flag(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED, True)
    assert len(fake.device.writes) == 1
    await client.close()


async def test_set_flag_refuses_unsupported_bit() -> None:
    """Bits outside the device's mask of supported bits are refused."""
    client, fake = await _client()
    with pytest.raises(VeBusNotSupportedError):
        await client.set_setting_flag(Setting.FLAGS_0, 1 << 15, True)
    assert fake.device.writes == []
    await client.close()


async def test_complete_setting_info_from_long_frames() -> None:
    """Ranges missing from truncated mode 1 replies are read in mode 2."""
    client, fake = await _client(SimulatedMultiPlus(truncate_setting_info=True))
    info = client.setting_info[Setting.U_BAT_FLOAT]
    assert info is not None
    assert info.maximum is None
    await client.complete_setting_info([Setting.U_BAT_FLOAT, Setting.I_BAT_BULK])
    info = client.setting_info[Setting.U_BAT_FLOAT]
    assert info is not None
    assert (info.minimum, info.maximum) == (2400, 3200)
    assert not fake.device.long_frames
    await client.close()


async def test_force_charge_state() -> None:
    """Forcing absorption is sent with GetSetDeviceState."""
    client, fake = await _client()
    info = await client.force_charge_state(ForceChargeState.ABSORPTION)
    assert fake.device.forced == [2]
    assert info.charge_state is ChargeState.FORCED_ABSORPTION
    assert client.state.device_state == info
    await client.close()


async def test_force_charge_needs_firmware_400() -> None:
    """Old firmware cannot force charge states."""
    client, fake = await _client(SimulatedMultiPlus(firmware=2623399))
    with pytest.raises(VeBusNotSupportedError, match="400"):
        await client.force_charge_state(ForceChargeState.FLOAT)
    assert fake.device.forced == []
    await client.close()
