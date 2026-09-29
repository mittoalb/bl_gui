"""Example widget-extension module — copy this file, rename, edit.

Two forms shown:

* **Atomic** — a QWidget factory. bl_gui wraps it in a fresh Panel.
* **Preset** — a builder that owns the whole Panel.

Both appear in the + Widget menu as soon as you drop this module in
`src/bl_gui/widgets_ext/` (or in `~/.bl_gui/widgets/` for a per-user
widget that isn't in git).

Leave `_ENABLED = False` so the built-in example doesn't clutter every
GUI; flip it to `True` in your copy.
"""
from PyQt5 import QtCore, QtWidgets

from bl_gui.widget_registry import register_widget, register_preset

_ENABLED = False


def _example_atomic_factory(parent):
    """Trivial demo widget: a labelled push-button that prints when clicked."""
    w = QtWidgets.QWidget(parent)
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(6, 6, 6, 6)
    lbl = QtWidgets.QLabel("Example widget")
    btn = QtWidgets.QPushButton("Click me")
    btn.clicked.connect(lambda: print("[EXAMPLE] atomic clicked"))
    lay.addWidget(lbl); lay.addWidget(btn)
    return w


def _example_preset_builder(win, tab_name):
    """Preset form — the builder creates its own Panel and populates it."""
    p, _ = win._make_panel("Example Preset", 320, 140, tab_name)
    lay = QtWidgets.QVBoxLayout()
    lay.setContentsMargins(6, 22, 6, 6)
    lbl = QtWidgets.QLabel("Example preset — I own the whole panel.")
    lbl.setAlignment(QtCore.Qt.AlignCenter)
    btn = QtWidgets.QPushButton("Preset action")
    btn.clicked.connect(lambda: print("[EXAMPLE] preset clicked"))
    lay.addWidget(lbl); lay.addWidget(btn)
    p.setLayout(lay)


if _ENABLED:
    register_widget("Example atomic",
                    "ExampleAtomic", 220, 100, _example_atomic_factory)
    register_preset("Example preset",
                    "Example Preset", 320, 140, _example_preset_builder)
