"""PARAM KEY: physical remotes for Home Assistant.

Skeleton. To come: each remote as a device holding its entities (battery, signal, mode, keys as an
event entity), the key dispatcher and screen renderer from packages/openhomeremote.yaml in Python,
and a configurator panel.
"""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

DOMAIN = "param_key"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True
