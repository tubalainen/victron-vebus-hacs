"""Diagnostics support for the Victron VE.Bus integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import VeBusConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VeBusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    client = runtime.client
    coordinator = runtime.coordinator
    stats = asdict(client.stats)
    stats["last_frames"] = list(client.stats.last_frames)
    stats["checksum_errors"] = client.checksum_errors
    stats["link_quality"] = coordinator.link.success_rate
    return {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "connected": client.connected,
        "address": client.address,
        "mk3_version": client.mk3_version,
        "firmware": client.firmware.raw if client.firmware else None,
        "phases": list(client.phases),
        "units": list(client.units),
        "unit_firmware": {
            str(address): firmware.raw if firmware else None
            for address, firmware in client.unit_firmware.items()
        },
        "info_frames": client.info_frames,
        "ess_records": {str(k): v for k, v in client.ess_records.items()},
        "ess_control": coordinator.ess_control,
        "ess_setpoints": {str(k): v for k, v in coordinator.ess_setpoints.items()},
        "battery_limits_enabled": coordinator.battery_limits_enabled,
        "interface_flags_supported": client.interface_flags_supported,
        "keep_awake": coordinator.keep_awake,
        "last_update_success": coordinator.last_update_success,
        "ram_var_info": {
            str(var): asdict(info) if info else None
            for var, info in client.ram_var_info.items()
        },
        "setting_info": {
            str(setting): asdict(info) if info else None
            for setting, info in client.setting_info.items()
        },
        "state": asdict(client.state),
        "statistics": stats,
        "last_write": client.last_write,
        "readsetting_reports": client.readsetting_reports,
        "ram_values": {str(k): v for k, v in client.ram_values.items()},
        "stored_raw": {str(k): v for k, v in client.stored_raw.items()},
        "active_read": {str(k): v for k, v in client.active_read.items()},
        "active_minimum": {str(k): v for k, v in client.active_minimum.items()},
    }
