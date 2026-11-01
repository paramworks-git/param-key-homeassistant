"""Setting up bridges and pairing remotes (docs/protocol.md in param-key).

A bridge is found by its ESPHome project over mDNS and becomes a bridge entry once its ESPHome
device is added; the entry keeps the integration loaded so remotes asking to pair are noticed.
The bridge's events (esphome.*) reach HA without ESPHome's "Allow the device to perform Home
Assistant actions", which stays off: the bridge never calls HA actions.

A remote asking to pair shows up as discovered (the hub starts the flow), or
the user adds the integration while the remote asks. HA answers through the
bridges with its public key; both ends then show the same 6-digit code, and
confirming it here makes the remote a config entry, whose setup gives the
bridges its key. A remote already paired can pair again; its entry keeps its
place and takes the new key.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import crypto
from .const import (
    CONF_BRIDGE,
    CONF_FIRMWARE,
    CONF_KEY,
    CONF_MAC,
    CONF_REMOTE,
    DOMAIN,
)
from .hub import async_get_hub
from .media import nav_platform


class ParamKeyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MediaSetupFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Remotes have a Media setup; bridges have nothing to configure."""
        return CONF_REMOTE in config_entry.data

    def __init__(self) -> None:
        self._mac: str | None = None
        self._pairing: crypto.Pairing | None = None
        self._firmware = ""
        self._bridge = ""
        self._bridge_mac = ""

    # --- Bridges -------------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        mac = discovery_info.properties.get("mac")
        if not mac:
            return self.async_abort(reason="not_a_bridge")
        self._bridge_mac = format_mac(mac)
        self._bridge = discovery_info.hostname.removesuffix(".").removesuffix(".local")
        await self.async_set_unique_id(f"bridge-{self._bridge_mac}")
        self._abort_if_unique_id_configured(updates={CONF_BRIDGE: self._bridge})
        self.context["title_placeholders"] = {"name": self._bridge}
        return await self.async_step_bridge()

    async def async_step_bridge(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._esphome_entry() is None:
                errors["base"] = "esphome_first"
            else:
                return self.async_create_entry(
                    title=f"Bridge {self._bridge}", data={CONF_BRIDGE: self._bridge, CONF_MAC: self._bridge_mac}
                )
        return self.async_show_form(step_id="bridge", description_placeholders={"name": self._bridge}, errors=errors)

    def _esphome_entry(self) -> ConfigEntry | None:
        for entry in self.hass.config_entries.async_entries("esphome"):
            if entry.unique_id and format_mac(entry.unique_id) == self._bridge_mac:
                return entry
        return None

    # --- Remotes -------------------------------------------------------------

    async def async_step_integration_discovery(self, discovery_info: dict[str, Any]) -> ConfigFlowResult:
        self._mac = discovery_info[CONF_REMOTE]
        await self.async_set_unique_id(self._mac)
        self.context["title_placeholders"] = {"name": f"remote {self._mac}"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="discovery_confirm",
                description_placeholders={"mac": self._mac, "again": self._again_note()},
            )
        return await self.async_step_pair()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = await async_get_hub(self.hass)
        waiting = {r.mac: r for r in hub.fresh_requests()}
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = user_input.get(CONF_REMOTE) or next(iter(waiting), None)
            if mac in waiting:
                self._mac = mac
                await self.async_set_unique_id(mac, raise_on_progress=False)
                return await self.async_step_pair()
            errors["base"] = "no_remote"
        schema = vol.Schema({vol.Required(CONF_REMOTE): vol.In(sorted(waiting))}) if len(waiting) > 1 else None
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._mac is not None
        hub = await async_get_hub(self.hass)
        if user_input is None:
            request = hub.requests.get(self._mac)
            if request is None or request not in hub.fresh_requests():
                return self.async_abort(reason="request_expired")
            try:
                self._pairing = crypto.pair(request.public_key, self._mac)
            except ValueError:
                return self.async_abort(reason="bad_key")
            self._firmware = request.firmware
            if not await hub.pair_reply(self._mac, request.public_key, self._pairing.ha_public):
                return self.async_abort(reason="no_bridge")
            return self.async_show_form(
                step_id="pair", description_placeholders={"code": crypto.format_code(self._pairing.code)}
            )
        assert self._pairing is not None
        hub.reset_counter(self._mac)
        data = {CONF_REMOTE: self._mac, CONF_KEY: self._pairing.key.hex(), CONF_FIRMWARE: self._firmware}
        if entry := self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, self._mac):
            return self.async_update_reload_and_abort(entry, data=data, reason="paired_again")
        return self.async_create_entry(title=f"PARAM KEY remote {self._mac}", data=data)

    def _again_note(self) -> str:
        if self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, self._mac or ""):
            return " It is already paired; pairing it again gives it a new key."
        return ""


class MediaSetupFlow(OptionsFlow):
    """A remote's Media setup (media.py): what volume, arrows, players and power control."""

    def __init__(self) -> None:
        self._media: dict[str, Any] = {}
        self._home = "devices"

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options.get("media") or {})
        if user_input is not None:
            nav = user_input.get("navigation")
            if nav and nav_platform(self.hass, nav) is None:
                errors["navigation"] = "nav_unsupported"
            if len(user_input.get("hotkeys") or []) > 4:
                errors["hotkeys"] = "too_many_hotkeys"
            if not errors:
                self._home = user_input.get("start", "devices")
                self._media = {k: user_input.get(k) or ([] if k in ("players", "hotkeys", "power") else None)
                               for k in ("volume", "navigation", "players", "hotkeys", "power")}
                self._media["sources"] = current.get("sources") or []
                if self._source_list():
                    return await self.async_step_sources()
                return self._save()
        schema = vol.Schema({
            vol.Optional("volume"): EntitySelector(EntitySelectorConfig(domain="media_player")),
            vol.Optional("navigation"): EntitySelector(EntitySelectorConfig(domain=["remote", "media_player"])),
            vol.Optional("players"): EntitySelector(EntitySelectorConfig(domain="media_player", multiple=True)),
            vol.Optional("hotkeys"): EntitySelector(
                EntitySelectorConfig(domain=["scene", "script", "button", "input_button"], multiple=True)),
            vol.Optional("power"): EntitySelector(
                EntitySelectorConfig(domain=["media_player", "remote", "switch", "light"], multiple=True)),
            vol.Required("start", default=self.config_entry.options.get("start", "devices")): SelectSelector(
                SelectSelectorConfig(options=["devices", "media"], translation_key="start",
                                     mode=SelectSelectorMode.LIST)),
        })
        suggested = {k: v for k, v in current.items() if v}
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(schema, user_input or suggested),
            errors=errors,
        )

    def _source_list(self) -> list[str]:
        state = self.hass.states.get(self._media.get("volume") or "")
        return list(state.attributes.get("source_list") or []) if state else []

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        available = self._source_list()
        if user_input is not None:
            chosen = user_input.get("sources") or []
            self._media["sources"] = [s for s in available if s in chosen]
            return self._save()
        previous = [s for s in self._media.get("sources", []) if s in available]
        schema = vol.Schema({
            vol.Optional("sources", default=previous or available): SelectSelector(
                SelectSelectorConfig(options=available, multiple=True, mode=SelectSelectorMode.LIST)),
        })
        return self.async_show_form(step_id="sources", data_schema=schema)

    def _save(self) -> ConfigFlowResult:
        return self.async_create_entry(data={"media": self._media, "start": self._home})
