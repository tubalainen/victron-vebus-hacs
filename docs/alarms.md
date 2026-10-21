# Alarm notifications

[← Back to the README](../README.md) · [Documentation index](README.md)

The Multi signals three alarms with its LEDs: **overload**, **low battery** and
**temperature**. A blinking LED is a *warning* (pre-alarm); a lit LED is an *alarm*,
and the Multi usually switches off the inverter, so the AC outputs can lose power.

Since 1.1.14 two more alarms come from the rest of the MK3's `L` reply, where the MK3
sends it:

- **Ripple** (`ripple`): high DC ripple, usually battery cables that are too thin or
  loose, or a battery that is too small for the load. It has a warning and an alarm,
  like the LED alarms.
- **VE.Bus error** (`vebus_error`): the VE.Bus error code is not 0. There is no
  warning. The event carries the code as `error_code`, and the repair issue names it.
  A different code while one is active is reported again. Look the code up in
  Victron's list of VE.Bus error codes.

Victron doesn't document these bytes. The integration reads them as VictronConnect
does.

- The **Alarm** event entity fires `<alarm>_warning`, `<alarm>_alarm` and
  `<alarm>_cleared` (for example `low_battery_warning`), with the attributes `alarm`
  and `level`. An alarm that is already active when Home Assistant starts is reported
  too. A VE.Bus error fires only `vebus_error_alarm` and `vebus_error_cleared`.
- When the MK3 can't read the LEDs (it reports 0x1F/0x1F), the alarm sensors are
  *unknown* and nothing is reported or cleared until the LEDs are known again.
- While an alarm is active, a **repair issue** explains what it means and what to
  check. It disappears when the alarm clears. Turn off *Show alarms as repair issues*
  in the integration settings if you don't want them.

Example: a notification on your phone for every alarm and warning.

```yaml
triggers:
  - trigger: state
    entity_id: event.victron_ve_bus_alarm
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Victron alarm
      message: >
        {{ state_attr('event.victron_ve_bus_alarm', 'alarm') }}:
        {{ state_attr('event.victron_ve_bus_alarm', 'level') }}
```
