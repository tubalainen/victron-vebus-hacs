"""Switches for the Victron VE.Bus integration.

The keep awake switch, and (with controls enabled) switches for flag settings
that are changed in RAM only, so a device restart restores its configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_ENABLE_CONTROLS
from .coordinator import VeBusConfigEntry, VeBusCoordinator
from .entity import VeBusEntity
from .vebus import Setting, SettingFlag

PARALLEL_UPDATES = 1

KEEP_AWAKE = SwitchEntityDescription(
    key="keep_awake",
    translation_key="keep_awake",
    entity_category=EntityCategory.CONFIG,
)


@dataclass(frozen=True, kw_only=True)
class VeBusFlagSwitchEntityDescription(SwitchEntityDescription):
    """Describes a switch backed by one bit of a flag setting."""

    setting: Setting
    flag: SettingFlag
    # The switch is on while the bit is clear.
    inverted: bool = False
    exists_fn: Callable[[VeBusCoordinator], bool] = lambda _: True


FLAG_SWITCHES: tuple[VeBusFlagSwitchEntityDescription, ...] = (
    VeBusFlagSwitchEntityDescription(
        key="power_assist",
        translation_key="power_assist",
        entity_category=EntityCategory.CONFIG,
        setting=Setting.FLAGS_0,
        flag=SettingFlag.ASSIST_ENABLED,
    ),
    VeBusFlagSwitchEntityDescription(
        key="weak_ac_input",
        translation_key="weak_ac_input",
        entity_category=EntityCategory.CONFIG,
        setting=Setting.FLAGS_0,
        flag=SettingFlag.WEAK_AC_INPUT,
    ),
    VeBusFlagSwitchEntityDescription(
        key="ups_function",
        translation_key="ups_function",
        entity_category=EntityCategory.CONFIG,
        setting=Setting.FLAGS_0,
        flag=SettingFlag.DISABLE_WAVE_CHECK,
        inverted=True,
    ),
    VeBusFlagSwitchEntityDescription(
        key="solar_wind_priority",
        translation_key="solar_wind_priority",
        setting=Setting.FLAGS_2,
        flag=SettingFlag.SOLAR_WIND_PRIORITY,
        exists_fn=lambda coordinator: coordinator.supports_solar_wind_priority(),
    ),
)


ESS_CONTROL = SwitchEntityDescription(
    key="ess_external_control",
    translation_key="ess_external_control",
)


@dataclass(frozen=True, kw_only=True)
class VeBusEssFlagSwitchEntityDescription(SwitchEntityDescription):
    """Describes a switch for one of the ESS assistant flags."""

    flag: str  # attribute of EssState and keyword of write_ess_flags


ESS_FLAG_SWITCHES: tuple[VeBusEssFlagSwitchEntityDescription, ...] = (
    VeBusEssFlagSwitchEntityDescription(
        key="ess_disable_charge",
        translation_key="ess_disable_charge",
        flag="disable_charge",
    ),
    VeBusEssFlagSwitchEntityDescription(
        key="ess_disable_feed_in",
        translation_key="ess_disable_feed_in",
        flag="disable_feed_in",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VE.Bus switches."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [VeBusKeepAwakeSwitch(coordinator, KEEP_AWAKE)]
    if entry.options.get(CONF_ENABLE_CONTROLS, False):
        entities.extend(
            VeBusFlagSwitch(coordinator, description)
            for description in FLAG_SWITCHES
            if coordinator.can_write_flag(description.setting, description.flag)
            and description.exists_fn(coordinator)
        )
        if coordinator.client.ess_records:
            entities.append(VeBusEssControlSwitch(coordinator, ESS_CONTROL))
            entities.extend(
                VeBusEssFlagSwitch(coordinator, description)
                for description in ESS_FLAG_SWITCHES
            )
    async_add_entities(entities)


class VeBusKeepAwakeSwitch(VeBusEntity, SwitchEntity, RestoreEntity):
    """Keeps the VE.Bus device awake while it is switched off.

    A switched off device goes to sleep after a while and stops powering the
    MK3, which then cannot switch it back on. The MK3 'standby' flag prevents
    this. The flag is lost whenever the device loses power, so it is sent again
    on every poll. The setting is stored in Home Assistant and restored on start.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the previous state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self.coordinator.keep_awake = (
            last_state is None or last_state.state != STATE_OFF
        )

    @property
    def available(self) -> bool:
        """The setting can be changed even while the device is unreachable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the device is kept awake."""
        return self.coordinator.keep_awake

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Report the flags last acknowledged by the interface."""
        flags = self.coordinator.data.interface_flags
        if flags is None:
            return None
        return {"interface_flags": f"0x{flags:02x}"}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Keep the device awake."""
        self.coordinator.keep_awake = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Allow the device to sleep."""
        self.coordinator.keep_awake = False
        self.async_write_ha_state()


class VeBusFlagSwitch(VeBusEntity, SwitchEntity):
    """One bit of a flag setting, changed in RAM only."""

    entity_description: VeBusFlagSwitchEntityDescription

    @property
    def is_on(self) -> bool | None:
        """Return whether the feature is enabled."""
        description = self.entity_description
        bit = self.coordinator.data.setting_flag(description.setting, description.flag)
        if bit is None:
            return None
        return bit != description.inverted

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the feature."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the feature."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        description = self.entity_description
        await self.async_device_call(
            self.coordinator.client.set_setting_flag(
                description.setting, description.flag, on != description.inverted
            )
        )


class VeBusEssControlSwitch(VeBusEntity, SwitchEntity, RestoreEntity):
    """Lets Home Assistant control the ESS grid setpoint.

    While on, the setpoint numbers are written to the ESS assistant and
    refreshed regularly. Turning it off writes 0 W, which returns the
    assistant to its standard behaviour. The state is restored on restart.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the previous state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_ON:
            # Resume control; the setpoint numbers restore their values too.
            self.coordinator.ess_control = True

    @property
    def is_on(self) -> bool:
        """Return whether Home Assistant controls the ESS setpoint."""
        return self.coordinator.ess_control

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start controlling the ESS setpoint."""
        await self.async_device_call(self.coordinator.async_set_ess_control(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hand the ESS setpoint back to the device."""
        await self.async_device_call(self.coordinator.async_set_ess_control(False))


class VeBusEssFlagSwitch(VeBusEntity, SwitchEntity):
    """The ESS disable charge / disable feed-in flag, on every phase master."""

    entity_description: VeBusEssFlagSwitchEntityDescription

    @property
    def is_on(self) -> bool | None:
        """Return the flag of the L1 phase master."""
        ess = self.coordinator.data.ess.get(self.coordinator.client.address)
        if ess is None:
            return None
        value: bool | None = getattr(ess, self.entity_description.flag)
        return value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the flag."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Clear the flag."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        client = self.coordinator.client
        for address in client.ess_records:
            await self.async_device_call(
                client.write_ess_flags(address, **{self.entity_description.flag: on})
            )
