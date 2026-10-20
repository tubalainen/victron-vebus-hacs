# Alarm notifications

[← Back to the README](../README.md) · [Documentation index](README.md)

The Multi signals three alarms with its LEDs: **overload**, **low battery** and
**temperature**. A blinking LED is a *warning* (pre-alarm); a lit LED is an *alarm*,
and the Multi usually switches off the inverter, so the AC outputs can lose power.

- The **Alarm** event entity fires `<alarm>_warning`, `<alarm>_alarm` and
  `<alarm>_cleared` (for example `low_battery_warning`), with the attributes `alarm`
  and `level`. An alarm that is already active when Home Assistant starts is reported
  too.
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
