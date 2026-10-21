"""Tests for reading the configured input current limit in effect (1.1.5)."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.victron_vebus.actions import panel_limit
from custom_components.victron_vebus.vebus import Mode, Setting, VeBusClient

from .simulator import FakeMK3


async def _client(fake: FakeMK3) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


def _multiplus(stored: int = 160) -> FakeMK3:
    """A MultiPlus-II as seen with firmware 556."""
    fake = FakeMK3()
    fake.device.ram_only_mode = "readback_eeprom"
    fake.device.limit_follows_setting = True
    fake.device.settings[6] = stored
    fake.device.eeprom[6] = stored
    return fake


async def test_remote_limit_is_read() -> None:
    """The remote panel limit is known once a panel state was sent ('D')."""
    fake = FakeMK3()
    client = await _client(fake)
    state = await client.poll(full=True)
    # The MK3 reports 04 00 00 until then: not "off, 0 A".
    assert state.remote_current_limit is None
    await client.set_remote_panel_state(Mode.ON, 9)
    state = await client.poll(full=True)
    assert state.remote_current_limit == 9.0
    await client.close()


async def test_panel_state_must_match_the_remote_mode() -> None:
    """A 'D' reply whose mode isn't the device's remote mode doesn't count."""
    fake = FakeMK3()
    client = await _client(fake)
    fake.device.panel_state = (1, 90)  # charger only, but the remote mode is on
    state = await client.poll(full=True)
    assert state.remote_current_limit is None
    await client.close()


async def test_limit_below_remote_limit_is_read() -> None:
    """Below the remote panel limit, the limit in effect is the RAM value."""
    fake = _multiplus()
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.ON, 16)
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


async def test_limit_above_stored_after_a_new_connection() -> None:
    """After a restart of Home Assistant the remote limit is unknown.

    As on the MultiPlus-II: stored 6 A, 7 A in RAM and in effect. The limit
    in effect above the stored value shows that ReadSetting reports the
    EEPROM and that RAM differs, but not its value.
    """
    fake = _multiplus(stored=60)
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.ON, 9)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 7)
    await client.close()

    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.remote_current_limit is None
    assert client.readsetting_reports == "eeprom"
    assert client.stored_raw[6] == 60
    assert client.active_minimum == {6: 70}
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "at_least"
    assert state.settings[6] is None
    # Nothing acknowledged, so nothing to save.
    assert client.unsaved_settings == set()

    # Once the remote limit is known again, the value is read.
    await client.set_remote_panel_state(Mode.ON, 9)
    state = await client.poll(full=True)
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "read"
    assert state.settings[6] == 7.0
    await client.close()


async def test_limit_at_or_below_stored_is_not_guessed() -> None:
    """A limit in effect at or below the stored value proves nothing."""
    fake = _multiplus()
    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.config is not None
    assert state.config.actual_current_limit == 16.0
    assert client.active_read == {}
    assert client.active_minimum == {6: 160}
    assert client.readsetting_reports is None
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


async def test_mode_change_keeps_the_remote_limit() -> None:
    """The limit sent with a new mode is the remote limit, if known."""
    fake = _multiplus(stored=60)
    client = await _client(fake)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 7)
    state = await client.poll(full=True)
    coordinator = SimpleNamespace(passthru=False, data=state)
    # Remote unknown: the limit in effect keeps the limit unchanged.
    assert panel_limit(coordinator) == 7.0  # type: ignore[arg-type]
    await client.set_remote_panel_state(Mode.ON, 9)
    coordinator.data = await client.poll(full=True)
    assert panel_limit(coordinator) == 9.0  # type: ignore[arg-type]
    coordinator.passthru = True
    assert panel_limit(coordinator) == 0.0  # type: ignore[arg-type]
    await client.close()
