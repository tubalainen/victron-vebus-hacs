"""Tests for recording and verifying setting writes (1.1.3, 1.1.4)."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import (
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusWriteError,
)

from .simulator import FakeMK3


async def _client(fake: FakeMK3) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


async def test_successful_write_is_recorded() -> None:
    """A RAM-only write records its reply, read back and frames."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "ok"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    record = client.last_write
    assert record is not None
    assert record["setting"] == 4
    assert record["value"] == 39
    assert record["flags"] == "0x03 (RAM only)"
    assert record["reply"] == "0x88 setting written"
    assert record["readback"] == 39
    assert record["result"] == "ok"
    frames = record["frames"]
    # The whole long-frame session: D, S (long), 0x37, 0x31, S (short).
    assert frames[0].startswith(">")
    assert any(" 37 03 04 27 00" in frame for frame in frames)
    assert any(frame.startswith("<") for frame in frames)
    await client.close()


async def test_read_back_mismatch_names_the_reply() -> None:
    """Without an acknowledgement, a different read back is an error."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    fake.device.ack_writes = False
    client = await _client(fake)
    with pytest.raises(VeBusWriteError) as err:
        await client.write_setting(Setting.I_BAT_BULK, 39)
    message = str(err.value)
    assert "reads back 70 after writing 39" in message
    assert "no reply" in message
    assert "0x03 (RAM only)" in message
    record = client.last_write
    assert record is not None
    assert record["readback"] == 70
    assert record["result"] == "read back differs"
    assert client.ram_values == {}
    # Frames stop being captured once the session has ended.
    count = len(record["frames"])
    await client.poll()
    assert len(record["frames"]) == count
    await client.close()


async def test_acknowledged_ram_write_with_eeprom_read_back() -> None:
    """A 0x88 acknowledged RAM write counts, even if ReadSetting shows EEPROM.

    Seen on a MultiPlus-II (firmware 556): the charger follows the new
    value while ReadSetting keeps reporting the stored one.
    """
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    assert client.active_value_source(Setting.I_BAT_BULK) == "unverified"
    assert await client.write_setting(Setting.I_BAT_BULK, 39) == 39
    record = client.last_write
    assert record is not None
    assert record["reply"] == "0x88 setting written"
    assert record["readback"] == 70
    assert record["result"] == "ok (read back shows the stored value)"
    assert client.readsetting_reports == "eeprom"
    assert client.ram_values == {4: 39}
    assert client.stored_raw[4] == 70
    assert client.unsaved_settings == {4}
    assert client.active_value_source(Setting.I_BAT_BULK) == "acknowledged"
    assert client.active_value_source(Setting.U_BAT_FLOAT) == "stored"
    assert fake.device.settings[4] == 39

    # Polling keeps showing the value in effect, and learns stored values.
    await client.poll(full=True)
    assert client.state.settings[4] == 39
    assert client.stored_raw[4] == 70
    assert client.stored_raw[3] == 2760
    await client.close()


async def test_read_back_showing_ram() -> None:
    """A device whose ReadSetting follows RAM-only writes reports RAM."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "ok"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert client.readsetting_reports == "ram"
    assert client.active_value_source(Setting.I_BAT_BULK) == "read"
    assert client.ram_values == {}
    assert client.stored_raw == {}
    assert client.unsaved_settings == {4}
    await client.close()


async def test_save_writes_the_ram_value() -> None:
    """Saving stores the RAM value, not the stored value ReadSetting shows."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert await client.save_settings() == [4]
    assert fake.device.eeprom[4] == 39
    assert client.ram_values == {}
    assert client.stored_raw[4] == 39
    assert client.unsaved_settings == set()
    assert client.active_value_source(Setting.I_BAT_BULK) == "stored"
    await client.poll(full=True)
    assert client.state.settings[4] == 39
    await client.close()


async def test_writing_the_stored_value_leaves_nothing_to_save() -> None:
    """Changing back to the stored value needs no EEPROM write."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    await client.write_setting(Setting.I_BAT_BULK, 70)
    assert client.last_write is not None
    assert client.last_write["result"] == "ok"
    assert client.ram_values == {}
    assert client.stored_raw[4] == 70
    assert client.unsaved_settings == set()
    assert client.active_value_source(Setting.I_BAT_BULK) == "stored"
    await client.close()


async def test_save_skips_values_equal_to_the_stored_ones() -> None:
    """Only RAM values that differ from the stored ones are written."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    # Written before the device showed that ReadSetting reports EEPROM.
    await client.write_setting(Setting.U_BAT_FLOAT, 27.6)
    assert client.readsetting_reports is None
    assert client.unsaved_settings == {3}
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert client.unsaved_settings == {3, 4}
    writes = len(fake.device.writes)
    assert await client.save_settings() == [4]
    assert fake.device.writes[writes:] == [(0x01, 4, 39)]
    assert client.unsaved_settings == set()
    await client.close()


async def test_flag_change_starts_from_the_ram_value() -> None:
    """A flag change keeps earlier RAM-only flag changes."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.set_setting_flag(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT, True)
    await client.set_setting_flag(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED, False)
    # Bit 15 stays set (0xC990).
    expected = (0x89B0 | SettingFlag.WEAK_AC_INPUT) & ~int(SettingFlag.ASSIST_ENABLED)
    assert fake.device.writes[-1] == (0x03, 0, expected)
    assert client.ram_values[0] == expected
    await client.close()


async def test_restart_forgets_ram_values() -> None:
    """After a restart the device runs on its stored settings again."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    await client.restart_device()
    assert client.ram_values == {}
    assert client.unsaved_settings == set()
    assert client.active_value_source(Setting.I_BAT_BULK) == "stored"
    # The stored values did not change.
    assert client.stored_raw[4] == 70
    await client.close()


async def test_reconnect_forgets_what_was_learnt() -> None:
    """Nothing learnt from the device is kept across connections."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    await client.close()
    await client.connect()
    assert client.readsetting_reports is None
    assert client.ram_values == {}
    assert client.stored_raw == {}
    assert client.active_value_source(Setting.I_BAT_BULK) == "unverified"
    await client.close()


async def test_persistent_write_with_eeprom_read_back() -> None:
    """A RAM and EEPROM write verifies even if ReadSetting reports the EEPROM."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39, persistent=True)
    record = client.last_write
    assert record is not None
    assert record["flags"] == "0x01 (RAM and EEPROM)"
    assert record["result"] == "ok"
    assert fake.device.eeprom[4] == 39
    await client.close()


async def test_no_reply_is_recorded() -> None:
    """A write without an acknowledgement is recorded as such."""
    fake = FakeMK3()
    fake.device.ack_writes = False
    fake.device.ram_only_mode = "ok"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert client.last_write is not None
    assert client.last_write["reply"] == "no reply"
    assert client.last_write["result"] == "ok"
    await client.close()
