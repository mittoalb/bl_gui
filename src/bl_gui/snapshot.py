"""State snapshots — like save-games for the beamline GUI.

Autosaves the current values of every monitored PV (plus the regime
file and the calibration JSON) into a **rolling JSONL log**. Each
record is one line in a ``log_NNN.jsonl`` file under
``~/.bl_gui/snapshots/``. When the current log file exceeds
``LOG_MAX_BYTES`` (default 5 MB), the next write starts a new file
with the incremented number — no single file ever grows past the
limit, so it stays openable in any editor.

The dedicated snapshot browser (see ``snapshot_window``) walks the
log files and lets the user re-load any past record by cherry-
picking which PVs to ``caput`` back. Nothing is written to EPICS
without an explicit confirmation.
"""
import json
import os
import re
import time
from typing import Callable, Dict, List, Optional, Tuple


SNAP_DIR = os.path.expanduser("~/.bl_gui/snapshots")

# Each log file caps at this many bytes; the next save opens a new
# file with the next number. Tuned so any editor / jq / grep chain
# can still open a full log file without pain.
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB

# File name pattern: log_001.jsonl, log_002.jsonl, ...
_LOG_PATTERN = re.compile(r"^log_(\d+)\.jsonl$")

# Files that are captured alongside the PV values so a snapshot fully
# describes the GUI's beamline state, not just live-EPICS values.
_REGIME_FILE = os.path.expanduser("~/.bl_gui/regime.txt")
_CALIB_FILE  = os.path.expanduser("~/.bl_gui/bl32id_zp_calibration.json")


# ── Utility ───────────────────────────────────────────────────────
def _ts_now() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path) as fh:
            return fh.read()
    except Exception:
        return None


def _write_text(path: str, text: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


# ── Log-file rotation ─────────────────────────────────────────────
def _list_log_files() -> List[Tuple[str, int]]:
    """Return ``[(path, number), …]`` in ascending number order."""
    try:
        entries = os.listdir(SNAP_DIR)
    except FileNotFoundError:
        return []
    out = []
    for name in entries:
        m = _LOG_PATTERN.match(name)
        if m:
            out.append((os.path.join(SNAP_DIR, name), int(m.group(1))))
    out.sort(key=lambda t: t[1])
    return out


def _current_log_path() -> str:
    """Path to the log file to APPEND the next record to. Opens a new
    file with the next number if the current one is at/above the size
    cap, or if no log files exist yet."""
    os.makedirs(SNAP_DIR, exist_ok=True)
    files = _list_log_files()
    if not files:
        return os.path.join(SNAP_DIR, "log_001.jsonl")
    last_path, last_num = files[-1]
    try:
        size = os.path.getsize(last_path)
    except OSError:
        size = 0
    if size >= LOG_MAX_BYTES:
        return os.path.join(SNAP_DIR, f"log_{last_num + 1:03d}.jsonl")
    return last_path


# ── Snapshot API ──────────────────────────────────────────────────
def take_snapshot(pv_names: List[str],
                  getter: Callable[[str], Optional[str]]) -> Dict:
    """Sample every PV in ``pv_names`` via ``getter`` and bundle with
    the current regime + calibration files. ``getter`` is typically
    ``pve.get`` — a synchronous caget wrapper returning None on
    failure. Returns a dict:
      - ts_epoch (float), ts_str (string)
      - pvs      {pv_name: value}  (None-valued PVs dropped)
      - regime   raw text or None
      - calib    raw text or None
    """
    pv_values: Dict[str, Optional[str]] = {}
    for pv in pv_names:
        try:
            v = getter(pv)
        except Exception:
            v = None
        if v is not None:
            pv_values[pv] = v
    return {
        "ts_epoch": time.time(),
        "ts_str":   _ts_now(),
        "pvs":      pv_values,
        "regime":   _read_text(_REGIME_FILE),
        "calib":    _read_text(_CALIB_FILE),
    }


def save_snapshot(data: Dict) -> Tuple[str, int]:
    """Append a snapshot record to the current log file (rolls to a
    new file if the current one is at the size cap). Returns the
    ``(path, byte_offset)`` where the record was written so callers
    can identify it later without re-scanning."""
    path = _current_log_path()
    line = json.dumps(data, separators=(",", ":")) + "\n"
    try:
        offset = os.path.getsize(path) if os.path.exists(path) else 0
    except OSError:
        offset = 0
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
    return path, offset


def load_snapshot_at(path: str, offset: int) -> Optional[Dict]:
    """Read one record from a log file starting at the given byte
    offset. Returns None on parse failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            fh.seek(offset)
            line = fh.readline()
        return json.loads(line)
    except Exception:
        return None


def list_snapshots() -> List[Tuple[str, int, float, int]]:
    """Walk every log file line by line and return an index of all
    records: ``[(path, byte_offset, ts_epoch, num_pvs), …]`` sorted
    newest first. Corrupt lines (partial writes, garbled JSON) are
    skipped so a single bad record can't hide the rest."""
    out: List[Tuple[str, int, float, int]] = []
    for path, _ in _list_log_files():
        offset = 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line_len = len(line.encode("utf-8"))
                    try:
                        d = json.loads(line)
                        ts = float(d.get("ts_epoch") or 0.0)
                        n = len(d.get("pvs") or {})
                        out.append((path, offset, ts, n))
                    except Exception:
                        pass
                    offset += line_len
        except Exception:
            continue
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def newest_snapshot() -> Optional[Dict]:
    """Return the most recently saved snapshot dict, or None if the
    log is empty. Used by the autosaver to compare against the fresh
    sample before deciding whether to write."""
    snaps = list_snapshots()
    if not snaps:
        return None
    path, offset, _, _ = snaps[0]
    return load_snapshot_at(path, offset)


def diff_pv_values(prev: Dict, curr: Dict) -> List[str]:
    """Return the list of PV names whose value changed between two
    snapshot dicts. Additions and removals count as changes."""
    prev_pvs = (prev or {}).get("pvs") or {}
    curr_pvs = (curr or {}).get("pvs") or {}
    changed = []
    for pv, v in curr_pvs.items():
        if prev_pvs.get(pv) != v:
            changed.append(pv)
    for pv in prev_pvs:
        if pv not in curr_pvs:
            changed.append(pv)
    return changed


def delete_snapshot(path: str, offset: int) -> bool:
    """Remove one record from the log file by rewriting the file
    without the target line. Safe for MB-scale files; the log-size
    cap keeps this cheap. Returns True on success."""
    tmp = path + ".tmp"
    try:
        with open(path, "r", encoding="utf-8") as src, \
             open(tmp, "w", encoding="utf-8") as dst:
            pos = 0
            for line in src:
                line_len = len(line.encode("utf-8"))
                if pos != offset:
                    dst.write(line)
                pos += line_len
        os.replace(tmp, path)
        # If the file is now empty, remove it to keep the browser tidy.
        if os.path.getsize(path) == 0:
            os.remove(path)
        return True
    except Exception:
        # Never leave a half-written tmp behind.
        try: os.remove(tmp)
        except OSError: pass
        return False


def restore_regime_file(text: Optional[str]) -> None:
    if text:
        _write_text(_REGIME_FILE, text)


def restore_calib_file(text: Optional[str]) -> None:
    if text:
        _write_text(_CALIB_FILE, text)
