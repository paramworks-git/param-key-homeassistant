"""Each remote's settings: brightness, and how long until the screen goes off and it sleeps.

The remote's settings screen (the settings key) changes the same values, and automations
can too (dimmer at night). The bridge passes them on at once and on every wake.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_REMOTE, SIGNAL_REMOTE_STATUS
from .entity import remote_device
from .hub import Hub, async_get_hub
from .screen import SETTINGS

ICONS = {"brightness": "mdi:brightness-6", "screen_off": "mdi:monitor-off", "sleep": "mdi:sleep"}
UNITS = {"brightness": PERCENTAGE, "screen_off": UnitOfTime.SECONDS, "sleep": UnitOfTime.SECONDS}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = await async_get_hub(hass)
    async_add_entities(RemoteSetting(hub, entry, key) for key in SETTINGS)


class RemoteSetting(NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hub: Hub, entry: ConfigEntry, key: str) -> None:
        self._hub = hub
        self._mac: str = entry.data[CONF_REMOTE]
        self._key = key
        label, _, lo, hi, step, _ = SETTINGS[key]
        self._attr_name = label
        self._attr_unique_id = f"{self._mac}_{key}"
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = lo, hi, step
        self._attr_native_unit_of_measurement = UNITS[key]
        self._attr_icon = ICONS[key]
        self._attr_device_info = remote_device(entry)

    @property
    def native_value(self) -> float:
        return self._hub.remote_settings(self._mac)[self._key]

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.set_setting(self._mac, self._key, int(value))

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE_STATUS.format(self._mac), self._changed)
        )

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
