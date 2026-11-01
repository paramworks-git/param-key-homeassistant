"""PARAM KEY: physical remotes for Home Assistant.

Each paired remote is a config entry and a device, with an infrared emitter
entity (HA's infrared platform), so HA's IR integrations can use it too. The
hub (hub.py) talks to the bridges: pairing, the remotes' keys, and IR.

Still in the package (packages/param_key.yaml) for now: the key map, the
screen renderer and the battery sensors.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.infrared import async_send_command
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CONF_BRIDGE, CONF_KEY, CONF_REMOTE, DOMAIN
from .hub import async_get_hub, bridge_prefix
from .ir import PROTOCOLS, build_command, code_int

PLATFORMS = ["infrared"]
SERVICE_SEND_IR = "send_ir"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_IR_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_REMOTE): vol.Any(cv.string, [cv.string]),
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("protocol"): vol.All(vol.Lower, vol.In(PROTOCOLS)),
        vol.Optional("address"): code_int,
        vol.Optional("command"): code_int,
        vol.Optional("address_bits"): vol.All(vol.Coerce(int), vol.In([5, 8, 13])),
        vol.Optional("toggle"): vol.All(vol.Coerce(int), vol.In([0, 1])),
        vol.Optional("repeat"): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
        vol.Optional("pronto"): cv.string,
        vol.Optional("timings"): [vol.Coerce(int)],
        vol.Optional("carrier"): vol.All(vol.Coerce(int), vol.Range(min=0, max=500000)),
    }
)


def emitter_unique_id(mac: str) -> str:
    return f"{mac}_ir"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await async_get_hub(hass)

    async def send_ir(call: ServiceCall) -> None:
        data: dict[str, Any] = dict(call.data)
        try:
            command = build_command(data)
        except vol.Invalid as err:
            raise ServiceValidationError(str(err)) from err
        macs = data.get(CONF_REMOTE) or []
        macs = [macs] if isinstance(macs, str) else list(macs)
        registry = er.async_get(hass)
        entity_ids = [
            registry.async_get_entity_id("infrared", DOMAIN, emitter_unique_id(mac.upper())) or mac for mac in macs
        ]
        entity_ids += data.get(ATTR_ENTITY_ID, [])
        if not entity_ids:
            raise ServiceValidationError("Give a remote (MAC) or an entity_id")
        for entity_id in entity_ids:
            entry = registry.async_get(entity_id)
            if entry is None or entry.platform != DOMAIN:
                raise ServiceValidationError(f"{entity_id} isn't a paired PARAM KEY remote")
            await async_send_command(hass, entity_id, command, context=call.context)

    hass.services.async_register(DOMAIN, SERVICE_SEND_IR, send_ir, schema=SEND_IR_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = await async_get_hub(hass)
    if CONF_BRIDGE in entry.data:
        # A bridge: give it the remotes' keys now, in case it connected before it was
        # allowed to tell HA so. Remotes asking to pair are noticed from here on.
        prefix = bridge_prefix(entry.data[CONF_BRIDGE])
        for mac, key in hub.paired().items():
            await hub.provision(mac, key, [prefix])
        return True
    await hub.provision(entry.data[CONF_REMOTE], entry.data[CONF_KEY])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if CONF_BRIDGE in entry.data:
        return True
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removed in HA: take its key off the bridges, so it can do nothing more."""
    if CONF_REMOTE in entry.data:
        hub = await async_get_hub(hass)
        await hub.forget(entry.data[CONF_REMOTE])
