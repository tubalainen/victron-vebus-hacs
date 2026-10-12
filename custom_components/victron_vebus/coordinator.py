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
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ESS_REFRESH_INTERVAL,
    UNAVAILABLE_ISSUE_DELAY,
)
from .vebus import (
    DeviceIdentity,
    InterfaceFlag,
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusError,
    VeBusState,
)
from .vebus.const import (
    MIN_FIRMWARE_RAM_ONLY_WRITE,
    MIN_FIRMWARE_SOLAR_WIND_PRIORITY,
)
from .vebus.models import BatteryLimits

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
        # Device registry id of the system device, parent of the unit devices.
        self.system_device_id: str | None = None
        # ESS external control: setpoints by phase master address, in the
        # Home Assistant / GX sign (positive = take power from the grid).
        self.ess_control = False
        self.ess_setpoints: dict[int, float] = {}
        self._ess_written: dict[int, tuple[int, float]] = {}
        # Battery Operational Limits, sent on every poll once set.
        self.battery_limits: BatteryLimits | None = None
        self._battery_limits_timeout_logged = False
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
                before_poll=self._async_periodic_writes,
            ),
            f"{DOMAIN} {self.client.url}",
        )

    @property
    def battery_limits_enabled(self) -> bool:
        """Return True if Battery Operational Limits are enabled and supported."""
        return bool(
            self.config_entry.options.get(CONF_ENABLE_BATTERY_LIMITS, False)
            and self.client.supports_battery_limits
        )

    async def _async_periodic_writes(self) -> None:
        """Refresh the ESS setpoints and battery limits before every poll."""
        if self.ess_control:
            await self._async_write_ess_setpoints()
        if self.battery_limits is not None and self.battery_limits_enabled:
            reply = await self.client.send_battery_limits(self.battery_limits)
            if reply.timeout and not self._battery_limits_timeout_logged:
                _LOGGER.warning("The device reports missing battery limit information")
                self._battery_limits_timeout_logged = True

    async def _async_write_ess_setpoints(self, *, force: bool = False) -> None:
        now = time.monotonic()
        for address in self.client.ess_records:
            # Home Assistant sign: positive = import. MK3 sign: negative = import.
            value = -round(self.ess_setpoints.get(address, 0.0))
            last = self._ess_written.get(address)
            if (
                not force
                and last is not None
                and last[0] == value
                and now - last[1] < ESS_REFRESH_INTERVAL
            ):
                continue
            await self.client.write_ess_setpoint(address, value)
            self._ess_written[address] = (value, now)

    async def async_set_ess_control(self, enabled: bool) -> None:
        """Start or stop external control of the ESS setpoint.

        Stopping writes a setpoint of 0 W, which returns the assistant to its
        standard behaviour.
        """
        self.ess_control = enabled
        self._ess_written.clear()
        if enabled:
            await self._async_write_ess_setpoints(force=True)
        else:
            for address in self.client.ess_records:
                await self.client.write_ess_setpoint(address, 0)

    async def async_set_ess_setpoint(self, address: int, watts: float) -> None:
        """Store an ESS setpoint (positive = import) and write it if in control."""
        self.ess_setpoints[address] = watts
        if self.ess_control:
            await self._async_write_ess_setpoints(force=True)

    async def async_set_battery_limits(self, limits: BatteryLimits) -> None:
        """Store the battery limits and send them right away."""
        self.battery_limits = limits
        if self.battery_limits_enabled:
            await self.client.send_battery_limits(limits)

    async def async_release(self) -> None:
        """Hand ESS control back to the device before unloading."""
        if not self.ess_control or not self.client.connected:
            return
        try:
            for address in self.client.ess_records:
                await self.client.write_ess_setpoint(address, 0)
        except VeBusError as err:
            _LOGGER.debug("Could not reset the ESS setpoint: %s", err)

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
