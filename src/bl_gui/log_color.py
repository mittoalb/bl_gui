"""Colourise bl_gui's log output by ``[TAG]`` prefix.

Wraps ``sys.stdout`` so any ``print()`` call that contains a
``"[TAG]"`` bracket has ANSI colour applied to the tag. Existing
modules (e.g. ``harmonic_correction``) that self-colour their output
keep working because the pattern matches only bare ``[TAG]`` literals
— already-ANSI-wrapped tags are ignored.

Set ``BL_GUI_NO_COLOR=1`` in the environment to disable (useful for
redirecting to a log file).
"""
import os
import re
import sys


_ENABLED = os.environ.get("BL_GUI_NO_COLOR", "").strip().lower() not in (
    "1", "true", "yes")

# Tag → ANSI SGR parameters (semicolon-separated for bold+color).
# Grouped by intent so the terminal reads like a scan: cyan for
# informational chatter, green for user actions, yellow for state /
# valve activity, magenta for energy / motion, red for stops / errors,
# grey (bright-black) for high-volume debug lines.
_COLORS = {
    # Info / lifecycle
    "LOAD":     "36",     # cyan
    "REGIME":   "36",     # cyan
    "GUI":      "36",     # cyan
    "CONFIG":   "36",     # cyan
    "CALIB":    "36",     # cyan
    "SAVE":     "36",     # cyan
    "QGMAX":    "1;36",   # bold cyan
    # User actions / commits
    "WIDGET":   "1;34",   # bold blue
    "PRESET":   "1;32",   # bold green
    "SET":      "32",     # green
    # State / valve / setpoint edits
    "SP":       "33",     # yellow
    "VALVE":    "33",     # yellow
    "TOGGLE":   "33",     # yellow
    # Motion / energy / motors
    "ENERGY":   "35",     # magenta
    "PAD":      "35",     # magenta
    "QGMAX":    "1;36",   # (duplicate key harmless)
    # Errors / stops
    "ALLSTOP":  "1;31",   # bold red
    "CAPUT":    "31",     # red for the negative branch (rc≠0)
    # Debug-noisy
    "PV":       "90",     # bright black (dim)
    "PLC":      "90",     # dim
    "AF":       "90",     # dim
    "BIN":      "90",     # dim
    "CAM":      "90",     # dim
    # HARMONIC intentionally omitted — that module self-colours its
    # own [HARMONIC] tag with bold cyan; double-wrapping is a no-op
    # but wastes bytes on the terminal.
}

# One regex, all tags. Word-boundary via literal square brackets.
_PATTERN = re.compile(r"\[(" + "|".join(re.escape(t) for t in _COLORS) + r")\]")


def _colourise(text: str) -> str:
    return _PATTERN.sub(
        lambda m: f"\033[{_COLORS[m.group(1)]}m[{m.group(1)}]\033[0m",
        text,
    )


class _ColorStream:
    """Thin wrapper around a stream. Adds ANSI colour to recognised
    ``[TAG]`` prefixes on write; passes other methods through so
    anything that peeks at ``fileno`` / ``isatty`` still works."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, s):
        if _ENABLED and s:
            s = _colourise(s)
        return self._stream.write(s)

    def flush(self):
        return self._stream.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()

    def fileno(self):
        return self._stream.fileno()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install():
    """Wrap ``sys.stdout`` with the colouriser. Idempotent — a second
    call is a no-op so importing the module twice or calling install
    from multiple entry points doesn't double-wrap."""
    if isinstance(sys.stdout, _ColorStream):
        return
    sys.stdout = _ColorStream(sys.stdout)
