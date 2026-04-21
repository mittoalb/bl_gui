"""One-shot QGMax trigger.

pystream's QGMax optimization (image-mean motor auto-alignment) watches
~/.pystream_qgmax_request.json for a new timestamp and runs a single
optimization cycle in response. This module just writes the request
file so any GUI can fire a QGMax run with a single call.
"""
import json
import os
import time

REQUEST_FILE = os.path.expanduser("~/.pystream_qgmax_request.json")


def trigger():
    """Write a new timestamp to the QGMax request file. Returns the
    timestamp so callers can later check the response file for an ack."""
    ts = time.time()
    try:
        os.makedirs(os.path.dirname(REQUEST_FILE) or ".", exist_ok=True)
    except Exception:
        pass
    with open(REQUEST_FILE, "w") as fh:
        json.dump({"ts": ts}, fh)
    return ts
