"""Coordinator pushing VE.Bus state into Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    UNAVAILABLE_ISSUE_DELAY,
)
from .vebus import (
    DeviceIdentity,
    InterfaceFlag,
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusState,
)
from .vebus.const import (
    MIN_FIRMWARE_RAM_ONLY_WRITE,
    MIN_FIRMWARE_SOLAR_WIND_PRIORITY,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class VeBusRuntimeData:
    """Runtime data stored on the config entry."""

    client: VeBusClient
    coordinator: VeBusCoordinator
    platforms: list[Platform]


type VeBusConfigEntry = ConfigEntry[VeBusRuntimeData]


class VeBusCoordinator(DataUpdateCoordinator[VeBusState]):
    """Receives state from the client's polling loop.

    The client runs its own loop (see ``VeBusClient.run``) and pushes every
    snapshot here, so the coordinator itself never polls.
    """

    config_entry: VeBusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VeBusConfigEntry,
        client: VeBusClient,
        identity: DeviceIdentity,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.client = client
        self.identity = identity
        self.keep_awake = True
        self._unavailable_since: float | None = None
        self.data = client.state

    async def _async_update_data(self) -> VeBusState:
        """Return the latest state; polling is done by the client loop."""
        return self.client.state

    def firmware_at_least(self, version: int) -> bool:
        """Return True if the device firmware revision is at least ``version``."""
        firmware = self.client.firmware
        return firmware is not None and firmware.version >= version

    def can_write_setting(self, setting: int) -> bool:
        """Return True if a numeric setting can be changed (range is known)."""
        info = self.client.setting_info.get(setting)
        return (
            self.firmware_at_least(MIN_FIRMWARE_RAM_ONLY_WRITE)
            and info is not None
            and info.minimum is not None
            and info.maximum is not None
        )

    def can_write_flag(self, setting: int, flag: int) -> bool:
        """Return True if a bit of a flag setting can be changed."""
        if not self.firmware_at_least(MIN_FIRMWARE_RAM_ONLY_WRITE):
            return False
        if self.client.state.settings_raw.get(setting) is None:
            return False
        info = self.client.setting_info.get(setting)
        return info is None or info.maximum is None or not flag & ~info.maximum

    def supports_solar_wind_priority(self) -> bool:
        """Return True if the device has solar & wind priority configured.

        The feature needs firmware 506. It is only offered when the device
        lists the bit as supported or has it set.
        """
        if not self.firmware_at_least(MIN_FIRMWARE_SOLAR_WIND_PRIORITY):
            return False
        raw = self.client.state.settings_raw.get(Setting.FLAGS_2)
        if raw is None:
            return False
        info = self.client.setting_info.get(Setting.FLAGS_2)
        mask = info.maximum if info is not None else None
        flag = SettingFlag.SOLAR_WIND_PRIORITY
        return bool(raw & flag) or (mask is not None and bool(mask & flag))

    @callback
    def async_start(self) -> None:
        """Start the client loop as a background task of the config entry."""
        interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.config_entry.async_create_background_task(
            self.hass,
            self.client.run(
                self._async_on_state,
                self._async_on_unavailable,
                interval=float(interval),
                interface_flags=self._interface_flags,
                initialized=True,
            ),
            f"{DOMAIN} {self.client.url}",
        )

    def _interface_flags(self) -> InterfaceFlag:
        """Return the MK3 flags to send on every poll.

        PANEL_DETECT is the interface default and is always kept. STANDBY
        prevents the device from going to sleep when switched off, which
        would cut the power to the MK3 so it could not wake it up again.
        """
        if self.keep_awake:
            return InterfaceFlag.PANEL_DETECT | InterfaceFlag.STANDBY
        return InterfaceFlag.PANEL_DETECT

    @callback
    def _async_on_state(self, state: VeBusState) -> None:
        if self._unavailable_since is not None:
            _LOGGER.info("VE.Bus device on %s is available again", self.client.url)
            self._unavailable_since = None
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self.async_set_updated_data(state)

    @callback
    def _async_on_unavailable(self, err: Exception) -> None:
        now = time.monotonic()
        if self._unavailable_since is None:
            _LOGGER.warning(
                "VE.Bus device on %s is unavailable: %s", self.client.url, err
            )
            self._unavailable_since = now
        elif now - self._unavailable_since > UNAVAILABLE_ISSUE_DELAY:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_unreachable",
                translation_placeholders={
                    "device": self.client.url,
                    "error": str(err),
                },
            )
        self.async_set_update_error(err)

    @property
    def _issue_id(self) -> str:
        return f"device_unreachable_{self.config_entry.entry_id}"

    async def async_refresh_config(self) -> None:
        """Re-read mode and current limit after a control command."""
        state = await self.client.refresh_config()
        self.async_set_updated_data(state)
