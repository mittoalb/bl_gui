"""QGMax trigger + status helpers.

pystream's QGMax optimization (image-mean motor auto-alignment) uses a
file-based request/response protocol:
  ~/.pystream_qgmax_request.json   — written here to request a cycle.
  ~/.pystream_qgmax_response.json  — pystream updates with started_ts /
                                     last_completed_ts so callers can
                                     tell whether a cycle is in flight.
"""
import json
import os
import time

REQUEST_FILE = os.path.expanduser("~/.pystream_qgmax_request.json")
RESPONSE_FILE = os.path.expanduser("~/.pystream_qgmax_response.json")


def trigger():
    """Write a new timestamp to the QGMax request file. Returns the
    timestamp so callers can later correlate with response state."""
    ts = time.time()
    try:
        os.makedirs(os.path.dirname(REQUEST_FILE) or ".", exist_ok=True)
    except Exception:
        pass
    with open(REQUEST_FILE, "w") as fh:
        json.dump({"ts": ts}, fh)
    return ts


def read_status():
    """Return a dict describing current QGMax state, or None if no
    response file exists yet. Keys of interest:
      running       bool — a cycle is in flight (started_ts > completed).
      started_ts    float — timestamp of the in-flight (or last) cycle.
      completed_ts  float — timestamp of last completed cycle.
      started_at    float — wall-clock of current cycle start (if any).
    """
    try:
        with open(RESPONSE_FILE) as fh:
            s = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    except Exception:
        return None
    started = float(s.get("started_ts") or 0.0)
    completed = float(s.get("last_completed_ts") or 0.0)
    return {
        "running": started > completed,
        "started_ts": started,
        "completed_ts": completed,
        "started_at": float(s.get("started_at") or 0.0),
    }
