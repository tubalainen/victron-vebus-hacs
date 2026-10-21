"""Tests for the remote panel limit and the MK3 panel state (1.1.5 to 1.1.10)."""

from __future__ import annotations

import asyncio
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


async def test_configured_limit_is_not_derived() -> None:
    """The limit in effect doesn't set the configured limit (1.1.10).

    1.1.5 to 1.1.9 read the configured limit from the limit in effect while
    that was below the remote limit; on a MultiPlus-II that rule held in some
    readings and not in others.
    """
    fake = _multiplus(stored=60)
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.ON, 16)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 7)
    state = await client.poll(full=True)
    assert state.config is not None
    assert state.config.actual_current_limit == 7.0
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "acknowledged"
    assert state.settings[6] == 7.0
    await client.close()

    # After a new connection only the stored value is known.
    client = await _client(fake)
    state = await client.poll(full=True)
    assert client.active_value_source(Setting.I_MAINS_LIMIT_AC1) == "unverified"
    assert state.settings[6] == 6.0
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


async def test_setting_write_keeps_the_panel_state() -> None:
    """A long-frame session doesn't make the MK3 forget the panel state.

    1.1.6 returned to mode 1 with switch state 0, after which 'D' reported
    04 00 00 and the remote limit was unknown.
    """
    fake = _multiplus(stored=60)
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.ON, 10)
    await client.write_setting(Setting.I_MAINS_LIMIT_AC1, 8)
    assert fake.device.panel_state == (3, 100)
    mode1 = [f for f in fake.device.received if f[2] == ord("S") and f[-2] == 0x01]
    assert mode1[-1][3:8] == bytes([3, 100, 0, 0x01, 0x90])
    state = await client.poll(full=True)
    assert state.remote_current_limit == 10.0
    assert state.settings[6] == 8.0
    await client.close()


async def test_new_connection_keeps_the_panel_state() -> None:
    """Starting up sends the panel state the MK3 holds back to it."""
    fake = _multiplus(stored=60)
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.ON, 9)
    await client.close()
    client = await _client(fake)
    state = await client.poll(full=True)
    assert state.remote_current_limit == 9.0
    await client.close()


async def test_off_panel_state_is_not_sent_back() -> None:
    """Only on, charger only and inverter only are sent back."""
    fake = FakeMK3()
    client = await _client(fake)
    await client.set_remote_panel_state(Mode.OFF, 9)
    await client.write_setting(Setting.I_BAT_BULK, 39)
    mode1 = [f for f in fake.device.received if f[2] == ord("S") and f[-2] == 0x01]
    assert mode1[-1][3:6] == bytes([0, 0, 0])
    await client.close()


async def test_write_during_a_poll_is_kept() -> None:
    """A poll that started before a write doesn't undo it."""
    fake = _multiplus(stored=60)
    client = await _client(fake)
    # A short poll keeps the settings it doesn't read from when it started.
    await asyncio.gather(
        client.poll(), client.write_setting(Setting.I_MAINS_LIMIT_AC1, 8)
    )
    assert client.state.settings[6] == 8.0
    await client.close()
