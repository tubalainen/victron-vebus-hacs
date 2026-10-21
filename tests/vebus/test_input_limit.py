"""Tests for reading the configured input current limit in effect (1.1.5)."""

from __future__ import annotations

from custom_components.victron_vebus.vebus import Setting, VeBusClient

from .simulator import FakeMK3


async def _client(fake: FakeMK3) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


def _multiplus() -> FakeMK3:
    """A MultiPlus-II as seen with firmware 556."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    fake.device.limit_follows_setting = True
    return fake


async def test_remote_limit_is_read() -> None:
    """The remote panel limit comes from the interface ('D')."""
    fake = FakeMK3()
    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.remote_current_limit == 16.0
    await client.close()


async def test_limit_below_remote_limit_is_read() -> None:
    """Below the remote panel limit, the limit in effect is the RAM value."""
    fake = _multiplus()
    client = await _client(fake)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 7)
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "acknowledged"
    state = await client.poll(full=True)
    assert state.config is not None
    assert state.config.actual_current_limit == 7.0
    assert client.active_read == {6: 70}
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "read"
    assert state.settings[6] == 7.0
    assert client.stored_raw[6] == 160
    await client.close()


async def test_limit_is_read_after_a_new_connection() -> None:
    """After a restart of Home Assistant everything comes from the device."""
    fake = _multiplus()
    client = await _client(fake)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 7)
    await client.close()

    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.settings[6] == 7.0
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "read"
    # ReadSetting shows 16 A while 7 A is in effect: it reports the EEPROM.
    assert client.readsetting_reports == "eeprom"
    assert client.stored_raw[6] == 160
    # Only changes the device acknowledged are saved.
    assert client.unsaved_settings == set()
    await client.close()


async def test_limit_at_the_remote_limit_is_not_read() -> None:
    """At the remote panel limit, the configured limit may be higher."""
    fake = _multiplus()
    fake.device.current_limit = 50
    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.config is not None
    assert state.config.actual_current_limit == 5.0
    assert client.active_read == {}
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "unverified"
    assert state.settings[6] == 16.0
    await client.close()


async def test_interface_without_remote_state() -> None:
    """An interface that doesn't answer 'D' is no longer asked."""
    fake = FakeMK3()
    client = await _client(fake)
    fake.device.drop_replies["D"] = 100
    for _ in range(3):
        await client.poll()
    assert not client.remote_state_supported
    received = len(fake.device.received)
    await client.poll()
    assert all(frame[2] != ord("D") for frame in fake.device.received[received:])
    await client.close()
