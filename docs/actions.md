# Actions for automations and scripts

[← Back to the README](../README.md) · [Documentation index](README.md)

Automations don't use the [*Allow critical changes* lock](controls.md). Every action needs the entry (`config_entry_id`, picked
in the UI editor) and `acknowledge_risk: true`, and only works with *Monitor and
control*.

| Action | Fields |
|---|---|
| `victron_vebus.set_mode` | `mode`: `on`, `off`, `charger_only`, `inverter_only` |
| `victron_vebus.set_setting` | `setting`: `absorption_voltage`, `float_voltage`, `charge_current_limit`, `configured_ac_input_current_limit`, `configured_ac_input_2_current_limit`, `solar_wind_priority_voltage`; `value`; `persistent` (default false) |
| `victron_vebus.set_feature` | `feature`: `power_assist`, `weak_ac_input`, `ups_function`, `solar_wind_priority`; `enabled`; `persistent` (default false) |
| `victron_vebus.equalise` | |
| `victron_vebus.restart` | |
| `victron_vebus.save_settings` | Stores the settings changed in RAM. |
| `victron_vebus.set_remote_panel_state` | `mode`, optional `current_limit` (0 = stop grid charging, passthru) |

```yaml
# Charger only at night, normal operation in the morning.
action: victron_vebus.set_mode
data:
  config_entry_id: 01J...
  mode: charger_only
  acknowledge_risk: true
```

Don't automate `persistent: true` or `save_settings`: the EEPROM endures a limited
number of writes.

## The remote panel action

`victron_vebus.set_remote_panel_state` sets the mode and optionally the AC input current
limit, like a Digital Multi Control panel. Because modes other than *on* and a 0 A limit
can cut the power on the AC outputs, the action fails unless `acknowledge_risk: true`,
and it only works on an entry set up for *Monitor and control*.
Changing the mode keeps the current limit when none is given.

```yaml
action: victron_vebus.set_remote_panel_state
data:
  config_entry_id: 01J...   # pick the Victron VE.Bus entry in the UI editor
  mode: "on"
  current_limit: 10
  acknowledge_risk: true
```

These commands only change RAM in the device, not its EEPROM. They are lost when the
device restarts.
