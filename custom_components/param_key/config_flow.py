"""Pairing a remote (docs/protocol.md in param-key, "Pairing").

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

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from . import crypto
from .const import CONF_FIRMWARE, CONF_KEY, CONF_REMOTE, DOMAIN
from .hub import async_get_hub


class ParamKeyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._mac: str | None = None
        self._pairing: crypto.Pairing | None = None
        self._firmware = ""

    async def async_step_integration_discovery(self, discovery_info: dict[str, Any]) -> ConfigFlowResult:
        self._mac = discovery_info[CONF_REMOTE]
        await self.async_set_unique_id(self._mac)
        self.context["title_placeholders"] = {"name": self._mac}
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
