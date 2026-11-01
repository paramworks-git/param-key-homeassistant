"""Exercise the param_key integration in an in-process Home Assistant.

Pairs a simulated remote end to end (discovery, code, provisioning, pairing
again, removal), checks counters and IR against stubbed bridge actions, and
checks the pairing crypto against tests/vectors.json, a copy of param-key's
docs/test-vectors.json (the firmware is tested against the same file). Runs
inside the official HA container (see run_tests.sh).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import MappingProxyType

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from homeassistant import config_entries, core, loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.core import ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

ROOT = Path(__file__).parent.parent
VECTORS = json.loads((Path(__file__).parent / "vectors.json").read_text())
MAC = "02:00:00:00:00:0A"
BRIDGE = "test-bridge"

calls: list[tuple[str, dict]] = []
failures: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def raw(public) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


async def main() -> None:
    config_dir = tempfile.mkdtemp()
    shutil.copytree(ROOT / "custom_components", Path(config_dir) / "custom_components")
    sys.path.insert(0, config_dir)
    from custom_components.param_key import crypto  # noqa: E402

    # --- Crypto against the shared vectors (the firmware's side is in param-key)
    v = VECTORS
    ha_priv = X25519PrivateKey.from_private_bytes(bytes.fromhex(v["ha_private"]))
    p = crypto.pair(bytes.fromhex(v["remote_public"]), "02:00:00:00:00:01", ha_priv)
    check("vectors: HA public key", p.ha_public.hex() == v["ha_public"])
    check("vectors: key and code", p.key.hex() == v["key"] and p.code == v["code"], p)
    try:
        crypto.pair(bytes(32), MAC)
        check("rejects low-order key", False)
    except ValueError:
        check("rejects low-order key", True)

    hass = core.HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    await async_load_base_functionality(hass)
    await hass.async_start()

    # A v2 bridge (records calls) and an old bridge without set_remote.
    for action in ["set_epoch", "set_remote", "forget_remote", "pair_reply", "send_ir", "show_status"]:
        async def record(call: ServiceCall, a=action) -> None:
            calls.append((a, dict(call.data)))
        hass.services.async_register("esphome", f"test_bridge_{action}", record)
    # Screen actions as bridge firmware 0.3 has them: *_for twins, one screen per remote.
    for action in ["show_list", "show_status", "set_hotkeys", "show_message", "set_config"]:
        async def record_screen(call: ServiceCall, a=action) -> None:
            calls.append((a, dict(call.data)))
        hass.services.async_register("esphome", f"test_bridge_{action}_for", record_screen)
    hass.services.async_register("esphome", "param_key_bridge_show_status", lambda call: None)

    assert await async_setup_component(hass, "param_key", {})
    await hass.async_block_till_done()
    flows = hass.config_entries.flow

    def of(action: str) -> list[dict]:
        return [d for a, d in calls if a == action]

    async def pair_request(remote_priv: X25519PrivateKey, mac: str = MAC) -> None:
        hass.bus.async_fire("esphome.param_key_pair_request", {
            "remote": mac, "public_key": raw(remote_priv.public_key()).hex(), "firmware": "0.2.0",
            "rssi": -50, "bridge": BRIDGE})
        await hass.async_block_till_done()

    def remote_side(remote_priv: X25519PrivateKey, ha_public_hex: str, mac: str = MAC) -> tuple[bytes, str]:
        ha_public = bytes.fromhex(ha_public_hex)
        shared = remote_priv.exchange(X25519PublicKey.from_public_bytes(ha_public))
        return crypto.derive(shared, raw(remote_priv.public_key()), ha_public, crypto.mac_bytes(mac))

    async def frame(event: str = "key", counter: int | None = 1, mac: str = MAC) -> None:
        data = {"remote": mac, "key": "ok", "type": "press", "rssi": -50, "battery": 80}
        if counter is not None:
            data.update(counter=counter, bridge=BRIDGE)
        hass.bus.async_fire(f"esphome.param_key_{event}", data)
        await hass.async_block_till_done()

    # --- Manual add with no remote asking
    r = await flows.async_init("param_key", context={"source": "user"})
    r = await flows.async_configure(r["flow_id"], {})
    check("user flow: no remote yet", r["type"] == FlowResultType.FORM and r["errors"] == {"base": "no_remote"}, r)
    flows.async_abort(r["flow_id"])

    # --- Discovery, code, entry
    remote_priv = X25519PrivateKey.generate()
    await pair_request(remote_priv)
    found = [f for f in flows.async_progress_by_handler("param_key") if f["context"].get("unique_id") == MAC]
    check("pair request -> discovered", len(found) == 1 and found[0]["context"]["source"] == "integration_discovery",
          found)
    await pair_request(remote_priv)
    check("repeated request -> still one flow", len(flows.async_progress_by_handler("param_key")) == 1)

    calls.clear()
    r = await flows.async_configure(found[0]["flow_id"], {})
    replies = of("pair_reply")
    check("pair_reply sent to the bridge", r["step_id"] == "pair" and len(replies) == 1
          and replies[0]["remote"] == MAC and replies[0]["remote_public"] == raw(remote_priv.public_key()).hex(),
          (r, calls))
    key, code = remote_side(remote_priv, replies[0]["ha_public"])
    check("both sides show the same code", r["description_placeholders"]["code"] == crypto.format_code(code), r)

    r = await flows.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    check("confirm -> entry", r["type"] == FlowResultType.CREATE_ENTRY and r["data"]["remote"] == MAC, r)
    entry = hass.config_entries.async_entries("param_key")[0]
    check("bridge gets the key, floor 0",
          of("set_remote") == [{"remote": MAC, "key": key.hex(), "floor": 0}], of("set_remote"))
    names = [a for a, _ in calls]
    minutes = int((dt_util.utcnow() - datetime(2026, 1, 1, tzinfo=UTC)).total_seconds() // 60)
    epoch1 = of("set_epoch")[0]["epoch"] if of("set_epoch") else 0
    check("an epoch first, then the key", names.index("set_epoch") < names.index("set_remote")
          and len(of("set_epoch")) == 1 and minutes <= epoch1 <= 0xFFFFFF, calls)
    ent = er.async_get(hass).async_get_entity_id("infrared", "param_key", f"{MAC}_ir")
    check("IR emitter entity", ent is not None, ent)
    check("asleep -> unavailable", hass.states.get(ent).state == "unavailable", hass.states.get(ent))

    # --- Counters and liveness
    await frame("key", 7)
    check("frame -> available", hass.states.get(ent).state != "unavailable", hass.states.get(ent))
    hub = hass.data["param_key"]
    hub._sent.clear()
    calls.clear()
    await frame("hello", 3)
    await frame("hello", 4)
    check("no labels: the screen says what to do, once", [d["items"] for d in of("show_list")]
          == [['Label entities "remote"']] and of("show_list")[0]["remote"] == MAC, calls)
    check("settings sent with the screen", of("set_config") == [
        {"brightness": 80, "backlight_seconds": 15, "sleep_seconds": 30, "remote": MAC}], of("set_config"))
    check("counter keeps the highest", hub.counters.get(MAC) == 7, hub.counters)
    battery = er.async_get(hass).async_get_entity_id("sensor", "param_key", f"{MAC}_battery")
    check("battery from the hello", battery and hass.states.get(battery).state == "80", battery)
    hass.services.async_register("script", "param_key_render", lambda call: None)
    hub._sent.clear()
    calls.clear()
    await frame("hello", 5)
    await frame("key", 6)
    check("package installed: it draws the screen", of("show_list") == [] and of("set_hotkeys") == [], calls)
    hass.services.async_remove("script", "param_key_render")
    await frame("key", 99, mac="02:00:00:00:00:0B")
    check("unpaired remote ignored", "02:00:00:00:00:0B" not in hub.counters and "02:00:00:00:00:0B" not in hub.seen)
    calls.clear()
    hass.bus.async_fire("esphome.param_key_bridge_online", {"bridge": BRIDGE})
    await hass.async_block_till_done()
    check("bridge online -> key, floor past the last counter",
          of("set_remote") == [{"remote": MAC, "key": key.hex(), "floor": 8}], of("set_remote"))
    names = [a for a, _ in calls]
    check("bridge online -> a higher epoch, before the key",
          len(of("set_epoch")) == 1 and of("set_epoch")[0]["epoch"] > epoch1
          and names.index("set_epoch") < names.index("set_remote"), calls)
    stored = await hub._store.async_load()
    check("epoch saved before the bridge got it",
          stored and stored["epochs"].get("test_bridge") == of("set_epoch")[0]["epoch"], stored)

    # --- IR
    from infrared_protocols.commands.nec import NECCommand  # noqa: E402

    calls.clear()
    await hass.services.async_call("param_key", "send_ir",
                                   {"remote": MAC, "protocol": "nec", "address": "0x04", "command": 8}, blocking=True)
    sent = of("send_ir")
    check("send_ir NEC -> bridge", len(sent) == 1 and sent[0]["remote"] == MAC and sent[0]["carrier"] == 38000
          and sent[0]["timings"] == NECCommand(address=4, command=8).get_raw_timings(), sent)
    check("emitter records the send", hass.states.get(ent).state not in ("unknown", "unavailable"),
          hass.states.get(ent))
    calls.clear()
    await hass.services.async_call("param_key", "send_ir", {"entity_id": ent, "protocol": "raw",
                                   "timings": [900, -450, 560], "carrier": 36000}, blocking=True)
    check("send_ir raw by entity", of("send_ir") == [{"remote": MAC, "carrier": 36000, "timings": [900, -450, 560]}],
          calls)
    calls.clear()
    await hass.services.async_call("param_key", "send_ir", {"remote": MAC, "protocol": "pronto", "pronto":
                                   "0000 006D 0001 0000 0010 0020"}, blocking=True)
    check("send_ir pronto", len(of("send_ir")) == 1 and len(of("send_ir")[0]["timings"]) == 2, calls)
    for name, data in [("missing command", {"remote": MAC, "protocol": "nec", "address": 4}),
                       ("unknown remote", {"remote": "02:00:00:00:00:0B", "protocol": "nec", "address": 4,
                                           "command": 1})]:
        try:
            await hass.services.async_call("param_key", "send_ir", data, blocking=True)
            check(f"send_ir rejects {name}", False)
        except ServiceValidationError:
            check(f"send_ir rejects {name}", True)
    hub.seen[MAC] = dt_util.utcnow() - timedelta(minutes=5)
    try:
        await hass.services.async_call("param_key", "send_ir",
                                       {"remote": MAC, "protocol": "nec", "address": 4, "command": 8}, blocking=True)
        check("asleep remote: send_ir fails", False)
    except HomeAssistantError as err:
        check("asleep remote: send_ir fails", "asleep" in str(err), err)
    await frame("key", None)
    check("old firmware's event doesn't count as awake", not hub.awake(MAC))

    # --- Menus from labels (menu.py, screen.py)
    from homeassistant.helpers import area_registry as ar, label_registry as lr  # noqa: E402
    acted: list[tuple[str, dict]] = []
    for domain, service in [("homeassistant", "toggle"), ("light", "turn_on"), ("light", "turn_off"),
                            ("scene", "turn_on"), ("climate", "set_temperature")]:
        async def act(call: ServiceCall, name=f"{domain}.{service}") -> None:
            acted.append((name, dict(call.data)))
        hass.services.async_register(domain, service, act)
    ents, areas, labels = er.async_get(hass), ar.async_get(hass), lr.async_get(hass)
    remote_label = labels.async_create("remote").label_id
    movie_label = labels.async_create("Remote/Movie night").label_id
    office, living = areas.async_create("Office").id, areas.async_create("Living").id

    def entity(entity_id: str, state: str, name: str, area: str | None, label: str, **attrs) -> None:
        domain, object_id = entity_id.split(".")
        ents.async_get_or_create(domain, "test", object_id, suggested_object_id=object_id)
        ents.async_update_entity(entity_id, area_id=area, labels={label})
        hass.states.async_set(entity_id, state, {"friendly_name": name, **attrs})

    entity("light.office_lamp", "on", "Office Lamp", office, remote_label, brightness=128)
    entity("light.office_desk", "off", "Office Desk", office, remote_label)
    entity("scene.office_focus", "scening", "Office Focus", office, remote_label)
    for i in range(3):
        entity(f"light.living_{i}", "on" if i == 0 else "off", f"Living Light {i}", living, remote_label, brightness=255)
    for i in range(2):
        entity(f"switch.living_{i}", "off", f"Living Switch {i}", living, remote_label)
    entity("fan.living", "off", "Living Fan", living, remote_label)
    entity("media_player.living", "playing", "Living TV", living, remote_label)
    entity("scene.movie", "scening", "Movie", None, movie_label)
    entity("light.backlight", "off", "Backlight", None, movie_label)
    entity("switch.porch", "off", "Porch", None, remote_label)
    entity("climate.hall", "heat", "Hall", None, remote_label, temperature=20, current_temperature=19,
           hvac_modes=["off", "heat"], target_temp_step=0.5)
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)  # the redraw after registry changes is debounced
    await hass.async_block_till_done()
    counter = 1000

    async def press(key: str, event: str = "press") -> None:
        nonlocal counter
        counter += 1
        hass.bus.async_fire("esphome.param_key_key", {"remote": MAC, "key": key, "type": event, "rssi": -50,
                                                     "counter": counter, "bridge": BRIDGE})
        await hass.async_block_till_done()

    def screen() -> dict:
        return of("show_list")[-1] if of("show_list") else {}

    R = "\uf054  "
    check("root: Areas, custom menus, then the rest", screen().get("items") == [
        R + "Living", R + "Office", R + "Movie night", "20°  Hall", "off  Porch"]
        and screen()["title"] == "Devices" and screen()["cursor"] == 0, screen())
    await press("down")
    await press("ok")
    check("Office: names without the Area, sorted", screen().get("items") == ["off  Desk", "50%  Lamp"]
          and screen()["title"] == "Office", screen())
    check("Office scene is a hotkey", of("set_hotkeys")[-1]["labels"] == ["Focus", "", "", ""], of("set_hotkeys"))
    await press("down")
    await press("ok")
    await press("right")
    await press("h1", "tap")
    check("OK toggles, ▶ brightens, hotkey runs", acted == [
        ("homeassistant.toggle", {"entity_id": "light.office_lamp"}),
        ("light.turn_on", {"entity_id": "light.office_lamp", "brightness_step_pct": 10}),
        ("scene.turn_on", {"entity_id": "scene.office_focus"})], acted)
    calls.clear()
    await press("h1", "hold")
    check("hotkey held: what it does", [m["message"] for m in of("show_message")] == ["Runs Office Focus"], calls)
    calls.clear()
    hass.states.async_set("light.office_lamp", "on", {"friendly_name": "Office Lamp", "brightness": 255})
    await asyncio.sleep(0.5)
    await hass.async_block_till_done()
    check("state change redraws the screen", screen().get("items") == ["off  Desk", "100%  Lamp"], calls)
    calls.clear()
    hass.states.async_set("light.living_1", "on", {"friendly_name": "Living Light 1", "brightness": 10})
    await asyncio.sleep(0.5)
    await hass.async_block_till_done()
    check("off-screen state change: nothing sent", of("show_list") == [], calls)
    await press("back")
    check("back: up a level, cursor kept", screen().get("cursor") == 1 and screen()["title"] == "Devices", screen())
    await press("up")
    await press("ok")
    check("big Area grouped by kind", screen().get("items") == [R + "Lights", R + "Switches", R + "Fans", R + "Media"]
          and screen()["title"] == "Living", screen())
    await press("home", "tap")
    for _ in range(3):
        await press("down")
    await press("ok")
    check("thermostat detail", screen().get("title") == "Hall  19°"
          and screen()["items"] == ["Mode  heat", "Target  20°"], screen())
    acted.clear()
    await press("down")
    await press("right")
    check("▶ on Target", acted == [("climate.set_temperature", {"entity_id": "climate.hall", "temperature": 20.5})],
          acted)
    await press("back")
    await press("home", "tap")
    acted.clear()
    await press("power", "tap")
    lit = sorted(acted[0][1]["entity_id"]) if acted else []
    check("power: lights in this menu off", lit == ["light.living_0", "light.living_1", "light.office_lamp"], acted)
    calls.clear()
    await press("settings", "tap")
    await press("right")
    number = ents.async_get_entity_id("number", "param_key", f"{MAC}_brightness")
    check("settings screen changes brightness", of("set_config")[-1]["brightness"] == 90
          and float(hass.states.get(number).state) == 90, (of("set_config"), number and hass.states.get(number)))
    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 40}, blocking=True)
    check("number entity changes it too", of("set_config")[-1]["brightness"] == 40
          and screen()["items"][0] == "Brightness  40%", (of("set_config"), screen()))
    await press("settings", "tap")
    ents.async_update_entity("switch.porch", labels=set())
    await asyncio.sleep(0.5)
    await hass.async_block_till_done()
    check("label removed: gone from the menu", "off  Porch" not in screen().get("items", []), screen())

    # --- Media setup (media.py) and modes
    for domain, service in [("remote", "send_command"), ("media_player", "volume_up"),
                            ("media_player", "media_play_pause"), ("media_player", "select_source"),
                            ("media_player", "turn_off")]:
        async def act2(call: ServiceCall, name=f"{domain}.{service}") -> None:
            acted.append((name, dict(call.data)))
        hass.services.async_register(domain, service, act2)
    hass.states.async_set("media_player.avr", "on", {"friendly_name": "AVR", "volume_level": 0.35, "source": "TV",
                                                      "source_list": ["TV", "Shield", "Phono"]})
    hass.states.async_set("media_player.shield", "playing", {"friendly_name": "Shield", "media_title": "Show",
                                                             "app_name": "Netflix"})
    ents.async_get_or_create("remote", "androidtv_remote", "shield", suggested_object_id="shield")
    ents.async_get_or_create("remote", "some_other", "box", suggested_object_id="box")
    hass.states.async_set("remote.shield", "on", {"friendly_name": "Shield remote"})
    remote_entry = hass.config_entries.async_entries("param_key")[0]
    await press("home", "hold")
    check("home held without Media: says where to set it up",
          "Set up Media" in (of("show_message")[-1]["message"] if of("show_message") else ""), of("show_message"))
    r = await hass.config_entries.options.async_init(remote_entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"navigation": "remote.box", "start": "media"})
    check("unknown navigation platform refused", r.get("errors") == {"navigation": "nav_unsupported"}, r)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "volume": "media_player.avr", "navigation": "remote.shield", "players": ["media_player.shield"],
        "hotkeys": ["scene.movie"], "power": ["media_player.avr"], "start": "media"})
    check("sources step lists the AVR's inputs", r["step_id"] == "sources", r)
    calls.clear()
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"sources": ["Shield", "TV"]})
    await hass.async_block_till_done()
    check("media setup saved", r["type"] == FlowResultType.CREATE_ENTRY
          and remote_entry.options["media"]["sources"] == ["TV", "Shield"], r)
    status = of("show_status")[-1] if of("show_status") else {}
    check("Media screen: playing title, app, volume", status.get("title") == "Show"
          and status.get("subtitle") == "Netflix" and status.get("value") == 35 and status.get("value_label") == "Vol 35",
          status)
    check("Media hotkeys", of("set_hotkeys")[-1]["labels"] == ["Movie", "", "", ""], of("set_hotkeys"))
    acted.clear()
    await press("up")
    await press("ok")
    await press("home", "tap")
    await press("vol_up")
    await press("play")
    check("Media keys: arrows to the streamer, volume to the AVR, play to the player", acted == [
        ("remote.send_command", {"entity_id": "remote.shield", "command": "DPAD_UP"}),
        ("remote.send_command", {"entity_id": "remote.shield", "command": "DPAD_CENTER"}),
        ("remote.send_command", {"entity_id": "remote.shield", "command": "HOME"}),
        ("media_player.volume_up", {"entity_id": "media_player.avr"}),
        ("media_player.media_play_pause", {"entity_id": "media_player.shield"})], acted)
    await press("settings", "tap")
    check("Source menu: chosen inputs, current marked", screen().get("items") == ["• TV", "Shield"]
          and screen()["cursor"] == 0, screen())
    acted.clear()
    await press("down")
    await press("ok")
    check("OK picks the source", acted == [("media_player.select_source",
                                            {"entity_id": "media_player.avr", "source": "Shield"})], acted)
    acted.clear()
    await press("power", "tap")
    check("power switches the AVR off", acted == [("media_player.turn_off", {"entity_id": "media_player.avr"})], acted)
    calls.clear()
    await press("home", "hold")
    check("home held: mode picker", screen().get("items") == ["Devices", "Media"] and screen()["cursor"] == 1, screen())
    await press("up")
    await press("ok")
    check("picked Devices: the menus again", screen().get("title") == "Devices", screen())
    check("mode kept across restarts", hub.modes.get(MAC) == "devices", hub.modes)
    acted.clear()
    await press("vol_up")
    check("volume works in Devices mode too", acted == [("media_player.volume_up", {"entity_id": "media_player.avr"})],
          acted)

    # --- Pairing again: same entry, new key, counters start over
    remote_priv2 = X25519PrivateKey.generate()
    await pair_request(remote_priv2)
    found = [f for f in flows.async_progress_by_handler("param_key") if f["context"].get("unique_id") == MAC]
    check("paired remote asking again -> discovered", len(found) == 1, found)
    calls.clear()
    r = await flows.async_configure(found[0]["flow_id"], {})
    key2, code2 = remote_side(remote_priv2, of("pair_reply")[0]["ha_public"])
    r = await flows.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries("param_key")
    check("pair again -> same entry, new key", r["type"] == FlowResultType.ABORT and r["reason"] == "paired_again"
          and len(entries) == 1 and entries[0].data["key"] == key2.hex() and key2 != key, r)
    check("new key provisioned from floor 0", {"remote": MAC, "key": key2.hex(), "floor": 0} in of("set_remote"),
          of("set_remote"))

    # --- Removal takes the key off the bridges
    calls.clear()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    check("remove -> forget_remote", of("forget_remote") == [{"remote": MAC}], calls)

    integration = await loader.async_get_integration(hass, "param_key")
    check("brand icon ships with the integration", integration.has_branding
          and (Path(integration.file_path) / "brand" / "icon.png").is_file())

    # --- A bridge on the network: found by its ESPHome project, set up once its ESPHome device is
    matchers = (await loader.async_get_zeroconf(hass)).get("_esphomelib._tcp.local.", [])
    check("zeroconf matches the bridge project", any(
        m.get("domain") == "param_key" and m.get("properties", {}).get("project_name") == "paramworks.param-key-bridge"
        for m in matchers), matchers)
    info = ZeroconfServiceInfo(
        ip_address=ip_address("192.0.2.10"), ip_addresses=[ip_address("192.0.2.10")], port=6053,
        hostname="param-key-bridge-00000c.local.", type="_esphomelib._tcp.local.",
        name="param-key-bridge-00000c._esphomelib._tcp.local.",
        properties={"mac": "02000000000c", "project_name": "paramworks.param-key-bridge"})
    r = await flows.async_init("param_key", context={"source": "zeroconf"}, data=info)
    check("bridge discovered", r["type"] == FlowResultType.FORM and r["step_id"] == "bridge"
          and r["description_placeholders"]["name"] == "param-key-bridge-00000c", r)
    r = await flows.async_configure(r["flow_id"], {})
    check("bridge waits for its ESPHome device", r["type"] == FlowResultType.FORM
          and r["errors"] == {"base": "esphome_first"}, r)
    esphome = config_entries.ConfigEntry(
        data={"host": "192.0.2.10"}, discovery_keys=MappingProxyType({}), domain="esphome", minor_version=1,
        options={}, source="zeroconf", subentries_data=None, title="param-key-bridge-00000c",
        unique_id="02:00:00:00:00:0c", version=1)
    hass.config_entries._entries[esphome.entry_id] = esphome
    r = await flows.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    check("bridge entry created", r["type"] == FlowResultType.CREATE_ENTRY
          and r["data"] == {"bridge": "param-key-bridge-00000c", "mac": "02:00:00:00:00:0c"}, r)
    check("ESPHome actions stay off", "allow_service_calls"
          not in hass.config_entries.async_get_entry(esphome.entry_id).options)
    r = await flows.async_init("param_key", context={"source": "zeroconf"}, data=info)
    check("same bridge again -> already set up", r["type"] == FlowResultType.ABORT
          and r["reason"] == "already_configured", r)

    await hass.async_stop()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


asyncio.run(main())
