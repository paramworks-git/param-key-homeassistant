"""The integration's link to the bridges (docs/protocol.md in param-key).

One Hub per Home Assistant: it listens to the bridges' events, remembers which
bridge last heard each remote and the last counter each remote sent, offers
remotes that ask to pair as discovered devices, and calls the bridges' actions
(pairing, keys, IR).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any

from infrared_protocols.commands import Command

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_FORGET_REMOTE,
    ACTION_PAIR_REPLY,
    ACTION_SEND_IR,
    ACTION_SET_EPOCH,
    ACTION_SET_REMOTE,
    AWAKE_FOR,
    CONF_KEY,
    CONF_REMOTE,
    DOMAIN,
    EVENT_BRIDGE_ONLINE,
    EVENT_HELLO,
    EVENT_KEY,
    EVENT_PAIR_REQUEST,
    MAX_IR_TIMINGS,
    PAIR_REQUEST_FRESH,
    SIGNAL_REMOTE_SEEN,
)

_LOGGER = logging.getLogger(__name__)

ESPHOME = "esphome"
STORE_VERSION = 1
MAX_REQUESTS = 20
# Bridge epochs (docs/protocol.md, "Security"): 24-bit, never below the minutes since this date.
EPOCH_BASE = datetime(2026, 1, 1, tzinfo=UTC)
MAX_EPOCH = 0xFFFFFF
# Until the integration draws screens itself: what a paired remote shows when the YAML
# package (which has its own renderer, script.param_key_render) isn't installed.
PACKAGE_RENDERER = "param_key_render"
READY_SCREEN = {"mode": "PARAM KEY", "title": "Ready", "subtitle": "Paired with Home Assistant",
                "value": -1, "value_label": ""}


@dataclass
class PairRequest:
    """A remote asking to pair, as its bridges reported it."""

    mac: str
    public_key: bytes
    firmware: str
    seen: datetime
    bridges: dict[str, int] = field(default_factory=dict)  # bridge -> RSSI


def bridge_prefix(bridge: str) -> str:
    """ESPHome node name -> the prefix of its actions."""
    return bridge.replace("-", "_")


async def async_get_hub(hass: HomeAssistant) -> Hub:
    """The hub, started on first use (setup, or a config flow before any entry)."""
    hub: Hub | None = hass.data.get(DOMAIN)
    if hub is None:
        hub = hass.data[DOMAIN] = Hub(hass)
        await hub.async_start()
    return hub


class Hub:
    """Bridges, remotes' radio state, and pairing requests."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, dict[str, int]]] = Store(hass, STORE_VERSION, f"{DOMAIN}.link")
        self.counters: dict[str, int] = {}  # MAC -> last counter HA saw
        self.epochs: dict[str, int] = {}  # bridge action prefix -> last epoch HA gave it
        self._epoch_sent: set[str] = set()  # bridges that have an epoch from this HA run
        self._bridge_locks: dict[str, asyncio.Lock] = {}
        self._ready_shown: set[str] = set()  # bridges that have the ready screen cached
        self.seen: dict[str, datetime] = {}  # MAC -> last frame
        self.heard_by: dict[str, str] = {}  # MAC -> bridge that heard the last frame
        self.requests: dict[str, PairRequest] = {}

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.counters = dict(stored.get("counters", {}))
        self.epochs = dict(stored.get("epochs", {}))
        bus = self.hass.bus
        bus.async_listen(EVENT_KEY, self._on_frame)
        bus.async_listen(EVENT_HELLO, self._on_frame)
        bus.async_listen(EVENT_PAIR_REQUEST, self._on_pair_request)
        bus.async_listen(EVENT_BRIDGE_ONLINE, self._on_bridge_online)

    # --- Remotes HA paired (one config entry each) --------------------------

    def paired(self) -> dict[str, str]:
        """MAC -> key (hex) of every paired remote whose entry is set up or setting up."""
        return {
            e.data[CONF_REMOTE]: e.data[CONF_KEY]
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if CONF_REMOTE in e.data and not e.disabled_by
        }

    def awake(self, mac: str) -> bool:
        seen = self.seen.get(mac)
        return seen is not None and dt_util.utcnow() - seen < AWAKE_FOR

    def _data(self) -> dict[str, dict[str, int]]:
        return {"counters": self.counters, "epochs": self.epochs}

    def _save_soon(self) -> None:
        self._store.async_delay_save(self._data, 10)

    def reset_counter(self, mac: str) -> None:
        """A new pairing: its counters start over."""
        self.counters.pop(mac, None)
        self._save_soon()

    # --- Bridges ------------------------------------------------------------

    def bridges(self) -> list[str]:
        """Action prefixes of every bridge (ESPHome devices offering set_remote)."""
        suffix = f"_{ACTION_SET_REMOTE}"
        return sorted(
            name[: -len(suffix)]
            for name in self.hass.services.async_services_for_domain(ESPHOME)
            if name.endswith(suffix)
        )

    async def _call(self, prefix: str, action: str, data: dict[str, Any]) -> bool:
        service = f"{prefix}_{action}"
        if not self.hass.services.has_service(ESPHOME, service):
            _LOGGER.debug("Bridge %s has no %s", prefix, action)
            return False
        try:
            await self.hass.services.async_call(ESPHOME, service, data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("Bridge %s: %s failed: %s", prefix, action, err)
            return False
        return True

    async def _ensure_epoch(self, prefix: str) -> bool:
        """Give a bridge its send epoch for this connection, once, before any key.

        A new epoch is higher than every one before it, and is saved before the bridge
        sees it: a bridge that was erased and reflashed must never get one again.
        """
        async with self._bridge_locks.setdefault(prefix, asyncio.Lock()):
            if prefix in self._epoch_sent:
                return True
            minutes = int((dt_util.utcnow() - EPOCH_BASE).total_seconds() // 60)
            epoch = max(self.epochs.get(prefix, 0) + 1, minutes, 1)
            if epoch > MAX_EPOCH:
                _LOGGER.error("Bridge %s: epochs exhausted", prefix)
                return False
            self.epochs[prefix] = epoch
            await self._store.async_save(self._data())
            if not await self._call(prefix, ACTION_SET_EPOCH, {"epoch": epoch}):
                return False
            self._epoch_sent.add(prefix)
            return True

    async def provision(self, mac: str, key: str, bridges: list[str] | None = None) -> None:
        """Give bridges a remote's key and the counter to accept from."""
        floor = self.counters.get(mac, -1) + 1
        for prefix in self.bridges() if bridges is None else bridges:
            if await self._ensure_epoch(prefix):
                await self._call(prefix, ACTION_SET_REMOTE, {"remote": mac, "key": key, "floor": floor})

    async def bridge_online(self, prefix: str) -> None:
        """A bridge (re)connected: a new epoch, then every paired remote's key."""
        self._epoch_sent.discard(prefix)
        self._ready_shown.discard(prefix)
        if not await self._ensure_epoch(prefix):
            return
        for mac, key in self.paired().items():
            await self.provision(mac, key, [prefix])

    async def forget(self, mac: str) -> None:
        for prefix in self.bridges():
            await self._call(prefix, ACTION_FORGET_REMOTE, {"remote": mac})
        self.counters.pop(mac, None)
        self.seen.pop(mac, None)
        self.heard_by.pop(mac, None)
        self._save_soon()

    async def pair_reply(self, mac: str, remote_public: bytes, ha_public: bytes) -> int:
        """Hand HA's public key to every bridge; returns how many took it."""
        data = {"remote": mac, "remote_public": remote_public.hex(), "ha_public": ha_public.hex()}
        sent = 0
        for prefix in self.bridges():
            sent += await self._call(prefix, ACTION_PAIR_REPLY, data)
        return sent

    async def send_ir(self, mac: str, command: Command) -> None:
        """Send through the bridge that last heard the remote. It must be awake."""
        timings = command.get_raw_timings()
        if not timings or len(timings) > MAX_IR_TIMINGS:
            raise HomeAssistantError(f"IR code has {len(timings)} timings; between 1 and {MAX_IR_TIMINGS} fit")
        if not self.awake(mac) or mac not in self.heard_by:
            raise HomeAssistantError(
                f"Remote {mac} is asleep: it sends IR only while awake (after a key press or being picked up)"
            )
        prefix = bridge_prefix(self.heard_by[mac])
        data = {"remote": mac, "carrier": int(command.modulation), "timings": [int(t) for t in timings]}
        if not await self._call(prefix, ACTION_SEND_IR, data):
            raise HomeAssistantError(f"Bridge {self.heard_by[mac]} couldn't take the IR code")

    # --- Events -------------------------------------------------------------

    @callback
    def _on_frame(self, event: Event) -> None:
        """Key or hello from a remote. Older firmware sends no counter: not ours."""
        data = event.data
        mac, counter, bridge = data.get("remote"), data.get("counter"), data.get("bridge")
        if not isinstance(mac, str) or counter is None or not bridge or mac not in self.paired():
            return
        self.seen[mac] = dt_util.utcnow()
        self.heard_by[mac] = str(bridge)
        try:
            counter = int(counter)
        except (TypeError, ValueError):
            return
        if counter > self.counters.get(mac, -1):
            self.counters[mac] = counter
            self._save_soon()
        async_dispatcher_send(self.hass, SIGNAL_REMOTE_SEEN.format(mac))
        prefix = bridge_prefix(str(bridge))
        if (event.event_type == EVENT_HELLO and prefix not in self._ready_shown
                and not self.hass.services.has_service("script", PACKAGE_RENDERER)):
            # The bridge caches it and answers every hello with it.
            self._ready_shown.add(prefix)
            self.hass.async_create_task(self._call(prefix, "show_status", READY_SCREEN), eager_start=False)

    @callback
    def _on_pair_request(self, event: Event) -> None:
        data = event.data
        mac, bridge = data.get("remote"), data.get("bridge")
        try:
            public_key = bytes.fromhex(str(data.get("public_key", "")))
        except ValueError:
            return
        if not isinstance(mac, str) or len(public_key) != 32:
            return
        now = dt_util.utcnow()
        request = self.requests.get(mac)
        if request is None or request.public_key != public_key:
            # A new request, or the remote started over with a new key pair.
            request = PairRequest(mac, public_key, str(data.get("firmware", "")), now)
        request.seen = now
        if bridge:
            request.bridges[str(bridge)] = int(data.get("rssi", 0) or 0)
        self.requests[mac] = request
        # Anyone can broadcast pairing requests: keep only the newest few.
        while len(self.requests) > MAX_REQUESTS:
            del self.requests[min(self.requests, key=lambda m: self.requests[m].seen)]
        in_progress = any(
            flow["context"].get("unique_id") == mac
            for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        )
        if not in_progress:
            discovery_flow.async_create_flow(
                self.hass, DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data={CONF_REMOTE: mac}
            )

    def fresh_requests(self) -> list[PairRequest]:
        now = dt_util.utcnow()
        return [r for r in self.requests.values() if now - r.seen < PAIR_REQUEST_FRESH]

    @callback
    def _on_bridge_online(self, event: Event) -> None:
        """A bridge (re)connected and has no keys: a new epoch, then every paired remote's."""
        bridge = event.data.get("bridge")
        prefixes = [bridge_prefix(str(bridge))] if bridge else self.bridges()
        for prefix in prefixes:
            self.hass.async_create_task(self.bridge_online(prefix), eager_start=False)
