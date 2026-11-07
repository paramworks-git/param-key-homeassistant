"""Each remote's battery, from its hello (sent on every wake and every 60 s while awake), and the
signal of its last frame at the bridge that heard it."""

from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_REMOTE, SIGNAL_REMOTE_SEEN, SIGNAL_REMOTE_STATUS
from .entity import remote_device
from .hub import Hub, async_get_hub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = await async_get_hub(hass)
    async_add_entities([RemoteBattery(hub, entry), RemoteSignal(hub, entry)])


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


class RemoteSignal(RestoreSensor):
    """The last frame's RSSI at the bridge that heard it (attribute bridge). Keeps it while the remote sleeps."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._mac: str = entry.data[CONF_REMOTE]
        self._attr_unique_id = f"{self._mac}_signal"
        self._attr_device_info = remote_device(entry)
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
        if (state := await self.async_get_last_state()) is not None and "bridge" in state.attributes:
            self._attr_extra_state_attributes = {"bridge": state.attributes["bridge"]}
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE_SEEN.format(self._mac), self._changed)
        )

    @callback
    def _changed(self) -> None:
        rssi, bridge = self._hub.rssi.get(self._mac), self._hub.heard_by.get(self._mac)
        if rssi is None:
            return
        attrs = {"bridge": bridge} if bridge else {}
        if rssi != self._attr_native_value or attrs != self._attr_extra_state_attributes:
            self._attr_native_value = rssi
            self._attr_extra_state_attributes = attrs
            self.async_write_ha_state()
