"""Headless (non-GUI) API surface for bl_gui.

Every callable here works without a display, a QApplication, or the
``bl_gui`` main window. Intended callers are the ``bl-cli`` argparse
CLI and the AI agent (via ``beamline-agent``), but any Python script
that wants to interrogate the layout or drive a motor can import
from here directly::

    from bl_gui.headless import (
        load_layout, list_motors, list_panels,
        caget, caget_rbv, caput, wait_dmov,
        interp_at_energy, move_motors_to_energy,
    )
    from bl_gui.headless import qgmax, regime, autofocus

Note that importing ``bl_gui.headless.calib`` transitively imports
``bl_gui.beamlines.bl32id.xanes_calib``, which does
``from PyQt5 import QtWidgets`` at module top (the calibration
*dialog* lives there). Import succeeds without a display — you just
don't call ``launch()``. Everything under ``.motors``, ``.layout``,
``.qgmax``, ``.regime``, and ``.autofocus`` is truly Qt-free."""
from .calib import interp_at_energy, move_motors_to_energy
from .layout import list_actions, list_motors, list_panels, load_layout
from .motors import caget, caget_float, caget_rbv, caput, wait_dmov

# Sub-namespaces (imported for `from bl_gui.headless import qgmax`).
from . import autofocus, qgmax, regime

__all__ = [
    # motors
    "caget", "caget_float", "caget_rbv", "caput", "wait_dmov",
    # layout
    "load_layout", "list_motors", "list_panels", "list_actions",
    # calibration
    "interp_at_energy", "move_motors_to_energy",
    # sub-namespaces
    "autofocus", "qgmax", "regime",
]
