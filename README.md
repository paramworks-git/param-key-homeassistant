# PARAM KEY for Home Assistant (param_key)

Home Assistant side of the PARAM KEY remote: turns key presses from the remote's bridge into
actions and draws the remote's screen. The firmware for the remote and the bridge is in
[param-key-firmware](../param-key-firmware).

Open firmware, open HA integration, documented hardware: MIT licensed (see [LICENSE](LICENSE)).

## Status

| Part | State |
|---|---|
| `packages/openhomeremote.yaml` | Working HA package (the key map, modes, screen renderer, battery sensors). The device entities in it are examples: change them to yours |
| `custom_components/param_key/` | Integration skeleton. Next: each remote as a device with its entities, the dispatcher in Python, and a configurator panel (remotes, live key monitor, pairing, keymap editor, setup wizard) |

## Install (package)

1. Copy `packages/openhomeremote.yaml` into your HA config's
   `packages/` folder. Packages have to be enabled in `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Restart HA, or for later changes run Developer tools → YAML → **All YAML
   configuration**.
3. The bridge's actions are named after its device name: `ohr-bridge` gives
   `esphome.ohr_bridge_show_status` and so on. If you rename the bridge, rename the
   actions in the package to match.

Reset the remote. Within a second, "Waiting for bridge" should change to HA's Media
screen.

## Configuring keys and modes

Everything is in `packages/openhomeremote.yaml`, so changes need no
reflash.

- **`keymap`** maps each key in each media context (`idle`, `shield`, `tv`, `pc`,
  `music`) to an action. Entries can be an Android TV command (`atv:`), an app
  launch (`app:`), an LG button (`lg:`), a Denon source or sound mode, a script, or
  any HA action with a target. `common` applies wherever a context doesn't override a
  key, and play, rew, fwd and stop fall back to the current media context, so
  they control what's playing from Devices or Settings mode too. The header comment in the file documents every entry type.
- **Key events:** arrows and volume act on `press` and repeat while held. home,
  settings, power and the hotkeys act on `tap`, which leaves `hold` free. Use
  `events:` on an entry to choose. Home held opens the mode picker, and a hotkey
  held shows what it does without running it.
- **Long press:** `hold:` on an entry gives the key a second action when held, and
  the entry itself then acts on `tap`. Shield OK uses it for a 1 s long press
  (`hold_secs`), which opens the Shield's context menu.
- **Settings mode** (in the mode picker): ▲▼ pick Brightness, Screen off after or
  Sleep after, ◀ ▶ change it. The bridge passes it to the remote at once and
  again on every wake; the remote keeps it in flash. Backed by
  `input_number.ohr_brightness`, `ohr_screen_off` and `ohr_sleep_after`, so an
  automation can change them too (e.g. dimmer at night).
- **Track time:** on the music screen the artist line gets "2:14 / 4:51" from
  the Roon zone, ticking every second while the remote is awake.
- **Roon:** the Music hotkey and source, or play when idle, wake the AVR and
  resume the queue on its Roon zone (`devices.roon`). The music keys act on the
  Roon zone the remote controls, or on HEOS when only HEOS plays. A Roon zone
  that's off is woken before playing.
- **Source menu:** settings held, in any media context, opens a list of the AVR
  sources (the current one marked •), a Sound row and, with Roon, an Output row.
  ◀ ▶ on Output pick one of the Roon integration's zones, and OK moves the queue
  there (if it's playing) and makes it the zone the remote controls. When idle or on music,
  where settings has no other job, a tap also opens it. OK picks a source and
  closes the menu, ◀ ▶ on Sound cycle the sound modes, and back or settings
  closes it. Volume keeps working, and the menu closes itself after a minute.
  Sources and sound modes are the `media_menu` data next to `keymap`.
- **`sensor.remote_media_context`** decides which context is active, based on the
  AVR's source and the TV's state. Adapt its template to your devices.
- **Devices mode:** give the label `remote` to lights, fans, covers, humidifiers
  and thermostats to list them, and to up to 4 scenes or scripts to make them the
  hotkeys. For lights, fans and covers, OK toggles and ◀ ▶ step brightness, speed
  or position by 10 %. A humidifier or thermostat opens a detail screen on OK or
  ▶: ▲▼ pick a setting (power, target humidity and mode; or HVAC mode, setpoints,
  fan, preset, and target humidity if the thermostat has one), ◀ ▶ change it, OK toggles power or steps to the next option,
  and back returns to the list.

To test a mapping without the remote, run `script.ohr_dispatch` from Developer tools
with `remote: test`, `key: vol_up`, `type: press`.

## Battery logging

The remote sends a hello on every wake and with every battery reading: about 2 s
after waking, then every 60 s while awake. The package turns it into:

| Entity | What it records |
|---|---|
| `sensor.remote_battery` | Percent from a LiPo discharge curve (`common/ohr_battery.h` in param-key-firmware), smoothed, and never rising while on battery. Keeps its last value through a reading that comes back unknown |
| `binary_sensor.remote_charging` | On while USB is charging the battery (TP4054 status pin) |
| `sensor.remote_signal` | RSSI of the hello at the bridge, in dBm |
| `sensor.remote_last_wake` | Why the remote last woke (`ext1` = key or accelerometer, `reset`), with the EXT1 wake mask and accelerometer diagnostics as attributes |

The battery and signal sensors have `state_class: measurement`, so HA keeps hourly
long-term statistics for them indefinitely, even after the recorder purges the raw
history (10 days by default). To see the discharge curve over weeks, add a
**Statistics graph** card:

```yaml
type: statistics-graph
title: Remote battery
entities:
  - sensor.remote_battery
chart_type: line
period: hour
days_to_show: 60
stat_types: [mean, min]
```

Notes on reading it:

- The battery is only measured while the remote is awake. A remote left untouched
  for a day reports nothing that day, and the next reading shows the drop.
- While charging, the cell voltage reads high, so the percentage jumps up and
  reaches 100 % before the battery is actually full. Use
  `binary_sensor.remote_charging` to mark charge cycles. After unplugging, the
  percentage starts from the (still slightly high) reading and only goes down.
- The percentage comes from voltage alone: each reading averages 16 samples, is
  mapped through a typical LiPo curve and smoothed across readings (also across
  deep sleep). Expect it within about ±5 %, not a phone's accuracy; that needs a
  fuel gauge chip such as the MAX17048 (a candidate for the next PCB revision).

## Development

```sh
./run_tests.sh      # package tests in the Home Assistant container
```

If the Docker daemon runs on another host, set `HOST_DIR` to this directory as that host sees it.
Install the secret scan before committing: `pre-commit install` (gitleaks). Keep your own
entity IDs and anything house-specific in a local copy outside this repository.
