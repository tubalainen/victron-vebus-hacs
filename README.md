# Victron VE.Bus (MK3) for Home Assistant

> [!CAUTION]
> **Disclaimer — use at your own risk.** This is an unofficial community project. It is
> not made, endorsed or supported by Victron Energy. It is provided "as is", without any
> warranty of any kind (see the [MIT license](LICENSE)). **The authors and contributors
> take no responsibility whatsoever for any damage** to inverters, chargers, batteries,
> other equipment or property, for injury, or for any other loss caused by using this
> integration, its command line tool or this documentation. Controlling an
> inverter/charger can cut the power to your loads and can damage batteries. If you are
> not sure what a setting does, **do not change it**.

A Home Assistant custom integration for Victron Energy VE.Bus inverter/chargers
(MultiPlus, MultiPlus-II, Quattro, ...). It talks to the device directly through a
**Victron MK3-USB interface**, so no GX device (Cerbo, Venus) is needed.

It is a ground-up rewrite inspired by
[j9brown/victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass). It fixes that
project's known bugs and adds new features. See [docs/ANALYSIS.md](docs/ANALYSIS.md) for
the analysis and the roadmap.

> **Status:** v1.1. Monitoring and mode changes are in daily use on a MultiPlus-II with
> an MK3-USB. Some protocol details (the ESS setpoint, battery operational limits,
> restart, permanent writes, multi-unit systems) have only been tested against a
> protocol simulator so far; they are listed in [docs/ANALYSIS.md](docs/ANALYSIS.md) §5
> and those features are marked *beta* in the docs. Try new controls with the
> [command line tool](docs/cli.md) first, and please report your results.

## Highlights

- **Monitoring** of AC input and output (per phase), battery, charger and inverter,
  state of charge, device and charge state, mode, alarms, the front panel LEDs and the
  charger settings. See [Entities](docs/entities.md).
- **Energy dashboard** sensors for grid import and export, the loads and the battery,
  for the system and every unit. See [Energy](docs/energy.md).
- **Alarm notifications**: an event entity and repair issues for overload, low battery
  and temperature alarms. See [Alarm notifications](docs/alarms.md).
- **Monitor only, or monitor and control**, chosen when you add the integration.
- **Every control in Home Assistant**: routine controls on the device page, and
  critical ones (mode, stop grid charging, charge voltages, PowerAssist, UPS, restart, ...)
  behind an *Allow critical changes* lock. See [Controlling the Multi](docs/controls.md)
  and [Actions](docs/actions.md) for automations.
- **Multi-unit systems** (parallel, split-phase, three-phase), **Quattro** and
  **Compact** devices, **ESS** external control and BMS-style **battery operational
  limits**. See [ESS and battery operational limits](docs/ess-and-bms.md).
- **Robust connection**: a bundled pure-Python driver that reconnects by itself, keeps
  the last good values through missed replies and reports its link quality. See
  [Connection health](docs/connection.md).

The complete list is in [Features](docs/features.md).

## Requirements

- Home Assistant 2026.8 or newer.
- A Victron **MK3-USB** interface. The older MK2-USB also works, but without the keep
  awake function.
- Exactly one program may use the MK3 at a time. Close VictronConnect and VEConfigure
  while Home Assistant is running.

## Quick start

1. In HACS, open *Integrations* → ⋮ → *Custom repositories*. Add
   `https://github.com/tubalainen/victron-vebus-hacs` with category *Integration*.
2. Install **Victron VE.Bus (MK3)** and restart Home Assistant.
3. Plug in the MK3. Home Assistant should discover it. Otherwise go to *Settings →
   Devices & services → Add integration → Victron VE.Bus (MK3)*, then choose the serial
   port or enter a path or URL.
4. Choose **Monitor only** (recommended to start with) or **Monitor and control**.
   *Monitor and control* shows a warning you must confirm: **do not change any setting
   if you are not sure what it does.** You can switch between the two later with
   *Settings → Devices & services → Victron VE.Bus → ⋮ → Reconfigure*.

More in [Installation and setup](docs/installation.md), including the options.

## Documentation

| Page | What it covers |
|---|---|
| [Features](docs/features.md) | The complete feature list |
| [Installation and setup](docs/installation.md) | Requirements, installing with HACS, *Monitor only* vs. *Monitor and control*, options |
| [Entities](docs/entities.md) | Every sensor, switch, number and button, and multi-unit, Quattro and Compact devices |
| [Energy](docs/energy.md) | The kWh sensors and how to use them in the Energy dashboard |
| [Alarm notifications](docs/alarms.md) | The Alarm event entity, repair issues and a notification example |
| [Connection health and keep awake](docs/connection.md) | Link quality, timeouts and the *Keep awake* switch |
| [Controlling the Multi](docs/controls.md) | The *Allow critical changes* lock, saving permanently, stopping grid charging, the modes compared, the risks, the guided dialog and how settings are written |
| [Actions](docs/actions.md) | `set_mode`, `set_setting`, `set_feature`, `equalise`, `restart`, `save_settings` and `set_remote_panel_state` for automations |
| [ESS and battery operational limits](docs/ess-and-bms.md) | ESS external control and BMS-style battery limits (advanced) |
| [Command line tool](docs/cli.md) | Testing on the hardware without Home Assistant |
| [Development](docs/development.md) | Tests, code layout and releasing |
| [Analysis and roadmap](docs/ANALYSIS.md) | Protocol notes, the version history and what still needs verifying on hardware |
| [Release notes](docs/releases/) | Notes of the stable releases |

## Credits

- [j9brown/victron-mk3](https://github.com/j9brown/victron-mk3) and
  [victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass), for the original
  integration and for discovering the MK3 `H` (standby) command and the MultiPlus-II
  quirks.
- [xcellsior/ve-bus-programming](https://github.com/xcellsior/ve-bus-programming), for
  the VE.Bus settings research.
- Victron Energy, *Interfacing with VE.Bus products – MK2 Protocol 3.14*.

This project is not affiliated with Victron Energy.
