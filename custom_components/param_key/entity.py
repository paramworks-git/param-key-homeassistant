"""What every entity of a remote shares: its device."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_FIRMWARE, CONF_REMOTE, DOMAIN


def remote_device(entry: ConfigEntry) -> DeviceInfo:
    mac: str = entry.data[CONF_REMOTE]
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, mac.lower())},
        name="PARAM KEY remote",
        manufacturer="PARAM KEY",
        model="Remote",
        sw_version=entry.data.get(CONF_FIRMWARE) or None,
    )
