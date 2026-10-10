"""The Victron VE.Bus (MK3) integration."""

from __future__ import annotations

import asyncio

from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENABLE_CONTROLS,
    DEFAULT_ADDRESS,
    DOMAIN,
    SETUP_TIMEOUT,
)
from .coordinator import VeBusConfigEntry, VeBusCoordinator, VeBusRuntimeData
from .services import async_setup_services
from .vebus import VeBusClient, VeBusError

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]
CONTROL_PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SELECT]


def _platforms(entry: VeBusConfigEntry) -> list[Platform]:
    if entry.options.get(CONF_ENABLE_CONTROLS, False):
        return [*PLATFORMS, *CONTROL_PLATFORMS]
    return PLATFORMS


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (register services)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VeBusConfigEntry) -> bool:
    """Set up a VE.Bus device from a config entry."""
    client = VeBusClient(entry.data[CONF_DEVICE], address=DEFAULT_ADDRESS)
    try:
        async with asyncio.timeout(SETUP_TIMEOUT):
            await client.connect()
            identity = await client.initialize()
    except (VeBusError, TimeoutError) as err:
        await client.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"device": client.url, "error": str(err)},
        ) from err

    coordinator = VeBusCoordinator(hass, entry, client, identity)
    platforms = _platforms(entry)
    entry.runtime_data = VeBusRuntimeData(
        client=client, coordinator=coordinator, platforms=platforms
    )
    entry.async_on_unload(client.close)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VeBusConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
