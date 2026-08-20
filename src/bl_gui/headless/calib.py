"""Energy calibration compute + motor-move core.

The GUI's ``_move_motors_from_plugin`` in ``main_window.py`` did all
of the polyfit + micro-regime skip + subprocess-caput sequence inline.
This module extracts the compute+caput core so the same code runs
from the CLI, from other Python scripts, and (via the CLI) from the
AI agent. The GUI's method now defers to this module.

``_polyfit_interp`` lives here (moved from main_window at line 32);
main_window re-exports it under the same name for anyone importing
the old path."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..beamlines.bl32id import xanes_calib as _xc
from . import motors as _mo


def _polyfit_interp(pts, col_idx, e_eV_target):
    """Fit a polynomial (degree auto-chosen up to 3) through the
    (Energy, axis-value) pairs from the calibration table and evaluate
    at ``e_eV_target``. Degree is clamped to ``len(points) - 1`` so we
    never over-fit. Returns None if fewer than 2 usable points exist.

    Numpy is preferred; falls back to piecewise linear (with endpoint
    extrapolation) so the module still works in an env without numpy.
    Signature and behaviour are byte-identical to the previous
    ``main_window._polyfit_interp``."""
    try:
        import numpy as _np
    except Exception:
        _np = None
    es, vs = [], []
    for row in pts:
        if len(row) <= col_idx:
            continue
        if row[0] is None or row[col_idx] is None:
            continue
        es.append(float(row[0]))
        vs.append(float(row[col_idx]))
    if len(es) < 2:
        return None
    if _np is None:
        order = sorted(range(len(es)), key=lambda i: es[i])
        es = [es[i] for i in order]
        vs = [vs[i] for i in order]
        if e_eV_target <= es[0]:
            slope = (vs[1] - vs[0]) / (es[1] - es[0])
            return vs[0] + slope * (e_eV_target - es[0])
        if e_eV_target >= es[-1]:
            slope = (vs[-1] - vs[-2]) / (es[-1] - es[-2])
            return vs[-1] + slope * (e_eV_target - es[-1])
        for i in range(1, len(es)):
            if es[i] >= e_eV_target:
                frac = (e_eV_target - es[i-1]) / (es[i] - es[i-1])
                return vs[i-1] + frac * (vs[i] - vs[i-1])
        return None
    deg = min(3, len(es) - 1)
    coeffs = _np.polyfit(es, vs, deg)
    return float(_np.polyval(coeffs, e_eV_target))


def _is_zp_xy_label(label: str) -> bool:
    """True for motor labels containing 'zp x' or 'zp y'
    (case-insensitive substring). Matches the GUI's micro-regime
    skip rule so callers can filter identically before caput."""
    l = (label or "").lower()
    return ("zp x" in l) or ("zp y" in l)


def _nano_from_regime(respect_regime: bool) -> bool:
    """Effective nano-mode flag. When ``respect_regime`` is False the
    caller wants to move every calibrated motor regardless of regime
    state; otherwise we consult ``bl_gui.beamlines.bl32id.regime``."""
    if not respect_regime:
        return True
    try:
        from ..beamlines.bl32id import regime as _rg
        return _rg.is_nano()
    except Exception:
        # No regime file yet, or read failed → default to nano (i.e.
        # move every motor). Matches ``regime.read(default='nano')``.
        return True


def interp_at_energy(e_keV: float,
                     respect_regime: bool = True,
                     cfg: Optional[Dict] = None,
                     ) -> List[Dict[str, Any]]:
    """Compute the target position for every calibrated motor at the
    given energy without moving anything. Returns one row per motor:

        {"label", "pv", "col", "include", "target", "status", "reason"}

    ``status`` is one of ``"ready"`` (would caput this value),
    ``"skip"`` (excluded — see ``reason``), ``"error"`` (missing PV,
    insufficient cal data, or numeric failure). Callers with
    ``respect_regime=True`` also see ZP X/Y motors marked skip in
    micro regime."""
    if cfg is None:
        cfg = _xc.load_config()
    pts = [p for p in (cfg.get("points") or []) if p and p[0] is not None]
    motors = list(cfg.get("motors") or [])
    e_eV = float(e_keV) * 1000.0
    nano = _nano_from_regime(respect_regime)

    rows: List[Dict[str, Any]] = []
    if len(pts) < 2:
        # Return one placeholder row so the caller sees why nothing
        # would happen. Every motor is marked error.
        for i, m in enumerate(motors):
            rows.append({
                "label":   m.get("label") or f"m{i+1}",
                "pv":      (m.get("pv") or "").strip(),
                "col":     i + 1,
                "include": bool(m.get("include", True)),
                "target":  None,
                "status":  "error",
                "reason":  f"only {len(pts)} cal point(s); need >= 2",
            })
        return rows

    for i, m in enumerate(motors):
        name    = m.get("label") or f"m{i+1}"
        col     = i + 1
        target_pv = (m.get("pv") or "").strip()
        include = bool(m.get("include", True))
        row = {"label": name, "pv": target_pv, "col": col,
               "include": include, "target": None,
               "status": "ready", "reason": ""}

        if not include:
            row["status"], row["reason"] = "skip", "Include=off"
            rows.append(row); continue
        if _is_zp_xy_label(name) and not nano:
            row["status"], row["reason"] = "skip", "MICRO regime — ZP X/Y frozen"
            rows.append(row); continue
        if not target_pv:
            row["status"], row["reason"] = "error", "no PV configured"
            rows.append(row); continue
        v = _polyfit_interp(pts, col, e_eV)
        if v is None:
            row["status"], row["reason"] = "error", f"no cal data for column {col}"
            rows.append(row); continue
        try:
            row["target"] = float(v)
        except (TypeError, ValueError) as e:
            row["status"], row["reason"] = "error", f"interp not numeric: {e}"
        rows.append(row)
    return rows


def move_motors_to_energy(e_keV: float,
                          dry_run: bool = False,
                          respect_regime: bool = True,
                          timeout: float = 5.0,
                          ) -> List[Dict[str, Any]]:
    """Compute (via ``interp_at_energy``) and issue sync caputs for
    every ready motor. Returns the same row shape as
    ``interp_at_energy`` with ``status`` updated to ``"ok"``
    (caput succeeded), ``"caput_failed"`` (rc != 0 or timed out),
    ``"dry-run"`` (dry_run=True — no caput issued), or the pre-caput
    ``"skip"`` / ``"error"`` values passed through unchanged.

    Sync caput matches the GUI's behaviour: surfaces failures in real
    time and one motor's failure doesn't block others."""
    rows = interp_at_energy(e_keV, respect_regime=respect_regime)
    for r in rows:
        if r["status"] != "ready":
            continue
        if dry_run:
            r["status"] = "dry-run"
            continue
        ok = _mo.caput(r["pv"], f"{float(r['target']):.6f}", timeout=timeout)
        r["status"] = "ok" if ok else "caput_failed"
    return rows
