"""Tests for keeping the last good value through missed replies (v0.7)."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus import (
    Mode,
    VeBusClient,
    client as client_module,
)
from custom_components.victron_vebus.vebus.client import MAX_STALE_READS

from .simulator import FakeMK3


async def _client(fake: FakeMK3) -> VeBusClient:
    client = VeBusClient("/dev/fake", opener=fake, request_timeout=0.05)
    await client.connect()
    await client.initialize()
    return client


async def test_missed_config_reply_keeps_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A few missed 'F' 5 replies keep the mode; more make it unknown."""
    monkeypatch.setattr(client_module, "MASTER_MULTI_LED_TIMEOUT", 0.05)
    fake = FakeMK3()
    client = await _client(fake)
    assert client.state.config is not None
    assert client.state.config.mode is Mode.ON

    fake.device.drop_replies["F5"] = MAX_STALE_READS
    for _ in range(MAX_STALE_READS):
        state = await client.poll()
        assert state.config is not None
        assert state.config.mode is Mode.ON
    # A good reply resets the count.
    assert (await client.poll()).config is not None

    fake.device.drop_replies["F5"] = MAX_STALE_READS + 1
    for _ in range(MAX_STALE_READS):
        assert (await client.poll()).config is not None
    assert (await client.poll()).config is None
    # Back as soon as the device answers again.
    state = await client.poll()
    assert state.config is not None
    assert state.config.mode is Mode.ON
    await client.close()


async def test_missed_led_reply_keeps_leds() -> None:
    """A missed 'L' reply keeps the LEDs."""
    fake = FakeMK3()
    client = await _client(fake)
    leds = client.state.leds
    assert leds is not None
    fake.device.drop_replies["L"] = 1
    assert (await client.poll()).leds == leds
    await client.close()


async def test_request_statistics() -> None:
    """Every request that expects a reply is counted, and so are its timeouts."""
    fake = FakeMK3()
    client = await _client(fake)
    requests, timeouts = client.stats.requests, client.stats.timeouts
    assert requests > 0
    fake.device.drop_replies["L"] = 1
    await client.poll()
    assert client.stats.requests > requests
    assert client.stats.timeouts == timeouts + 1
    assert client.checksum_errors == 0
    await client.close()
