"""Each remote's IR LED as an infrared emitter (HA's infrared platform)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.infrared import InfraredCommand, InfraredEmitterEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import emitter_unique_id
from .const import CONF_REMOTE, SIGNAL_REMOTE_SEEN
from .entity import remote_device
from .hub import Hub, async_get_hub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RemoteEmitter(await async_get_hub(hass), entry)])


class RemoteEmitter(InfraredEmitterEntity):
    """Available while the remote is awake: asleep, it can't hear the bridge."""

    _attr_has_entity_name = True
    _attr_name = "IR"

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._mac: str = entry.data[CONF_REMOTE]
        self._attr_unique_id = emitter_unique_id(self._mac)
        self._attr_device_info = remote_device(entry)
        self._was_available = False

    @property
    def available(self) -> bool:
        return self._hub.awake(self._mac)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE_SEEN.format(self._mac), self._refresh)
        )
        # Notice the remote falling asleep.
        self.async_on_remove(async_track_time_interval(self.hass, self._refresh, timedelta(seconds=10)))

    @callback
    def _refresh(self, *_: object) -> None:
        if self.available != self._was_available:
            self._was_available = self.available
            self.async_write_ha_state()

    async def async_send_command(self, command: InfraredCommand) -> None:
        await self._hub.send_ir(self._mac, command)
