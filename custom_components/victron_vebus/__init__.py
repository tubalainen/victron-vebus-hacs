"""The Victron VE.Bus (MK3) integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENABLE_CONTROLS,
    DEFAULT_ADDRESS,
    DOMAIN,
    SETUP_TIMEOUT,
)
from .coordinator import (
    WRITABLE_SETTINGS,
    VeBusConfigEntry,
    VeBusCoordinator,
    VeBusRuntimeData,
)
from .entity import device_identifier, system_device_info
from .services import async_setup_services
from .vebus import VeBusClient, VeBusError

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]
CONTROL_PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT]
# Entities of earlier versions that no longer exist; removed from the registry.
REMOVED_ENTITIES: tuple[tuple[str, str], ...] = (
    # The LED binary sensors became sensors showing off / on / blinking (v0.6).
    *(
        ("binary_sensor", f"led_{name}")
        for name in (
            "mains",
            "absorption",
            "bulk",
            "float",
            "inverter",
            "overload",
            "low_battery",
            "temperature",
        )
    ),
)


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

    if entry.options.get(CONF_ENABLE_CONTROLS, False):
        try:
            await client.complete_setting_info(WRITABLE_SETTINGS)
        except VeBusError as err:
            _LOGGER.warning(
                "Could not read the ranges of the charger settings: %s", err
            )

    coordinator = VeBusCoordinator(hass, entry, client, identity)
    # Register the system device first so unit devices can refer to it.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **system_device_info(coordinator)
    )
    coordinator.system_device_id = device.id
    _async_remove_old_entities(hass, coordinator)
    platforms = _platforms(entry)
    entry.runtime_data = VeBusRuntimeData(
        client=client, coordinator=coordinator, platforms=platforms
    )
    entry.async_on_unload(client.close)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    coordinator.async_start()
    return True


def _async_remove_old_entities(
    hass: HomeAssistant, coordinator: VeBusCoordinator
) -> None:
    """Remove entities of earlier versions that no longer exist."""
    registry = er.async_get(hass)
    identifier = device_identifier(coordinator)
    for domain, key in REMOVED_ENTITIES:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{identifier}_{key}")
        if entity_id is not None:
            registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: VeBusConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_release()
    coordinator.async_clear_issues()
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
