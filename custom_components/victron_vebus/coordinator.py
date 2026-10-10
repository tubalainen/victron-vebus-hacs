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
from .vebus import DeviceIdentity, InterfaceFlag, VeBusClient, VeBusState

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
