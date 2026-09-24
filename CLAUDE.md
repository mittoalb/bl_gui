# bl_gui — repo-level guidance

## bmsg discipline (required for any new widget)

bl_gui is a peer client of the same EPICS blackboard as pystream. PV
traffic goes through `bmsg`: `https://github.com/mittoalb/bmsg`.
bl_gui's existing `PVEngine` (in `bl_gui/pv.py`) stays for the
layout-wide monitor set; bmsg's `PVHub` handles **writes that must
verify**.

**On `Win` (main window):**

```python
from bmsg import PVHub
self.hub = PVHub(parent=self)   # already wired in Win.__init__
```

**Rules:**

* PV write that must be known-to-have-stuck (Apply Binning, Apply ROI,
  anything AreaDetector-sensitive) → `self.hub.put(pv, v, verify_timeout=2.0)`.
  If it returns `False`, log the mismatch — a silent IOC revert
  (Acquire lock, autosave, competing writer) is otherwise invisible.
* Fire-and-forget write (mostly PVField / motor UI where the user
  sees the effect immediately) → `caput_bg` from `bl_gui/pv.py` is
  fine and remains in use.
* PV read that a widget displays → `PVEngine.monitor(pv)` + subscribe
  to `PVEngine.updated(pv_name, value)` — this is the existing pattern
  and works well; do not switch to raw `caget`.

**Never in widget code:**

* `subprocess.run(["caget", ...])`.
* A raw `pvaccess.Channel` opened outside `PVEngine` / `PVHub`.
* A `~/.pystream_*.json` handshake for something that could go
  through EPICS (a dedicated status PV both apps monitor is almost
  always the right answer).

Reference migration: `Win._apply_cam_binning` (verified caput via
`self.hub.put`).
