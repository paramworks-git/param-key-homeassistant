"""Each remote's charging state, from its hello."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_REMOTE, SIGNAL_REMOTE_STATUS
from .entity import remote_device
from .hub import Hub, async_get_hub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RemoteCharging(await async_get_hub(hass), entry)])


class RemoteCharging(BinarySensorEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._mac: str = entry.data[CONF_REMOTE]
        self._attr_unique_id = f"{self._mac}_charging"
        self._attr_device_info = remote_device(entry)

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE_STATUS.format(self._mac), self._changed)
        )

    @callback
    def _changed(self) -> None:
        status = self._hub.status.get(self._mac)
        if status is not None and status["charging"] != self._attr_is_on:
            self._attr_is_on = status["charging"]
            self.async_write_ha_state()
