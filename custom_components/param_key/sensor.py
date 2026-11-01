"""Each remote's battery, from its hello (sent on every wake and every 60 s while awake)."""

from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_REMOTE, SIGNAL_REMOTE_STATUS
from .entity import remote_device
from .hub import Hub, async_get_hub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RemoteBattery(await async_get_hub(hass), entry)])


class RemoteBattery(RestoreSensor):
    """Keeps the last reading while the remote sleeps."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._mac: str = entry.data[CONF_REMOTE]
        self._attr_unique_id = f"{self._mac}_battery"
        self._attr_device_info = remote_device(entry)

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE_STATUS.format(self._mac), self._changed)
        )

    @callback
    def _changed(self) -> None:
        battery = self._hub.status.get(self._mac, {}).get("battery")
        if battery is not None and battery != self._attr_native_value:
            self._attr_native_value = battery
            self.async_write_ha_state()
