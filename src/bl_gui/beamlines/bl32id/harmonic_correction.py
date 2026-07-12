"""Background central-bright-spot ("harmonic") correction for bl32-ID.

When enabled, a 5-second timer polls the camera. If the frame is bright
enough (mean > threshold) and shows a central bright spot, the QG V motor
(32idQG:m1) is nudged by one step size to push the spot away from the
image centre. Same detection maths as pystream's QGMax online spot check
(spot mean / background mean ratio, direction from spot Y position).

Only runs when the caller's `is_nano()` callable returns True — in Micro
regime the ZP is parked out and there's no bright spot to chase.
"""
import subprocess
from typing import Callable, Optional, Tuple

import numpy as np
from PyQt5 import QtCore

from .autofocus import _grab_image
from ...pv import caput_bg


# Defaults match pystream's QGMax dialog so behaviour is consistent.
DEFAULTS = {
    "interval_ms":       5000,
    "mean_min":          300.0,
    "image_pv":          "32idbSP1:Pva1:Image",
    "mean_pv":           "32idbSP1:Stats1:MeanValue_RBV",
    "qg_motor_pv":       "32idQG:m1",
    "step_size":         0.01,      # mm
    "spot_radius_px":    50,
    "bg_inner_radius_px": 150,
    "ratio_threshold":   1.30,
    "direction_sign":    +1,        # flip to -1 if the correction goes the wrong way
}


def _caget_float(pv, timeout=1.5):
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        return None


def find_bright_spot(image: np.ndarray,
                     spot_radius: float,
                     bg_inner_radius: float,
                     ) -> Optional[Tuple[float, int, int]]:
    """Return (ratio, peak_y, peak_x). Ratio is mean intensity inside a
    disk of ``spot_radius`` around the brightest coarse-block, divided by
    the mean intensity outside ``bg_inner_radius``. Returns None if the
    image is too small or parameters are degenerate. Same formulation as
    pystream QGMax's _find_bright_spot."""
    if image.ndim == 3:
        image = image.mean(axis=-1)
    image = np.asarray(image, dtype=np.float32)
    h, w = image.shape[:2]
    if spot_radius < 1 or bg_inner_radius <= spot_radius:
        return None
    if min(h, w) < 2 * bg_inner_radius:
        return None

    # Coarse block-mean smoothing to suppress single-pixel hot spots
    # before locating the peak.
    block = max(1, int(spot_radius))
    bh = h // block
    bw = w // block
    if bh < 2 or bw < 2:
        py, px = np.unravel_index(int(np.argmax(image)), image.shape)
    else:
        coarse = image[: bh * block, : bw * block].reshape(
            bh, block, bw, block
        ).mean(axis=(1, 3))
        by, bx = np.unravel_index(int(np.argmax(coarse)), coarse.shape)
        py = int((by + 0.5) * block)
        px = int((bx + 0.5) * block)

    r = int(spot_radius)
    py = min(max(py, r), h - r - 1)
    px = min(max(px, r), w - r - 1)

    y, x = np.ogrid[:h, :w]
    dist_sq = (y - py) ** 2 + (x - px) ** 2
    spot_mask = dist_sq < spot_radius ** 2
    bg_mask = dist_sq >= bg_inner_radius ** 2
    if not np.any(spot_mask) or not np.any(bg_mask):
        return None
    spot_mean = float(np.mean(image[spot_mask]))
    bg_mean = float(np.mean(image[bg_mask]))
    if bg_mean <= 0:
        return None
    return spot_mean / bg_mean, py, px


class HarmonicCorrection(QtCore.QObject):
    """Poll the camera every ``interval_ms``. If the toggle is on AND we're
    in Nano regime AND the frame is bright enough AND a central bright
    spot is present, caput a relative move to the QG V motor. Fire-and-
    forget — no waiting for motion to complete, no retry loop; the next
    tick will re-check and nudge again if the spot is still there."""

    def __init__(self, is_nano: Callable[[], bool], parent=None, **cfg):
        super().__init__(parent)
        self._is_nano = is_nano
        for k, v in DEFAULTS.items():
            setattr(self, k, cfg.get(k, v))
        self._enabled = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        # Guard against overlapping ticks (image grab can take a couple
        # hundred ms; if a previous tick is still running we skip).
        self._in_flight = False

    # ── Public API ────────────────────────────────────────────────────
    def set_enabled(self, on: bool):
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            print(f"[HARMONIC] enabled — {self.interval_ms/1000:g}s tick, "
                  f"mean>{self.mean_min:g}, motor={self.qg_motor_pv}, "
                  f"step={self.step_size:g} mm")
            self._timer.start()
        else:
            print("[HARMONIC] disabled")
            self._timer.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    # ── Timer tick ────────────────────────────────────────────────────
    def _tick(self):
        if self._in_flight:
            print("[HARMONIC] tick skipped: previous tick still in flight")
            return
        if not self._is_nano():
            print("[HARMONIC] tick skipped: MICRO regime (nano_mode=False)")
            return
        self._in_flight = True
        try:
            # Compute the frame mean directly from the grabbed image
            # instead of relying on <cam>:Stats1:MeanValue_RBV, which is
            # 0 unless the Stats plugin is enabled and wired to the
            # current stream. One less external dependency.
            img = _grab_image(self.image_pv, timeout=2.0)
            if img is None:
                print(f"[HARMONIC] tick skipped: no image from {self.image_pv}")
                return
            mean = float(np.mean(img))
            if mean < self.mean_min:
                print(f"[HARMONIC] tick skipped: image mean={mean:.1f} < "
                      f"{self.mean_min:g} (dim frame / no beam / shutter closed)")
                return
            print(f"[HARMONIC] tick: image {img.shape} dtype={img.dtype} "
                  f"min={img.min():.0f} max={img.max():.0f} mean={mean:.1f}")
            result = find_bright_spot(img, self.spot_radius_px,
                                      self.bg_inner_radius_px)
            if result is None:
                print(f"[HARMONIC] tick skipped: find_bright_spot returned None "
                      f"(image too small? spot_radius={self.spot_radius_px}, "
                      f"bg_inner={self.bg_inner_radius_px}, "
                      f"image dims={img.shape})")
                return
            ratio, peak_y, peak_x = result
            if ratio <= self.ratio_threshold:
                print(f"[HARMONIC] no correction: peak=({peak_x},{peak_y}) "
                      f"ratio={ratio:.2f} ≤ threshold {self.ratio_threshold:.2f}")
                return
            # Push the spot away from the image centre: upper half → +1,
            # lower → -1, scaled by direction_sign for motor wiring.
            image_center_y = img.shape[0] / 2.0
            y_sign = +1 if peak_y < image_center_y else -1
            direction = y_sign * self.direction_sign
            shift = direction * self.step_size
            # Relative move: caput to .RLV, which is the motor record's
            # "move by this delta from current position" field. Caputting
            # a small number like 0.01 to the base PV (or .VAL) would
            # instead set the ABSOLUTE target to 0.01 mm and slam the
            # piezo across its range.
            rlv_pv = f"{self.qg_motor_pv}.RLV"
            print(f"[HARMONIC] mean={mean:.1f} peak=({peak_x},{peak_y}) "
                  f"ratio={ratio:.2f} > {self.ratio_threshold:.2f} → "
                  f"nudge {rlv_pv} by {shift:+.4f} mm")
            caput_bg(rlv_pv, shift, t=3.0)
        finally:
            self._in_flight = False
