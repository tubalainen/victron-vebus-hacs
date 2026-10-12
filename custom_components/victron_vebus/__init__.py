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
from .coordinator import VeBusConfigEntry, VeBusCoordinator, VeBusRuntimeData
from .entity import device_identifier, system_device_info
from .services import async_setup_services
from .vebus import Setting, VeBusClient, VeBusError

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]
CONTROL_PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.NUMBER]
# Entities of earlier versions whose changes are now only offered in the guided
# "Device actions" dialog, which shows their risks first (v0.5).
REMOVED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("select", "remote_mode"),
    ("switch", "power_assist"),
    ("switch", "weak_ac_input"),
    ("switch", "ups_function"),
    ("number", "absorption_voltage"),
    ("number", "float_voltage"),
    ("button", "force_equalise"),
)
# Settings that control entities may change; their ranges are needed up front.
WRITABLE_SETTINGS = (
    Setting.U_BAT_ABSORPTION,
    Setting.U_BAT_FLOAT,
    Setting.I_BAT_BULK,
    Setting.I_MAINS_LIMIT_AC1,
    Setting.I_MAINS_LIMIT_AC2,
    Setting.SOLAR_WIND_PRIORITY_VOLTAGE,
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
    await entry.runtime_data.coordinator.async_release()
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
