"""Tests for 1.1.12: protocol robustness and safe EEPROM writes."""

from __future__ import annotations

from dataclasses import replace
import time

import pytest

from custom_components.victron_vebus.vebus import (
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusTimeoutError,
    VeBusWriteError,
)
from custom_components.victron_vebus.vebus.const import CMD_WINMON_TAGGED, RamVar

from .simulator import FakeMK3, SimulatedMultiPlus


async def _client(device: SimulatedMultiPlus | None = None) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=FakeMK3(device), request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


# ---------------------------------------------------------------------------
# Late replies


async def test_tag_waiting_for_late_reply_is_skipped() -> None:
    """A tag whose request timed out is not used again for a while."""
    client = await _client()
    x, y, z = CMD_WINMON_TAGGED
    client._late_tags = {x: time.monotonic() + 60, y: time.monotonic() + 60}
    assert {client._next_tag() for _ in range(3)} == {z}
    # All tags waiting: the oldest is used rather than none.
    client._late_tags = {x: time.monotonic() + 30, y: time.monotonic() + 60}
    client._late_tags[z] = time.monotonic() + 90
    assert client._next_tag() == x
    await client.close()


async def test_late_reply_is_not_taken_for_the_next_request() -> None:
    """A reply that arrives after its timeout is dropped and counted."""
    device = SimulatedMultiPlus()
    device.settings[Setting.U_BAT_FLOAT] = 2760
    client = await _client(device)
    # Requests during start-up that went unanswered on purpose.
    client._late_tags.clear()
    late = client.stats.late_replies
    device.late_winmon = 1
    with pytest.raises(VeBusTimeoutError):
        await client.read_setting_raw(Setting.U_BAT_ABSORPTION)
    assert len(client._late_tags) == 1
    # The late reply (2880, absorption) arrives first; the float voltage
    # request must get its own reply.
    assert await client.read_setting_raw(Setting.U_BAT_FLOAT) == 2760
    assert client.stats.late_replies == late + 1
    assert client._late_tags == {}
    await client.close()


async def test_ess_write_to_the_wrong_target_is_detected() -> None:
    """A lost WriteRAMVar writes to the previous target; the read back shows it."""
    device = SimulatedMultiPlus()
    device.enable_ess()
    client = await _client(device)
    await client.write_ess_setpoint(0, -200)
    device.lost_write_ram_var = 1
    with pytest.raises(VeBusWriteError, match="reads back"):
        await client.write_ess_flags(0, disable_charge=True)
    await client.close()


async def test_ess_flags_not_read_are_not_written() -> None:
    """Writing the flags from 0 would clear the other flag."""
    device = SimulatedMultiPlus()
    device.enable_ess()
    client = await _client(device)
    del device.assistant_ram[0][132]
    writes = len(device.ram_writes)
    with pytest.raises(VeBusWriteError, match="could not be read"):
        await client.write_ess_flags(0, disable_charge=True)
    assert len(device.ram_writes) == writes
    await client.close()


# ---------------------------------------------------------------------------
# Safe EEPROM writes


async def test_flag_change_needs_two_equal_reads() -> None:
    """A flag change is based on a value read twice the same."""
    device = SimulatedMultiPlus()
    client = await _client(device)
    device.read_sequence[Setting.FLAGS_0] = [0x89B0, 0x09B0]
    with pytest.raises(VeBusWriteError, match="nothing was written"):
        await client.set_setting_flag(
            Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT, True, persistent=True
        )
    assert device.writes == []
    await client.close()


async def test_save_refuses_a_value_outside_the_range() -> None:
    """Nothing reaches the EEPROM outside the range the device reports."""
    device = SimulatedMultiPlus()
    client = await _client(device)
    client.unsaved_settings.add(Setting.I_BAT_BULK)
    client.ram_values[Setting.I_BAT_BULK] = 999
    with pytest.raises(VeBusWriteError, match="outside"):
        await client.save_settings()
    assert device.writes == []
    await client.close()


async def test_save_refuses_without_a_range() -> None:
    """Without a known range a value is not stored."""
    device = SimulatedMultiPlus()
    client = await _client(device)
    await client.write_setting(Setting.I_BAT_BULK, 50)
    info = client.setting_info[Setting.I_BAT_BULK]
    assert info is not None
    client.setting_info[Setting.I_BAT_BULK] = replace(info, maximum=None)
    with pytest.raises(VeBusWriteError, match="isn't known"):
        await client.save_settings()
    assert [w for w in device.writes if w[0] == 0x01] == []
    await client.close()


async def test_eeprom_writes_are_logged() -> None:
    """Every permanent write is in the log, the failed ones too."""
    device = SimulatedMultiPlus()
    client = await _client(device)
    await client.write_setting(Setting.I_BAT_BULK, 50, persistent=True)
    assert [(e["setting"], e["value"], e["result"]) for e in client.eeprom_writes] == [
        (4, 50, "ok")
    ]
    # The read back differs: the write may have reached the EEPROM anyway.
    device.read_sequence[Setting.U_BAT_FLOAT] = [2600]
    with pytest.raises(VeBusWriteError, match="reads back"):
        await client.write_setting(Setting.U_BAT_FLOAT, 27.0, persistent=True)
    assert client.eeprom_writes[-1]["setting"] == 3
    assert client.eeprom_writes[-1]["value"] == 2700
    assert client.eeprom_writes[-1]["result"] == "read back differs"
    await client.close()


# ---------------------------------------------------------------------------
# Stale values


async def test_slow_value_expires_after_failed_reads() -> None:
    """A slow value is unknown once it could not be read a few times."""
    device = SimulatedMultiPlus()
    client = await _client(device)
    state = await client.poll(full=True)
    assert state.settings[Setting.U_BAT_FLOAT] == pytest.approx(27.6)
    device.unanswered_settings = {Setting.U_BAT_FLOAT}
    for _ in range(2):
        state = await client.poll(full=True)
        assert state.settings[Setting.U_BAT_FLOAT] == pytest.approx(27.6)
    state = await client.poll(full=True)
    assert state.settings[Setting.U_BAT_FLOAT] is None
    device.unanswered_settings = set()
    state = await client.poll(full=True)
    assert state.settings[Setting.U_BAT_FLOAT] == pytest.approx(27.6)
    await client.close()


async def test_ram_fallback_shows_unknown_not_zero() -> None:
    """Without info frames, a RAM value that was not read is unknown."""
    device = SimulatedMultiPlus(info_frames=False)
    client = await _client(device)
    dc, ac = await client.read_info_from_ram()
    assert ac.mains_voltage is not None
    # Variables the device gives no scale for are read as None.
    client.ram_var_info.pop(RamVar.I_BAT)
    client.ram_var_info.pop(RamVar.I_MAINS_RMS)
    dc, ac = await client.read_info_from_ram()
    assert dc.battery_current is None
    assert dc.charger_current is None
    assert ac.mains_current is None
    await client.close()
