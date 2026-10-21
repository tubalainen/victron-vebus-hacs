"""Critical device actions and the risks shown before they are carried out.

Every action that can interrupt the AC outputs, restart the device, change
battery charge voltages or write the EEPROM is only offered through the
guided "Device actions" dialog (options flow), which shows these warnings and
requires the user to confirm them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .coordinator import VeBusCoordinator
from .vebus import ForceChargeState, Mode, Setting, SettingFlag
from .vebus.const import MIN_PERSISTENT_WRITE_INTERVAL

OUTPUTS = "AC out 1 and AC out 2"

RESTART_WARNING = f"""**The Multi restarts.**

- Its relays open while it restarts: **{OUTPUTS} lose power** for several \
seconds, until the Multi has started and connected again: **every device \
connected to them is switched off and on again (power cycled).**
- Settings changed in RAM by this integration are lost; the configuration stored \
in the Multi is used again. The remote mode and current limit are reset.
- If the Multi cannot start (for example because the battery is too low), the AC \
outputs stay off until it is started at the device.
- The MK3 interface also restarts; Home Assistant reconnects by itself."""

MODE_WARNINGS: dict[Mode, str] = {
    Mode.OFF: f"""**Inverter and charger switch off.**

- **{OUTPUTS} lose power now** and stay off until the mode is changed back.
- The battery is not charged.
- A switched-off Multi can go to sleep. Keep the *Keep awake* switch on, or it can \
only be switched on again at the device.""",
    Mode.CHARGER_ONLY: f"""**Inverter off, charger on.**

- {OUTPUTS} are only powered while the grid is available.
- **On a grid failure they lose power** — there is no battery backup in this mode.""",
    Mode.INVERTER_ONLY: """**The Multi disconnects from the grid.**

- The AC input relay opens. AC out 1 is powered from the battery only (a short \
transfer).
- **AC out 2 loses power** — it is only powered while connected to the grid.
- The battery is not charged from the grid and discharges.""",
    Mode.ON: """**Charger and inverter on (normal operation).**

- If the Multi is off it starts; the AC outputs come back after its start-up and \
its relays click when it connects to the grid.""",
}

PASSTHRU_ENTER_WARNING = f"""**Stop grid charging: the remote AC input current limit \
is set to 0 A** (Victron calls this passthru).

- With PowerAssist **off**, the charger stops and the grid is passed straight \
through to {OUTPUTS}. On a grid failure the inverter takes over, if the mode \
allows it.
- Some firmware returns to charging after about 15 seconds (j9brown issue #9).
- Resume grid charging with the same action, or by setting the remote current \
limit. This is not needed for normal operation (mode *on*)."""

PASSTHRU_POWER_ASSIST_WARNING = """**PowerAssist is on.** With PowerAssist on, a \
0 A limit does not just pass the grid through: **the Multi supplies the loads from \
the battery** (it inverts) while the grid is connected. Turn PowerAssist off first \
if you only want to stop charging from the grid."""

PASSTHRU_LEAVE_WARNING = """**The remote AC input current limit is set to {limit} A.**

- The charger can run again and the Multi takes up to {limit} A from the grid."""

EQUALISE_WARNING = """**Equalisation charges the battery above the absorption \
voltage for a period.**

- It is meant for flooded lead-acid batteries only.
- **Never equalise lithium (LiFePO4), gel or AGM batteries** unless the \
manufacturer allows it — it can damage them.
- Loads on the DC side see the higher voltage."""

SETTING_WARNINGS: dict[Setting, str] = {
    Setting.U_BAT_ABSORPTION: """**Battery charge voltage.** A wrong value can \
overcharge or undercharge the battery and damage it. Check the limits of the \
battery manufacturer (and of a BMS).""",
    Setting.U_BAT_FLOAT: """**Battery float voltage.** A wrong value can \
overcharge the battery over time or keep it undercharged. Check the limits of the \
battery manufacturer.""",
    Setting.I_BAT_BULK: """**Battery charge current.** Too high a value can \
damage the battery or make its BMS or a DC fuse disconnect it.""",
    Setting.I_MAINS_LIMIT_AC1: """**AC input 1 current limit.** Too high a value \
can overload the grid connection and **trip its breaker**, which disconnects the \
Multi from the grid.""",
    Setting.I_MAINS_LIMIT_AC2: """**AC input 2 current limit.** Too high a value \
can overload that input (for example a generator) and trip its breaker.""",
    Setting.SOLAR_WIND_PRIORITY_VOLTAGE: """**Solar & wind priority voltage.** The \
battery is held at this voltage while solar or wind is preferred. Too low a value \
keeps the battery undercharged.""",
}

FLAG_WARNINGS: dict[tuple[SettingFlag, bool], str] = {
    (SettingFlag.ASSIST_ENABLED, True): """**PowerAssist on.** The Multi adds \
battery power when the loads exceed the AC input current limit.""",
    (SettingFlag.ASSIST_ENABLED, False): """**PowerAssist off.** The Multi no \
longer adds battery power when the loads exceed the AC input current limit, so \
**the grid breaker can trip** under high load.""",
    (SettingFlag.WEAK_AC_INPUT, True): """**Weak AC input on.** A distorted AC \
input (for example a generator) is accepted. The charge current can be lower.""",
    (SettingFlag.WEAK_AC_INPUT, False): """**Weak AC input off.** A poor AC input, \
such as some generators, can be rejected, and the Multi may keep connecting and \
disconnecting.""",
    # The UPS switch is the inverse of the "disable wave check" bit.
    (SettingFlag.DISABLE_WAVE_CHECK, False): """**UPS function on.** The Multi \
transfers to the inverter fast on a grid failure. A poor AC input (generator) can \
be rejected more often.""",
    (SettingFlag.DISABLE_WAVE_CHECK, True): f"""**UPS function off.** The Multi \
tolerates a poor AC input but transfers to the inverter more slowly: **{OUTPUTS} \
can see a short interruption on a grid failure**, enough to reset computers and \
other sensitive loads.""",
    (SettingFlag.SOLAR_WIND_PRIORITY, True): """**Solar & wind priority on.** The \
battery is held at the priority voltage so solar and wind can charge it.""",
    (SettingFlag.SOLAR_WIND_PRIORITY, False): """**Solar & wind priority off.** The \
Multi charges the battery to the full absorption voltage from the grid.""",
}

RAM_ONLY_NOTE = """The change applies to the Multi's RAM only. It is **lost when the \
Multi restarts**, which then uses its stored configuration again."""

PERSISTENT_NOTE = f"""**The value is stored permanently in the EEPROM.**

- It survives restarts and replaces the value set with VictronConnect or \
VEConfigure.
- The EEPROM endures a limited number of writes, so don't repeat this often. The \
integration allows one permanent write per setting every \
{MIN_PERSISTENT_WRITE_INTERVAL // 60} minutes."""

NO_RESTART_NOTE = f"""Victron documents no restart for this command and the \
integration does not restart the Multi. Some settings may only take full effect \
after the next restart. A restart power cycles every device on {OUTPUTS}, so \
choose a convenient moment for it."""


@dataclass(frozen=True)
class FlagChoice:
    """A flag the user can switch in the guided dialog."""

    setting: Setting
    flag: SettingFlag
    # The user-facing switch is on while the bit is clear.
    inverted: bool = False


FLAG_CHOICES: dict[str, FlagChoice] = {
    "power_assist": FlagChoice(Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED),
    "weak_ac_input": FlagChoice(Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT),
    "ups_function": FlagChoice(
        Setting.FLAGS_0, SettingFlag.DISABLE_WAVE_CHECK, inverted=True
    ),
    "solar_wind_priority": FlagChoice(Setting.FLAGS_2, SettingFlag.SOLAR_WIND_PRIORITY),
}

SETTING_CHOICES: dict[str, Setting] = {
    "absorption_voltage": Setting.U_BAT_ABSORPTION,
    "float_voltage": Setting.U_BAT_FLOAT,
    "charge_current_limit": Setting.I_BAT_BULK,
    "configured_ac_input_current_limit": Setting.I_MAINS_LIMIT_AC1,
    "configured_ac_input_2_current_limit": Setting.I_MAINS_LIMIT_AC2,
    "solar_wind_priority_voltage": Setting.SOLAR_WIND_PRIORITY_VOLTAGE,
}


# One-line risks, shown when a critical change is refused because "Allow
# critical changes" is off (entities), next to the full warnings above.
MODE_RISKS: dict[Mode, str] = {
    Mode.OFF: f"{OUTPUTS} lose power",
    Mode.CHARGER_ONLY: f"{OUTPUTS} lose power on a grid failure",
    Mode.INVERTER_ONLY: "the Multi disconnects from the grid and AC out 2 loses power",
    Mode.ON: "the Multi starts and its relays switch",
}
PASSTHRU_RISK = (
    "the charger stops; with PowerAssist on the Multi supplies the loads from the "
    "battery instead"
)
PASSTHRU_LEAVE_RISK = "the charger can run again and take power from the grid"
SETTING_RISK = "a wrong value can damage the battery or trip a breaker"
FLAG_RISKS: dict[tuple[SettingFlag, bool], str] = {
    (SettingFlag.ASSIST_ENABLED, True): "the Multi adds battery power to the grid",
    (SettingFlag.ASSIST_ENABLED, False): "the grid breaker can trip under high load",
    (SettingFlag.WEAK_AC_INPUT, True): "the charge current can be lower",
    (SettingFlag.WEAK_AC_INPUT, False): "a poor AC input, such as a generator, can "
    "be rejected",
    (SettingFlag.DISABLE_WAVE_CHECK, False): "a poor AC input can be rejected more "
    "often",
    (SettingFlag.DISABLE_WAVE_CHECK, True): f"{OUTPUTS} can see a short interruption "
    "on a grid failure",
    (SettingFlag.SOLAR_WIND_PRIORITY, True): "the battery is held below the "
    "absorption voltage",
    (SettingFlag.SOLAR_WIND_PRIORITY, False): "the battery is charged from the grid",
}
EQUALISE_RISK = (
    "charging above the absorption voltage can damage lithium, gel and AGM batteries"
)
RESTART_RISK = (
    f"the relays open and {OUTPUTS} lose power for several seconds; every connected "
    "device is power cycled"
)
SAVE_RISK = "the changed settings replace the stored configuration (EEPROM)"
BATTERY_LIMIT_RISK = (
    "the Multi follows these limits instead of its charger settings; a discharge "
    "current of 0 A, or limits that stop being sent, stop charging and inverting "
    "until the Multi is restarted"
)


@dataclass(frozen=True)
class PendingAction:
    """An action waiting for the user to confirm its risks."""

    title: str
    warning: str
    run: Callable[[], Awaitable[str]]


def setting_warning(setting: Setting, persistent: bool) -> str:
    """Return the warning for changing a setting."""
    parts = [SETTING_WARNINGS[setting]]
    parts.append(PERSISTENT_NOTE if persistent else RAM_ONLY_NOTE)
    parts.append(NO_RESTART_NOTE)
    return "\n\n".join(parts)


def flag_warning(choice: FlagChoice, enabled: bool, persistent: bool) -> str:
    """Return the warning for switching a flag (``enabled`` as the user sees it)."""
    bit = enabled != choice.inverted
    parts = [FLAG_WARNINGS[(choice.flag, bit)]]
    parts.append(PERSISTENT_NOTE if persistent else RAM_ONLY_NOTE)
    parts.append(NO_RESTART_NOTE)
    return "\n\n".join(parts)


def passthru_warning(coordinator: VeBusCoordinator) -> str:
    """Return the warning for entering passthru, depending on PowerAssist."""
    power_assist = coordinator.data.setting_flag(
        Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED
    )
    if power_assist:
        return f"{PASSTHRU_ENTER_WARNING}\n\n{PASSTHRU_POWER_ASSIST_WARNING}"
    return PASSTHRU_ENTER_WARNING


async def async_restart(coordinator: VeBusCoordinator) -> str:
    """Restart the Multi."""
    await coordinator.client.restart_device()
    # The remote panel state is reset with the device.
    coordinator.passthru = False
    return "The restart command was sent. The Multi reconnects by itself."


def panel_limit(coordinator: VeBusCoordinator) -> float | None:
    """Return the limit to send with a new mode, keeping the one in place.

    That is 0 A in passthru, else the remote panel limit if known, else the
    limit in effect (which keeps it: the configured limit is at least that).
    """
    if coordinator.passthru:
        return 0.0
    config = coordinator.data.config
    if config is None or config.dmc_dedicated:
        return None
    remote = coordinator.data.remote_current_limit
    return remote if remote is not None else config.actual_current_limit


def check_panel_limit(
    coordinator: VeBusCoordinator, value: float, *, allow_zero: bool
) -> str | None:
    """Return an error if a remote panel limit is outside the device's range.

    The range is the one the Multi reports (MasterMultiLED). 0 A, which stops
    grid charging, is only allowed where the caller already asked for it.
    """
    if allow_zero and value == 0:
        return None
    config = coordinator.data.config
    if config is None:
        return "the current limit range of the device is unknown"
    low, high = config.minimum_current_limit, config.maximum_current_limit
    if not low <= value <= high:
        return f"{value:g} A is outside {low:g}..{high:g} A"
    return None


def raise_invalid(error: str | None) -> None:
    """Raise a validation error for the message, if there is one."""
    if error is not None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_value",
            translation_placeholders={"error": error},
        )


async def async_set_mode(coordinator: VeBusCoordinator, mode: Mode) -> str:
    """Change the remote panel mode, keeping the current limit (or passthru)."""
    await coordinator.client.set_remote_panel_state(mode, panel_limit(coordinator))
    await coordinator.async_refresh_config()
    return f"The mode was set to {mode.value}."


def panel_mode(coordinator: VeBusCoordinator) -> Mode:
    """Return the mode this remote panel requests, to keep it unchanged.

    That is the remote mode, not the mode in effect: with the front switch
    on "charger only" the mode in effect would lock the remote to it too.
    """
    config = coordinator.data.config
    mode = config.remote_mode if config is not None else None
    if mode is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="remote_mode_unknown"
        )
    return mode


def resume_limit(coordinator: VeBusCoordinator) -> float | None:
    """Return the limit to leave passthru with, None if none is known.

    The last remote panel limit above 0 A, else the last limit in effect
    above 0 A, else the configured limit of the active AC input. There is no
    fixed default: it could be above the breaker of the input.
    """
    for limit in (coordinator.last_remote_limit, coordinator.last_current_limit):
        if limit:
            return limit
    config = coordinator.data.config
    setting = (
        Setting.I_MAINS_LIMIT_AC2
        if config is not None and config.last_active_ac_input == 1
        else Setting.I_MAINS_LIMIT_AC1
    )
    return coordinator.data.setting_value(setting) or None


async def async_set_passthru(
    coordinator: VeBusCoordinator, enter: bool, limit: float | None
) -> str:
    """Stop grid charging (passthru, limit 0) or resume it (restore a limit)."""
    mode = panel_mode(coordinator)
    current = 0.0
    if not enter:
        if limit is None:
            limit = resume_limit(coordinator)
        if limit is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="limit_unknown"
            )
        raise_invalid(check_panel_limit(coordinator, limit, allow_zero=False))
        current = limit
    await coordinator.client.set_remote_panel_state(mode, current)
    coordinator.passthru = enter
    await coordinator.async_refresh_config()
    if enter:
        return "Charging from the grid was stopped (AC input current limit 0 A)."
    return f"Charging from the grid was resumed with an AC input limit of {limit} A."


async def async_write_setting(
    coordinator: VeBusCoordinator, setting: Setting, value: float, persistent: bool
) -> str:
    """Write a setting and publish the new state."""
    result = await coordinator.client.write_setting(
        setting, value, persistent=persistent
    )
    coordinator.async_set_updated_data(coordinator.client.state)
    where = "permanently" if persistent else "in RAM"
    return f"The setting was changed {where} and reads back as {result:g}."


async def async_write_flag(
    coordinator: VeBusCoordinator, choice: FlagChoice, enabled: bool, persistent: bool
) -> str:
    """Switch a flag and publish the new state."""
    await coordinator.client.set_setting_flag(
        choice.setting, choice.flag, enabled != choice.inverted, persistent=persistent
    )
    coordinator.async_set_updated_data(coordinator.client.state)
    where = "permanently" if persistent else "in RAM"
    return f"The setting was switched {'on' if enabled else 'off'} {where}."


def setting_name(setting: int) -> str:
    """Return a readable name of a setting ID."""
    for key, choice in SETTING_CHOICES.items():
        if choice == setting:
            return key.replace("_", " ")
    for key, flag in FLAG_CHOICES.items():
        if flag.setting == setting:
            return f"flags ({key.replace('_', ' ')}, ...)"
    try:
        return Setting(setting).name.lower().replace("_", " ")
    except ValueError:
        return f"setting {setting}"


async def async_save_settings(coordinator: VeBusCoordinator) -> str:
    """Store the settings changed in RAM permanently."""
    saved = await coordinator.client.save_settings()
    coordinator.async_set_updated_data(coordinator.client.state)
    if not saved:
        return "There were no changed settings to save."
    names = ", ".join(setting_name(setting) for setting in saved)
    return f"Saved permanently: {names}."


async def async_equalise(coordinator: VeBusCoordinator) -> str:
    """Force an equalisation charge."""
    await coordinator.client.force_charge_state(ForceChargeState.EQUALISE)
    coordinator.async_set_updated_data(coordinator.client.state)
    return "Equalisation was started."


def check_setting(
    coordinator: VeBusCoordinator, setting: Setting, value: float
) -> str | None:
    """Return an error if the value is outside the device range or inconsistent."""
    info = coordinator.client.setting_info.get(setting)
    if info is None or info.minimum is None or info.maximum is None:
        return "the range of this setting is unknown"
    low, high = info.decode(info.minimum), info.decode(info.maximum)
    if not low <= value <= high:
        return f"{value:g} is outside {low:g}..{high:g}"
    state = coordinator.data
    absorption = state.setting_value(Setting.U_BAT_ABSORPTION)
    float_voltage = state.setting_value(Setting.U_BAT_FLOAT)
    # The charge voltages are checked against each other. When the other one
    # is unknown the change is refused rather than written unchecked (1.1.12).
    if setting in (Setting.U_BAT_FLOAT, Setting.SOLAR_WIND_PRIORITY_VOLTAGE):
        if absorption is None:
            return "the absorption voltage is unknown, so the value cannot be checked"
        if value > absorption:
            what = "float voltage" if setting == Setting.U_BAT_FLOAT else "value"
            return f"{what} above the absorption voltage ({absorption:g} V)"
    if setting == Setting.U_BAT_ABSORPTION:
        if float_voltage is None:
            return "the float voltage is unknown, so the value cannot be checked"
        if value < float_voltage:
            return f"absorption voltage below the float voltage ({float_voltage:g} V)"
    return None


def check_battery_limit_voltage(
    coordinator: VeBusCoordinator, value: float
) -> str | None:
    """Return why a battery limit voltage is refused, None if it is fine.

    It must lie within the range the device reports for the absorption
    voltage, so a typo can't ask for a voltage the battery isn't built for.
    """
    info = coordinator.client.setting_info.get(Setting.U_BAT_ABSORPTION)
    if info is None or info.minimum is None or info.maximum is None:
        return "the range of the absorption voltage isn't known yet"
    low, high = info.decode(info.minimum), info.decode(info.maximum)
    if not low <= value <= high:
        return f"{value:g} V is outside the absorption voltage range {low:g}-{high:g} V"
    return None
