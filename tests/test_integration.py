"""Exercise the param_key integration in an in-process Home Assistant.

Pairs a simulated remote end to end (discovery, code, provisioning, pairing
again, removal), checks counters and IR against stubbed bridge actions, and
checks the pairing crypto against tests/vectors.json, a copy of param-key's
docs/test-vectors.json (the firmware is tested against the same file). Runs
inside the official HA container (see run_tests.sh).
"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from homeassistant import config_entries, core, loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.core import ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
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
    for action in ["set_remote", "forget_remote", "pair_reply", "send_ir", "show_status"]:
        async def record(call: ServiceCall, a=action) -> None:
            calls.append((a, dict(call.data)))
        hass.services.async_register("esphome", f"test_bridge_{action}", record)
    hass.services.async_register("esphome", "ohr_bridge_show_status", lambda call: None)

    assert await async_setup_component(hass, "param_key", {})
    await hass.async_block_till_done()
    flows = hass.config_entries.flow

    def of(action: str) -> list[dict]:
        return [d for a, d in calls if a == action]

    async def pair_request(remote_priv: X25519PrivateKey, mac: str = MAC) -> None:
        hass.bus.async_fire("esphome.openhomeremote_pair_request", {
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
        hass.bus.async_fire(f"esphome.openhomeremote_{event}", data)
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
    ent = er.async_get(hass).async_get_entity_id("infrared", "param_key", f"{MAC}_ir")
    check("IR emitter entity", ent is not None, ent)
    check("asleep -> unavailable", hass.states.get(ent).state == "unavailable", hass.states.get(ent))

    # --- Counters and liveness
    await frame("key", 7)
    check("frame -> available", hass.states.get(ent).state != "unavailable", hass.states.get(ent))
    await frame("hello", 3)
    hub = hass.data["param_key"]
    check("counter keeps the highest", hub.counters.get(MAC) == 7, hub.counters)
    await frame("key", 99, mac="02:00:00:00:00:0B")
    check("unpaired remote ignored", "02:00:00:00:00:0B" not in hub.counters and "02:00:00:00:00:0B" not in hub.seen)
    calls.clear()
    hass.bus.async_fire("esphome.openhomeremote_bridge_online", {"bridge": BRIDGE})
    await hass.async_block_till_done()
    check("bridge online -> key, floor past the last counter",
          of("set_remote") == [{"remote": MAC, "key": key.hex(), "floor": 8}], of("set_remote"))

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

    await hass.async_stop()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


asyncio.run(main())
