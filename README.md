# bl_gui — Beamline Optics GUI

Generic PyQt5 + pvaccess motor / optics control panel. Originally built for
the APS 32-ID-B transmission X-ray microscope and generalised to any beamline
via per-beamline JSON layouts.

Features:
- Live motor cards (RBV, VAL, TWV, STOP, SET, tweak arrows, enable, limit).
- Setpoint / readback / LED / valve / combo / action-button PV widgets.
- Edit-mode canvas: add/remove tabs, panels, motor cards, PV rows,
  configurable action buttons — drag-and-drop, right-click configuration.
- Plugin widgets (Web view / Camera, Motor Pad, …) added from a top-bar
  `+ Widget` menu and persisted across restarts.
- Live camera embed via QtWebEngine with per-host credentials and per-panel
  URL persistence.
- ZP energy-calibration table with dynamic motor list (add/remove/include
  per motor), auto-migrating from the old fixed schema.
- Persistent **Nano / Micro regime** — stored in `~/.bl_gui/regime.txt` so
  any tool (or a text editor) can flip it; energy interp skips ZP X/Y in
  Micro automatically.
- Harmonic-correction background service — during a tomoscan's flat-field
  acquisition, nudges QG V once per pass to push a central bright spot off
  axis.
- ALL STOP button — kills motors, condenser shaker, camera acquire, He PLC
  AO1, uniblitz shutter, B-station shutter.
- Hourly PV snapshots to a rolling JSONL log (`~/.bl_gui/snapshots/`) with
  a browser to preview and cherry-pick which PVs to restore.
- Coloured log output by `[TAG]` prefix (`BL_GUI_NO_COLOR=1` to disable).

## Install

```bash
pip install .
```

Editable install (edits in `src/bl_gui/` take effect immediately, no
reinstall between changes):

```bash
pip install -e .
```

Optional dep for the Web view / Camera plugin:

```bash
pip install PyQtWebEngine
```

## Run

Basic:

```bash
bl_gui                              # default layout, view mode
bl_gui edit                         # default layout, edit mode
bl_gui bl32id.json                  # load bundled layouts/bl32id.json
bl_gui /path/to/custom.json         # any absolute path works
bl_gui MyGui                        # loads ~/.bl_gui/MyGui.json —
                                    # created empty on first save
bl_gui edit MyGui                   # same, edit mode
bl_gui MyGui edit                   # order-independent
```

`edit` is a keyword; the other argument is the layout name. The `.json`
extension is optional.

**Filename resolution** (first match wins):
1. Absolute path.
2. Current-directory relative.
3. `src/bl_gui/<name>` (bundled).
4. `src/bl_gui/layouts/<name>` (bundled per-beamline).
5. `~/.bl_gui/<name>` (per-user).
6. If none match → treated as a **new** layout: the GUI opens **blank**
   (no default panels) and the first save creates `~/.bl_gui/<name>`.

The provided launcher script wraps `conda activate pystream` + `bl_gui`:

```bash
./run_bl_gui.sh                     # default layout, view
./run_bl_gui.sh edit                # default layout, edit
./run_bl_gui.sh MyGui               # user layout MyGui.json
./run_bl_gui.sh edit MyGui          # any order
```

## Building a GUI from scratch

```bash
./run_bl_gui.sh edit MyGui
```

The window opens with two empty tabs. From the top bar:
- `+ Tab` — create a new tab (asks for a name).
- `+ Panel` — add an empty panel to the current tab.
- `+ Widget ▼` — drop a pre-built widget (Motor card, Valve/Shutter,
  Toggle button, Setpoint/Readback/LED, Action button, Web view / Camera)
  in a new panel. Each widget is right-clickable for PV configuration.

Right-click on:
- **A tab label** → Rename Tab / Delete Tab / Tab Label Size / Restore
  All Default Panels.
- **A panel** → Add Button / Add PV Row / Rename / Duplicate / Delete /
  Move to Tab / Panel Title Font.
- **A motor card** or **PV field** → Edit / Duplicate / Delete / Font Size.

Exit edit mode — layout auto-saves to `~/.bl_gui/MyGui.json`.

## Where things are saved

Per user, all under `~/.bl_gui/`:

| File | Contents |
|---|---|
| `<layout>.json` | Panel positions, titles, custom widgets, plugin widget kinds. |
| `regime.txt` | `nano` or `micro`. Read at every energy decision. |
| `bl32id_zp_calibration.json` | ZP calibration table (motors list + points). |
| `web_views.json` | Web view URLs keyed by panel name. |
| `web_credentials.json` | Camera HTTP-basic-auth logins (mode 0600). |
| `snapshots/log_NNN.jsonl` | Hourly PV snapshots, one record per line, ~5 MB / file. |

Bundled read-only templates live inside the installed package under
`src/bl_gui/layouts/`. User layouts always override them.

## Snapshots

- An hourly `QTimer` samples every monitored PV via the CA engine and
  compares against the newest saved snapshot. A record is written only if
  any PV changed. Terminal shows `[SNAPSHOT] saved …` on each new record.
- Files: `~/.bl_gui/snapshots/log_001.jsonl`, `log_002.jsonl`, … . When
  the current log exceeds 5 MB, the next tick opens the next-numbered
  file. Each record is one JSON line — `less` / `grep` / `jq` friendly.
- The **Snapshots** button in the top bar opens the browser:
  list of records newest-first → preview PV values → check the ones you
  want to restore → **Restore selected** with confirm dialog.

## Harmonic correction

Toggle button in the In/Out row. When enabled:
- Watches `32id:TomoScan:HDF5Location` for `/exchange/data_white`
  (flat-field acquisition).
- On the first tick that enters that state, samples the camera image
  from `32idbSP1:Pva1:Image` (via a persistent pvaccess monitor),
  finds a central bright spot (spot vs background ratio, restricted
  to the central 50% of the frame), and if the ratio exceeds threshold
  nudges `32idQG:m1` by ± the configured step (default 0.5 mm).
- One nudge per pass through the flat-field state — will not stack.
- Skipped in Micro regime.

Log output is coloured for at-a-glance status (green = success / cleared,
yellow = skip, magenta = nudge, red = error).

## ZP energy calibration

Open the Calibration window from the Energy panel's **Calibration** button.
- **Motors** list (top): Label / PV / Include checkbox — add or remove
  rows; only motors with Include=on get interpolated on energy change.
- **Data table** (below): one row per measured calibration point;
  columns dynamically match the motor list.
- **Save current E + motors** reads live RBVs and appends a row.
- Micro regime auto-skips motors labelled "ZP X" / "ZP Y" so a parked
  ZP transverse position isn't disturbed.

Legacy configs (`zp_x_pv` / `qg_v_pv` keys) auto-migrate on first load.

## Environment variables

| Variable | Effect |
|---|---|
| `BL_GUI_NO_COLOR=1` | Disable ANSI colour in the terminal log. |
| `BL_GUI_NO_WEBENGINE=1` | Force-disable QtWebEngine (auto-set on SSH X-forwarding). |
| `BL_GUI_NO_WEBENGINE=0` | Force-enable even when SSH is detected. |

## Adding a bundled beamline layout

```bash
bl_gui edit                           # arrange panels, save (writes to ~/.bl_gui/…)
cp ~/.bl_gui/<name>.json src/bl_gui/layouts/<name>.json
git add src/bl_gui/layouts/<name>.json && git commit
```

Then on any machine with the package installed: `bl_gui <name>.json`.

## Deploy to another machine

```bash
git clone <repo-url>
cd bl_gui
pip install -e .        # editable so subsequent git pulls just take effect
./run_bl_gui.sh
```

## Package layout

```
bl_gui/
├── pyproject.toml
├── README.md
├── run_bl_gui.sh
└── src/bl_gui/
    ├── __init__.py
    ├── __main__.py               # python -m bl_gui entry
    ├── main_window.py            # Win class, main()
    ├── motor.py                  # MC motor card + GROUPS
    ├── motor_debug.py            # Motor details dialog (all record fields)
    ├── widgets.py                # Panel, CfgButton, helpers
    ├── pv_field.py               # PVField, ValveField, ToggleField
    ├── pv.py                     # PVEngine + caput_bg
    ├── theme.py                  # stylesheets + default layout path
    ├── log_color.py              # ANSI colouriser (wraps sys.stdout)
    ├── snapshot.py               # rolling JSONL PV snapshot log
    ├── snapshot_window.py        # snapshot browser + restore
    ├── layout.json               # bundled default layout
    ├── layouts/
    │   ├── bl32id.json           # per-beamline preset
    │   └── bl2bm.json
    └── beamlines/bl32id/
        ├── xanes_calib.py        # ZP energy calibration table (dynamic motors)
        ├── regime.py             # nano/micro state file helpers
        ├── harmonic_correction.py# background bright-spot nudge service
        ├── qgmax_trigger.py      # pystream QGMax file-based trigger
        ├── camera_view.py        # QtWebEngine embed (any URL)
        └── autofocus.py          # scintillator autofocus (CLI + import)
```
