"""Tests for recording setting writes (1.1.3)."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import (
    Setting,
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


@pytest.mark.parametrize("mode", ["ignored", "readback_eeprom"])
async def test_read_back_mismatch_names_the_reply(mode: str) -> None:
    """The error and the record say what the device replied."""
    fake = FakeMK3()
    fake.device.ram_only_mode = mode
    client = await _client(fake)
    with pytest.raises(VeBusWriteError) as err:
        await client.write_setting(Setting.I_BAT_BULK, 39)
    message = str(err.value)
    assert "reads back 70 after writing 39" in message
    assert "0x88 setting written" in message
    assert "0x03 (RAM only)" in message
    record = client.last_write
    assert record is not None
    assert record["readback"] == 70
    assert record["result"] == "read back differs"
    # Frames stop being captured once the session has ended.
    count = len(record["frames"])
    await client.poll()
    assert len(record["frames"]) == count
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
