"""Extensible widget registry for bl_gui.

Two kinds of registrable widgets:

* **Atomic** — factory returns a QWidget that gets dropped inside a
  freshly-created Panel by the main window's `_add_widget_from_registry`.
  Motor cards, PV rows, camera views, MCTOptics view are atomics.

* **Preset**  — factory (a "builder") creates its own Panel and
  populates it fully. The extracted `Win._build_panel_*` methods
  (Shutters, Beam Info, Energy, …) are presets, wrapped as builders
  when Win is constructed.

Register from anywhere at import time:

    from bl_gui.widget_registry import register_widget, register_preset

    def _my_widget(parent):
        return MyWidget(parent=parent)
    register_widget("My Widget", "MyWidget", 300, 200, _my_widget)

    # decorator form for atomics
    @register_widget("My Widget", "MyWidget", 300, 200)
    def _my_widget(parent):
        return MyWidget(parent=parent)

    # preset form — builder takes (win, tab_name), builds a whole panel
    def _my_preset(win, tab_name):
        p, _ = win._make_panel("MyPreset", 400, 300, tab_name)
        ...populate p...
    register_preset("My Preset", "MyPreset", 400, 300, _my_preset)

Auto-discovery: on app startup, everything under `bl_gui.widgets_ext`
is imported. Drop a `<name>.py` file in that package (or its optional
per-user twin `~/.bl_gui/widgets/`) with `register_*` calls at module
scope and the widget appears in the + Widget menu with no framework
edits.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import types
from typing import Callable, Dict, Optional, Tuple

# menu_label -> (default_panel_name, default_w, default_h, factory(parent))
_ATOMIC: Dict[str, Tuple[str, int, int, Callable]] = {}

# menu_label -> (default_panel_name, default_w, default_h, builder(win, tab))
_PRESETS: Dict[str, Tuple[str, int, int, Callable]] = {}


def register_widget(menu_label: str, default_name: str,
                    default_w: int, default_h: int,
                    factory: Optional[Callable] = None):
    """Register an atomic widget factory.

    Callable form: `register_widget("My", "My", 300, 200, my_factory)`.
    Decorator form: use as `@register_widget("My", "My", 300, 200)`
    above the factory.
    """
    def _do(fn: Callable) -> Callable:
        _ATOMIC[menu_label] = (default_name, default_w, default_h, fn)
        return fn
    if factory is not None:
        return _do(factory)
    return _do


def register_preset(menu_label: str, default_name: str,
                    default_w: int, default_h: int,
                    builder: Optional[Callable] = None):
    """Register a preset panel builder.

    The builder must accept `(win, tab_name)` and build its own Panel
    via `win._make_panel(...)`. Same call / decorator ergonomics as
    `register_widget`.
    """
    def _do(fn: Callable) -> Callable:
        _PRESETS[menu_label] = (default_name, default_w, default_h, fn)
        return fn
    if builder is not None:
        return _do(builder)
    return _do


def registered_widgets() -> Dict[str, Tuple[str, int, int, Callable]]:
    """A copy of the atomic-widget registry (menu order preserved)."""
    return dict(_ATOMIC)


def registered_presets() -> Dict[str, Tuple[str, int, int, Callable]]:
    """A copy of the preset registry (menu order preserved)."""
    return dict(_PRESETS)


def preset_for_base(base_name: str) -> Optional[Tuple[str, int, int, Callable]]:
    """Look up a preset by its default_name — used by the load path to
    match a saved panel key like "Shutters::User Mode" against a
    registered preset. Returns None if no preset claims that name.
    """
    for _label, entry in _PRESETS.items():
        if entry[0] == base_name:
            return entry
    return None


def widget_for_base(base_name: str) -> Optional[Tuple[str, int, int, Callable]]:
    """Same as preset_for_base but for atomic widgets."""
    for _label, entry in _ATOMIC.items():
        if entry[0] == base_name:
            return entry
    return None


def discover_package(package_name: str) -> None:
    """Import every submodule of `package_name`, triggering the
    register_* calls each one performs at import time. Missing package
    is a silent no-op — the built-in widgets_ext package is optional
    and per-user widgets_ext may not exist at all."""
    try:
        pkg = importlib.import_module(package_name)
    except ImportError:
        return
    if not hasattr(pkg, "__path__"):
        return
    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg.__path__):
        try:
            importlib.import_module(f"{package_name}.{mod_name}")
        except Exception as e:  # noqa: BLE001
            print(f"[WIDGETS] failed to import "
                  f"{package_name}.{mod_name}: {e}")


def discover_directory(path: str, virtual_package: str = "_bl_gui_user_widgets") -> None:
    """Import every top-level `.py` under `path` as if it were a
    module. Used to load per-user widget extensions from
    ~/.bl_gui/widgets/ without requiring them to be installed as a
    real Python package. Silently no-ops if the directory is missing.
    """
    if not path or not os.path.isdir(path):
        return
    # Create a synthetic parent package so relative imports don't blow up.
    if virtual_package not in sys.modules:
        pkg = types.ModuleType(virtual_package)
        pkg.__path__ = [path]
        sys.modules[virtual_package] = pkg
    for entry in sorted(os.listdir(path)):
        if not entry.endswith(".py") or entry.startswith("_"):
            continue
        mod_name = entry[:-3]
        try:
            importlib.import_module(f"{virtual_package}.{mod_name}")
        except Exception as e:  # noqa: BLE001
            print(f"[WIDGETS] failed to import user widget "
                  f"{entry}: {e}")


def discover_all() -> None:
    """Convenience: import the built-in extension package and any
    per-user widgets directory. Idempotent — safe to call more than
    once (repeated `importlib.import_module` is a no-op)."""
    discover_package("bl_gui.widgets_ext")
    discover_directory(os.path.expanduser("~/.bl_gui/widgets"))
