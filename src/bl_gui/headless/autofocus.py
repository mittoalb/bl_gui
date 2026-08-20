"""Convenience re-export of the autofocus sweep.

Underlying implementation lives in
``bl_gui.beamlines.bl32id.autofocus`` — pure numpy + pvaccess + our
subprocess caput/caget helpers. See that module's ``run()`` for the
argument list; nothing here beyond the re-export."""
from ..beamlines.bl32id.autofocus import run

__all__ = ["run"]
