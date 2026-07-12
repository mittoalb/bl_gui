"""Persistent Nano/Micro regime for bl32-ID.

Stored as a one-line text file at ``~/.bl_gui/regime.txt`` so the regime
survives GUI restart AND can be updated by any other tool at the
beamline (medm macro, shell script, pystream, whatever) — bl_gui just
reads the file at every decision point instead of trusting a variable
that only gets written when a specific button in bl_gui is clicked.

Values are the strings ``"nano"`` or ``"micro"`` (lowercase). Anything
else is treated as absent and the caller's default is returned.
"""
import os

STATE_FILE = os.path.expanduser("~/.bl_gui/regime.txt")

_VALID = ("nano", "micro")


def read(default: str = "nano") -> str:
    """Return the current regime, or `default` if the file is missing /
    unreadable / holds anything other than nano|micro."""
    try:
        with open(STATE_FILE) as f:
            v = f.read().strip().lower()
    except (FileNotFoundError, OSError):
        return default
    return v if v in _VALID else default


def write(mode: str) -> None:
    """Persist the regime. Raises ValueError on an invalid mode string
    so a caller typo (e.g. 'nano ' with a space would already be lower-
    stripped) cannot silently write garbage that then reads as
    'default'."""
    mode = str(mode).strip().lower()
    if mode not in _VALID:
        raise ValueError(f"invalid regime {mode!r}; expected 'nano' or 'micro'")
    d = os.path.dirname(STATE_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(mode + "\n")


def is_nano() -> bool:
    return read() == "nano"
