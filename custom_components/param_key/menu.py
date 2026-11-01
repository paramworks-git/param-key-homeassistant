"""The remote's menus, built from labels (README, "Using the remote").

An entity is on the remote when it has the label `remote`, or a path label such as
`remote/Movie night` or `remote/Movie night/Lights` (custom menus, nested by `/`).

- The root lists Areas (an entity's own Area, else its device's), then custom menus,
  then `remote` entities in no Area.
- An Area with more than one screen of entities (6) is grouped by kind: Lights, Fans...
- A level with a single choice is skipped, so a small setup stays one flat list.
- Scenes, scripts and buttons in a menu become its hotkeys (up to 4); any more are listed.

Menus are matched by key across rebuilds, so a remote keeps its place when labels change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)

LABEL = "remote"
ROWS = 6  # list rows on one screen
HOTKEYS = 4
RUN_DOMAINS = ("scene", "script", "button", "input_button")
# Kinds an Area is split into when it has more than one screen of entities.
KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("Lights", ("light",)),
    ("Switches", ("switch", "input_boolean")),
    ("Fans", ("fan",)),
    ("Covers", ("cover",)),
    ("Climate", ("climate", "humidifier", "water_heater")),
    ("Media", ("media_player",)),
    ("Scenes", RUN_DOMAINS),
]
OTHER_KIND = "Other"


@dataclass
class Menu:
    key: str
    title: str
    items: list[Menu | str] = field(default_factory=list)  # submenus and entity IDs
    hotkeys: list[str] = field(default_factory=list)  # entity IDs, at most HOTKEYS
    area: str = ""  # Area name, stripped from entity names shown in it

    def find(self, key: str) -> Menu | None:
        return next((m for m in self.items if isinstance(m, Menu) and m.key == key), None)

    def entities(self) -> list[str]:
        """Every entity in this menu and below, hotkeys included."""
        out = list(self.hotkeys)
        for item in self.items:
            out += item.entities() if isinstance(item, Menu) else [item]
        return out


def membership(label_names: list[str]) -> list[tuple[str, ...]]:
    """Menu paths an entity's labels put it in: () for `remote`, ("A", "B") for `remote/A/B`."""
    paths: list[tuple[str, ...]] = []
    for name in label_names:
        parts = [p.strip() for p in name.split("/")]
        if parts[0].casefold() != LABEL:
            continue
        path = tuple(p for p in parts[1:] if p)
        if path not in paths:
            paths.append(path)
    return paths


def _kind(entity_id: str) -> str:
    domain = entity_id.split(".", 1)[0]
    return next((name for name, domains in KINDS if domain in domains), OTHER_KIND)


def _fill(menu: Menu, entity_ids: list[str], name_of) -> None:
    """Entities into a menu: scenes and scripts first become hotkeys, the rest is listed."""
    ordered = sorted(set(entity_ids), key=lambda e: name_of(e).casefold())
    runnable = [e for e in ordered if e.split(".", 1)[0] in RUN_DOMAINS]
    menu.hotkeys = runnable[:HOTKEYS]
    menu.items += [e for e in ordered if e not in menu.hotkeys]


def _collapse(menu: Menu) -> Menu:
    """Skip levels with a single choice; their hotkeys move up when there's room."""
    menu.items = [_collapse(m) if isinstance(m, Menu) else m for m in menu.items]
    while len(menu.items) == 1 and isinstance(menu.items[0], Menu):
        only = menu.items[0]
        menu.items = only.items
        menu.area = menu.area or only.area
        menu.hotkeys = (menu.hotkeys + [h for h in only.hotkeys if h not in menu.hotkeys])[:HOTKEYS]
    return menu


def build(hass: HomeAssistant, name_of) -> Menu:
    """The root menu. `name_of(entity_id)` gives the name to sort by."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    labels = lr.async_get(hass)

    by_area: dict[str, list[str]] = {}
    no_area: list[str] = []
    custom: dict[tuple[str, ...], list[str]] = {}
    for entry in entities.entities.values():
        if not entry.labels or entry.disabled_by:
            continue
        names = [label.name for lid in entry.labels if (label := labels.async_get_label(lid))]
        for path in membership(names):
            if path:
                custom.setdefault(path, []).append(entry.entity_id)
                continue
            area_id = entry.area_id
            if area_id is None and entry.device_id and (device := devices.async_get(entry.device_id)):
                area_id = device.area_id
            area = areas.async_get_area(area_id) if area_id else None
            if area is None:
                no_area.append(entry.entity_id)
            else:
                by_area.setdefault(area.name, []).append(entry.entity_id)

    root = Menu("root", "Devices")
    for area_name in sorted(by_area, key=str.casefold):
        menu = Menu(f"area:{area_name}", area_name, area=area_name)
        members = by_area[area_name]
        if len(members) > ROWS:
            for kind, _ in [*KINDS, (OTHER_KIND, ())]:
                group = [e for e in members if _kind(e) == kind]
                if group:
                    sub = Menu(f"kind:{kind}", kind, area=area_name)
                    _fill(sub, group, name_of)
                    menu.items.append(sub)
            # Scenes of the Area are its hotkeys, not a submenu, while they fit.
            scenes = menu.find("kind:Scenes")
            if scenes is not None and not scenes.items:
                menu.items.remove(scenes)
                menu.hotkeys = scenes.hotkeys
        else:
            _fill(menu, members, name_of)
        root.items.append(menu)

    # Custom menus: a tree from the label paths, sorted by name, not by Area.
    tree: dict[tuple[str, ...], Menu] = {}

    def menu_at(path: tuple[str, ...]) -> Menu:
        if path not in tree:
            parent = root if len(path) == 1 else menu_at(path[:-1])
            tree[path] = Menu("menu:" + "/".join(path), path[-1])
            parent.items.append(tree[path])
        return tree[path]

    for path in sorted(custom, key=lambda p: [s.casefold() for s in p]):
        menu_at(path)
    for path, members in custom.items():
        _fill(tree[path], members, name_of)

    _fill(root, no_area, name_of)
    return _collapse(root)
