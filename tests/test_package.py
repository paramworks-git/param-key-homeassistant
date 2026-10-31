"""Exercise the openhomeremote HA package in an in-process Home Assistant.

Loads packages/openhomeremote.yaml, fakes device states, fires remote key
events and records which actions the dispatcher and renderer call. Runs inside
the official HA container (see run_tests.sh).
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from homeassistant import config_entries, core, loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config import load_yaml_config_file
from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.setup import async_setup_component

PACKAGE = Path(__file__).parent.parent / "packages" / "openhomeremote.yaml"

# Services the package calls on real devices; recorded instead of executed.
STUB_SERVICES = {
    "remote": ["send_command", "turn_on"],
    "webostv": ["button"],
    "media_player": ["volume_up", "volume_down", "volume_mute", "select_source",
                     "select_sound_mode", "media_play_pause", "media_next_track",
                     "media_previous_track", "media_stop", "turn_off", "media_play", "media_pause"],
    "roon": ["transfer"],
    "light": ["turn_on", "turn_off"],
    "esphome": ["ohr_bridge_show_status", "ohr_bridge_show_list",
                "ohr_bridge_set_hotkeys", "ohr_bridge_show_message", "ohr_bridge_set_config"],
    "button": ["press"],
    "humidifier": ["set_humidity", "set_mode", "toggle"],
    "climate": ["set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode", "set_humidity"],
    "param_key": ["send_ir"],
}

calls: list[tuple[str, dict]] = []
failures: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


async def main() -> None:
    config_dir = tempfile.mkdtemp()
    hass = core.HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await async_load_base_functionality(hass)
    await hass.async_start()

    for domain, services in STUB_SERVICES.items():
        for service in services:
            async def record(call: ServiceCall, d=domain, s=service) -> None:
                calls.append((f"{d}.{s}", dict(call.data)))
            hass.services.async_register(domain, service, record)

    # The AVR's Roon zone comes up (paused on its queue) when the AVR turns on.
    async def turn_on(call: ServiceCall) -> None:
        calls.append(("media_player.turn_on", dict(call.data)))
        if "media_player.avr" in call.data["entity_id"]:
            hass.states.async_set("media_player.roon_zone", "paused", {"friendly_name": "Media Center"})
    hass.services.async_register("media_player", "turn_on", turn_on)

    # Scripts the key map calls that live elsewhere in the HA config.
    pkg = load_yaml_config_file(str(PACKAGE))
    for name in ["media_center_off", "shield_on_and_source", "apple_music_tv"]:
        pkg["script"][name] = {"sequence": [{"action": "button.press", "data": {"entity_id": f"button.{name}"}}]}
    # No Roon integration here: name its zones.
    zones = ["media_player.roon_zone", "media_player.kitchen"]
    for s in ["ohr_dispatch", "ohr_render"]:
        pkg["script"][s]["variables"]["devices"]["roon_zones"] = zones
    # An IR hotkey (the integration's param_key.send_ir is stubbed above).
    pkg["script"]["ohr_dispatch"]["variables"]["keymap"]["tv"]["h4"] = {
        "label": "Soundbar", "ir": {"protocol": "nec", "address": "0x04", "command": 8}}

    for domain in ["homeassistant", "input_select", "input_boolean", "input_number", "input_text"]:
        assert await async_setup_component(hass, domain, {domain: pkg.get(domain, {})}), domain
    assert await async_setup_component(hass, "template", {"template": pkg["template"]})
    assert await async_setup_component(hass, "script", {"script": pkg["script"]})
    # The render automation isn't loaded: renders are called explicitly below.
    await hass.async_block_till_done()

    avr, lg, shield = "media_player.avr", "media_player.tv", "media_player.shield"

    def set_media(source: str | None, avr_state: str = "on", tv: str = "on") -> None:
        hass.states.async_set(avr, avr_state, {"source": source, "volume_level": 0.42, "is_volume_muted": False})
        hass.states.async_set(lg, tv, {"source": "AVR-X3800H"})
        hass.states.async_set(shield, "on", {"app_name": "Plex"})
        hass.states.async_set("binary_sensor.avr_source_heos", "off")

    async def key(k: str, t: str = "press") -> list[tuple[str, dict]]:
        calls.clear()
        await hass.services.async_call("script", "ohr_dispatch", {"remote": "AA", "key": k, "type": t}, blocking=True)
        await hass.async_block_till_done()
        return [c for c in calls if not c[0].startswith("esphome.")]

    async def render() -> dict[str, dict]:
        calls.clear()
        await hass.services.async_call("script", "ohr_render", {}, blocking=True)
        await hass.async_block_till_done()
        return {c[0].removeprefix("esphome.ohr_bridge_"): c[1] for c in calls}

    # --- Media context detection
    set_media("SHIELD")
    await hass.async_block_till_done()
    check("context shield", hass.states.get("sensor.remote_media_context").state == "shield",
          hass.states.get("sensor.remote_media_context").state)
    set_media(None, "off", "off")
    await hass.async_block_till_done()
    check("context idle", hass.states.get("sensor.remote_media_context").state == "idle")

    # --- Shield context keys
    set_media("SHIELD")
    await hass.async_block_till_done()
    c = await key("up")
    check("shield up -> DPAD_UP", c == [("remote.send_command", {"entity_id": ["remote.shield"], "command": "DPAD_UP"})], c)
    c = await key("up", "repeat")
    check("shield up repeat -> DPAD_UP", len(c) == 1 and c[0][1]["command"] == "DPAD_UP", c)
    c = await key("home", "press")
    check("home press ignored (acts on tap)", c == [], c)
    c = await key("home", "tap")
    check("home tap -> HOME", c and c[0][1].get("command") == "HOME", c)
    c = await key("h1")
    check("hotkey press ignored (acts on tap)", c == [], c)
    c = await key("h1", "tap")
    check("shield h1 -> Plex", c == [("remote.turn_on", {"entity_id": ["remote.shield"], "activity": "com.plexapp.android"})], c)
    c = await key("h1", "hold")
    msgs = [x for x in calls if x[0] == "esphome.ohr_bridge_show_message"]
    check("hold h1 previews, runs nothing", c == [] and msgs
          and msgs[0][1]["message"] == "Plex: Opens com.plexapp.android on the Shield", msgs)
    c = await key("ok")
    check("shield ok press waits (has a hold)", c == [], c)

    # --- IR entry: sent by the remote that pressed the key
    set_media("TV Audio")
    await hass.async_block_till_done()
    c = await key("h4", "tap")
    check("ir entry -> param_key.send_ir", c == [("param_key.send_ir", {
        "protocol": "nec", "address": "0x04", "command": 8, "remote": "AA"})], c)
    c = await key("h4", "hold")
    msgs = [x for x in calls if x[0] == "esphome.ohr_bridge_show_message"]
    check("hold IR hotkey previews it", c == [] and msgs and msgs[0][1]["message"] == "Soundbar: IR NEC 8", msgs)
    set_media("SHIELD")
    await hass.async_block_till_done()
    c = await key("ok", "tap")
    check("shield ok tap -> DPAD_CENTER", c == [("remote.send_command", {"entity_id": ["remote.shield"], "command": "DPAD_CENTER"})], c)
    c = await key("ok", "hold")
    check("shield ok hold -> long press", c == [("remote.send_command", {"entity_id": ["remote.shield"],
                                                 "command": "DPAD_CENTER", "hold_secs": 1})], c)
    c = await key("vol_up")
    check("vol_up -> Denon", c == [("media_player.volume_up", {"entity_id": [avr]})], c)
    c = await key("mute")
    check("mute toggles on", c == [("media_player.volume_mute", {"entity_id": [avr], "is_volume_muted": True})], c)
    c = await key("power", "tap")
    check("power tap -> media_center_off", c == [("button.press", {"entity_id": "button.media_center_off"})], c)
    c = await key("ok", "release")
    check("release ignored", c == [], c)

    # --- Idle / TV / music contexts
    set_media(None, "off", "off")
    await hass.async_block_till_done()
    c = await key("power", "tap")
    check("idle power -> shield_on_and_source", c == [("button.press", {"entity_id": "button.shield_on_and_source"})], c)
    c = await key("h2", "tap")
    check("idle h2 -> TV Audio", c == [("media_player.select_source", {"entity_id": [avr], "source": "TV Audio"})], c)
    set_media("TV Audio")
    await hass.async_block_till_done()
    c = await key("left", "repeat")
    check("tv left -> LG LEFT", c == [("webostv.button", {"entity_id": [lg], "button": "LEFT"})], c)
    set_media("NET")
    hass.states.async_set("binary_sensor.avr_source_heos", "on")
    await hass.async_block_till_done()
    c = await key("h1", "tap")
    check("music h1 -> PURE DIRECT", c == [("media_player.select_sound_mode", {"entity_id": [avr], "sound_mode": "PURE DIRECT"})], c)
    mc, kitchen, heos = "media_player.roon_zone", "media_player.kitchen", "media_player.avr_heos"
    hass.states.async_set(mc, "off", {"friendly_name": "Media Center"})
    hass.states.async_set(kitchen, "paused", {"friendly_name": "Kitchen pair"})
    hass.states.async_set(heos, "playing", {})
    await hass.async_block_till_done()
    c = await key("fwd")
    check("music fwd -> HEOS next (only HEOS playing)", c == [("media_player.media_next_track", {"entity_id": [heos]})], c)
    hass.states.async_set(mc, "playing", {"friendly_name": "Media Center"})
    await hass.async_block_till_done()
    c = await key("fwd")
    check("music fwd -> Roon next", c == [("media_player.media_next_track", {"entity_id": [mc]})], c)
    c = await key("play")
    check("music play while playing -> Roon pause", c == [("media_player.media_pause", {"entity_id": [mc]})], c)
    hass.states.async_set(mc, "off", {"friendly_name": "Media Center"})
    hass.states.async_set(heos, "idle", {})
    set_media(None, "off", "off")
    await hass.async_block_till_done()
    c = await key("play")
    check("idle play wakes the AVR, resumes Roon", c == [("media_player.turn_on", {"entity_id": [avr]}),
                                                         ("media_player.media_play", {"entity_id": [mc]})], c)
    hass.states.async_set(mc, "off", {"friendly_name": "Media Center"})
    await hass.async_block_till_done()
    c = await key("h4", "tap")
    check("idle Music hotkey -> Roon on the AVR", c == [("media_player.turn_on", {"entity_id": [avr]}),
                                                        ("media_player.media_play", {"entity_id": [mc]})], c)
    c = await key("h4", "hold")
    msgs = [x for x in calls if x[0] == "esphome.ohr_bridge_show_message"]
    check("hold Music previews Roon", msgs and msgs[0][1]["message"] == "Music: Plays Roon on Media Center", msgs)
    set_media("NET")
    hass.states.async_set("binary_sensor.avr_source_heos", "on")
    c = await key("h2", "hold")
    msgs = [x for x in calls if x[0] == "esphome.ohr_bridge_show_message"]
    check("hold h2 previews sound mode", msgs and msgs[0][1]["message"] == "Stereo: Sound mode STEREO", msgs)
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    hass.states.async_set("media_player.roon_zone", "playing", {
        "media_title": "Fix You", "media_artist": "Coldplay", "media_duration": 291, "media_position": 120,
        "media_position_updated_at": dt_util.utcnow() - timedelta(seconds=14)})
    await hass.async_block_till_done()
    r = await render()
    s = r.get("show_status", {})
    check("music shows Roon track and time", s.get("title") == "Fix You" and s.get("subtitle") == "Coldplay\n2:14 / 4:51", s)
    hass.states.async_set("media_player.roon_zone", "off", {"friendly_name": "Media Center"})
    await hass.async_block_till_done()

    # --- Render: media status + hotkeys
    set_media("SHIELD")
    await hass.async_block_till_done()
    r = await render()
    s = r.get("show_status", {})
    check("render status title", s.get("title") == "Plex", s)
    check("render status volume", s.get("value") == 42 and s.get("value_label") == "Vol 42", s)
    check("render hotkeys", r.get("set_hotkeys", {}).get("labels") == ["Plex", "YTM", "Netflix", "Tidal"], r.get("set_hotkeys"))
    set_media(None, "off", "off")
    await hass.async_block_till_done()
    r = await render()
    check("render idle hides volume", r["show_status"]["value"] == -1 and r["show_status"]["title"] == "Off", r["show_status"])

    # --- Source menu (Media mode)
    set_media("SHIELD")
    await hass.async_block_till_done()
    c = await key("settings", "hold")
    check("hold settings opens source menu", c == [] and hass.states.get("input_boolean.ohr_sources").state == "on"
          and float(hass.states.get("input_number.ohr_menu_cursor").state) == 0, c)
    hass.states.async_set(avr, "on", {"source": "SHIELD", "volume_level": 0.42, "sound_mode": "STEREO"})
    await hass.async_block_till_done()
    r = await render()
    lst = r.get("show_list", {})
    check("source menu rows", lst.get("items") == ["• Shield", "TV", "PC", "Music", "Sound  STEREO", "Output  Media Center"]
          and lst.get("title") == "Source" and lst.get("cursor") == 0, lst)
    check("source menu hides hotkeys", r["set_hotkeys"]["labels"] == ["", "", "", ""], r["set_hotkeys"])
    c = await key("vol_up")
    check("source menu keeps volume", c == [("media_player.volume_up", {"entity_id": [avr]})], c)
    c = await key("settings", "tap")
    check("settings tap in menu closes it", hass.states.get("input_boolean.ohr_sources").state == "off", c)
    c = await key("settings", "tap")
    check("shield settings tap still -> SETTINGS", c and c[0][1].get("command") == "SETTINGS", c)
    await key("settings", "hold")
    for _ in range(4):
        await key("down")
    c = await key("right")
    check("sound row right -> next sound mode", c == [("media_player.select_sound_mode", {"entity_id": [avr], "sound_mode": "DIRECT"})], c)
    c = await key("left")
    check("sound row left -> previous sound mode", c and c[0][1]["sound_mode"] == "PURE DIRECT", c)
    check("sound change keeps menu open", hass.states.get("input_boolean.ohr_sources").state == "on")
    await key("up")
    c = await key("ok")
    check("ok on Music -> Roon on the AVR, menu closes", c == [("media_player.turn_on", {"entity_id": [avr]}),
                                                               ("media_player.media_play", {"entity_id": [mc]})]
          and hass.states.get("input_boolean.ohr_sources").state == "off", c)
    # Output row: pick a zone, OK moves the playing queue there.
    hass.states.async_set(mc, "playing", {"friendly_name": "Media Center"})
    await key("settings", "hold")
    for _ in range(5):
        await key("down")
    c = await key("right")
    r = await render()
    check("output right picks the next zone", c == [] and r["show_list"]["items"][5] == "Output  Kitchen pair  (OK)"
          and hass.states.get("input_boolean.ohr_sources").state == "on", r["show_list"])
    c = await key("ok")
    check("output ok moves the queue", c == [("roon.transfer", {"entity_id": [mc], "transfer_id": kitchen})]
          and hass.states.get("input_text.ohr_roon_zone").state == kitchen
          and hass.states.get("input_boolean.ohr_sources").state == "off", c)
    hass.states.async_set(mc, "idle", {"friendly_name": "Media Center"})
    hass.states.async_set(kitchen, "playing", {"friendly_name": "Kitchen pair"})
    set_media("NET")
    hass.states.async_set("binary_sensor.avr_source_heos", "on")
    await hass.async_block_till_done()
    c = await key("play")
    check("music keys follow the picked zone", c == [("media_player.media_pause", {"entity_id": [kitchen]})], c)
    await key("settings", "hold")
    r = await render()
    check("menu reopens on the picked zone", r["show_list"]["items"][5] == "Output  Kitchen pair", r["show_list"])
    for _ in range(5):
        await key("down")
    await key("right")
    hass.states.async_set(kitchen, "paused", {"friendly_name": "Kitchen pair"})
    hass.states.async_set(mc, "playing", {"friendly_name": "Media Center"})
    await hass.async_block_till_done()
    c = await key("ok")
    check("output ok onto a playing zone just switches", c == []
          and hass.states.get("input_text.ohr_roon_zone").state == mc, c)
    hass.states.async_set(mc, "off", {"friendly_name": "Media Center"})
    set_media("NET")
    hass.states.async_set("binary_sensor.avr_source_heos", "on")
    await hass.async_block_till_done()
    await key("settings", "tap")
    check("music settings tap opens menu on Music", hass.states.get("input_boolean.ohr_sources").state == "on"
          and float(hass.states.get("input_number.ohr_menu_cursor").state) == 3)
    await key("up")
    await key("up")
    c = await key("ok")
    check("ok on TV -> TV Audio", c == [("media_player.select_source", {"entity_id": [avr], "source": "TV Audio"})], c)
    await key("settings", "tap")
    c = await key("back")
    check("back closes source menu", hass.states.get("input_boolean.ohr_sources").state == "off", c)

    # --- Mode picker
    c = await key("home", "hold")
    check("hold home opens picker", hass.states.get("input_boolean.ohr_picker").state == "on")
    r = await render()
    check("picker renders list", r.get("show_list", {}).get("items") == ["Media", "Devices", "Settings"], r)
    await key("down")
    check("picker cursor down", float(hass.states.get("input_number.ohr_picker_cursor").state) == 1)
    c = await key("vol_up")
    check("picker swallows other keys", c == [], c)
    await key("ok")
    check("picker ok selects Devices", hass.states.get("input_select.ohr_mode").state == "Devices"
          and hass.states.get("input_boolean.ohr_picker").state == "off")

    # --- Devices mode (entities labelled "remote")
    labels = lr.async_get(hass)
    label = labels.async_create("remote")
    ents = er.async_get(hass)
    for eid, state, attrs in [("light.desk", "on", {"brightness": 128, "friendly_name": "Desk"}),
                              ("light.arc", "off", {"friendly_name": "Arc"}),
                              ("scene.relax", "unknown", {"friendly_name": "Relax"})]:
        domain, obj = eid.split(".")
        entry = ents.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        ents.async_update_entity(entry.entity_id, labels={label.label_id})
        hass.states.async_set(eid, state, attrs)
    await hass.async_block_till_done()
    r = await render()
    lst = r.get("show_list", {})
    check("devices list sorted with state", lst.get("items") == ["off  Arc", "50%  Desk"] and lst.get("cursor") == 0, lst)
    check("devices hotkeys = labelled scenes", r.get("set_hotkeys", {}).get("labels") == ["Relax", "", "", ""], r.get("set_hotkeys"))
    await key("down")
    c = await key("right")
    check("devices right -> brightness +10 on Desk", c == [("light.turn_on", {"entity_id": ["light.desk"], "brightness_step_pct": 10})], c)
    c = await key("power", "tap")
    check("devices power -> lights off", c and c[0][0] == "light.turn_off" and set(c[0][1]["entity_id"]) == {"light.arc", "light.desk"}, c)
    set_media("NET")
    hass.states.async_set("binary_sensor.avr_source_heos", "on")
    await hass.async_block_till_done()
    hass.states.async_set("media_player.avr_heos", "playing", {})
    await hass.async_block_till_done()
    c = await key("play")
    check("devices play -> music pause", c == [("media_player.media_pause", {"entity_id": ["media_player.avr_heos"]})], c)
    set_media("SHIELD")
    await hass.async_block_till_done()
    c = await key("fwd")
    check("devices fwd -> Shield fast forward", c and c[0][1].get("command") == "MEDIA_FAST_FORWARD", c)
    c = await key("vol_down")
    check("devices vol still -> Denon", c == [("media_player.volume_down", {"entity_id": [avr]})], c)

    # --- Devices mode: humidifier and thermostat open a detail screen
    for eid, state, attrs in [("humidifier.mist", "on", {"humidity": 60, "min_humidity": 30, "max_humidity": 80,
                                                         "available_modes": ["auto", "humidity", "sleep"], "mode": "humidity",
                                                         "current_humidity": 55, "friendly_name": "Mist"}),
                              ("climate.thermo", "heat_cool", {"target_temp_low": 11.5, "target_temp_high": 22.5,
                                                               "target_temp_step": 0.5, "current_temperature": 20.5,
                                                               "hvac_modes": ["off", "heat", "cool", "heat_cool"],
                                                               "fan_modes": ["Auto low", "Low"], "fan_mode": "Auto low",
                                                               "friendly_name": "Thermo"})]:
        domain, obj = eid.split(".")
        entry = ents.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        ents.async_update_entity(entry.entity_id, labels={label.label_id})
        hass.states.async_set(eid, state, attrs)
    await hass.async_block_till_done()
    hass.states.async_set("input_number.ohr_cursor", 0)
    r = await render()
    check("devices list shows targets", r["show_list"]["items"] == ["off  Arc", "50%  Desk", "60%  Mist", "11.5-22.5°  Thermo"],
          r["show_list"]["items"])
    await key("down"); await key("down")
    c = await key("right")
    check("right on humidifier opens detail, no device call", c == [] and
          hass.states.get("input_text.ohr_detail").state == "humidifier.mist", c)
    await key("back")
    c = await key("ok")
    check("ok opens humidifier detail", c == [] and hass.states.get("input_text.ohr_detail").state == "humidifier.mist", c)
    r = await render()
    check("detail screen rows", r["show_list"]["items"] == ["Power  on", "Target  60%", "Mode  humidity"]
          and r["show_list"]["title"] == "Mist  55%" and r["show_list"]["cursor"] == 0, r["show_list"])
    check("detail hides hotkeys", r["set_hotkeys"]["labels"] == ["", "", "", ""], r["set_hotkeys"])
    c = await key("ok")
    check("detail ok on power toggles", c == [("homeassistant.toggle", {"entity_id": ["humidifier.mist"]})]
          or (c and c[0][0] == "humidifier.toggle"), c)
    await key("down")
    c = await key("right")
    check("detail target +5%", c == [("humidifier.set_humidity", {"entity_id": ["humidifier.mist"], "humidity": 65})], c)
    hass.states.async_set("humidifier.mist", "on", {**hass.states.get("humidifier.mist").attributes, "humidity": 78})
    c = await key("right")
    check("detail target clamps to max", c and c[0][1]["humidity"] == 80, c)
    c = await key("ok")
    check("detail ok on number does nothing", c == [], c)
    await key("down")
    c = await key("right")
    check("detail mode cycles forward", c == [("humidifier.set_mode", {"entity_id": ["humidifier.mist"], "mode": "sleep"})], c)
    c = await key("left")
    check("detail mode cycles back", c and c[0][1]["mode"] == "auto", c)
    await key("down")
    check("detail cursor stops at last row", float(hass.states.get("input_number.ohr_detail_cursor").state) == 2)
    await key("back")
    check("back closes detail", hass.states.get("input_text.ohr_detail").state == "")
    r = await render()
    check("back to devices list", r["show_list"]["title"] == "Devices", r["show_list"])

    await key("down")
    await key("right")
    check("right opens thermostat detail", hass.states.get("input_text.ohr_detail").state == "climate.thermo"
          and float(hass.states.get("input_number.ohr_detail_cursor").state) == 0)
    r = await render()
    check("thermostat rows", r["show_list"]["items"] == ["Mode  heat/cool", "Heat to  11.5°", "Cool to  22.5°", "Fan  Auto low"]
          and r["show_list"]["title"] == "Thermo  20.5°", r["show_list"])
    c = await key("right")
    check("hvac mode wraps", c == [("climate.set_hvac_mode", {"entity_id": ["climate.thermo"], "hvac_mode": "off"})], c)
    await key("down")
    c = await key("right")
    check("heat-to up keeps cool-to", c == [("climate.set_temperature", {
        "entity_id": ["climate.thermo"], "target_temp_low": 12.0, "target_temp_high": 22.5})], c)
    hass.states.async_set("climate.thermo", "heat_cool", {**hass.states.get("climate.thermo").attributes,
                                                          "target_temp_low": 22.0, "target_temp_high": 22.5})
    c = await key("right")
    check("heat-to stays a step below cool-to", c and c[0][1]["target_temp_low"] == 22.0, c)
    await key("down")
    c = await key("left")
    check("cool-to down stays above heat-to", c and c[0][1]["target_temp_high"] == 22.5 and c[0][1]["target_temp_low"] == 22.0, c)
    await key("down")
    c = await key("ok")
    check("ok on fan steps forward", c == [("climate.set_fan_mode", {"entity_id": ["climate.thermo"], "fan_mode": "Low"})], c)
    hass.states.async_set("climate.thermo", "heat", {"temperature": 21.0, "target_temp_step": 0.5,
                                                     "hvac_modes": ["off", "heat"], "friendly_name": "Thermo"})
    await hass.async_block_till_done()
    hass.states.async_set("input_number.ohr_detail_cursor", 1)
    c = await key("left")
    check("heat target -0.5", c == [("climate.set_temperature", {"entity_id": ["climate.thermo"], "temperature": 20.5})], c)
    hass.states.async_set("climate.thermo", "heat", {"temperature": 21.0, "target_temp_step": 0.5, "humidity": 56,
                                                     "current_temperature": 20.5, "current_humidity": 53.56,
                                                     "min_humidity": 52, "max_humidity": 65,
                                                     "hvac_modes": ["off", "heat"], "friendly_name": "Thermo"})
    await hass.async_block_till_done()
    r = await render()
    check("thermostat title shows temperature and humidity", r["show_list"]["title"] == "Thermo  20.5° 54%", r["show_list"])
    check("thermostat with target humidity gets a Humidity row",
          r["show_list"]["items"][:3] == ["Mode  heat", "Target  21.0°", "Humidity  56%"], r["show_list"])
    hass.states.async_set("input_number.ohr_detail_cursor", 2)
    c = await key("right")
    check("climate humidity +1", c == [("climate.set_humidity", {"entity_id": ["climate.thermo"], "humidity": 57})], c)
    hass.states.async_set("climate.thermo", "heat", {**hass.states.get("climate.thermo").attributes, "humidity": 52})
    c = await key("left")
    check("climate humidity clamps to min", c and c[0][1]["humidity"] == 52, c)
    await key("home", "hold"); await key("ok")
    check("picking a mode closes detail", hass.states.get("input_text.ohr_detail").state == "")

    # --- Settings mode
    for eid, v in [("input_number.ohr_brightness", 60), ("input_number.ohr_screen_off", 15),
                   ("input_number.ohr_sleep_after", 30)]:
        await hass.services.async_call("input_number", "set_value", {"entity_id": eid, "value": v}, blocking=True)
    await key("home", "hold"); await key("down"); await key("ok")
    check("picker selects Settings", hass.states.get("input_select.ohr_mode").state == "Settings")
    r = await render()
    lst = r.get("show_list", {})
    check("settings rows", lst.get("items") == ["Brightness  60%", "Screen off after  15 s", "Sleep after  30 s"]
          and lst.get("cursor") == 0 and r["set_hotkeys"]["labels"] == ["", "", "", ""], r)
    await key("right")
    check("right raises brightness", float(hass.states.get("input_number.ohr_brightness").state) == 70)
    for _ in range(5):
        await key("right", "repeat")
    check("brightness stops at 100", float(hass.states.get("input_number.ohr_brightness").state) == 100)
    await key("down"); await key("down")
    await key("left")
    check("left lowers sleep after", float(hass.states.get("input_number.ohr_sleep_after").state) == 20)
    c = await key("settings", "hold")
    check("settings hold does nothing in Settings", c == [] and hass.states.get("input_boolean.ohr_sources").state == "off", c)
    await hass.services.async_call("input_select", "select_option", {"entity_id": "input_select.ohr_mode", "option": "Media"}, blocking=True)

    # --- Battery log from hello
    async def hello(battery: int, charging: str, rssi: int = -60) -> None:
        hass.bus.async_fire("esphome.openhomeremote_hello", {
            "remote": "AA", "battery": battery, "charging": charging, "firmware": "0.1.0", "rssi": rssi})
        await hass.async_block_till_done()

    await hello(80, "false", -55)
    check("battery from hello", hass.states.get("sensor.remote_battery").state == "80",
          hass.states.get("sensor.remote_battery"))
    check("battery has state_class", hass.states.get("sensor.remote_battery").attributes.get("state_class") == "measurement")
    check("signal from hello", hass.states.get("sensor.remote_signal").state == "-55")
    check("not charging", hass.states.get("binary_sensor.remote_charging").state == "off")
    await hello(-1, "true")
    check("unknown battery keeps last value", hass.states.get("sensor.remote_battery").state == "80",
          hass.states.get("sensor.remote_battery").state)
    check("charging", hass.states.get("binary_sensor.remote_charging").state == "on")
    hass.bus.async_fire("esphome.openhomeremote_hello", {
        "remote": "AA", "battery": 70, "charging": "false", "firmware": "0.1.0", "rssi": -60,
        "wake": "ext1", "wake_mask": "0x200000", "diag": 1})
    await hass.async_block_till_done()
    w = hass.states.get("sensor.remote_last_wake")
    check("wake diagnostics", w.state == "ext1" and w.attributes.get("wake_mask") == "0x200000"
          and w.attributes.get("accelerometer") == "found" and w.attributes.get("acc_int_at_boot") == "low", w)
    await hello(71, "false")
    w = hass.states.get("sensor.remote_last_wake")
    check("old hello keeps wake diagnostics", w.state == "ext1" and w.attributes.get("accelerometer") == "found", w)

    # --- Several remotes: per-MAC readings
    hass.bus.async_fire("esphome.openhomeremote_hello", {
        "remote": "BB", "battery": 40, "charging": "true", "firmware": "0.2.0", "rssi": -70})
    await hass.async_block_till_done()
    await hello(-1, "false")
    r = hass.states.get("sensor.remote_battery").attributes.get("remotes") or {}
    check("per-remote readings", set(r) == {"AA", "BB"} and r["AA"]["battery"] == 71
          and r["BB"]["battery"] == 40 and r["BB"]["charging"] is True and r["BB"]["firmware"] == "0.2.0", r)
    hass.bus.async_fire("esphome.openhomeremote_hello", {
        "remote": "02:00:00:00:00:02", "battery": 68, "charging": "true", "firmware": "0.1.0", "rssi": -60})
    await hass.async_block_till_done()
    r = hass.states.get("sensor.remote_battery").attributes.get("remotes") or {}
    check("labelled remote", r.get("Bedroom", {}).get("mac") == "02:00:00:00:00:02" and "AA" in r, r)

    await hass.async_stop()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


asyncio.run(main())
