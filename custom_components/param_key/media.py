"""Media mode: the remote as a TV/AVR/streamer remote, set up on the remote's Configure page.

The setup (MediaConfig) names a volume device (AVR or TV), a navigation device for the
arrows, OK, back and home, the players to show and control, the sources for the Source menu,
up to four hotkeys, and what the power key switches. Each part is optional.

Navigation goes through each platform's own commands: a `remote` entity's send_command for
Android TV Remote, Apple TV, Roku and Samsung, and webostv.button for an LG TV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

Action = tuple[str, str, dict[str, Any]]

NAV_KEYS = ("up", "down", "left", "right", "ok", "back", "home")
# Platform -> how to send a navigation key: (domain, service, data field, {key: command}).
NAV_PLATFORMS: dict[str, tuple[str, str, str, dict[str, str]]] = {
    "androidtv_remote": ("remote", "send_command", "command", {
        "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
        "ok": "DPAD_CENTER", "back": "BACK", "home": "HOME"}),
    "apple_tv": ("remote", "send_command", "command", {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "ok": "select", "back": "menu", "home": "home"}),
    "roku": ("remote", "send_command", "command", {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "ok": "select", "back": "back", "home": "home"}),
    "samsungtv": ("remote", "send_command", "command", {
        "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
        "ok": "KEY_ENTER", "back": "KEY_RETURN", "home": "KEY_HOME"}),
    "webostv": ("webostv", "button", "button", {
        "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
        "ok": "ENTER", "back": "BACK", "home": "HOME"}),
}
NAV_NAMES = "Android TV Remote, Apple TV, Roku, Samsung Smart TV or LG webOS"
OFF_STATES = ("off", "standby", STATE_UNAVAILABLE, STATE_UNKNOWN)


@dataclass
class MediaConfig:
    volume: str | None = None
    navigation: str | None = None
    players: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    hotkeys: list[str] = field(default_factory=list)
    power: list[str] = field(default_factory=list)

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> MediaConfig | None:
        media = options.get("media") or {}
        config = cls(
            volume=media.get("volume") or None,
            navigation=media.get("navigation") or None,
            players=list(media.get("players") or []),
            sources=list(media.get("sources") or []),
            hotkeys=list(media.get("hotkeys") or [])[:4],
            power=list(media.get("power") or []),
        )
        return config if (config.volume or config.navigation or config.players) else None

    def entities(self) -> list[str]:
        return [e for e in [self.volume, self.navigation, *self.players, *self.hotkeys, *self.power] if e]


def nav_platform(hass: HomeAssistant, entity_id: str) -> str | None:
    """The navigation device's platform, if PARAM KEY knows its commands."""
    entry = er.async_get(hass).async_get(entity_id)
    platform = entry.platform if entry else None
    if platform == "webostv" and not entity_id.startswith("media_player."):
        return None
    if platform != "webostv" and not entity_id.startswith("remote."):
        return None
    return platform if platform in NAV_PLATFORMS else None


def nav_action(hass: HomeAssistant, entity_id: str, key: str) -> Action | None:
    platform = nav_platform(hass, entity_id)
    if platform is None:
        return None
    domain, service, data_field, commands = NAV_PLATFORMS[platform]
    return (domain, service, {"entity_id": entity_id, data_field: commands[key]})


def active_player(hass: HomeAssistant, config: MediaConfig) -> State | None:
    """The player to show and control: playing, else paused, else on, else none."""
    states = [s for e in config.players if (s := hass.states.get(e))]
    for wanted in (("playing",), ("paused",)):
        if found := next((s for s in states if s.state in wanted), None):
            return found
    return next((s for s in states if s.state not in OFF_STATES), None)


def media_status(hass: HomeAssistant, config: MediaConfig) -> dict[str, Any]:
    """The Media screen (packet S): what's playing, and the volume as the bar."""
    player = active_player(hass, config)
    volume = hass.states.get(config.volume) if config.volume else None
    if player is None:
        title, subtitle = "Off", "Hold home for Devices"
        if volume is not None and volume.state not in OFF_STATES:
            title = volume.attributes.get("source") or volume.name
            subtitle = ""
    else:
        a = player.attributes
        title = a.get("media_title") or a.get("app_name") or a.get("source") or player.name
        subtitle = a.get("media_artist") or a.get("media_series_title") or (a.get("app_name") if a.get("media_title") else "") or ""
    value, label = -1, ""
    if volume is not None and volume.state not in OFF_STATES:
        level = volume.attributes.get("volume_level")
        if isinstance(level, (int, float)):
            value = round(level * 100)
            label = "Muted" if volume.attributes.get("is_volume_muted") else f"Vol {value}"
    return {"mode": "Media", "title": str(title), "subtitle": str(subtitle), "value": value, "value_label": label}


def global_key(hass: HomeAssistant, config: MediaConfig, key: str, event: str) -> Action | None:
    """Volume and the media keys: the same in every mode."""
    if key in ("vol_up", "vol_down") and event in ("press", "repeat") and config.volume:
        return ("media_player", "volume_up" if key == "vol_up" else "volume_down", {"entity_id": config.volume})
    if key == "mute" and event == "press" and config.volume:
        state = hass.states.get(config.volume)
        muted = bool(state and state.attributes.get("is_volume_muted"))
        return ("media_player", "volume_mute", {"entity_id": config.volume, "is_volume_muted": not muted})
    transport = {"play": "media_play_pause", "rew": "media_previous_track", "fwd": "media_next_track",
                 "stop": "media_stop"}
    if key in transport and event == "press":
        player = active_player(hass, config)
        target = player.entity_id if player else (config.players[0] if config.players else None)
        if target:
            return ("media_player", transport[key], {"entity_id": target})
    return None


def power_actions(hass: HomeAssistant, config: MediaConfig) -> list[Action]:
    """Power: everything set up for it off if any is on, else on."""
    states = [s for e in config.power if (s := hass.states.get(e))]
    turn = "turn_off" if any(s.state not in OFF_STATES for s in states) else "turn_on"
    return [(s.domain, turn, {"entity_id": s.entity_id}) for s in states]


def source_rows(hass: HomeAssistant, config: MediaConfig) -> tuple[list[str], int]:
    """The Source menu: the chosen sources, the current one marked."""
    state = hass.states.get(config.volume) if config.volume else None
    current = state.attributes.get("source") if state else None
    rows = [("• " if s == current else "") + s for s in config.sources]
    at = config.sources.index(current) if current in config.sources else 0
    return rows, at
