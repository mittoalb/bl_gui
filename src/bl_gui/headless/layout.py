"""Data-only bl_gui layout parser.

The GUI's ``_load_layout`` in ``main_window.py`` is 300+ lines of
mingled JSON parsing + widget construction. Agents and CLI callers
only need the JSON side: what motors exist, which panels hold them,
what actions are configured. This module reads the same JSON files
the GUI uses (bundled ``layouts/<bl>.json`` + optional per-user
``~/.bl_gui/<bl>.json`` override) and returns a normalised dict.

Path resolution mirrors ``main_window._lay_path`` exactly so a
layout saved through the GUI is visible to this parser without any
sync step."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


_BUNDLED_LAYOUTS_DIR = os.path.join(os.path.dirname(__file__),
                                    os.pardir, "layouts")


def _bundled_layout_path(name: str) -> str:
    """Absolute path to the packaged ``layouts/<name>.json``. Accepts
    either ``bl32id`` or ``bl32id.json`` for convenience."""
    if not name.endswith(".json"):
        name = name + ".json"
    return os.path.abspath(os.path.join(_BUNDLED_LAYOUTS_DIR, name))


def _user_layout_path(name: str) -> str:
    """Per-user override path (``~/.bl_gui/<basename>.json``). Matches
    ``main_window._user_lay_path`` — a layout saved by the GUI as a
    user-override lives here."""
    if name.endswith(".json"):
        name = name[:-len(".json")]
    return os.path.expanduser(f"~/.bl_gui/{name}.json")


def _lay_path(name: Optional[str] = None) -> str:
    """Resolve the layout path bl_gui would actually load: prefer the
    user override on load; fall back to the bundled template. If
    ``name`` is None, use whatever the GUI has as its current default
    (``theme._LAY``)."""
    if name is None:
        # theme._LAY holds the absolute path to the layout the GUI is
        # currently pointed at (main() sets it before Win is built).
        from .. import theme as _theme_mod
        default = _theme_mod._LAY
        # Strip .json for the user-override match.
        base = os.path.splitext(os.path.basename(default))[0]
        u = _user_layout_path(base)
        return u if os.path.isfile(u) else default
    u = _user_layout_path(name)
    if os.path.isfile(u):
        return u
    b = _bundled_layout_path(name)
    if os.path.isfile(b):
        return b
    # Fall back to name being an absolute path already.
    return name


def load_layout(name: Optional[str] = None) -> Dict:
    """Read the layout JSON and return a normalised structure. See the
    module docstring for how ``name`` resolves. Missing panels in
    ``_tab_map``/``_titles`` default sensibly (tab = "User Mode",
    title = the panel's key)."""
    path = _lay_path(name)
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"cannot read layout {path!r}: {e}") from e

    tabs           = raw.get("_tabs") or ["User Mode", "Expert Mode"]
    mcs            = raw.get("_mcs") or {}
    buttons        = raw.get("_buttons") or {}
    panels_geom    = raw.get("_panels") or {}
    tab_map        = raw.get("_tab_map") or {}
    titles         = raw.get("_titles") or {}
    pv_fields      = raw.get("_pv_fields") or {}
    custom_rows    = raw.get("_custom_rows") or {}
    deleted        = raw.get("_deleted_panels") or []

    # Panel keys come from every source; merge them so we don't lose
    # a panel that only has geometry (no motors) or only motors (no
    # geometry saved).
    keys = set(mcs) | set(buttons) | set(panels_geom) | set(tab_map) \
         | set(titles) | set(pv_fields) | set(custom_rows)

    panels = {}
    for key in sorted(keys):
        base_title = key.split("::", 1)[0]
        panels[key] = {
            "tab":         tab_map.get(key, tabs[0] if tabs else "User Mode"),
            "title":       titles.get(key, base_title),
            "geometry":    panels_geom.get(key),
            "motors":      list(mcs.get(key, [])),
            "buttons":     list(buttons.get(key, [])),
            "pv_fields":   dict(pv_fields.get(key, {})),
            "custom_rows": list(custom_rows.get(key, [])),
        }

    return {
        "name":            os.path.splitext(os.path.basename(path))[0],
        "path":            path,
        "tabs":            list(tabs),
        "panels":          panels,
        "deleted_panels":  list(deleted),
    }


def list_motors(layout: Optional[Dict] = None) -> List[Dict]:
    """Flat list of every motor across every panel. Each entry:
    ``{"panel_key", "panel_title", "tab", "label", "pv", "custom", "twv"}``.
    Empty PVs are dropped — a motor card with no PV can't be moved."""
    if layout is None:
        layout = load_layout()
    out = []
    for key, p in layout["panels"].items():
        for m in p["motors"]:
            pv = (m.get("pv") or "").strip()
            if not pv:
                continue
            out.append({
                "panel_key":   key,
                "panel_title": p["title"],
                "tab":         p["tab"],
                "label":       m.get("label") or pv,
                "pv":          pv,
                "custom":      bool(m.get("custom", False)),
                "twv":         m.get("twv", ""),
            })
    return out


def list_panels(layout: Optional[Dict] = None) -> List[Dict]:
    """Summary row per panel: key, title, tab, motor count, button count.
    Useful for the agent asking 'what panels are on this beamline'."""
    if layout is None:
        layout = load_layout()
    out = []
    for key, p in layout["panels"].items():
        out.append({
            "key":          key,
            "title":        p["title"],
            "tab":          p["tab"],
            "motor_count":  len(p["motors"]),
            "button_count": len(p["buttons"]),
            "pv_field_count": len(p["pv_fields"]),
        })
    return out


def list_actions(layout: Optional[Dict] = None) -> List[Dict]:
    """Every CfgButton across all panels. Empty when the layout has
    no saved buttons (bundled layouts ship with ``_buttons: {}`` —
    defaults are baked into ``_build_all_panels`` in main_window). Each
    entry: ``{"panel_key", "label", "type", "action"}``."""
    if layout is None:
        layout = load_layout()
    out = []
    for key, p in layout["panels"].items():
        for b in p["buttons"]:
            out.append({
                "panel_key": key,
                "label":     b.get("label", ""),
                "type":      b.get("type", ""),
                "action":    b.get("action", ""),
            })
    return out
