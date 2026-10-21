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
    assert await client.write_setting(Setting.I_BAT_BULK, 39) == 39
    record = client.last_write
    assert record is not None
    assert record["reply"] == "0x88 setting written"
    assert record["readback"] == 70
    assert record["result"] == "ok (read back shows the stored value)"
    assert client.ram_values == {4: 39}
    assert client.stored_raw == {4: 70}
    assert client.unsaved_settings == {4}
    assert fake.device.settings[4] == 39

    # Polling keeps showing the value in effect, not the stored one.
    await client.poll(full=True)
    assert client.state.settings[4] == 39
    assert client.stored_raw == {4: 70}
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
    assert client.stored_raw == {}
    assert client.unsaved_settings == set()
    await client.poll(full=True)
    assert client.state.settings[4] == 39
    await client.close()


async def test_writing_the_stored_value_drops_the_override() -> None:
    """Changing back to the stored value needs no RAM override."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    await client.write_setting(Setting.I_BAT_BULK, 70)
    assert client.last_write is not None
    assert client.last_write["result"] == "ok"
    assert client.ram_values == {}
    assert client.stored_raw == {}
    await client.close()


async def test_flag_change_starts_from_the_ram_value() -> None:
    """A flag change keeps earlier RAM-only flag changes."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    client = await _client(fake)
    await client.set_setting_flag(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT, True)
    await client.set_setting_flag(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED, False)
    expected = (0x89B0 | SettingFlag.WEAK_AC_INPUT) & ~SettingFlag.ASSIST_ENABLED
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
    assert client.stored_raw == {}
    assert client.unsaved_settings == set()
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
    client = await _client(fake)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    assert client.last_write is not None
    assert client.last_write["reply"] == "no reply"
    assert client.last_write["result"] == "ok"
    await client.close()
