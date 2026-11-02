# PARAM KEY for Home Assistant

The Home Assistant integration for the [PARAM KEY](https://github.com/paramworks-git/param-key)
remote: it sets up bridges, pairs remotes, puts the devices you choose on the remote's screen, and
gives each remote's IR blaster to Home Assistant.
The remote's firmware, the bridge and the installer are in
[param-key](https://github.com/paramworks-git/param-key). MIT licensed (see [LICENSE](LICENSE)).

Needs Home Assistant 2026.10 or later.

Questions and feedback: the [forum thread](https://community.home-assistant.io/t/param-key-an-esphome-physical-remote-for-home-assistant-with-real-buttons-screen-and-an-ir-for-devices-ha-cannot-control/1031837) or this repository's Issues.

## Install

1. In HACS, open the menu (⋮) → **Custom repositories**, add
   `https://github.com/paramworks-git/param-key-homeassistant` as type **Integration**, then
   download **PARAM KEY** and restart Home Assistant.
2. Install a bridge from the [installer](https://paramworks-git.github.io/param-key/) (Chrome or
   Edge; any ESP32 board).
3. In Settings → Devices & services, under **Discovered**:
   - add the ESPHome device `param-key-bridge-…` (Home Assistant sets its encryption key);
   - then add **PARAM KEY** for that bridge.

   The bridge has to be on a network Home Assistant can connect to (Home Assistant opens the
   connection), with internet access for firmware updates.
4. Switch the remote on. It appears under Discovered as a PARAM KEY remote: add it, and confirm
   only if the 6-digit code matches the one on the remote's screen.
5. Give the label **`remote`** to the lights, switches, fans, covers, thermostats, media players,
   scenes and scripts you want on the remote. They show up on its screen at once.

A remote that is already paired can be paired again by holding **home** and **back** for 5
seconds. Removing a remote in Home Assistant takes its key off the bridges, so it can do nothing
more until it is paired again.

## IR

Each remote is a device with an **IR** entity (Home Assistant's infrared platform). Home
Assistant's IR integrations (LG, Samsung, Marantz and others) can use it as their emitter, and the
`param_key.send_ir` action sends NEC, Samsung, Sony, RC5, RC6, Pronto or raw codes. The remote
sends IR only while it's awake (after a key press, or being picked up), so IR suits key presses
rather than automations that run while it sleeps.

## Using the remote

The remote's screen lists what you labelled `remote`, by Area:

- **Top level:** your Areas, then your own menus (below), then labelled entities that have no Area.
  An Area with more than 6 entities is split into Lights, Switches, Fans, Covers, Climate, Media
  and Scenes. A level with only one choice is skipped, so a small setup is one flat list.
- **Your own menus:** a label such as `remote/Movie night` makes a menu called "Movie night";
  `remote/Movie night/Lights` nests one inside it. They're listed by name, whatever the Area.
- **Hotkeys:** the scenes, scripts and buttons in a menu become its four hotkeys (the bar under the
  screen). Hold a hotkey to see what it runs.

| Key | Does |
|---|---|
| ▲ ▼ | Move |
| OK | Toggle, run a scene or script, play/pause a media player, or open a menu |
| ◀ ▶ | Dimmer/brighter, fan slower/faster, cover position, volume |
| back | Up one level |
| home | Back to the top (held: switch between Devices and Media) |
| settings | Brightness, screen-off and sleep times (also on the remote's device page in HA) |
| power | Turns off the lights in the current menu (at the top: all labelled lights) |

Thermostats and humidifiers open a detail screen on OK: ▲ ▼ pick mode, target, fan or preset,
◀ ▶ change it. Locks, alarms and sensors are shown but not changed from the remote.

Each remote also has **Battery** and **Charging** sensors, and every key press is an
`esphome.param_key_key` event (`remote`, `key`, `type`: `press`, `tap`, `hold`, `repeat`), so
automations can give keys the menus don't use (volume, media keys) a job.

Each remote has a screen of its own with bridge firmware 0.3.0 or later; with an older bridge, the
remotes on it share one.

## Media mode

On the remote's page in Home Assistant (Settings → Devices & services → PARAM KEY → the remote →
**Configure**), choose what it controls:

- **Volume:** the AVR or TV for the volume and mute keys (in every mode). Its inputs make the
  **Source menu**: press settings in Media mode, ▲ ▼ and OK to switch.
- **Arrows, OK, back and home:** an Android TV Remote, Apple TV, Roku or Samsung Smart TV remote
  entity, or an LG webOS TV.
- **Players:** the screen shows what's playing on them, and ⏯ ⏪ ⏩ ⏹ control whichever plays.
- **Hotkeys** (up to 4 scenes or scripts) and what the **power** key switches (all off if any is on,
  else all on).

**Hold home** on the remote to switch between Devices and Media; it remembers the mode.

## What's next

- **Rooms:** with a bridge per room, the remote shows the Area it's in.
- **IR learning** (PARAM KEY v2, which has an IR receiver): point an old remote at it to copy a button.

## Advanced: the YAML package

The package (`packages/param_key.yaml`) is the full media setup the remote started with: modes,
a key map per TV/AVR/streamer context, a source menu and Roon. Its devices are examples to adapt.
When it is installed, it draws the remote's screen instead of the integration.

1. Copy `packages/param_key.yaml` into your HA config's
   `packages/` folder. Packages have to be enabled in `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Restart HA, or for later changes run Developer tools → YAML → **All YAML
   configuration**.
3. The package draws the screen through one bridge's actions, written as
   `esphome.param_key_bridge_show_status` and so on. A bridge from the installer has the end of
   its MAC in its name (`param-key-bridge-a1b2c3` gives `esphome.param_key_bridge_a1b2c3_show_status`):
   replace `param_key_bridge_` in the package with your bridge's name, `-` as `_`, plus `_`.

Reset the remote. Within a second, "Waiting for bridge" should change to HA's Media
screen.

## Configuring keys and modes (YAML package)

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

## Battery logging (YAML package)

The integration's Battery and Charging sensors cover most uses. The package adds a few more.
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
Run `pre-commit install` once: it checks every commit for secrets (gitleaks) and for names listed
in `~/.config/paramworks/private-patterns.txt`, if you keep one. Keep your own entity IDs and
anything house-specific in a local copy outside this repository.

Every push runs hassfest, the HACS checks, these tests and a scan for secrets, real MAC
addresses and file metadata (`.github/workflows/validate.yml`). To release, set `version` in
`manifest.json` and push a matching tag `vX.Y.Z`: the GitHub Release becomes that version in HACS.
