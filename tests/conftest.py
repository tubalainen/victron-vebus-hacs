"""Fixtures for the Victron VE.Bus tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

from homeassistant.const import CONF_DEVICE
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.const import DOMAIN

from .vebus.simulator import FakeMK3

DEVICE_PATH = "/dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_HQ123-if00-port0"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration."""


@pytest.fixture(autouse=True)
def fast_timeouts() -> Generator[None]:
    """Make request timeouts short so tests run quickly."""
    with patch("custom_components.victron_vebus.vebus.client.REQUEST_TIMEOUT", 0.05):
        yield


@pytest.fixture
def fake_mk3() -> Generator[FakeMK3]:
    """Replace the serial port with a simulated MK3 and MultiPlus-II."""
    fake = FakeMK3()
    with patch("custom_components.victron_vebus.vebus.transport.open_serial", fake):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Victron VE.Bus",
        unique_id="HQ123",
        data={CONF_DEVICE: DEVICE_PATH},
        options={},
    )
