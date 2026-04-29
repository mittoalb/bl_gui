# bl_gui — agent documentation

This file is the single point of reference for an AI agent working on
this repository. It captures architecture, conventions, gotchas, the
beamline-specific integration points, and the history of decisions
already baked into the code.

---

## 1. Purpose

bl_gui is a generic PyQt5 + pvaccess motor / optics control panel,
originally built for the APS 32-ID-B transmission X-ray microscope
(`layouts/bl32id.json`) and generalised to support multiple beamlines
through per-beamline JSON layout files.

It is a **thin GUI on top of EPICS**: every interactive widget either
issues a `caput` (subprocess-bounded) or subscribes to a PV via
`pvaccess` and renders the live value. There is **no business logic in
the IOC layer**; computations like calibration interpolation are kept
in Python on the GUI side.

---

## 2. Repo layout

```
bl_gui/
├── pyproject.toml            ─ setuptools build, console script `bl_gui`
├── README.md
├── AGENTS.md                 ─ this file
├── run_bl_gui.sh             ─ wrapper that activates the pystream conda
│                                env and launches the GUI
└── src/bl_gui/
    ├── __init__.py
    ├── __main__.py           ─ python -m bl_gui entry
    ├── main_window.py        ─ Win class, panels, save/load, all wiring
    ├── motor.py              ─ MC motor card widget; GROUPS list of
    │                            motor PVs; SET button, flash-on-move
    ├── motor_debug.py        ─ "Motor Details…" dialog (per-card popup)
    ├── pv.py                 ─ PVEngine (pvaccess monitors) + caput_bg
    │                            (subprocess caput thread pool)
    ├── pv_field.py           ─ PVField (sp/rb/btn/btn_pair/cmb/led),
    │                            ValveField (3-PV row, optional
    │                            highlight_buttons mode), ToggleField
    │                            (single state-aware button)
    ├── widgets.py            ─ Panel (draggable frame), CfgButton
    │                            (configurable shell/caput/url/script
    │                            launcher), WidgetEditor dialog, helpers
    ├── row_builder.py        ─ "Add PV Row…" dialog used in edit mode
    ├── theme.py              ─ Qt stylesheet + _LAY default-path holder
    ├── layout.json           ─ default layout (legacy)
    ├── layouts/
    │   ├── bl32id.json       ─ committed beamline default (template)
    │   └── bl2bm.json
    └── beamlines/
        └── bl32id/
            ├── __init__.py
            ├── xanes_calib.py   ─ ZP energy-calibration table dialog,
            │                      JSON persistence
            ├── qgmax_trigger.py ─ writes pystream's QGMax request file
            └── autofocus.py     ─ scintillator-screen autofocus sweep
```

---

## 3. How the app starts

`bl_gui [<layout-name>] [edit]`

1. `main()` resolves the layout argument (absolute → cwd-relative →
   bundled `src/bl_gui/layouts/<name>` → bundled `<name>`). The
   resolved path is stored in `theme._LAY`.
2. `QApplication` is created. App-wide font is **DejaVu Sans 9pt**.
   HiDPI scaling and HiDPI pixmaps are enabled before the QApplication
   exists.
3. A global `_PressFlash` event filter is installed on the QApplication.
   It paints a translucent yellow `QFrame` overlay on **any**
   `QAbstractButton` for ~200 ms when the user clicks/presses-with-
   keyboard. The flash is applied **before** any slow caput runs, so it
   stays visible during stalls — visual confirmation that the click
   registered.
4. `Win(allow_edit=…)` constructs the window:
   * builds tabs (`User Mode`, `Expert Mode`),
   * for each tab calls `_build_all_panels(tab_name)` which constructs
     all panels (Shutters, Beam, Presets, In/Out, Energy, Camera, Crop,
     Cells, BPM/EPID, Shaker, Launchers, Displays, motor groups, …),
   * `_load_layout()` reads the per-user override file, falling back to
     the bundled template,
   * (if `allow_edit`) enters edit mode after a 0 ms QTimer,
   * `QTimer.singleShot(300, _start_monitors)` schedules monitor setup.
5. `_start_monitors` collects the union of PVs from every motor card
   and every `monitored_pvs()` helper, **then runs `monitor_many` in a
   background `threading.Thread`**. Each `pvaccess.Channel(pv, CA)` +
   `startMonitor()` can block for seconds on CA discovery; without the
   thread the GUI freezes for the duration.
6. PV updates flow back via `PVEngine.updated` (Qt signal,
   thread-safe). `Win._on_pv` dispatches each update to motor cards
   (`MC.apply_one`) and to PVField/ValveField/ToggleField widgets that
   declare a matching PV via their `pv` / `status_pv` attribute.

---

## 4. Layout: storage and overwrite-safety

### Per-user override

Layouts are saved to **`~/.bl_gui/<bl_name>.json`**, never to the
bundled file in the repo. Load order:

```python
def _lay_path():
    u = _user_lay_path()        # ~/.bl_gui/<basename(_LAY)>.json
    return u if os.path.isfile(u) else _bundled_lay_path()
```

This means:

* Different accounts on the same NFS-mounted home tree are isolated.
* The bundled `src/bl_gui/layouts/bl32id.json` stays as a clean
  beamline starter, committed to git.
* First run on a fresh account loads the bundled template. The first
  save (Ctrl+S or edit-mode exit) creates the user file.

### Save triggers

Saves are **explicit** (this is intentional — auto-saves were
overwriting hand-edited layouts):

| trigger | path |
|---|---|
| `Ctrl+S` / `File → Save Layout` | `_explicit_save_layout` → `_save_layout` |
| Exit edit mode | `_toggle_edit(False)` → `_save_layout` |
| `closeEvent` while in edit mode | `_save_layout` (only if `_edit_mode=True`) |
| TWV step Enter, click, etc. | **does not** save |

`_save_layout` writes via `tmp + os.fsync + os.replace` to make the
write atomic and survive a crash mid-save.

### What is saved

```python
{
  "_font_scale": 100,
  "_tabs": ["User Mode", "Expert Mode"],
  "_tab_sizes": {"User Mode": [w, h], ...},
  "_tab_label_fs": 9,
  "_deleted_panels": [...],
  "_panels": {key: [x,y,w,h]},      # geometry per panel
  "_tab_map": {key: "User Mode"},   # which tab holds the panel
  "_titles": {key: "Energy"},       # panel title text overrides
  "_title_fonts": {key: 9},
  "_buttons": {key: [CfgButton.to_dict(), ...]},
  "_styles": {"<key>|||<btn-text>": {bg, fg, fs, w, h}},
  "_mcs": {key: [{label, pv, custom, twv}]},
  "_pv_fields": {key: {field_id: pv | get_pvs_dict()}},
  "_custom_rows": {key: [row_cfg, ...]},
  "_io_labels": {fid: text}
}
```

Panel keys are `"<Base>::<Tab>"` (e.g. `Energy::User Mode`).
Duplicated panels become `"<Base>#<N>::<Tab>"` to keep keys unique.

### Cross-tab sync

Single-GUI changes propagate across tabs in three ways:

* PV value changes — flow naturally via the EPICS monitor.
* PV **reassignments** (right-click → Edit PV…) — `_pv_field_rebind`
  → `_propagate_field_change` copies state to every sibling field
  (same `field_id`, same panel base name, different tab).
* In/Out header label renames — every fid's label is held in
  `self._io_labels[fid]` as a list of QLabels (one per tab); a rename
  walks the list.

---

## 5. PV layer (`pv.py`)

### `PVEngine`

* Single instance owned by `Win`, used by `_on_pv`.
* Thread-safe `monitor(pv_name)`: idempotent, lazy-creates a
  `pvaccess.Channel` and `startMonitor`. `monitor_many(list)` is just
  a loop — but **must** run off the GUI thread (see point 5 of section
  3).
* `_on_change` extracts the value via `_extract` (handles enum dicts
  with `index/choices`, char waveforms decoded as utf-8, fallbacks),
  emits `updated(pv_name, val)` on a Qt signal.
* `stop_all` sets `_shutting_down=True` and disconnects the signal
  **before** tearing channels down. Prior versions segfaulted on
  close because pvaccess callbacks could fire into half-deleted Qt
  objects.

### `caput_bg(pv, val, t=5.0)`

* Submits to a 16-worker `ThreadPoolExecutor`.
* Uses bounded `subprocess.run(["caput", pv, str(val)], timeout=t)` —
  not `pvaccess.Channel.put()`, which has no timeout and was observed
  to wedge under heavy use, freezing the worker pool and stalling the
  GUI.
* Logs failures (`[CAPUT] PV=val rc=N stderr=…`); silent on success.

---

## 6. Widgets (`pv_field.py`)

### `PVField` — single-PV row

`kind`:

| kind | widget | direction |
|---|---|---|
| `sp` | QLineEdit | caput on Enter |
| `rb` | QLabel    | monitored, displays formatted value (`fmt`) |
| `cmb` | QComboBox | monitored; caput selected enum index |
| `btn` | QPushButton | caput a fixed `button_value` on click |
| `btn_pair` | two QPushButtons | each caputs to a different PV/value |
| `led` | QLabel "●" | monitored; lights green when non-zero |

`sp` widgets carry a distinct dark-blue background (`#2c3e50`) with a
bright blue border so editable fields are obviously different from
read-only labels.

### `ValveField` — three-PV row

`status_pv` (monitored) + `on_pv` (action) + `off_pv` (action). Two
buttons. Has flags:

* `pulse=True` (default): on click writes `value`, then 300 ms later
  writes `0`. Required for edge-triggered PLC bits which latch high
  and would refuse subsequent writes.
* `invert_status=False`: for `CLSD_PL` shutter records where 1 means
  closed; flips the on/off interpretation.
* `vertical=False`: when `True`, lays out as `[name][status][btn][btn]`
  vertically — used for the Shutters panel.
* `highlight_buttons=False`: hides the status label; instead renders
  the active button bright/bold and the inactive one dimmed. Used for
  Camera Acquire and Shaker run/stop.
* `status_on_text`/`status_off_text`: customise the status-label text
  (default `"ON"`/`"OFF"`; shutters use `"OPEN"`/`"CLOSED"`).
* `on_value`/`off_value`: int **or** string. Strings get caput'd
  literally — used for the Uniblitz shutter where the PV is an enum
  with names `"Open"`/`"Close"`.

`update_value` strings list of recognised on-states:
`on, open, true, high, yes, 1, run, running, active, busy, start,
started, enable, enabled, acquire, acquiring`.

### `ToggleField` — single state-aware button

Same monitor/edit-trigger structure as ValveField but with one
button. `state_label=True` makes the button text reflect the **current
state** (e.g. `YES`/`NO`) instead of what the click will do
(`Enable`/`Disable`); the colour (green=on, red=off) follows state in
both modes.

### Right-click menus

Every PV-bound widget exposes `Copy <name>: <pv>` entries (always
available, regardless of edit mode). Edit-mode adds the `Edit…`,
`Delete Row`, `Add PV Row here…` entries.

---

## 7. CfgButton (configurable launcher)

`widgets.CfgButton` is a `QPushButton` with `action_type` ∈
{`shell`, `caput`, `url`, `script`} and an `action` string.

* In edit mode, right-click → `Edit…` opens a `WidgetEditor` dialog
  whose **third tab** is **Action** (label, action_type, action) —
  the PV tab is hidden for CfgButtons because they don't talk to a
  single PV.
* `to_dict()` saves `label, type, action, bg, fg, font_size`.
* The Energy panel's *Calibration*, *Cal Files*, the QGMax button,
  and the Launchers/Displays/Presets panels are all CfgButtons.

### Default-button merge

Launchers / Displays / Presets panels register two attributes on
themselves at build time:

* `p._cfg_btn_defaults = (bg, fg, font_size)` — colour scheme.
* `p._default_btn_specs = [(label, cmd) | (label, atype, cmd)]` —
  the canonical default set.

On load, `_load_layout` clears `p.custom_buttons`, then iterates the
**saved** button list. **If a default's label OR action is missing
from the saved list**, the default is appended (so newly added defaults
appear for users with old saves). Buttons saved with a stale
`bg="#2d2d2d"` are forcibly rewritten to the panel's current default
colours; user-customised colours are preserved.

For Launchers panel `_grid_cols=4`, Displays `_grid_cols=3` — the
load path uses these to restore the grid `(row, col)` placement.

---

## 8. Energy panel — calibration plumbing

The Energy panel's *Go* button is **not** a default `caput PV value`.
Its handler `_on_set_energy` does:

1. Validate the SP via `_on_energy_sp_return` (range 6.5–12 keV is
   enforced before `caput`).
2. Read the **`Use Calibration`** toggle (`32id:TXMOptics:EnergyUseCalibration`).
3. **If `YES`** → call `_move_motors_from_plugin`:
   * Read calibration table from
     `~/.bl_gui/bl32id_zp_calibration.json`.
   * For each axis (X/Y/Z/QG-V/QG-H), polynomial-fit the (E, value)
     pairs (`_polyfit_interp`, degree = `min(3, N-1)`), evaluate at
     the target energy, and synchronously `caput` the result to the
     motor PV. The synchronous caput surfaces failures in real time.
4. **If `NO`** → no motor moves; just step 5.
5. `caput_bg("32id:TXMOptics:EnergySet", 1)` — fires the IOC's
   energy-change handler for the mono and undulator.

The IOC's cal-file machinery (`txmoptics.py`) is **bypassed entirely**
in the YES path. Cal-file PVs are only touched by the explicit
**Generate Cal Files** button (purple) in the Energy panel:

* Reads target = current SP, range = `range_keV` from the calibration
  JSON, and the calibration table.
* Writes `Energy_<E−range>keV.txt` and `Energy_<E+range>keV.txt` into
  `/home/beams/USERTXM/epics/synApps/support/txmoptics/iocBoot/iocTXMOptics/`.
* Each file contains `energy <kev>\n<motor_pv> <value>\n…`.
* Synchronously `caput`s **just the filenames** to the
  `EnergyCalibrationFileOne/Two` PVs (those are 40-char `stringout`
  records — full paths get truncated). The IOC's working directory is
  expected to resolve them.

The **Range ± (keV)** spinbox in the Energy panel persists on every
change to `range_keV` in the same JSON.

### Calibration JSON format

```json
{
  "pvs": {
    "energy_rb_pv": "32ida:BraggERdbkAO",
    "energy_units": "keV",
    "zp_x_pv":      "32idbTXM:mcs2:c1:m13",
    "zp_y_pv":      "32idbTXM:mcs2:c1:m14",
    "zp_z_pv":      "32idbTXM:mcs2:c1:m15",
    "qg_v_pv":      "32idQG:m1",
    "qg_h_pv":      "32idQG:m2"
  },
  "points": [
    [E_eV, X_mm, Y_mm, Z_mm, QGV_mm, QGH_mm],
    ...
  ],
  "range_keV": 0.5
}
```

Legacy formats (4-column `[E, ZP, X, Z]`, 5-column `[E, ZP, X, Y, Z]`)
are auto-migrated on load: the ZP-focus column folds into the Z slot
when Z is empty.

`xanes_calib.py` exposes `load_config()` / `save_config(cfg)` used by
both the dialog and the energy handler.

The dialog auto-saves on **any** close (X, Alt+F4, `Save & Close`) so
edits aren't silently lost.

### IOC-side patch (`txmoptics.py`)

Lives outside this repo at
`/home/beams/USERTXM/epics/synApps/support/txmoptics/txmoptics/txmoptics.py`.
A patch was added to the calibration loop (around line 909) so any PV
in the cal file that doesn't match the named aliases (`DetectorZ`,
`ZonePlateZ/X/Y`) is `caput` directly via `epics.PV(pv).put(val,
wait=True)`. This is what makes Queensgate motors (or any other motor
the cal file mentions) move through the IOC path.

---

## 9. Beamline-specific code

`src/bl_gui/beamlines/<bl_name>/` holds beamline integrations. Each
sub-package is a normal namespace package; it is auto-discovered by
`setuptools.packages.find` (so an editable install picks new ones up
on `pip install -e .` only — re-run if you add a new sub-package).

* **xanes_calib.py** — the calibration table dialog (above).
* **qgmax_trigger.py** — `trigger()` writes
  `~/.pystream_qgmax_request.json` with a fresh timestamp; pystream's
  `QGMaxBackgroundWatcher` polls that file and runs one optimization
  cycle. `read_status()` reads
  `~/.pystream_qgmax_response.json` and returns
  `{running, started_ts, completed_ts, started_at}`.
  The main GUI's QGMax button polls this every 500 ms and styles
  itself running/idle.
* **autofocus.py** — sweeps a focus motor across `center ± half_range`,
  pulls a frame from a PVA image PV, computes variance-of-Laplacian
  sharpness, and drives the motor to the maximum. Importable as
  `autofocus.run(motor_pv=…, image_pv=…, …)` or runnable as
  `python -m bl_gui.beamlines.bl32id.autofocus`.

---

## 10. Editing in the GUI

`bl_gui <layout> edit` enables edit mode at startup. In edit mode:

* Panels can be dragged and resized; their geometry is saved.
* Right-click on a Panel: rename, change title font, add motor /
  PV row, duplicate panel, delete panel.
* Right-click on an MC motor card: open Motor Details, edit motor PV,
  duplicate, change font, delete.
* Right-click on a PVField/ValveField/ToggleField: edit PV(s), delete
  row, add PV row here.
* Right-click on a CfgButton: edit Action (label, type, command).

The **font slider** in the top bar (50–200 %) re-applies fonts to
every motor card; `Ctrl+= / Ctrl+- / Ctrl+0` are the keyboard shortcuts
(also active in view mode).

Any panel duplication snapshots the source's geometry, layout
orientation, motor cards, and `custom_buttons` list (via
`CfgButton.to_dict`/`from_dict`), then rebuilds them in the new panel.

---

## 11. Conventions baked into this codebase

* **No comments unless the *why* is non-obvious.** The codebase
  follows a "narrate intent, not the code" rule — most one-liners
  carry no comment.
* **Defer expensive PV work off the GUI thread.** Monitor setup runs
  in a `threading.Thread`. caput goes through a 16-worker pool.
  Synchronous caput (used in the energy handler) uses
  `subprocess.run(["caput", …], timeout=…)` with a hard timeout.
* **Subprocess `caput`/`caget` over `pvaccess`.** The pvaccess Python
  bindings have no put-timeout and were observed to wedge; the
  EPICS-base `caput` CLI binary has a robust timeout model and is
  always used for writes.
* **Always quote PV string values into caput**: `subprocess.run(["caput",
  pv, str(val)], …)`. Argv form, not shell.
* **Per-user persistence.** Config files live under `~/.bl_gui/`.
  Bundled defaults stay clean.
* **Atomic writes.** Every `_save_layout` call does
  `open(tmp, "w") + fsync + os.replace`.
* **Permission boundary.** Anything that lives outside this repo —
  e.g. `/home/beams/USERTXM/epics/synApps/support/txmoptics/...` — is
  **read-only** for the agent. Patches have been provided as
  copy-pasteable snippets rather than direct edits.

---

## 12. Known gotchas

| symptom | cause | fix |
|---|---|---|
| `[Errno 2] No such file or directory: ''` from txmoptics | EnergyCalibrationFile* PV was empty when EnergySet ran (race) | Cal files are now sync-caput'd, and only by the explicit Generate Cal Files button |
| Cal file path truncated to 40 chars | EPICS `stringout` 40-char limit | Write filenames only, IOC resolves via its own CWD |
| Buttons don't flash visibly | `QGraphicsColorizeEffect` is suppressed by inline stylesheets | Use the QFrame overlay `_PressFlash` (current implementation) |
| Stale gray button colours after pip install | `CfgButton.from_dict` uses the saved `bg` ("#2d2d2d") | Load-path forcibly overrides "#2d2d2d" with `_cfg_btn_defaults` |
| Same calibration appears in two accounts | Layout JSON saved into the bundled (NFS-shared) path | Save path is now `~/.bl_gui/<bl_name>.json` |
| Motors don't follow `caput` | `.SET=1` (set mode), motor disabled, or limit reached — *not* a GUI bug | `caget pv.SET pv.DISP pv.HLS pv.LLS pv.LVIO` to diagnose |
| MEDM display fields are white | Macro substitutions missing (e.g. `dcm_motors9.adl` needs `P, P1, M1..M9`) | Pass full macro list in the `medm -macro` arg |
| Segfault on close | pvaccess callback firing into a half-torn-down Qt | `PVEngine.stop_all` sets `_shutting_down=True` and disconnects the signal first |
| Random GUI freezes | pvaccess `Channel.put()` blocking the worker pool | All caputs go through bounded `subprocess.run(["caput", …], timeout=t)` |

---

## 13. Running

```
# Editable install in the active conda env
pip install -e .

# Run with the bl32-ID bundled layout
bl_gui bl32id.json

# Edit mode
bl_gui bl32id.json edit

# Or via the wrapper which activates pystream conda env
./run_bl_gui.sh
./run_bl_gui.sh edit
```

Conda env requirement: `pystream` (as set up at APS); the package
declares `PyQt5 >= 5.15` and `pvapy` (which installs `pvaccess`).
`numpy` is required for the polynomial calibration interpolation.

---

## 14. Where to extend

* **New beamline** → add `src/bl_gui/layouts/<bl>.json` and
  optionally a sub-package `src/bl_gui/beamlines/<bl>/`. Run
  `bl_gui <bl>.json edit` to lay it out and save.
* **New PV-bound widget kind** → add to `pv_field.PVField._build_inner`,
  give it a stable id, register a `monitored_pvs()` and
  `update_value()` if it should react to a PV.
* **New programmatic action button** → add to
  `Win._build_all_panels` as a `CfgButton` with the right
  `action_type`/`action`, *and* register a default spec on the panel
  (`p._default_btn_specs`) so future installs auto-include it.
* **Integration with another pystream tool** → mimic
  `qgmax_trigger.py`: a tiny module that writes a request file plus a
  `read_status()` helper, with a button in the main window polling
  the status file.

---

## 15. EPICS PVs and CLI tools

This codebase assumes a working EPICS environment (`EPICS_CA_ADDR_LIST`
or auto-discovery), with the standard CLI binaries on `$PATH`.

### CLI tools used

| binary | role | typical use here |
|---|---|---|
| `caget`  | one-shot read | bounded `subprocess.run(["caget", "-t", pv], timeout=…)` for reading cal-file PVs, `MaxSizeX/Y` for binning math, motor RBVs in autofocus |
| `caput`  | one-shot write | the **only** write path used by bl_gui (see `pv.caput_bg` and the sync caput in `_apply_zp_calib_from_plugin`) |
| `caput -S` / `caget -S` | string-coerce a char waveform | used for cal-file PV emptiness detection — char waveforms otherwise show "0 …" byte counts |
| `medm`  | open `.adl` displays | every entry in the **Displays** panel is `medm -x -macro '…' /path/file.adl &` |
| `pvaccess` (Python) | live PV monitors and PVA image fetch | `pv.PVEngine` for monitors, `autofocus._grab_image` for one-shot image grabs |

`pvaccess.Channel.put()` is **never** used. It has no timeout and was
observed to wedge the worker pool. Always go through `subprocess` +
the `caput` CLI for writes; pvaccess is monitor-only.

### PV record types we touch

| record | notes / gotchas |
|---|---|
| `motor`         | Writing to the bare PV name (`32idbTXM:mcs2:c1:m13`) targets `.VAL` and moves the motor. `.RBV` is the readback. Fields used: `RBV`, `VAL`, `DMOV`, `MOVN`, `EGU`, `DESC`, `HLS`, `LLS`, `LVIO`, `TWV`, `TWF`, `TWR`, `STOP`, `SET`. The `_able` companion record (e.g. `32idbTXM:mcs2:c1:m13_able`) follows APS convention: `0` = enable, `1` = disable. |
| `bi` / `bo`     | Enums with `ZNAM`/`ONAM` (e.g. `Yes`/`No`, `Run`/`Stop`, `Acquire`/`Done`, `Open`/`Close`). pvaccess delivers a dict `{"index": N, "choices": [...]}` — `_extract` returns `choices[idx]` so `update_value` sees the **string**. The on/off-state matcher in `ValveField` / `ToggleField` therefore lists every plausible string spelling. caput accepts either the integer index or the string name. |
| `mbbi` / `mbbo` | Same dict structure, more states. Same handling. |
| `stringout`     | **40-char limit** (silent truncation past 40). Use bare filenames, not full paths. Hit by `EnergyCalibrationFileOne/Two`. |
| `waveform` (CHAR) | Long-form strings. Read with `caget -S` to get the string; raw `caget` returns the byte-count + bytes form. Empty waveforms show `"0"` (zero bytes). |
| `ai` / `ao`     | Plain analog. Energy SP, exposure time, AcquireTime, etc. |
| `longout` / `longin` | Integer counters / set-points. Camera `BinX`, `SizeX`, `MaxSizeX_RBV`. |
| `calc` / `transform` | TXMOptics composite PVs (`Energy`, `EnergySet`, `EnergyBusy`). Writing `EnergySet=1` triggers the IOC's energy-change handler. |
| `proc`-on-put   | `Open.PROC` / `Close.PROC` etc. — any write fires the `process` of the linked record. `caput PV 1` is the standard way to "trigger" a sequence. The shutter pair `32idb:rshtrA:Open.PROC` / `Close.PROC` work like this. |

### PV naming conventions at bl32-ID

| prefix | scope |
|---|---|
| `32id:TXMOptics:…` | TXMOptics composite IOC (Energy, EnergySet, EnergyBusy, Use Calibration, Cal File One/Two, MoveAllIn/Out, MovePinholeIn/Out, …) |
| `32ida:…`         | DCM / mono-side IOC (`BraggERdbkAO`, motors `m1..m8`) |
| `32idb:…`         | B-station soft IOC, has motor `m5` for the 9th DCM channel; `rshtrA/B:Open.PROC` shutter triggers |
| `32idbSoft:…`     | beamline soft IOC (`PLC1:C1`, `PLC1:oC2x`, `PLC1:oC3x` — valve PVs) |
| `32idbTXM:mcs2:c1:m1..m15` | piezo controller — Zone Plate, Pinhole, Beamstop, Sample motors |
| `32idbTXM:mcs:c2:m1/m2`    | Bertrand Lens motors |
| `32idbTXM:nf:m2/m4`        | Nano Focus motors |
| `32idbTXM:ens:c1:m1`       | Ensemble |
| `32idQG:m1/m2`             | Queensgate piezo (V, H) |
| `32idbTXM:uniblitz:control` | Uniblitz shutter (single PV, enum `Open`/`Close`) |
| `32idbSP1:cam1:…`          | AreaDetector camera 1 (Acquire, AcquireTime, BinX/Y, SizeX/Y, MaxSizeX/Y_RBV) |
| `32idbSP1:Pva1:Image`      | AreaDetector PVA plugin — live NTNDArray image stream |
| `32idbSP1:Proc1:…`         | AreaDetector Proc plugin (filter, flat-field) |
| `32idbShaker:shaker:…`     | shaker controller (`run`, `frequency`, `numPoints`, `A:ampMult`, …) |
| `PB:32ID:STA_*_CLSD_PL`    | front-end shutter "closed-please" status — invert (`1` = closed) |
| `S32ID:USID:…`             | upstream insertion device (undulator) |
| `S-DCCT:CurrentM`, `S:ActualMode` | storage-ring bunch info |

### Image acquisition (PVA)

`autofocus.py` reads NTNDArray images via `pvaccess.Channel(...).get()`,
then `pv_obj.toDict()`. The dict has:

* `dimension`: list of `{size, ...}` per axis (use `[1]['size']` for
  rows, `[0]['size']` for cols).
* `value`: a sub-dict whose key is the dtype tag —
  `ubyteValue` / `ushortValue` / `uintValue` / `byteValue` /
  `shortValue` / `intValue` / `floatValue` / `doubleValue`. Pick
  whichever exists, reshape to `(ny, nx)`.

This is the canonical AreaDetector PVA layout; if a beamline switches
to `mjpg` or some other plugin, `_grab_image` needs adjustment.

### How we test PV existence quickly

```bash
caget 32id:TXMOptics:Energy 32id:TXMOptics:EnergyBusy
caget -t 32idbTXM:mcs2:c1:m13.RBV
caget -d dbr_class 32id:TXMOptics:EnergyCalibrationFileOne   # type info
cainfo 32idQG:m1                                              # full record info
```

If a PV is missing the IOC is down or your `EPICS_CA_ADDR_LIST` is
wrong. The bl_gui itself will silently fail to monitor (it logs
`[PV] failed to monitor <pv>: <err>`), and any `[CAPUT] … rc=...` log
line carries the actual stderr from `caput`.

---

## 16. Glossary

* **MC** — motor card, the visual block per motor (label, RBV, go-to,
  arrows + step, STOP, SET, Enable). Defined in `motor.py`.
* **PV** — EPICS Process Variable. All I/O is by PV name string.
* **CA / pvaccess** — the EPICS protocol layers. We use `pvaccess`
  Python bindings for monitors only; writes are CLI `caput`.
* **TWV / TWF / TWR** — motor record fields: tweak value, tweak
  forward, tweak reverse.
* **CLSD_PL** — APS shutter status record convention; 1 means
  *closed*. ValveField's `invert_status=True` handles it.
