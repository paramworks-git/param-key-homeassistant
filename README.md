# PARAM KEY for Home Assistant (param_key)

Home Assistant side of the PARAM KEY remote: turns key presses from the remote's bridge into
actions and draws the remote's screen. The firmware for the remote and the bridge is in
the param-key repository (`firmware/`).

Open firmware, open HA integration, documented hardware: MIT licensed (see [LICENSE](LICENSE)).

## Status

| Part | State |
|---|---|
| `packages/param_key.yaml` | Working HA package (the key map, modes, screen renderer, battery sensors). The device entities in it are examples: change them to yours |
| `custom_components/param_key/` | Pairing (each remote a device; only paired remotes are heard), and each remote's IR LED as an HA infrared emitter. Next: the key map, modes and screen renderer move here from the package |

Needs Home Assistant 2026.10 or later, and bridge and remote firmware with protocol v2
(param-key).

## Install (integration)

1. Copy `custom_components/param_key/` into your HA config's `custom_components/`
   folder (or add this repository to HACS) and restart HA.
2. Settings → Devices & services → **Add integration** → PARAM KEY, with a remote
   on its "Pair with Home Assistant" screen. A new remote shows it on its own; hold
   **home** and **back** for 5 seconds to pair one again. Once the integration is
   set up, further remotes appear there as discovered.
3. The remote shows a 6-digit code. Confirm it only if HA shows the same one.

Each remote becomes a device with an **IR** entity (HA's infrared platform). It is
available while the remote is awake: the remote can only send IR then, so IR suits
key presses, not automations that run while it sleeps. HA's IR integrations (LG,
Samsung, Marantz and others) can use it as their emitter.

Removing a remote in HA takes its key off the bridges, so it can do nothing more
until it is paired again.

## Install (package)

1. Copy `packages/param_key.yaml` into your HA config's
   `packages/` folder. Packages have to be enabled in `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Restart HA, or for later changes run Developer tools → YAML → **All YAML
   configuration**.
3. The bridge's actions are named after its device name: `param-key-bridge` gives
   `esphome.param_key_bridge_show_status` and so on. If you rename the bridge, rename the
   actions in the package to match.

Reset the remote. Within a second, "Waiting for bridge" should change to HA's Media
screen.

## Configuring keys and modes

Everything is in `packages/param_key.yaml`, so changes need no
reflash.

- **`keymap`** maps each key in each media context (`idle`, `shield`, `tv`, `pc`,
  `music`) to an action. Entries can be an Android TV command (`atv:`), an app
  launch (`app:`), an LG button (`lg:`), a Denon source or sound mode, an IR code
  from the remote's own IR LED (`ir:`, needs the integration), a script, or
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
  `input_number.param_key_brightness`, `param_key_screen_off` and `param_key_sleep_after`, so an
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

- **IR codes:** `ir: {protocol: nec, address: 0x04, command: 0x08}` sends a code
  from the remote that was pressed. Protocols: `nec`, `samsung`, `sony` (with
  `address_bits` 5, 8 or 13), `rc5`, `rc6`, or `{protocol: pronto, pronto: "0000
  006D ..."}` for a code from a public Pronto database, or raw `timings`. `repeat:`
  sends it more times. From an automation, call `param_key.send_ir` with the
  remote's IR entity.

To test a mapping without the remote, run `script.param_key_dispatch` from Developer tools
with `remote: test`, `key: vol_up`, `type: press`.

## Battery logging

The remote sends a hello on every wake and with every battery reading: about 2 s
after waking, then every 60 s while awake. The package turns it into:

| Entity | What it records |
|---|---|
| `sensor.remote_battery` | Percent from a LiPo discharge curve (`firmware/common/pk_battery.h` in param-key), smoothed, and never rising while on battery. Keeps its last value through a reading that comes back unknown |
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
./run_tests.sh      # package and integration tests in the Home Assistant container
```

`tests/vectors.json` is a copy of param-key's `docs/test-vectors.json`: the
firmware and the integration are both tested against it, so they agree on
pairing. Copy it again when the protocol changes.

If the Docker daemon runs on another host, set `HOST_DIR` to this directory as that host sees it.
Install the secret scan before committing: `pre-commit install` (gitleaks). Keep your own
entity IDs and anything house-specific in a local copy outside this repository.
