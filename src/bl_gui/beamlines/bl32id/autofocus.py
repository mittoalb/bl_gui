"""Scintillator-screen autofocus for bl32-ID.

Sweeps a focus motor through a range, captures an image from the camera
area-detector PV at each step, computes a sharpness metric, and drives
the motor back to the position that maximized sharpness.

Image retrieval uses pvaccess to read the camera's live PVA image stream
(`32idbSP1:Pva1:Image` or equivalent). Sharpness uses variance of the
Laplacian (classic cheap blur metric) — swap to Tenengrad, normalized
gradient, etc., if a specific scintillator pattern calls for it.

Usage (command line):
    python -m bl_gui.beamlines.bl32id.autofocus \\
        --motor 32idbTXM:nf:m4 \\
        --range 1.0 --steps 21 \\
        --image-pv 32idbSP1:Pva1:Image \\
        --exposure 0.1

Or import and call run():
    from bl_gui.beamlines.bl32id import autofocus
    best = autofocus.run(motor_pv="32idbTXM:nf:m4",
                        image_pv="32idbSP1:Pva1:Image",
                        half_range_mm=1.0, steps=21)
"""
import argparse
import os
import subprocess
import sys
import time
from typing import Optional, Tuple, List

import numpy as np


# ── Motor I/O via subprocess caget/caput (bounded; same pattern as bl_gui) ──
def _caget(pv, timeout=2.0):
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        return None


def _caput(pv, val, timeout=5.0):
    try:
        r = subprocess.run(["caput", pv, str(val)],
                           capture_output=True, timeout=timeout, text=True)
        return r.returncode == 0
    except Exception:
        return False


def _wait_dmov(motor_pv, timeout=30.0, poll=0.1):
    """Wait for motor .DMOV = 1. Returns True on settled, False on timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = _caget(f"{motor_pv}.DMOV")
        if v is not None and float(v) > 0.5:
            return True
        time.sleep(poll)
    return False


# ── Image acquisition via pvaccess ────────────────────────────────────────
def _grab_image(image_pv, timeout=5.0):
    """Pull one frame from an AreaDetector PVA image PV. Returns a 2-D
    numpy array (grayscale) or None on failure."""
    try:
        from pvaccess import Channel
        ch = Channel(image_pv)
        ch.setTimeout(timeout)
        pv_obj = ch.get()
    except Exception as e:
        print(f"[AF] image get failed: {e}")
        return None
    try:
        d = pv_obj.toDict()
        # areaDetector NTNDArray shape is in d['dimension']; data is in
        # d['value'][field_tag] where field_tag depends on dtype.
        dims = d.get("dimension") or []
        if len(dims) < 2:
            print(f"[AF] unexpected image dims: {dims}")
            return None
        ny, nx = int(dims[1]["size"]), int(dims[0]["size"])
        val = d.get("value", {})
        arr = None
        for key in ("ubyteValue", "ushortValue", "uintValue",
                    "byteValue",  "shortValue",  "intValue",
                    "floatValue", "doubleValue"):
            if key in val:
                arr = np.asarray(val[key])
                break
        if arr is None:
            print(f"[AF] unknown value dtype in image: {list(val.keys())}")
            return None
        return arr.reshape((ny, nx))
    except Exception as e:
        print(f"[AF] image parse failed: {e}")
        return None


# ── Sharpness metric ──────────────────────────────────────────────────────
def _sharpness(img: np.ndarray) -> float:
    """Variance of the 3x3 Laplacian. Higher = sharper. Pure-numpy so no
    scipy/opencv dependency."""
    if img.ndim != 2:
        img = img.mean(axis=-1) if img.ndim == 3 else img.squeeze()
    img = img.astype(np.float32)
    # Discrete Laplacian via slicing (avoids scipy.ndimage).
    lap = (img[:-2, 1:-1] + img[2:, 1:-1] +
           img[1:-1, :-2] + img[1:-1, 2:] -
           4.0 * img[1:-1, 1:-1])
    return float(np.var(lap))


# ── Sweep driver ──────────────────────────────────────────────────────────
def run(motor_pv: str,
        image_pv: str,
        half_range_mm: float = 1.0,
        steps: int = 21,
        exposure_s: Optional[float] = None,
        exposure_pv: str = "32idbSP1:cam1:AcquireTime",
        acquire_pv: str = "32idbSP1:cam1:Acquire",
        settle_s: float = 0.05,
        timeout_s: float = 30.0,
        ) -> Optional[Tuple[float, List[Tuple[float, float]]]]:
    """Run an autofocus sweep around the current motor position.

    Returns (best_position_mm, [(pos, sharpness), ...]) or None on error.
    """
    center = _caget(f"{motor_pv}.RBV")
    if center is None:
        center = _caget(motor_pv)
    if center is None:
        print(f"[AF] cannot read motor {motor_pv}")
        return None
    positions = np.linspace(center - half_range_mm, center + half_range_mm, steps)
    print(f"[AF] sweep {motor_pv} around {center:.4f} "
          f"±{half_range_mm} mm, {steps} steps")

    # Optional: set exposure and ensure camera is acquiring free-run.
    if exposure_s is not None:
        _caput(exposure_pv, exposure_s)
    # Make sure Acquire is running (ignore result — IOC may already be).
    _caput(acquire_pv, 1)

    results: List[Tuple[float, float]] = []
    try:
        for p in positions:
            _caput(motor_pv, float(p))
            if not _wait_dmov(motor_pv, timeout=timeout_s):
                print(f"[AF] motor didn't settle at {p:.4f}; aborting")
                break
            time.sleep(settle_s)
            img = _grab_image(image_pv, timeout=timeout_s)
            if img is None:
                print(f"[AF] no image at {p:.4f}; skipping")
                continue
            s = _sharpness(img)
            results.append((float(p), s))
            print(f"[AF] pos={p:.5f} mm   sharpness={s:.2f}")
    finally:
        if not results:
            # Leave motor where we found it if the sweep aborted early.
            _caput(motor_pv, float(center))
            _wait_dmov(motor_pv, timeout=timeout_s)
            return None

    best_pos, best_s = max(results, key=lambda t: t[1])
    print(f"[AF] best pos={best_pos:.5f} mm  sharpness={best_s:.2f}")
    _caput(motor_pv, float(best_pos))
    _wait_dmov(motor_pv, timeout=timeout_s)
    return best_pos, results


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--motor", required=True,
                    help="Focus motor PV (e.g. 32idbTXM:nf:m4)")
    ap.add_argument("--image-pv", default="32idbSP1:Pva1:Image",
                    help="AreaDetector PVA image PV")
    ap.add_argument("--range", type=float, default=1.0,
                    help="Half-range (mm) around current position")
    ap.add_argument("--steps", type=int, default=21,
                    help="Number of sample points")
    ap.add_argument("--exposure", type=float, default=None,
                    help="Optional exposure time (s)")
    ap.add_argument("--exposure-pv", default="32idbSP1:cam1:AcquireTime")
    ap.add_argument("--acquire-pv",  default="32idbSP1:cam1:Acquire")
    ap.add_argument("--settle", type=float, default=0.05,
                    help="Extra dwell (s) after motor settles")
    a = ap.parse_args()
    rv = run(motor_pv=a.motor, image_pv=a.image_pv,
             half_range_mm=a.range, steps=a.steps,
             exposure_s=a.exposure,
             exposure_pv=a.exposure_pv, acquire_pv=a.acquire_pv,
             settle_s=a.settle)
    sys.exit(0 if rv else 2)


if __name__ == "__main__":
    main()
