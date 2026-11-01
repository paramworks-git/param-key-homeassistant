"""One remote's screen: its mode (Devices or Media), where it is, and what keys do there.

The screen is a list (docs/protocol.md, packet L): six rows, HA sends the visible window.
Rows read "state  name", so the state survives when a long name is cut short.

Keys (docs/protocol.md, "Key events"): ▲▼ ◀▶ act on press and repeat, OK and back on press,
home, settings, power and the hotkeys on tap; a hotkey held shows what it does, and home held
picks the mode. With Media set up, volume and the media keys work in every mode (media.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from . import media as m
from .media import MediaConfig
from .menu import ROWS, RUN_DOMAINS, Menu

SYMBOL_RIGHT = ""  # LVGL's built-in symbol font, in the remote's Montserrat
SYMBOL_PLAY = ""
TOGGLE_DOMAINS = ("light", "switch", "input_boolean", "fan", "cover", "automation", "siren")
DETAIL_DOMAINS = ("climate", "humidifier")
STEP_PCT = 10
MESSAGE_SECONDS = 3
MODES = ["devices", "media"]
MODE_LABELS = {"devices": "Devices", "media": "Media"}

# Remote settings (number entities, docs in README): key -> (label, unit, min, max, step, default).
SETTINGS: dict[str, tuple[str, str, int, int, int, int]] = {
    "brightness": ("Brightness", "%", 10, 100, 10, 80),
    "screen_off": ("Screen off after", " s", 5, 60, 5, 15),
    "sleep": ("Sleep after", " s", 10, 300, 10, 30),
}

Call = tuple[str, dict[str, Any]]  # a bridge action and its data


@dataclass
class Level:
    key: str
    cursor: int = 0


@dataclass
class Effects:
    """What a key did: HA actions to run, a message to show, and whether to redraw."""

    actions: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # domain, service, data
    message: str = ""
    redraw: bool = False
    settings: dict[str, int] = field(default_factory=dict)  # changed remote settings


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    return f"{value:g}"


def short_name(state: State | None, entity_id: str, area: str) -> str:
    name = state.name if state else entity_id
    if area and name.casefold().startswith(area.casefold() + " ") and len(name) > len(area) + 1:
        rest = name[len(area) + 1 :]
        name = rest[0].upper() + rest[1:]
    return name


def tag(state: State | None) -> str:
    """The state part of a row: short, so the name keeps the rest of the line."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return "--"
    domain, s, a = state.domain, state.state, state.attributes
    if domain in RUN_DOMAINS:
        return SYMBOL_PLAY
    if domain == "light" and s == "on" and (b := _num(a.get("brightness"))) is not None:
        return f"{round(b / 2.55)}%"
    if domain == "fan" and s == "on" and (p := _num(a.get("percentage"))) is not None:
        return f"{round(p)}%"
    if domain == "cover" and s not in ("closed",) and (p := _num(a.get("current_position"))) is not None:
        return f"{round(p)}%"
    if domain == "humidifier" and s == "on" and (h := _num(a.get("humidity"))) is not None:
        return f"{round(h)}%"
    if domain == "climate" and s != "off":
        if (t := _num(a.get("temperature"))) is not None:
            return f"{_fmt(t)}°"
        lo, hi = _num(a.get("target_temp_low")), _num(a.get("target_temp_high"))
        if lo is not None and hi is not None:
            return f"{_fmt(lo)}-{_fmt(hi)}°"
        return s.replace("_", "/")
    if domain in ("sensor", "number", "input_number") and (v := _num(s)) is not None:
        return f"{_fmt(round(v, 1))}{a.get(ATTR_UNIT_OF_MEASUREMENT) or ''}"
    return s.replace("_", " ")


class RemoteScreen:
    def __init__(self, hass: HomeAssistant, mode: str = "devices") -> None:
        self.hass = hass
        self.mode = mode if mode in MODES else "devices"
        self.levels: list[Level] = [Level("root")]
        self.detail: str | None = None
        self.detail_cursor = 0
        self.settings_open = False
        self.settings_cursor = 0
        self.picker_open = False
        self.picker_cursor = 0
        self.sources_open = False
        self.sources_cursor = 0

    def in_media(self, media: MediaConfig | None) -> bool:
        return self.mode == "media" and media is not None

    # --- Where the remote is -------------------------------------------------

    def path(self, root: Menu) -> list[Menu]:
        """The menus from the root down to the current one; drops levels that are gone."""
        menus = [root]
        for i, level in enumerate(self.levels[1:], start=1):
            sub = menus[-1].find(level.key)
            if sub is None:
                del self.levels[i:]
                break
            menus.append(sub)
        for menu, level in zip(menus, self.levels):
            level.cursor = max(0, min(level.cursor, len(menu.items) - 1))
        return menus

    def menu(self, root: Menu) -> Menu:
        return self.path(root)[-1]

    def selected(self, root: Menu) -> Menu | str | None:
        menu = self.menu(root)
        return menu.items[self.levels[-1].cursor] if menu.items else None

    def visible_entities(self, root: Menu, media: MediaConfig | None = None) -> list[str]:
        if self.in_media(media):
            return [e for e in [media.volume, *media.players, *media.hotkeys] if e]
        if self.detail:
            return [self.detail]
        if self.settings_open:
            return []
        menu = self.menu(root)
        return [e for e in menu.items if isinstance(e, str)] + menu.hotkeys

    def home(self) -> None:
        self.levels = [Level("root")]
        self.detail = None
        self.settings_open = False
        self.sources_open = False

    # --- Drawing -------------------------------------------------------------

    def render(self, root: Menu, settings: dict[str, int], media: MediaConfig | None = None) -> list[Call]:
        if self.picker_open:
            labels = [MODE_LABELS[k] for k in MODES]
            return [_list("Mode", "Choose a mode", labels, self.picker_cursor), _hotkeys([])]
        if self.settings_open:
            rows = [f"{label}  {settings[k]}{unit}" for k, (label, unit, *_) in SETTINGS.items()]
            return [_list("Settings", "Remote settings", rows, self.settings_cursor), _hotkeys([])]
        if self.in_media(media):
            if self.sources_open:
                rows, _ = m.source_rows(self.hass, media)
                return [_list("Media", "Source", rows, self.sources_cursor), _hotkeys([])]
            labels = [short_name(self.hass.states.get(e), e, "") for e in media.hotkeys]
            return [("show_status", m.media_status(self.hass, media)), _hotkeys(labels)]
        if self.detail:
            state = self.hass.states.get(self.detail)
            rows = detail_rows(state)
            self.detail_cursor = max(0, min(self.detail_cursor, len(rows) - 1))
            title = short_name(state, self.detail, self.menu(root).area)
            if reading := current_reading(state):
                title += f"  {reading}"
            items = [f"{r['label']}  {r['value']}" for r in rows] or ["Unavailable, back to close"]
            return [_list("Devices", title, items, self.detail_cursor if rows else -1), _hotkeys([])]
        menu = self.menu(root)
        if not root.items and not root.hotkeys:
            return [_list("Devices", "No devices", ['Label entities "remote"'], -1), _hotkeys([])]
        rows = []
        for item in menu.items:
            if isinstance(item, Menu):
                rows.append(f"{SYMBOL_RIGHT}  {item.title}")
            else:
                state = self.hass.states.get(item)
                rows.append(f"{tag(state)}  {short_name(state, item, menu.area)}")
        title = menu.title if menu is not root else "Devices"
        if not rows:
            rows, cursor = ["Hotkeys below"], -1
        else:
            cursor = self.levels[-1].cursor
        labels = [short_name(self.hass.states.get(e), e, menu.area) for e in menu.hotkeys]
        return [_list("Devices", title, rows, cursor), _hotkeys(labels)]

    # --- Keys ----------------------------------------------------------------

    def key(self, root: Menu, key: str, event: str, settings: dict[str, int],
            media: MediaConfig | None = None) -> Effects:
        fx = Effects()
        moving = event in ("press", "repeat")
        if media is not None and (action := m.global_key(self.hass, media, key, event)):
            fx.actions.append(action)
            return fx
        if key == "home" and event == "hold":
            if media is None:
                fx.message = "Set up Media on the remote's page in Home Assistant"
            else:
                self.picker_open = not self.picker_open
                self.picker_cursor = MODES.index(self.mode)
                fx.redraw = True
            return fx
        if self.picker_open:
            if key in ("up", "down") and moving:
                self.picker_cursor = max(0, min(self.picker_cursor + (1 if key == "down" else -1), len(MODES) - 1))
                fx.redraw = True
            elif key == "ok" and event == "press":
                self.mode = MODES[self.picker_cursor]
                self.picker_open = False
                self.home()
                fx.redraw = True
            elif (key == "back" and event == "press") or (key == "home" and event == "tap"):
                self.picker_open = False
                fx.redraw = True
            return fx
        if self.in_media(media) and not self.settings_open:
            self._media_key(media, key, event, moving, fx)
            return fx
        if key == "home" and event == "tap":
            self.home()
            fx.redraw = True
        elif key == "settings" and event == "tap":
            self.settings_open = not self.settings_open
            self.detail = None
            fx.redraw = True
        elif self.settings_open:
            self._settings_key(key, event, moving, settings, fx)
        elif self.detail:
            self._detail_key(key, moving, event, fx)
        else:
            self._menu_key(root, key, event, moving, fx)
        return fx

    def _media_key(self, media: MediaConfig, key: str, event: str, moving: bool, fx: Effects) -> None:
        if key == "settings" and event == "tap":
            if media.sources and media.volume:
                self.sources_open = not self.sources_open
                self.sources_cursor = m.source_rows(self.hass, media)[1]
            else:
                self.settings_open = True
            fx.redraw = True
        elif self.sources_open:
            if key in ("up", "down") and moving:
                self.sources_cursor = max(0, min(self.sources_cursor + (1 if key == "down" else -1),
                                                 len(media.sources) - 1))
                fx.redraw = True
            elif key == "ok" and event == "press" and media.sources:
                fx.actions.append(("media_player", "select_source",
                                   {"entity_id": media.volume, "source": media.sources[self.sources_cursor]}))
                self.sources_open = False
                fx.redraw = True
            elif key == "back" and event == "press":
                self.sources_open = False
                fx.redraw = True
        elif key in m.NAV_KEYS and media.navigation and (
            (key in ("up", "down", "left", "right") and moving)
            or (key in ("ok", "back") and event == "press")
            or (key == "home" and event == "tap")
        ):
            if action := m.nav_action(self.hass, media.navigation, key):
                fx.actions.append(action)
        elif key in ("h1", "h2", "h3", "h4"):
            i = int(key[1]) - 1
            target = media.hotkeys[i] if i < len(media.hotkeys) else None
            if event == "tap" and target:
                self._activate(target, fx)
            elif event == "hold":
                state = self.hass.states.get(target) if target else None
                fx.message = f"Runs {short_name(state, target, '')}" if target else "No hotkey here"
        elif key == "power" and event == "tap":
            actions = m.power_actions(self.hass, media)
            fx.actions += actions
            if not actions:
                fx.message = "Choose what power switches on the remote's page in HA"

    def _settings_key(self, key: str, event: str, moving: bool, settings: dict[str, int], fx: Effects) -> None:
        if key in ("up", "down") and moving:
            self.settings_cursor = max(0, min(self.settings_cursor + (1 if key == "down" else -1), len(SETTINGS) - 1))
            fx.redraw = True
        elif key in ("left", "right") and moving:
            k = list(SETTINGS)[self.settings_cursor]
            _, _, lo, hi, step, _ = SETTINGS[k]
            value = max(lo, min(hi, settings[k] + (step if key == "right" else -step)))
            if value != settings[k]:
                fx.settings[k] = value
            fx.redraw = True
        elif key == "back" and event == "press":
            self.settings_open = False
            fx.redraw = True

    def _menu_key(self, root: Menu, key: str, event: str, moving: bool, fx: Effects) -> None:
        menu = self.menu(root)
        level = self.levels[-1]
        item = self.selected(root)
        if key in ("up", "down") and moving:
            if menu.items:
                level.cursor = max(0, min(level.cursor + (1 if key == "down" else -1), len(menu.items) - 1))
                fx.redraw = True
        elif key == "back" and event == "press":
            if len(self.levels) > 1:
                self.levels.pop()
                fx.redraw = True
        elif key in ("ok", "right") and event == "press" and isinstance(item, Menu):
            self.levels.append(Level(item.key))
            fx.redraw = True
        elif key in ("ok", "right") and event == "press" and isinstance(item, str) and item.split(".")[0] in DETAIL_DOMAINS:
            self.detail, self.detail_cursor = item, 0
            fx.redraw = True
        elif key == "ok" and event == "press" and isinstance(item, str):
            self._activate(item, fx)
        elif key in ("left", "right") and moving and isinstance(item, str):
            self._step(item, key == "right", fx)
        elif key in ("h1", "h2", "h3", "h4"):
            i = int(key[1]) - 1
            target = menu.hotkeys[i] if i < len(menu.hotkeys) else None
            if event == "tap" and target:
                self._activate(target, fx)
            elif event == "hold":
                state = self.hass.states.get(target) if target else None
                fx.message = f"Runs {short_name(state, target, '')}" if target else "No hotkey here"
        elif key == "power" and event == "tap":
            lights = [e for e in menu.entities() if e.startswith("light.")]
            lit = [e for e in lights if (s := self.hass.states.get(e)) and s.state == "on"]
            if lit:
                fx.actions.append(("light", "turn_off", {"entity_id": lit}))
            fx.message = "Lights off" if lit else "No lights on here"

    def _activate(self, entity_id: str, fx: Effects) -> None:
        domain = entity_id.split(".", 1)[0]
        target = {"entity_id": entity_id}
        if domain in ("scene", "script"):
            fx.actions.append((domain, "turn_on", target))
        elif domain in ("button", "input_button"):
            fx.actions.append((domain, "press", target))
        elif domain in TOGGLE_DOMAINS:
            fx.actions.append(("homeassistant", "toggle", target))
        elif domain == "media_player":
            fx.actions.append(("media_player", "media_play_pause", target))
        else:
            fx.message = "Change this in Home Assistant"

    def _step(self, entity_id: str, up: bool, fx: Effects) -> None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return
        domain, a = state.domain, state.attributes
        target = {"entity_id": entity_id}
        sign = 1 if up else -1
        if domain == "light":
            fx.actions.append(("light", "turn_on", {**target, "brightness_step_pct": sign * STEP_PCT}))
        elif domain == "fan":
            fx.actions.append(("fan", "increase_speed" if up else "decrease_speed", target))
        elif domain == "cover" and (pos := _num(a.get("current_position"))) is not None:
            position = int(max(0, min(100, pos + sign * STEP_PCT)))
            fx.actions.append(("cover", "set_cover_position", {**target, "position": position}))
        elif domain == "media_player":
            fx.actions.append(("media_player", "volume_up" if up else "volume_down", target))
        elif domain in ("number", "input_number") and (v := _num(state.state)) is not None:
            step = _num(a.get("step")) or 1
            lo, hi = _num(a.get("min")), _num(a.get("max"))
            value = v + sign * step
            value = max(lo, value) if lo is not None else value
            value = min(hi, value) if hi is not None else value
            fx.actions.append((domain, "set_value", {**target, "value": value}))

    def _detail_key(self, key: str, moving: bool, event: str, fx: Effects) -> None:
        state = self.hass.states.get(self.detail or "")
        rows = detail_rows(state)
        if key == "back" and event == "press":
            self.detail = None
            fx.redraw = True
        elif key in ("up", "down") and moving and rows:
            self.detail_cursor = max(0, min(self.detail_cursor + (1 if key == "down" else -1), len(rows) - 1))
            fx.redraw = True
        elif state is not None and rows and (
            (key in ("left", "right") and moving) or (key == "ok" and event == "press")
        ):
            row = rows[min(self.detail_cursor, len(rows) - 1)]
            if action := detail_action(state, row["k"], key):
                fx.actions.append(action)


def _list(mode: str, title: str, rows: list[str], cursor: int) -> Call:
    """A list screen: the window of rows around the cursor, the cursor within it."""
    start = max(0, min(cursor - 2, len(rows) - ROWS)) if cursor >= 0 else 0
    return ("show_list", {"mode": mode, "title": title, "cursor": cursor - start if cursor >= 0 else -1,
                          "items": rows[start : start + ROWS]})


def _hotkeys(labels: list[str]) -> Call:
    return ("set_hotkeys", {"labels": (labels + ["", "", "", ""])[:4]})


# --- Thermostats and humidifiers ------------------------------------------------

OPTION_ROWS = {  # row -> (attribute with the options, attribute or None for the state, service, field)
    "mode": ("available_modes", "mode", "humidifier.set_mode", "mode"),
    "hvac": ("hvac_modes", None, "climate.set_hvac_mode", "hvac_mode"),
    "fan": ("fan_modes", "fan_mode", "climate.set_fan_mode", "fan_mode"),
    "preset": ("preset_modes", "preset_mode", "climate.set_preset_mode", "preset_mode"),
}


def detail_rows(state: State | None) -> list[dict[str, str]]:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return []
    a = state.attributes
    rows: list[dict[str, str]] = []
    if state.domain == "humidifier":
        rows = [{"k": "power", "label": "Power", "value": state.state},
                {"k": "humidity", "label": "Target", "value": f"{a.get('humidity')}%"}]
        if a.get("available_modes"):
            rows.append({"k": "mode", "label": "Mode", "value": str(a.get("mode"))})
    elif state.domain == "climate":
        rows = [{"k": "hvac", "label": "Mode", "value": state.state.replace("_", "/")}]
        if _num(a.get("temperature")) is not None:
            rows.append({"k": "temp", "label": "Target", "value": f"{_fmt(a['temperature'])}°"})
        elif _num(a.get("target_temp_low")) is not None:
            rows += [{"k": "low", "label": "Heat to", "value": f"{_fmt(a['target_temp_low'])}°"},
                     {"k": "high", "label": "Cool to", "value": f"{_fmt(a['target_temp_high'])}°"}]
        if _num(a.get("humidity")) is not None:
            rows.append({"k": "humidity", "label": "Humidity", "value": f"{round(a['humidity'])}%"})
        if a.get("fan_modes"):
            rows.append({"k": "fan", "label": "Fan", "value": str(a.get("fan_mode"))})
        if a.get("preset_modes"):
            rows.append({"k": "preset", "label": "Preset", "value": str(a.get("preset_mode"))})
    return rows


def current_reading(state: State | None) -> str:
    if state is None:
        return ""
    a = state.attributes
    if state.domain == "humidifier" and (h := _num(a.get("current_humidity"))) is not None:
        return f"{round(h)}%"
    if state.domain == "climate":
        parts = []
        if (t := _num(a.get("current_temperature"))) is not None:
            parts.append(f"{_fmt(t)}°")
        if (h := _num(a.get("current_humidity"))) is not None:
            parts.append(f"{round(h)}%")
        return " ".join(parts)
    return ""


def detail_action(state: State, row: str, key: str) -> tuple[str, str, dict[str, Any]] | None:
    """What ◀ ▶ or OK does on a detail row. OK toggles power or steps to the next option."""
    a, entity_id = state.attributes, state.entity_id
    target = {"entity_id": entity_id}
    up = key != "left"
    if row == "power":
        return ("homeassistant", "toggle", target)
    if row in OPTION_ROWS:
        options_attr, current_attr, service, data_field = OPTION_ROWS[row]
        options = list(a.get(options_attr) or [])
        if not options:
            return None
        current = state.state if current_attr is None else a.get(current_attr)
        i = options.index(current) if current in options else -1
        domain, name = service.split(".")
        return (domain, name, {**target, data_field: options[(i + (1 if up else -1)) % len(options)]})
    if key == "ok":
        return None
    step = _num(a.get("target_temp_step")) or 0.5
    sign = 1 if up else -1
    if row == "humidity":
        # Humidifiers step 5 %, a thermostat's target humidity 1 %.
        h = (_num(a.get("humidity")) or 50) + (1 if state.domain == "climate" else 5) * sign
        h = max(_num(a.get("min_humidity")) or 0, min(_num(a.get("max_humidity")) or 100, h))
        return (state.domain, "set_humidity", {**target, "humidity": int(h)})
    if row == "temp" and (t := _num(a.get("temperature"))) is not None:
        return ("climate", "set_temperature", {**target, "temperature": t + sign * step})
    if row in ("low", "high"):
        lo, hi = _num(a.get("target_temp_low")), _num(a.get("target_temp_high"))
        if lo is None or hi is None:
            return None
        # heat_cool needs both ends; keep them at least one step apart.
        if row == "low":
            lo = min(lo + sign * step, hi - step)
        else:
            hi = max(hi + sign * step, lo + step)
        return ("climate", "set_temperature", {**target, "target_temp_low": lo, "target_temp_high": hi})
    return None
