"""Convenience re-export of the QGMax request/status helpers.

The underlying implementation lives in
``bl_gui.beamlines.bl32id.qgmax_trigger`` and is already 100 %
headless. Re-exporting here keeps the ``bl_gui.headless.*`` namespace
uniform for agents / CLI callers who shouldn't need to know that
QGMax lives under a beamline-specific path."""
from ..beamlines.bl32id.qgmax_trigger import (
    REQUEST_FILE,
    RESPONSE_FILE,
    read_status,
    trigger,
)

__all__ = ["REQUEST_FILE", "RESPONSE_FILE", "read_status", "trigger"]
