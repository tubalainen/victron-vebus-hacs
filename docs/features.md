# Features

[← Back to the README](../README.md) · [Documentation index](README.md)

- **Bundled protocol driver.**
  - Pure Python, no extra PyPI package. Installation does not need internet access and
    does not break on Home Assistant upgrades.
  - Survives unplugging, sleeping devices and line noise. It reconnects automatically
    with backoff and resynchronises the byte stream after corrupted frames.
  - All requests are serialised, so control commands never get crossed with polling
    replies.
- **Setup.**
  - The MK3 is auto-discovered over USB, or you can pick a serial port.
  - Remote connections work too, e.g. ser2net via `socket://host:port`.
  - The device is identified by the MK3 serial number, so its entities survive
    `/dev/ttyUSBx` renumbering.
- **Measurements.**
  - AC input and output voltage, current and frequency, per detected phase.
  - AC input and output power.
  - Battery voltage, current and power; charger and inverter DC current; battery ripple.
  - State of charge, when the built-in battery monitor is configured.
- **Energy** for the Home Assistant Energy dashboard: energy taken from and fed into
  the grid, energy delivered to the loads, and battery energy charged and discharged
  (kWh, integrated from the power readings).
- **States.**
  - Device state (inverting, passthru, charging, ...) and charge state (bulk,
    absorption, float, ...).
  - Effective mode and front switch position.
  - Overload, low battery and temperature alarms (ok / warning / alarm).
  - **Alarm notifications:** an *Alarm* event entity for automations, and a repair
    issue while an alarm is active.
  - AC input connected, the multi-functional relay, the virtual switch and the "ignore
    AC input" state.
- **Diagnostics.**
  - Firmware version, input current limit and its range.
  - Charger settings: absorption and float voltage, charge current, battery capacity,
    charge characteristic and maximum absorption time.
  - Inverter output voltage, low battery shutdown and restart voltage.
  - The eight front panel LEDs, each off, on or blinking.
  - A diagnostics download with communication statistics and the last raw frames.
  - **Connection health:** link quality (share of answered requests), request
    timeouts and checksum errors, plus a repair issue while the connection is poor.
  - A single missed or garbled reply no longer makes values flicker to "unknown":
    the last good reading is kept through up to two missed replies.
- **Multi-unit systems** (parallel, split-phase and three-phase).
  - All units on the VE.Bus are found at startup. Each gets its own device, linked to
    the system device, with AC and battery values, power and device state.
  - The system's AC input, AC output and battery power are the totals over all units.
  - Energy sensors for every unit, besides the system totals.
  - Alarms are system-wide: the MK3 protocol reports the alarm LEDs for the whole
    VE.Bus system, not per unit.
  - Each phase gets its apparent power.
- **Quattro:** the second AC input's current limit and which input is active.
- **Phoenix/Compact inverters** that don't send the standard info frames: values are
  read from RAM variables instead.
- **Keep awake switch.** It stops a switched-off device from sleeping, which would cut
  the MK3's power.
- **Monitor only, or monitor and control.** You choose this when you add the
  integration and can change it with *Reconfigure*. *Monitor only* never changes how
  the Multi operates.
- **Controls** (only with *Monitor and control*), all usable from the device page,
  dashboards, automations and scripts. See [Controlling the Multi](controls.md).
  - **Routine controls:** the remote AC input current limit, charge current limit,
    configured AC input limit(s), force absorption or float, solar & wind priority, and
    ESS external control (grid setpoint per phase, disable charge / feed-in).
  - **Critical controls**, behind the *Allow critical changes* lock: mode, passthru,
    absorption and float voltage, PowerAssist, weak AC input, UPS function,
    equalisation, restart and *Save settings permanently*.
  - **Actions for automations:** `set_mode`, `set_setting`, `set_feature`, `equalise`,
    `restart`, `save_settings` and `set_remote_panel_state`, each requiring
    `acknowledge_risk: true`.
  - **Guided device actions** under *Configure → Device actions*: the same changes
    with a full explanation of their risks and a confirmation.
- **Battery operational limits** (advanced, lithium only, separate option): the Multi
  receives charge/discharge voltage and current limits, like it would from a BMS.
