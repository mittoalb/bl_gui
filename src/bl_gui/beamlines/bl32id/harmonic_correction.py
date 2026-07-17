"""Background central-bright-spot ("harmonic") correction for bl32-ID.

When enabled, a 5-second timer inspects the most recent frame from the
camera's pvaccess NTNDArray PV. If the frame is bright enough (mean >
threshold) and shows a central bright spot, the QG V motor (32idQG:m1)
is nudged by one step size to push the spot away from the image centre.
Same detection maths as pystream's QGMax online spot check (spot mean /
background mean ratio, direction from spot Y position).

Frames come from a persistent pvaccess monitor — one-shot Channel.get()
on an NTNDArray doesn't reliably pull the payload; the monitor callback
gets the full frame on every push, matching how pystream's viewer sees
the stream.

Only runs when the caller's `is_nano()` callable returns True — in Micro
regime the ZP is parked out and there's no bright spot to chase.
"""
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np
from PyQt5 import QtCore

from ...pv import caput_bg


# ANSI colour helpers. Colours are only emitted when stdout is a TTY
# so pipes / log files stay clean (no `\033[...` litter).
_USE_COLOR = sys.stdout.isatty()
_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "blue":   "\033[34m",
    "magenta":"\033[35m",
    "cyan":   "\033[36m",
}


def _c(text: str, *styles: str) -> str:
    if not _USE_COLOR:
        return text
    prefix = "".join(_C[s] for s in styles if s in _C)
    return f"{prefix}{text}{_C['reset']}"


# Prefix used on every log line so a `grep [HARMONIC]` still works even
# once ANSI codes are attached to the surrounding text.
_TAG = _c("[HARMONIC]", "bold", "cyan")


def _caget_float(pv, timeout=1.5):
    """Bounded subprocess caget → float or None. Used only in the tick,
    so a few hundred ms of latency is fine."""
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        return None


def _caget_str(pv, timeout=1.5):
    """Bounded subprocess caget → stripped string or None."""
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except Exception:
        return None


# Defaults match pystream's QGMax dialog so behaviour is consistent.
DEFAULTS = {
    "interval_ms":       5000,
    "mean_min":          300.0,
    "image_pv":          "32idbSP1:Pva1:Image",
    "mean_pv":           "32idbSP1:Stats1:MeanValue_RBV",
    "qg_motor_pv":       "32idQG:m1",
    "step_size":         0.5,       # mm — one step is enough to clear the spot
    "spot_radius_px":    50,
    "bg_inner_radius_px": 150,
    "ratio_threshold":   1.30,
    "direction_sign":    +1,        # flip to -1 if the correction goes the wrong way
    # Burst mode: after a nudge, wait `settle_ms` for the motor + a new
    # frame, then re-check the spot. Keep nudging until the spot is gone
    # or `max_corrections_per_burst` is reached, then wait for the next
    # 5 s tick. Prevents having to wait a full tick between corrections.
    "settle_ms":                600,
    "max_corrections_per_burst": 1,
    # If no new frame has arrived from the monitor within this many
    # seconds, treat the cache as stale and skip. Guards against nudging
    # based on a frozen last-known frame when the camera stops acquiring.
    "max_frame_age_s":           3.0,
    # Scan-state gate: only nudge while the tomoscan is acquiring flat
    # fields. Reading the current HDF5 sub-path from TomoScan tells us
    # what kind of frame the detector is producing right now:
    #   /exchange/data        — projections (data)
    #   /exchange/data_white  — flat fields (whites) ← we act here
    #   /exchange/data_dark   — dark frames
    # If scan_state_pv is empty, the gate is disabled and the correction
    # runs on every tick (previous behaviour). Set scan_state_active to
    # the empty string to also disable.
    "scan_state_pv":     "32id:TomoScan:HDF5Location",
    "scan_state_active": "/exchange/data_white",
    # Fraction of each image dimension in which the peak may live. 0.5
    # means search only the central 50% (i.e. ignore the outer 25% on
    # each side). Physical spots caused by harmonics show up near the
    # optical axis; detector-edge artifacts (dead columns, vignetting,
    # gain-map borders) never do — a tight central search excludes them
    # entirely, no matter how bright they are.
    "center_search_frac": 0.5,
}


def _decode_ntnd(ntnda) -> Optional[np.ndarray]:
    """Turn a pvaccess NTNDArray PV object into a 2-D numpy array, or
    None on any structural mismatch. Uses the same subscript-access
    pattern as pystream's reshape_ntnda — .toDict() drops the payload of
    the value union in some pvaccess versions, subscripting works."""
    try:
        dims = ntnda["dimension"]
        if len(dims) < 2:
            return None
        nx = int(dims[0]["size"])
        ny = int(dims[1]["size"])
        if nx <= 0 or ny <= 0:
            return None
        # value is a union — one of *Value fields carries the raw array.
        try:
            field_key = ntnda.getSelectedUnionFieldName()
            raw = ntnda["value"][0][field_key]
        except Exception:
            try:
                field_key = next(iter(ntnda["value"][0].keys()))
                raw = ntnda["value"][0][field_key]
            except (StopIteration, KeyError):
                return None
        arr = np.asarray(raw)
        if arr.size < ny * nx:
            return None
        return arr[: ny * nx].reshape((ny, nx))
    except Exception:
        return None


def find_bright_spot(image: np.ndarray,
                     spot_radius: float,
                     bg_inner_radius: float,
                     center_search_frac: float = 0.4,
                     ) -> Optional[Tuple[float, int, int]]:
    """Return (ratio, peak_y, peak_x). Ratio is mean intensity inside a
    disk of ``spot_radius`` around the brightest coarse-block, divided by
    the mean intensity outside ``bg_inner_radius``. Returns None if the
    image is too small or parameters are degenerate.

    Peak search is restricted to the central ``center_search_frac`` of
    each image dimension. 0.4 = central 40% (peak may live only in the
    innermost 40% of the field on each axis; outer 30% on each side is
    excluded). Set to 1.0 to search the whole image.
    """
    if image.ndim == 3:
        image = image.mean(axis=-1)
    image = np.asarray(image, dtype=np.float32)
    h, w = image.shape[:2]
    if spot_radius < 1 or bg_inner_radius <= spot_radius:
        return None
    if min(h, w) < 2 * bg_inner_radius:
        return None

    # Coarse block-mean smoothing to suppress single-pixel hot spots
    # before locating the peak. Peak search is confined to a central
    # window whose half-size is center_search_frac/2 of each dimension
    # — a real harmonic spot always lands near the optical axis, so
    # excluding the outer part of the image entirely stops the
    # detector's edge artefacts (vignetting, dead columns, gain-map
    # borders) from masquerading as a bright spot.
    block = max(1, int(spot_radius))
    bh = h // block
    bw = w // block
    frac = max(0.05, min(1.0, float(center_search_frac)))
    # Half-window in blocks; must also leave bg_inner_radius margin so
    # the background-mean disk stays inside the image.
    hy_blocks = max(1, int(bh * frac / 2))
    hx_blocks = max(1, int(bw * frac / 2))
    margin_blocks = max(1, int(bg_inner_radius / block))
    hy_blocks = max(hy_blocks, 1)
    hx_blocks = max(hx_blocks, 1)
    if bh < 2 or bw < 2:
        py, px = np.unravel_index(int(np.argmax(image)), image.shape)
    else:
        coarse = image[: bh * block, : bw * block].reshape(
            bh, block, bw, block
        ).mean(axis=(1, 3))
        cy = bh // 2
        cx = bw // 2
        # Combined constraint: within the central-fraction window AND
        # at least margin_blocks from the image edge.
        y_lo = max(cy - hy_blocks, margin_blocks)
        y_hi = min(cy + hy_blocks, bh - margin_blocks)
        x_lo = max(cx - hx_blocks, margin_blocks)
        x_hi = min(cx + hx_blocks, bw - margin_blocks)
        if y_hi <= y_lo or x_hi <= x_lo:
            # Constraints collapse (tiny image); fall back to full-frame
            # search rather than returning None.
            by, bx = np.unravel_index(int(np.argmax(coarse)), coarse.shape)
        else:
            inner = coarse[y_lo:y_hi, x_lo:x_hi]
            iy, ix = np.unravel_index(int(np.argmax(inner)), inner.shape)
            by = iy + y_lo
            bx = ix + x_lo
        py = int((by + 0.5) * block)
        px = int((bx + 0.5) * block)

    # Even after the central-only search we clamp to keep the spot disk
    # fully inside the image (defensive against integer rounding at the
    # margin boundary).
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
    """Every ``interval_ms`` inspect the most recent camera frame. If the
    toggle is on AND we're in Nano regime AND the frame is bright enough
    AND a central bright spot is present, caput a relative move to the
    QG V motor. Fire-and-forget — no waiting for motion to complete, no
    retry loop; the next tick will re-check and nudge again if the spot
    is still there.

    The image is delivered by a persistent pvaccess monitor started when
    the toggle turns on. The monitor callback runs on a pvaccess worker
    thread, so `_latest_image` is protected by a lock."""

    def __init__(self, is_nano: Callable[[], bool], parent=None, **cfg):
        super().__init__(parent)
        self._is_nano = is_nano
        for k, v in DEFAULTS.items():
            setattr(self, k, cfg.get(k, v))
        self._enabled = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        # Latest decoded frame; None until the first monitor callback.
        # _latest_image_ts is the wall-clock time we received it so we
        # can detect a stopped camera (no fresh frames) and skip.
        self._latest_image = None
        self._latest_image_ts = 0.0
        self._image_lock = threading.Lock()
        self._channel = None
        self._frames_seen = 0
        # Burst state: 0 = idle, >0 = an active correction loop counting
        # nudges. Used to prevent the 5 s tick from overlapping with a
        # burst that's still cycling through nudges.
        self._burst_iter = 0
        # Track the last observed scan-state value so we only fire ONCE
        # per entry into the "flat fields" state. Without this the 5 s
        # tick would fire another nudge every tick that flat fields are
        # still being collected, drifting the intensity.
        self._last_scan_state = None

    # ── Public API ────────────────────────────────────────────────────
    def set_enabled(self, on: bool):
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            print(f"{_TAG} {_c('enabled', 'bold', 'green')} — "
                  f"{self.interval_ms/1000:g}s tick, "
                  f"mean>{self.mean_min:g}, motor={self.qg_motor_pv}, "
                  f"step={self.step_size:g} mm")
            self._start_monitor()
            self._timer.start()
        else:
            print(f"{_TAG} {_c('disabled', 'bold', 'red')}")
            self._timer.stop()
            self._stop_monitor()

    def is_enabled(self) -> bool:
        return self._enabled

    # ── pvaccess image monitor ────────────────────────────────────────
    def _start_monitor(self):
        if self._channel is not None:
            return
        try:
            from pvaccess import Channel
            self._channel = Channel(self.image_pv)
            self._channel.subscribe("harm", self._on_frame)
            # No field request — pystream's viewer subscribes plainly
            # and gets full NTNDArray frames. Passing a field() request
            # was observed to yield callbacks with empty dimensions on
            # some pvaccess versions.
            self._channel.startMonitor()
            print(f"{_TAG} {_c('monitor started', 'green')} on "
                  f"{self.image_pv}")
        except Exception as e:
            print(f"{_TAG} {_c('monitor start failed', 'red')}: {e}")
            self._channel = None

    def _stop_monitor(self):
        if self._channel is None:
            return
        try:
            self._channel.stopMonitor()
            self._channel.unsubscribe("harm")
        except Exception:
            pass
        self._channel = None
        with self._image_lock:
            self._latest_image = None
        self._frames_seen = 0

    def _on_frame(self, pv_obj):
        img = _decode_ntnd(pv_obj)
        if img is None:
            return
        ts = time.monotonic()
        with self._image_lock:
            self._latest_image = img
            self._latest_image_ts = ts
        self._frames_seen += 1

    # ── Timer tick ────────────────────────────────────────────────────
    def _tick(self):
        # Prevent overlap: if a previous burst is still nudging & waiting
        # for the motor to settle, skip this tick.
        skip = _c("tick skipped", "yellow")
        if self._burst_iter > 0:
            print(f"{_TAG} {skip}: burst still running "
                  f"(iter {self._burst_iter}/{self.max_corrections_per_burst})")
            return
        if not self._is_nano():
            print(f"{_TAG} {skip}: MICRO regime (nano_mode=False)")
            return
        # Scan-state gate: only nudge on the FIRST tick we see the scan
        # in the "flat field" state, then wait for the state to leave &
        # re-enter before nudging again. Skipping the state check
        # entirely if scan_state_pv is blank preserves the old
        # unconditional behaviour for users who don't want the gate.
        if self.scan_state_pv and self.scan_state_active:
            state = _caget_str(self.scan_state_pv)
            if state is None:
                print(f"{_TAG} {skip}: could not read {self.scan_state_pv}")
                return
            if state != self.scan_state_active:
                # Track transitions so leaving+re-entering the active
                # state re-arms the one-shot below.
                self._last_scan_state = state
                print(f"{_TAG} {skip}: scan state {state!r} "
                      f"(waiting for {self.scan_state_active!r})")
                return
            if self._last_scan_state == self.scan_state_active:
                # Already nudged during this pass through the flat-field
                # state — don't stack corrections that would drift the
                # intensity.
                print(f"{_TAG} {skip}: already nudged for this "
                      f"{self.scan_state_active!r} pass — waiting for "
                      f"state to leave and re-enter")
                return
            print(f"{_TAG} {_c('scan state entered', 'cyan')} "
                  f"{self.scan_state_active!r} — arming one nudge")
            self._last_scan_state = self.scan_state_active
        with self._image_lock:
            img = self._latest_image
            ts = self._latest_image_ts
        if img is None:
            print(f"{_TAG} {skip}: no frame received yet from "
                  f"{self.image_pv} (frames seen: {self._frames_seen}) — "
                  f"is the camera acquiring?")
            return
        age = time.monotonic() - ts
        if age > self.max_frame_age_s:
            print(f"{_TAG} {skip}: last frame is {age:.1f}s old "
                  f"(> {self.max_frame_age_s:g}s) — camera stopped?")
            return
        print(f"{_TAG} {_c('tick', 'cyan')}: image {img.shape} "
              f"dtype={img.dtype} min={img.min():.0f} max={img.max():.0f} "
              f"mean={float(np.mean(img)):.1f} "
              f"(frames seen: {self._frames_seen}, age {age:.1f}s)")
        # Kick off a correction burst — one shot now, then _step_burst
        # re-checks every settle_ms until the spot is gone or the cap is
        # hit.
        self._burst_iter = 0
        self._step_burst()

    def _step_burst(self):
        # Re-verify preconditions each iteration so a mid-burst regime
        # switch or camera stop cleanly aborts the loop.
        stop = _c("burst stopped", "yellow")
        if not self._enabled or not self._is_nano():
            print(f"{_TAG} {stop}: disabled or MICRO regime")
            self._burst_iter = 0
            return
        with self._image_lock:
            img = self._latest_image
            ts = self._latest_image_ts
        if img is None:
            print(f"{_TAG} {stop}: no frame available")
            self._burst_iter = 0
            return
        age = time.monotonic() - ts
        if age > self.max_frame_age_s:
            print(f"{_TAG} {stop}: last frame is {age:.1f}s old "
                  f"(> {self.max_frame_age_s:g}s) — camera stopped mid-burst?")
            self._burst_iter = 0
            return
        mean = float(np.mean(img))
        if mean < self.mean_min:
            print(f"{_TAG} {stop}: image mean={mean:.1f} < {self.mean_min:g}")
            self._burst_iter = 0
            return
        result = find_bright_spot(img, self.spot_radius_px,
                                  self.bg_inner_radius_px,
                                  center_search_frac=self.center_search_frac)
        if result is None:
            print(f"{_TAG} {stop}: could not compute spot ratio")
            self._burst_iter = 0
            return
        ratio, peak_y, peak_x = result
        if ratio <= self.ratio_threshold:
            print(f"{_TAG} {_c('spot cleared', 'bold', 'green')} at iter "
                  f"{self._burst_iter}: peak=({peak_x},{peak_y}) "
                  f"ratio={ratio:.2f} ≤ threshold {self.ratio_threshold:.2f}")
            self._burst_iter = 0
            return
        if self._burst_iter >= self.max_corrections_per_burst:
            print(f"{_TAG} {_c('burst cap reached', 'yellow')} "
                  f"({self.max_corrections_per_burst} nudges); "
                  f"spot still ratio={ratio:.2f} — waiting for next tick")
            self._burst_iter = 0
            return
        # Push the spot away from the image centre: upper half → +1,
        # lower → -1, scaled by direction_sign for motor wiring.
        image_center_y = img.shape[0] / 2.0
        y_sign = +1 if peak_y < image_center_y else -1
        direction = y_sign * self.direction_sign
        shift = direction * self.step_size
        rbv_pv = f"{self.qg_motor_pv}.RBV"
        current = _caget_float(rbv_pv)
        if current is None:
            current = _caget_float(self.qg_motor_pv)
        if current is None:
            print(f"{_TAG} {stop}: cannot read {rbv_pv}")
            self._burst_iter = 0
            return
        target = current + shift
        self._burst_iter += 1
        print(f"{_TAG} {_c('nudge', 'bold', 'magenta')} "
              f"{self._burst_iter}/{self.max_corrections_per_burst}: "
              f"mean={mean:.1f} peak=({peak_x},{peak_y}) "
              f"ratio={_c(f'{ratio:.2f}', 'bold', 'yellow')} → "
              f"{self.qg_motor_pv} {current:+.4f} → "
              f"{_c(f'{target:+.4f}', 'bold')} "
              f"(Δ {_c(f'{shift:+.4f}', 'bold', 'magenta')} mm)")
        caput_bg(self.qg_motor_pv, target, t=3.0)
        # Give the motor + camera time to settle, then re-check.
        QtCore.QTimer.singleShot(self.settle_ms, self._step_burst)
