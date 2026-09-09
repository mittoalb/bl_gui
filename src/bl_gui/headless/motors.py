"""Bounded subprocess wrappers around EPICS caget / caput and DMOV polling.

The rest of bl_gui already avoids pvaccess for writes because
``Channel.put()`` has no timeout and was observed to wedge the worker
pool under heavy use. Every read/write here goes through the
``caget`` / ``caput`` CLI binaries with a hard ``subprocess.run(...,
timeout=…)`` — matches the pattern in
``beamlines/bl32id/xanes_calib.py`` and ``beamlines/bl32id/autofocus.py``.
Consolidating those in one place so the CLI + agent share exactly one
implementation."""
from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional


def caget(pv: str, timeout: float = 2.0) -> Optional[str]:
    """Return ``caget -t <pv>`` stdout stripped, or None on any failure
    (missing PV, timeout, nonzero rc). String form so callers can pick
    the type they want (enum name, integer index, float — all come out
    as text on the wire)."""
    if not pv:
        return None
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def caget_float(pv: str, timeout: float = 2.0) -> Optional[float]:
    """``caget`` + float() convenience. None on failure or non-numeric."""
    s = caget(pv, timeout=timeout)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def caget_rbv(motor_pv: str, timeout: float = 2.0) -> Optional[float]:
    """Read the motor's readback (``.RBV`` field). Falls back to the
    bare PV if the RBV read fails — matches xanes_calib._read_motor_rbv
    behaviour, which handles motors whose RBV isn't a distinct record."""
    v = caget_float(f"{motor_pv}.RBV", timeout=timeout)
    return v if v is not None else caget_float(motor_pv, timeout=timeout)


def caput(pv: str, value, timeout: float = 5.0) -> bool:
    """One-shot ``caput <pv> <value>``. Returns True on success. Logs
    the ``[CAPUT] pv=val rc=… stderr=…`` line bl_gui uses when the
    write fails, so failures surface in the CLI + agent transcript."""
    if not pv:
        return False
    try:
        r = subprocess.run(["caput", pv, str(value)],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            print(f"[CAPUT] {pv}={value!r} rc={r.returncode} "
                  f"stderr={r.stderr.strip()!r}", file=sys.stderr)
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"[CAPUT] {pv}={value!r} timed out after {timeout}s",
              file=sys.stderr)
        return False
    except (FileNotFoundError, OSError) as e:
        print(f"[CAPUT] {pv}={value!r} failed: {e}", file=sys.stderr)
        return False


def wait_dmov(motor_pv: str, timeout: float = 30.0, poll: float = 0.1) -> bool:
    """Poll ``<motor>.DMOV`` until it reads 1. Returns True when the
    motor has settled, False on timeout. ``poll`` seconds between reads;
    100 ms matches the autofocus sweep default."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = caget_float(f"{motor_pv}.DMOV")
        if v is not None and v > 0.5:
            return True
        time.sleep(poll)
    return False
