"""Beamline Optics Control GUI (PyQt5 + pvaccess).

Generalised from the APS 32-ID-B TXM GUI. Layouts for different beamlines
are stored under src/bl_gui/layouts/*.json.
Run with:   bl_gui           or   python -m bl_gui
"""
from .main_window import main

__all__ = ["main"]
