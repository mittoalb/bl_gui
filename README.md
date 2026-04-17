# bl_gui — Beamline Optics GUI

Generic PyQt5 + pvaccess motor/optics control panel. Originally built for the
APS 32-ID-B transmission X-ray microscope (`layouts/bl32id.json`) and
generalised to support multiple beamlines via per-beamline JSON layouts.

## Install

```bash
pip install .
```

Or, for development (edits in `src/bl_gui/` take effect immediately):

```bash
pip install -e .
```

## Run

```bash
bl_gui                          # default layout, view-only
bl_gui edit                     # default layout, edit mode
bl_gui bl32id.json              # load layouts/bl32id.json
bl_gui bl32id.json edit         # custom layout + edit mode
bl_gui /path/to/custom.json     # any absolute path works
```

**Filename resolution order:** absolute → current-dir relative →
`src/bl_gui/<name>` → `src/bl_gui/layouts/<name>`.

Layout is saved to the same file you loaded (default
`src/bl_gui/layout.json`) on window close and on edit-mode exit.

## Adding a beamline

```bash
bl_gui edit                                  # arrange panels, save
cp src/bl_gui/layout.json src/bl_gui/layouts/bl2bm.json
git add src/bl_gui/layouts/bl2bm.json && git commit
```

Then on any machine with the package installed:

```bash
bl_gui bl2bm.json
```

## Deploy to another machine

```bash
git clone <repo-url>
cd bl_gui
pip install .
bl_gui
```

## Layout

```
bl_gui/
├── pyproject.toml
├── README.md
└── src/
    └── bl_gui/
        ├── __init__.py
        ├── __main__.py        # python -m bl_gui entry
        ├── main_window.py     # Win class, main()
        ├── motor.py           # MC motor card + GROUPS
        ├── widgets.py         # Panel, CfgButton, WidgetEditor, helpers
        ├── pv.py              # PVEngine + caput_bg
        ├── theme.py           # stylesheets + layout path
        ├── layout.json        # default layout (also the write target)
        └── layouts/           # per-beamline presets
            └── bl32id.json
```
