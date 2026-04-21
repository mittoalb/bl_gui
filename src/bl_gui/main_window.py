"""Main application window (beamline optics GUI) and entry point."""
import json
import os
import subprocess
import sys
from typing import Dict, List
from functools import partial

from PyQt5 import QtCore, QtGui, QtWidgets

from .motor import MC, GROUPS, _DEFAULT_TABS, _fs, _rb, _act, set_font_scale
from .pv import PVEngine, caput_bg
from .pv_field import PVField, ValveField, ToggleField
from . import theme as _theme_mod
from .theme import _IMG, _PANEL_SS, _PANEL_SS_EDIT, _SS


def _bundled_lay_path():
    """Path to the beamline default layout bundled with the package
    (and committed to git). Used as a read-only template."""
    return _theme_mod._LAY


def _user_lay_path():
    """Per-user layout override path: ~/.bl_gui/<bl_name>.json.
    Every save writes here so one user's layout cannot overwrite another's,
    and the shared beamline template in the repo stays pristine."""
    bl_name = os.path.splitext(os.path.basename(_theme_mod._LAY))[0]
    return os.path.expanduser(f"~/.bl_gui/{bl_name}.json")


def _polyfit_interp(pts, col_idx, e_eV_target):
    """Fit a polynomial (degree auto-chosen up to 3) through the
    (Energy, axis-value) pairs from the calibration table and evaluate
    at ``e_eV_target``. Degree is clamped to ``len(points) - 1`` so we
    never over-fit. Returns None if fewer than 2 usable points exist."""
    try:
        import numpy as _np
    except Exception:
        _np = None
    es, vs = [], []
    for row in pts:
        if len(row) <= col_idx: continue
        if row[0] is None or row[col_idx] is None: continue
        es.append(float(row[0])); vs.append(float(row[col_idx]))
    if len(es) < 2:
        return None
    if _np is None:
        # Fallback to piecewise linear if numpy unavailable.
        order = sorted(range(len(es)), key=lambda i: es[i])
        es = [es[i] for i in order]; vs = [vs[i] for i in order]
        if e_eV_target <= es[0]:
            if len(es) < 2: return vs[0]
            slope = (vs[1] - vs[0]) / (es[1] - es[0])
            return vs[0] + slope * (e_eV_target - es[0])
        if e_eV_target >= es[-1]:
            slope = (vs[-1] - vs[-2]) / (es[-1] - es[-2])
            return vs[-1] + slope * (e_eV_target - es[-1])
        for i in range(1, len(es)):
            if es[i] >= e_eV_target:
                frac = (e_eV_target - es[i-1]) / (es[i] - es[i-1])
                return vs[i-1] + frac * (vs[i] - vs[i-1])
        return None
    # Polynomial fit: deg = min(3, N-1). 2 points → linear, 3 → quad,
    # 4+ → cubic. Using np.polyfit / np.polyval (no scipy dependency).
    deg = min(3, len(es) - 1)
    coeffs = _np.polyfit(es, vs, deg)
    return float(_np.polyval(coeffs, e_eV_target))


def _lay_path():
    """Layout path actually used by load/save: prefer the user override
    (if it exists) on load; save always goes there so the bundled copy
    is never touched by a user session."""
    u = _user_lay_path()
    return u if os.path.isfile(u) else _bundled_lay_path()
from .widgets import CfgButton, Panel, _ButtonEditFilter, _change_font_size, _duplicate_widget, _edit_widget

class Win(QtWidgets.QMainWindow):
    def __init__(self, allow_edit=False):
        super().__init__()
        self._allow_edit = allow_edit
        # Beamline name = currently loaded layout file's basename (no .json).
        # This gets updated by main() if the user passes a layout file.
        self._bl_name = os.path.splitext(os.path.basename(_lay_path()))[0]
        self.setWindowTitle(self._bl_name)
        self.resize(1800, 1000)
        # All motor cards and shutter/readback labels across ALL tabs
        self.mcs: List[MC] = []
        # PVField / ValveField rows: panel_key -> {field_id: widget}
        self._pv_fields: Dict[str, Dict[str, "PVField"]] = {}
        # User-added rows (via "Add PV Row..." in edit mode) — per-panel config
        # lists. Each entry = {kind, label, field_id, pv, on_pv, off_pv, opts}
        self._custom_rows: Dict[str, List[dict]] = {}
        # panel_key -> Panel  (keys are "PanelName::TabName")
        self._panels: Dict[str, Panel] = {}
        self._tab_canvases: Dict[str, QtWidgets.QWidget] = {}
        self._panel_tab_map: Dict[str, str] = {}  # panel_key -> tab_name
        self._edit_mode = False
        self._next_panel_id = 0  # for generating unique keys
        self._tab_label_font_size = 8  # default tab label font pt
        self._deleted_panels: List[str] = []  # "BaseName::TabName" keys deleted by user
        # Per-tab window sizes: tab_name -> (width, height)
        self._tab_sizes: Dict[str, tuple] = {
            "User Mode": (1000, 600),
            "Expert Mode": (1800, 1000),
        }

        cw = QtWidgets.QWidget(); self.setCentralWidget(cw)
        root = QtWidgets.QVBoxLayout(cw); root.setContentsMargins(4, 4, 4, 4); root.setSpacing(4)

        # ═══ TOP BAR ═══
        top = QtWidgets.QHBoxLayout(); top.setSpacing(6)
        self._title_lbl = QtWidgets.QLabel(self._bl_name)
        self._title_lbl.setStyleSheet("font:bold 14pt;color:#73dfff;")
        top.addWidget(self._title_lbl)
        top.addStretch()
        # Edit controls — only created if launched with 'edit' argument
        self._font_label_widget = QtWidgets.QLabel("Font:")
        top.addWidget(self._font_label_widget)
        self.font_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.font_slider.setRange(50, 200); self.font_slider.setValue(100); self.font_slider.setFixedWidth(100)
        self.font_slider.setStyleSheet(
            "QSlider::groove:horizontal{border:1px solid #404040;height:6px;background:#2d2d2d;border-radius:3px;}"
            "QSlider::handle:horizontal{background:#2980b9;border:1px solid #3a95d8;width:14px;margin:-5px 0;border-radius:7px;}"
            "QSlider::handle:horizontal:hover{background:#3a95d8;}")
        self.font_slider.valueChanged.connect(self._change_font_scale); top.addWidget(self.font_slider)
        self.font_lbl = QtWidgets.QLabel("100%"); self.font_lbl.setFixedWidth(36); self.font_lbl.setStyleSheet("font:8pt;"); top.addWidget(self.font_lbl)
        top.addSpacing(6)

        # Edit-mode is now chosen at launch (`bl_gui edit`) — no in-GUI toggle.
        self.add_panel_btn = QtWidgets.QPushButton("+ Panel"); self.add_panel_btn.setFixedSize(70, 28)
        self.add_panel_btn.setStyleSheet("background:#2d2d2d;color:#e0e0e0;font:9pt;border:1px solid #404040;border-radius:3px;")
        self.add_panel_btn.clicked.connect(self._add_new_panel)
        top.addWidget(self.add_panel_btn)

        self.add_tab_btn = QtWidgets.QPushButton("+ Tab"); self.add_tab_btn.setFixedSize(60, 28)
        self.add_tab_btn.setStyleSheet("background:#2d2d2d;color:#e0e0e0;font:9pt;border:1px solid #404040;border-radius:3px;")
        self.add_tab_btn.clicked.connect(self._add_new_tab)
        top.addWidget(self.add_tab_btn)

        # Hide all edit controls unless edit mode was requested at launch
        if not self._allow_edit:
            self._font_label_widget.setVisible(False)
            self.font_slider.setVisible(False)
            self.font_lbl.setVisible(False)
            self.add_panel_btn.setVisible(False)
            self.add_tab_btn.setVisible(False)

        root.addLayout(top)

        # ═══ TAB WIDGET ═══
        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.tabBar().setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tab_widget.tabBar().customContextMenuRequested.connect(self._tab_context_menu)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tab_widget)

        # Create tabs and build identical panels on each
        for tab_name in _DEFAULT_TABS:
            self._create_tab(tab_name)
            self._build_all_panels(tab_name)

        self._current_tab = ""
        self._load_layout()
        self.setStyleSheet(_SS)
        self._apply_tab_label_style()  # must be after setStyleSheet to not get overridden
        # Apply first tab's size and visibility
        if self.tab_widget.count() > 0:
            first = self.tab_widget.tabText(0)
            self._current_tab = first
            size = self._tab_sizes.get(first)
            if size:
                self.resize(size[0], size[1])
            self._on_tab_changed(self.tab_widget.currentIndex())
        # If launched with `bl_gui edit`, enter edit mode immediately.
        if self._allow_edit:
            QtCore.QTimer.singleShot(0, lambda: self._toggle_edit(True))
        QtCore.QTimer.singleShot(300, self._start_monitors)

        # Explicit save: Ctrl+S / menu. The GUI no longer auto-saves the
        # layout on close — this is the only way a save happens outside of
        # exiting edit mode, preventing accidental overwrites.
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        save_action = file_menu.addAction("Save Layout")
        save_action.setShortcut(QtGui.QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._explicit_save_layout)

        # Keyboard zoom: Ctrl+=, Ctrl+-, Ctrl+0 to boost/shrink/reset font
        # scale without needing to see the top-bar slider.
        for keys, delta in (("Ctrl+=", +10), ("Ctrl++", +10), ("Ctrl+-", -10)):
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(keys), self)
            sc.activated.connect(lambda d=delta: self.font_slider.setValue(
                max(50, min(200, self.font_slider.value() + d))))
        sc0 = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+0"), self)
        sc0.activated.connect(lambda: self.font_slider.setValue(100))

    # ── helpers ───────────────────────────────────────────────────────

    def _unique_key(self, base, tab_name):
        """Generate unique panel key: base::tab_name, with dedup."""
        key = f"{base}::{tab_name}"
        if key not in self._panels:
            return key
        self._next_panel_id += 1
        return f"{base}#{self._next_panel_id}::{tab_name}"

    def _register_pv_fields(self, panel, field_defs, form_layout):
        """Build PVField rows, wire into form_layout, and register them with the window
        so save/load + PV monitoring work.

        field_defs: list of (kind, row_label, field_id, default_pv, options_dict)
        """
        panel_key = panel.key
        slot = self._pv_fields.setdefault(panel_key, {})
        for kind, row_label, field_id, default_pv, opts in field_defs:
            f = PVField(kind=kind, pv=default_pv, field_id=field_id, parent=panel, **opts)
            form_layout.addRow(row_label, f)
            slot[field_id] = f

    def add_pv_row_dialog(self, panel):
        """Triggered by a panel's context menu — opens the row-builder dialog
        and, on accept, adds a live PVField/ValveField row to the panel."""
        from .row_builder import AddPVRowDialog
        existing = set(self._pv_fields.get(panel.key, {}).keys())
        dlg = AddPVRowDialog(existing_field_ids=existing, parent=panel)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or not dlg.cfg:
            return
        self._add_custom_pv_row(panel, dlg.cfg, record=True)

    def _add_custom_pv_row(self, panel, cfg, record=True):
        """Instantiate a PVField / ValveField from a config dict and insert it
        into the panel. If record=True the config is also stashed in
        self._custom_rows so it persists across save/load."""
        kind = cfg.get("kind")
        field_id = cfg.get("field_id") or kind
        label = cfg.get("label", "")
        pv = cfg.get("pv", "")
        on_pv = cfg.get("on_pv", "")
        off_pv = cfg.get("off_pv", "")
        opts = dict(cfg.get("opts", {}) or {})

        # Build the widget
        if kind == "valve":
            f = ValveField(status_pv=pv, on_pv=on_pv, off_pv=off_pv,
                           field_id=field_id, label_text=label, parent=panel)
        elif kind == "toggle":
            f = ToggleField(status_pv=pv, open_pv=on_pv, close_pv=off_pv,
                            field_id=field_id, label_text=label, parent=panel)
        elif kind == "btn_pair":
            f = PVField("btn_pair", "", field_id,
                        on_pv=on_pv, off_pv=off_pv,
                        button_text=opts.pop("button_text", "In/Out"),
                        parent=panel, **opts)
        else:
            f = PVField(kind, pv, field_id, parent=panel, **opts)

        # Insert into the panel's existing layout, adapting to its type
        lay = panel.layout()
        if lay is None:
            lay = QtWidgets.QFormLayout(panel)
            lay.setContentsMargins(6, 22, 6, 6); lay.setSpacing(4)

        if isinstance(lay, QtWidgets.QFormLayout):
            lay.addRow(label, f)
        elif isinstance(lay, QtWidgets.QGridLayout):
            row = lay.rowCount()
            lay.addWidget(QtWidgets.QLabel(label), row, 0)
            lay.addWidget(f, row, 1)
        else:
            # QVBoxLayout / QHBoxLayout — wrap label+field in a horizontal row
            wrap = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(wrap); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
            if label:
                hl.addWidget(QtWidgets.QLabel(label))
            hl.addWidget(f, 1)
            # Insert before any trailing stretch so new rows stack at the top
            insert_at = lay.count()
            for i in range(lay.count() - 1, -1, -1):
                item = lay.itemAt(i)
                if item and item.spacerItem() is not None:
                    insert_at = i; break
            lay.insertWidget(insert_at, wrap)

        # Register the row so PV updates find it
        slot = self._pv_fields.setdefault(panel.key, {})
        slot[field_id] = f
        f.set_edit_mode(self._edit_mode)
        if hasattr(self, "_pve"):
            pvs = f.monitored_pvs()
            if pvs:
                self._pve.monitor_many(pvs)

        # Record for persistence
        if record:
            self._custom_rows.setdefault(panel.key, []).append({
                "kind": kind,
                "label": label,
                "field_id": field_id,
                "pv": pv,
                "on_pv": on_pv,
                "off_pv": off_pv,
                "opts": opts,
            })

    def _delete_custom_row(self, field):
        """Remove a PVField/ValveField row from its panel + bookkeeping."""
        # Find which panel owns it
        panel_key = None
        field_id = getattr(field, "field_id", None)
        for k, slot in self._pv_fields.items():
            if slot.get(field_id) is field:
                panel_key = k
                break
        if panel_key is None:
            field.setParent(None); field.deleteLater()
            return
        # Remove from PVField registry
        self._pv_fields[panel_key].pop(field_id, None)
        # Remove from _custom_rows (if user-added)
        rows = self._custom_rows.get(panel_key, [])
        self._custom_rows[panel_key] = [r for r in rows if r.get("field_id") != field_id]
        if not self._custom_rows[panel_key]:
            self._custom_rows.pop(panel_key, None)
        # Detach the whole row: label + the field widget.
        # In a QFormLayout, removing the field widget also removes its label.
        p = self._panels.get(panel_key)
        lay = p.layout() if p else None
        if isinstance(lay, QtWidgets.QFormLayout):
            lay.removeRow(field)
        else:
            parent = field.parentWidget()
            if parent and parent is not p:
                # It's inside the wrap QWidget we made for QVBoxLayout/QHBoxLayout
                parent.setParent(None); parent.deleteLater()
            else:
                field.setParent(None); field.deleteLater()

    def _pv_field_rebind(self, field, old_pv, new_pv):
        """Called by PVField / ValveField / ToggleField after the user
        changes a PV. Subscribes the new PV on the live engine AND
        propagates the change to the matching widget in every other tab
        so edits made in User Mode immediately reflect in Expert Mode
        (and vice-versa)."""
        if hasattr(self, '_pve') and new_pv:
            self._pve.monitor_many([new_pv])
        self._propagate_field_change(field)

    def _propagate_field_change(self, source):
        """Copy the full state of ``source`` onto every other field with
        the same field_id living in a sibling tab's copy of the same
        panel. Panel keys are ``"<Base>::<Tab>"`` so we match on the
        ``Base`` component."""
        fid = getattr(source, "field_id", None)
        if not fid:
            return
        # Locate the source's panel key.
        src_key = None
        for key, slot in self._pv_fields.items():
            if slot.get(fid) is source:
                src_key = key; break
        if src_key is None:
            return
        src_base = src_key.split("::")[0]
        # Snapshot source state for efficient copying.
        src_dict = source.get_pvs_dict() if hasattr(source, "get_pvs_dict") else None
        src_pv = getattr(source, "pv", None)

        for key, slot in self._pv_fields.items():
            if key == src_key:
                continue
            if key.split("::")[0] != src_base:
                continue
            sibling = slot.get(fid)
            if sibling is None or sibling is source:
                continue
            if src_dict is not None and hasattr(sibling, "set_pvs_dict"):
                sibling.set_pvs_dict(src_dict)
            elif src_pv is not None and hasattr(sibling, "pv"):
                sibling.pv = src_pv
            # If it's a simple sp/rb QLineEdit/QLabel inside the PVField,
            # mirror its text too so the other tab's view updates visually.
            if hasattr(source, "_inner") and hasattr(sibling, "_inner"):
                try:
                    s_txt = source._inner.text() if hasattr(source._inner, "text") else None
                except Exception:
                    s_txt = None
                if s_txt is not None:
                    try:
                        sibling._inner.blockSignals(True)
                        if hasattr(sibling._inner, "setText"):
                            sibling._inner.setText(s_txt)
                    except Exception:
                        pass
                    finally:
                        try: sibling._inner.blockSignals(False)
                        except Exception: pass

    def _create_tab(self, name):
        canvas = QtWidgets.QWidget()
        canvas.setAutoFillBackground(True)
        pal = canvas.palette(); pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#000000")); canvas.setPalette(pal)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(canvas)
        self.tab_widget.addTab(scroll, name); self._tab_canvases[name] = canvas
        return canvas

    def _tab_names(self):
        return [self.tab_widget.tabText(i) for i in range(self.tab_widget.count())]

    def _get_panel_tab(self, panel_key):
        return self._panel_tab_map.get(panel_key, _DEFAULT_TABS[0])

    def _make_panel(self, title, w, h, tab_name):
        canvas = self._tab_canvases.get(tab_name)
        key = self._unique_key(title, tab_name)
        p = Panel(title, key, canvas); p.resize(w, h); p.show()
        self._panels[key] = p; self._panel_tab_map[key] = tab_name
        return p, key

    # ── build all panels for one tab ─────────────────────────────────

    def _build_all_panels(self, tab_name):
        x, y = 0, 0; GAP = 4

        # --- Shutters (3 shutters horizontally; each column: name / status / Open+Close) ---
        p, _ = self._make_panel("Shutters", 560, 170, tab_name)
        lay = QtWidgets.QHBoxLayout(); lay.setContentsMargins(6, 22, 6, 6); lay.setSpacing(10)
        shutter_rows = [
            # (field_id, label, status PV, Open PV, Close PV, invert_status, on_value, off_value)
            # CLSD_PL records read 1=closed, so invert the on/off mapping.
            # Uniblitz is a single-PV binary toggle: 1=open, 0=closed — the
            # status PV is the same PV we write, no inversion needed.
            ("shtr_A", "A-Stn",    "PB:32ID:STA_A_FES_CLSD_PL", "32idb:rshtrA:Open.PROC",     "32idb:rshtrA:Close.PROC",   True,  1, 1),
            ("shtr_B", "B-Stn",    "PB:32ID:STA_B_SBS_CLSD_PL", "32idb:rshtrB:Open.PROC",     "32idb:rshtrB:Close.PROC",   True,  1, 1),
            ("shtr_U", "Uniblitz", "32idbTXM:uniblitz:control", "32idbTXM:uniblitz:control", "32idbTXM:uniblitz:control", False, "Open", "Close"),
        ]
        slot = self._pv_fields.setdefault(p.key, {})
        for fid, lbl, st_pv, o_pv, c_pv, inv, ov, ofv in shutter_rows:
            vf = ValveField(status_pv=st_pv, on_pv=o_pv, off_pv=c_pv, field_id=fid,
                            label_text=lbl, on_text="Open", off_text="Close",
                            status_on_text="OPEN", status_off_text="CLOSED",
                            on_value=ov, off_value=ofv,
                            pulse=False, invert_status=inv, vertical=True, parent=p)
            lay.addWidget(vf, 1)
            slot[fid] = vf
        p.setLayout(lay); p.setGeometry(x, y, 560, 170); x += 564

        # --- Beam Info ---
        p, _ = self._make_panel("Beam", 350, 90, tab_name)
        bl = QtWidgets.QFormLayout(); bl.setContentsMargins(6, 20, 6, 4); bl.setSpacing(3)
        self._register_pv_fields(p, [
            ('rb', "I (mA):",  "beam_curr",  "S-DCCT:CurrentM",        dict(fmt=".2f")),
            ('rb', "Life:",    "beam_life",  "S-DCCT:LifetimeM",       dict(fmt=".1f")),
            ('rb', "Mode:",    "beam_mode",  "S:ActualMode",           {}),
            ('rb', "Und E:",   "beam_und_e", "S32ID:USID:EnergyM.VAL", dict(fmt=".3f")),
        ], bl)
        p.setLayout(bl); p.setGeometry(x, y, 350, 90); x += 354

        # --- Presets ---
        p, _ = self._make_panel("Presets", 200, 80, tab_name)
        pl = QtWidgets.QHBoxLayout(); pl.setContentsMargins(6, 20, 6, 4); pl.setSpacing(4)
        # Presets are CfgButtons so they: (1) get duplicated when the panel
        # is duplicated, (2) are editable via right-click in edit mode,
        # (3) save/load through the normal _buttons path.
        p._cfg_btn_defaults = ("#27ae60", "#ffffff", 11)
        presets = [
            ("Nano",  "caput", "32id:TXMOptics:MoveAllIn 1"),
            ("Micro", "caput", "32id:TXMOptics:MoveAllOut 1"),
        ]
        p._default_btn_specs = presets
        bg_pr, fg_pr, fs_pr = p._cfg_btn_defaults
        for lbl, atype, action in presets:
            b = CfgButton(lbl, action_type=atype, action=action,
                          bg=bg_pr, fg=fg_pr, font_size=fs_pr, parent=p)
            b.setMinimumHeight(34)
            pl.addWidget(b)
            p.custom_buttons.append(b)
        p.setLayout(pl); p.setGeometry(x, y, 200, 80)

        # --- Motor groups ---
        x, y = 0, 84 + GAP; COLS = 5; ci = 0
        for gname, motors in GROUPS:
            pw = len(motors) * 116 + 16; ph = 190
            p, _ = self._make_panel(gname, pw, ph, tab_name)
            ml = QtWidgets.QHBoxLayout(); ml.setContentsMargins(4, 20, 4, 4); ml.setSpacing(3)
            for mlbl, mpv in motors:
                mc = MC(mlbl, mpv); ml.addWidget(mc); self.mcs.append(mc)
            p.setLayout(ml); p.setGeometry(x, y, pw, ph)
            x += pw + GAP; ci += 1
            if ci >= COLS: ci = 0; x = 0; y += ph + GAP

        # --- In/Out ---
        iy = y + (0 if ci == 0 else 190 + GAP)
        p, _ = self._make_panel("In / Out", 760, 70, tab_name)
        iol = QtWidgets.QGridLayout(); iol.setContentsMargins(6, 18, 6, 4); iol.setSpacing(6)
        inout_rows = [
            ("io_sample",   "Sample",  "32id:TXMOptics:MoveSampleIn",     "32id:TXMOptics:MoveSampleOut"),
            ("io_phring",   "PhRing",  "32id:TXMOptics:MovePhaseRingIn",  "32id:TXMOptics:MovePhaseRingOut"),
            ("io_zp",       "ZP",      "32id:TXMOptics:MoveZonePlateIn",  "32id:TXMOptics:MoveZonePlateOut"),
            ("io_pinhole",  "Pinhole", "32id:TXMOptics:MovePinholeIn",    "32id:TXMOptics:MovePinholeOut"),
            ("io_cond",     "Cond",    "32id:TXMOptics:MoveCondenserIn",  "32id:TXMOptics:MoveCondenserOut"),
            ("io_bs",       "BS",      "32id:TXMOptics:MoveBeamstopIn",   "32id:TXMOptics:MoveBeamstopOut"),
            ("io_diff",     "Diff",    "32id:TXMOptics:MoveDiffuserIn",   "32id:TXMOptics:MoveDiffuserOut"),
        ]
        slot = self._pv_fields.setdefault(p.key, {})
        # Keep ALL In/Out label widgets across tabs so a rename applies
        # consistently everywhere. Indexed by fid → list of labels.
        if not hasattr(self, "_io_labels"):
            self._io_labels = {}
        for col, (fid, lbl, pv_in, pv_out) in enumerate(inout_rows):
            label = QtWidgets.QLabel(lbl)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            label.customContextMenuRequested.connect(
                lambda _pos, f=fid: self._rename_io_label_by_fid(f))
            label.setToolTip("Right-click to rename")
            iol.addWidget(label, 0, col, alignment=QtCore.Qt.AlignCenter)
            self._io_labels.setdefault(fid, []).append(label)
            pf = PVField('btn_pair', "", fid, button_text="In/Out",
                         on_pv=pv_in, off_pv=pv_out, on_value=1, off_value=1, parent=p)
            iol.addWidget(pf, 1, col)
            slot[fid] = pf
        # A convenience launcher button (non-PV). Keep as plain button.
        bp = QtWidgets.QPushButton("PyStream"); bp.setFixedSize(90, 28)
        bp.setStyleSheet("background:#27ae60;color:#fff;font:bold 10pt;border-radius:3px;")
        bp.clicked.connect(lambda: subprocess.Popen(["/home/beams/USERTXM/scripts/start_pystream.sh"], start_new_session=True))
        iol.addWidget(bp, 0, len(inout_rows), 2, 1)
        # QGMax one-shot optimization — writes the request file that
        # pystream's background watcher picks up. The button polls the
        # response file to show live running/idle state.
        qgmax_btn = QtWidgets.QPushButton("QGMax")
        qgmax_btn.setFixedSize(90, 28)
        qgmax_btn.clicked.connect(self._trigger_qgmax)
        iol.addWidget(qgmax_btn, 0, len(inout_rows) + 1, 2, 1)
        self._qgmax_btn = qgmax_btn
        # Only create the polling infrastructure once across both tabs.
        if not hasattr(self, "_qgmax_buttons"):
            self._qgmax_buttons = []
            self._qgmax_running = False
            self._qgmax_timer = QtCore.QTimer(self)
            self._qgmax_timer.setInterval(500)
            self._qgmax_timer.timeout.connect(self._poll_qgmax_status)
            self._qgmax_timer.start()
        self._qgmax_buttons.append(qgmax_btn)
        self._style_qgmax_button(qgmax_btn, running=False)
        p.setLayout(iol); p.setGeometry(0, iy, 860, 70)

        # --- Energy ---
        p, _ = self._make_panel("Energy", 380, 300, tab_name)
        el = QtWidgets.QFormLayout(); el.setContentsMargins(6, 22, 6, 6); el.setSpacing(4)

        # Top row: big Energy setpoint + Bragg readback + Go button all on
        # the same line — this is the main user-facing control so they must
        # be easy to read and act on without hunting for a separate button.
        slot = self._pv_fields.setdefault(p.key, {})
        energy_sp = PVField(kind='sp', pv="32id:TXMOptics:Energy",
                            field_id="energy_sp", placeholder="keV", parent=p)
        bragg_rb  = PVField(kind='rb', pv="32ida:BraggERdbkAO",
                            field_id="bragg_rbv", fmt=".3f", parent=p)
        energy_go = PVField(kind='btn', pv="32id:TXMOptics:EnergySet",
                            field_id="energy_set",
                            button_text="Go", button_value=1, parent=p)
        energy_sp._inner.setMinimumHeight(36)
        energy_sp._inner.setStyleSheet(
            "QLineEdit{background:#2c3e50;color:#ecf0f1;"
            "border:1px solid #3498db;border-radius:3px;"
            "padding:4px 8px;font:bold 15pt 'Liberation Mono','DejaVu Sans Mono',monospace;}"
            "QLineEdit:focus{background:#34495e;border:1px solid #5dade2;}")
        bragg_rb._inner.setMinimumHeight(36)
        bragg_rb._inner.setStyleSheet(
            "color:#2ecc71;background:transparent;font:bold 15pt 'Liberation Mono','DejaVu Sans Mono',monospace;padding:4px 6px;")
        energy_go._inner.setMinimumHeight(36)
        energy_go._inner.setStyleSheet(
            "background:#27ae60;color:#fff;font:bold 13pt;"
            "border:1px solid #2ecc71;border-radius:3px;padding:4px 16px;")
        row = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(6)
        hl.addWidget(energy_sp, 1); hl.addWidget(bragg_rb, 1); hl.addWidget(energy_go, 0)
        el.addRow("Energy (keV):", row)
        slot["energy_sp"] = energy_sp
        slot["bragg_rbv"] = bragg_rb
        slot["energy_set"] = energy_go
        # Replace Go's default caput with a handler that first falls back to
        # the ZP calibration plugin when both EPICS cal-file PVs are blank.
        try: energy_go._inner.clicked.disconnect()
        except TypeError: pass
        energy_go._inner.clicked.connect(self._on_set_energy)
        # Enforce 6.5-12 keV range on the SP: hijack returnPressed so
        # out-of-range values never reach caput.
        try: energy_sp._inner.returnPressed.disconnect()
        except TypeError: pass
        energy_sp._inner.returnPressed.connect(
            lambda e=energy_sp: self._on_energy_sp_return(e))
        energy_sp._inner.setToolTip("Allowed range: 6.5 – 12 keV")

        # Every remaining field is a PVField with a stable id → right-click
        # allows per-beamline PV reassignment, and the PV is persisted in
        # layout.json.
        energy_fields = [
            ('sp',  "Detune (eV):",  "energy_detune",   "32id:TXMOptics:EnergyDetune",             {}),
            ('sp',  "Cal File 1:",   "energy_calfile1", "32id:TXMOptics:EnergyCalibrationFileOne", dict(placeholder="calib file 1")),
            ('sp',  "Cal File 2:",   "energy_calfile2", "32id:TXMOptics:EnergyCalibrationFileTwo", dict(placeholder="calib file 2")),
        ]
        self._register_pv_fields(p, energy_fields, el)

        # Busy indicator (LED — green when EnergyBusy != 0)
        self._register_pv_fields(p, [
            ('led', "Busy:", "energy_busy_led", "32id:TXMOptics:EnergyBusy", {}),
        ], el)

        # Use Calibration — single state-aware toggle button. Colour = current
        # state (green/ON = using calibration, red/OFF = not). Button text
        # describes what clicking will do.
        use_calib = ToggleField(
            status_pv="32id:TXMOptics:EnergyUseCalibration",
            open_pv="32id:TXMOptics:EnergyUseCalibration",
            close_pv="32id:TXMOptics:EnergyUseCalibration",
            field_id="energy_usecalib",
            label_text="",
            open_text="YES",          # shown when state is ON (green)
            close_text="NO",          # shown when state is OFF (red)
            open_value="Yes", close_value="No",
            state_label=True,
            parent=p,
        )
        use_calib.name_lbl.hide()
        el.addRow("Use Calib:", use_calib)
        self._pv_fields[p.key]["energy_usecalib"] = use_calib

        # Zone-plate / energy calibration table (opens the external xanes_gui
        # GUI_2D window). Kept as a plain launcher button — no PV binding.
        from .beamlines.bl32id import xanes_calib
        calib_btn = QtWidgets.QPushButton("ZP Calibration...")
        calib_btn.setStyleSheet(
            "background:#1e5a8e;color:#fff;font:bold 9pt;"
            "border:1px solid #2980b9;border-radius:3px;padding:4px 10px;")
        calib_btn.clicked.connect(lambda: xanes_calib.launch(self))
        el.addRow("Calibration:", calib_btn)

        # Cal files: a ± range spinbox (persisted in the calibration
        # config JSON) + a button to generate the two Energy_*keV.txt
        # files from the ZP calibration table without triggering EnergySet.
        range_spin = QtWidgets.QDoubleSpinBox()
        range_spin.setDecimals(3)
        range_spin.setRange(0.001, 50.0)
        range_spin.setSingleStep(0.05)
        range_spin.setSuffix(" keV")
        try:
            _initial_range = float(xanes_calib.load_config().get(
                "range_keV", xanes_calib.DEFAULT_RANGE_KEV))
        except Exception:
            _initial_range = xanes_calib.DEFAULT_RANGE_KEV
        range_spin.setValue(_initial_range)
        range_spin.valueChanged.connect(self._on_cal_range_changed)
        range_spin.setToolTip("Half-width used when generating cal files: "
                              "low file at E−range, high file at E+range.")
        el.addRow("Range ± (keV):", range_spin)
        self._cal_range_spin = range_spin

        gen_btn = QtWidgets.QPushButton("Generate Cal Files")
        gen_btn.setStyleSheet(
            "background:#8e44ad;color:#fff;font:bold 9pt;"
            "border:1px solid #9b59b6;border-radius:3px;padding:4px 10px;")
        gen_btn.setToolTip(
            "Writes Energy_<E±range>keV.txt files from the ZP calibration "
            "table and sets EnergyCalibrationFileOne/Two. Does NOT change "
            "energy.")
        gen_btn.clicked.connect(self._apply_zp_calib_from_plugin)
        el.addRow("Cal Files:", gen_btn)

        p.setLayout(el); p.setGeometry(700 + GAP, 84 + GAP, 380, 340)

        # --- Camera ---
        p, _ = self._make_panel("Camera", 340, 180, tab_name)
        cl = QtWidgets.QFormLayout(); cl.setContentsMargins(6, 22, 6, 6); cl.setSpacing(4)
        # Start + Stop as a two-button ValveField in highlight mode so the
        # currently-active side is bright and the other dim.
        cam_slot = self._pv_fields.setdefault(p.key, {})
        cam_run = ValveField(
            status_pv="32idbSP1:cam1:Acquire",
            on_pv="32idbSP1:cam1:Acquire",
            off_pv="32idbSP1:cam1:Acquire",
            field_id="cam_run",
            label_text="",
            on_text="Start", off_text="Stop",
            on_value=1, off_value=0,
            pulse=False, btn_width=80,
            highlight_buttons=True, parent=p,
        )
        cam_run.name_lbl.hide()
        cl.addRow("Acquire:", cam_run)
        cam_slot["cam_run"] = cam_run
        self._register_pv_fields(p, [
            ('sp',  "Exp (s):",  "cam_exp_sp",   "32idbSP1:cam1:AcquireTime",     dict(placeholder="sec")),
            ('rb',  "Size X:",   "cam_sizex",    "32idbSP1:cam1:SizeX_RBV",       {}),
            ('rb',  "Size Y:",   "cam_sizey",    "32idbSP1:cam1:SizeY_RBV",       {}),
            ('sp',  "Bin X:",    "cam_binx",     "32idbSP1:cam1:BinX",            dict(placeholder="1")),
            ('sp',  "Bin Y:",    "cam_biny",     "32idbSP1:cam1:BinY",            dict(placeholder="1")),
        ], cl)
        # Make the exposure-time field noticeably bigger (main user control).
        exp_edit = cam_slot['cam_exp_sp']._inner
        exp_edit.setMinimumHeight(32)
        exp_edit.setStyleSheet(
            "QLineEdit{background:#2c3e50;color:#ecf0f1;"
            "border:1px solid #3498db;border-radius:3px;"
            "padding:4px 8px;font:bold 14pt 'Liberation Mono','DejaVu Sans Mono',monospace;}"
            "QLineEdit:focus{background:#34495e;border:1px solid #5dade2;}")
        # AreaDetector binning needs SizeX/SizeY to be recomputed from the
        # sensor's max size when BinX/BinY change (same logic as pystream's
        # "Apply Binning" button). Hook Enter on both bin fields to run the
        # full apply sequence automatically.
        cam_slot['cam_binx']._inner.returnPressed.connect(self._apply_cam_binning)
        cam_slot['cam_biny']._inner.returnPressed.connect(self._apply_cam_binning)
        p.setLayout(cl); p.setGeometry(700 + GAP + 344, 84 + GAP, 340, 180)

        # --- Crop ---
        p, _ = self._make_panel("Crop", 360, 100, tab_name)
        crl = QtWidgets.QGridLayout(); crl.setContentsMargins(6, 22, 6, 4); crl.setSpacing(4)
        crop_rows = [
            ("crop_L", "L:", "32id:TXMOptics:CropLeft"),
            ("crop_R", "R:", "32id:TXMOptics:CropRight"),
            ("crop_T", "T:", "32id:TXMOptics:CropTop"),
            ("crop_B", "B:", "32id:TXMOptics:CropBottom"),
        ]
        slot = self._pv_fields.setdefault(p.key, {})
        for col, (fid, lbl, pv) in enumerate(crop_rows):
            crl.addWidget(QtWidgets.QLabel(lbl), 0, col, alignment=QtCore.Qt.AlignCenter)
            f = PVField('sp', pv, fid, placeholder=lbl[0], parent=p)
            crl.addWidget(f, 1, col)
            slot[fid] = f
        apply_f = PVField('btn', "32id:TXMOptics:Crop", "crop_apply",
                          button_text="Apply", button_value=1, parent=p)
        crl.addWidget(apply_f, 1, len(crop_rows))
        slot["crop_apply"] = apply_f
        p.setLayout(crl); p.setGeometry(700 + GAP, iy, 360, 100)

        # --- Valves ---
        p, _ = self._make_panel("Valves", 320, 120, tab_name)
        vl = QtWidgets.QVBoxLayout(); vl.setContentsMargins(6, 22, 6, 4); vl.setSpacing(3)
        valve_rows = [
            # (field_id,       label,       status PV,             On PV,                  Off PV)
            ("valve_all",       "all",       "32idbSoft:PLC1:C1", "32idbSoft:PLC1:oC21", "32idbSoft:PLC1:oC31"),
            ("valve_granite_x", "Granite X", "32idbSoft:PLC1:C2", "32idbSoft:PLC1:oC22", "32idbSoft:PLC1:oC32"),
            ("valve_granite_y", "Granite Y", "32idbSoft:PLC1:C3", "32idbSoft:PLC1:oC23", "32idbSoft:PLC1:oC33"),
        ]
        slot = self._pv_fields.setdefault(p.key, {})
        for fid, lbl, st, on, off in valve_rows:
            vf = ValveField(status_pv=st, on_pv=on, off_pv=off, field_id=fid,
                            label_text=lbl, parent=p)
            vl.addWidget(vf)
            slot[fid] = vf
        p.setLayout(vl); p.setGeometry(1004 + GAP, iy, 320, 120)

        # --- PLC Outputs (analog) ---
        p, _ = self._make_panel("PLC Outputs", 320, 80, tab_name)
        plc_form = QtWidgets.QFormLayout(); plc_form.setContentsMargins(6, 22, 6, 6); plc_form.setSpacing(4)
        plc_fields = [
            ('sp', "AO1 set (V):", "plc_ao1_sp", "32idbSoft:PLC1:ao1", dict(placeholder="0–10")),
            ('rb', "AO1 RBV:",     "plc_ao1_rb", "32idbSoft:PLC1:ao1", {}),
        ]
        self._register_pv_fields(p, plc_fields, plc_form)
        p.setLayout(plc_form); p.setGeometry(1004 + GAP, iy + 114, 320, 80)

        # --- BPM/EPID ---
        p, _ = self._make_panel("BPM/EPID", 440, 110, tab_name)
        epl = QtWidgets.QGridLayout(); epl.setContentsMargins(6, 22, 6, 4); epl.setSpacing(3)
        epl.addWidget(QtWidgets.QLabel("Axis"),     0, 0, alignment=QtCore.Qt.AlignCenter)
        epl.addWidget(QtWidgets.QLabel("Setpoint"), 0, 1, alignment=QtCore.Qt.AlignCenter)
        epl.addWidget(QtWidgets.QLabel("Current"),  0, 2, alignment=QtCore.Qt.AlignCenter)
        epl.addWidget(QtWidgets.QLabel("FB"),       0, 3, alignment=QtCore.Qt.AlignCenter)
        slot = self._pv_fields.setdefault(p.key, {})
        bpm_rows = [
            ("bpm_H", "Horiz", "32idbSoft:epidH"),
            ("bpm_V", "Vert",  "32idbSoft:epidV"),
        ]
        for i, (fid, ax, base) in enumerate(bpm_rows, 1):
            epl.addWidget(QtWidgets.QLabel(f"{ax}:"), i, 0)
            sp = PVField('sp',  f"{base}.VAL",  f"{fid}_sp", parent=p); epl.addWidget(sp, i, 1); slot[f"{fid}_sp"] = sp
            rb = PVField('rb',  f"{base}.CVAL", f"{fid}_rb", fmt=".3f", parent=p); epl.addWidget(rb, i, 2); slot[f"{fid}_rb"] = rb
            fb = PVField('cmb', f"{base}:on",   f"{fid}_fb", choices=["Off", "On"], parent=p); epl.addWidget(fb, i, 3); slot[f"{fid}_fb"] = fb
        p.setLayout(epl); p.setGeometry(700 + GAP, iy + 64, 440, 110)

        # --- PV Save/Load ---
        p, _ = self._make_panel("PV Save/Load", 360, 90, tab_name)
        pvl = QtWidgets.QFormLayout(); pvl.setContentsMargins(6, 22, 6, 4); pvl.setSpacing(4)
        self._register_pv_fields(p, [
            ('sp', "Filename:", "pvsr_file", "32id:TXMOptics:FileAllPVs", dict(placeholder="config file path")),
        ], pvl)
        btn_row = QtWidgets.QHBoxLayout(); btn_row.setSpacing(6)
        btn_row.addWidget(_act("Save", lambda: caput_bg("32id:TXMOptics:SaveAllPVs", 1)))
        btn_row.addWidget(_act("Load", lambda: caput_bg("32id:TXMOptics:LoadAllPVs", 1)))
        pvl.addRow(btn_row)
        p.setLayout(pvl); p.setGeometry(1104 + GAP, iy + 64, 360, 90)

        # --- Beam Status ---
        p, _ = self._make_panel("Beam Status", 400, 100, tab_name)
        bsl = QtWidgets.QFormLayout(); bsl.setContentsMargins(6, 22, 6, 4); bsl.setSpacing(3)
        self._register_pv_fields(p, [
            ('rb', "Desired Mode:", "bs_desired_mode", "S:DesiredMode",                {}),
            ('rb', "Actual Mode:",  "bs_actual_mode",  "S:ActualMode",                 {}),
            ('rb', "Inj Period:",   "bs_inj_period",   "S-INJ:InjectionPeriodCounterM", dict(fmt=".2f")),
        ], bsl)
        p.setLayout(bsl); p.setGeometry(700 + GAP, iy + 168, 400, 100)

        # --- OPS Messages ---
        p, _ = self._make_panel("OPS Messages", 400, 130, tab_name)
        opl = QtWidgets.QFormLayout(); opl.setContentsMargins(6, 20, 6, 4); opl.setSpacing(1)
        self._register_pv_fields(p, [
            ('rb', f"msg {i}:", f"ops_msg{i}", f"OPS:message{i}", {}) for i in range(1, 7)
        ], opl)
        p.setLayout(opl); p.setGeometry(700 + GAP, iy + 272, 400, 130)

        # --- Shaker ---
        p, _ = self._make_panel("Shaker", 360, 360, tab_name)
        skl = QtWidgets.QFormLayout(); skl.setContentsMargins(6, 22, 6, 4); skl.setSpacing(4)
        shaker_fields = [
            # Shared
            ('sp',  "Frequency:",      "shaker_freq",    "32idbShaker:shaker:frequency.VAL",    dict(placeholder="Hz")),
            ('sp',  "Time / Point:",   "shaker_time",    "32idbShaker:shaker:timePerPoint.VAL", dict(placeholder="s")),
            ('sp',  "Num Points:",     "shaker_npts",    "32idbShaker:shaker:numPoints.VAL",    {}),
            # Function type — mbbo (Circle / Lissajous). Extra live states arrive
            # from the PV itself get added dynamically by the combo.
            ('cmb', "Function:",       "shaker_menu",    "32idbShaker:shakerMenu",              dict(choices=["Circle", "Lissajous"])),
            # Channel A
            ('sp',  "A: Amp Mult:",    "shaker_A_amp",   "32idbShaker:shaker:A:ampMult.VAL",    {}),
            ('sp',  "A: Amp Offset:",  "shaker_A_off",   "32idbShaker:shaker:A:ampOffset.VAL",  {}),
            ('sp',  "A: Phase Shift:", "shaker_A_phase", "32idbShaker:shaker:A:phaseShift.VAL", {}),
            # Channel B
            ('sp',  "B: Amp Mult:",    "shaker_B_amp",   "32idbShaker:shaker:B:ampMult",        {}),
            ('sp',  "B: Amp Offset:",  "shaker_B_off",   "32idbShaker:shaker:B:ampOffset.VAL",  {}),
            ('sp',  "B: Freq Mult:",   "shaker_B_fmult", "32idbShaker:shaker:B:freqMult.VAL",   {}),
        ]
        self._register_pv_fields(p, shaker_fields, skl)
        # Run / Stop as a two-button pair. highlight_buttons=True makes
        # the active side bright and the inactive one dim so you can see
        # the current state at a glance.
        shaker_run = ValveField(
            status_pv="32idbShaker:shaker:run",
            on_pv="32idbShaker:shaker:run",
            off_pv="32idbShaker:shaker:run",
            field_id="shaker_run",
            label_text="",
            on_text="Run", off_text="Stop",
            on_value=1, off_value=0,
            pulse=False, btn_width=80,
            highlight_buttons=True, parent=p,
        )
        shaker_run.name_lbl.hide()
        skl.addRow("Shaker:", shaker_run)
        self._pv_fields[p.key]["shaker_run"] = shaker_run
        p.setLayout(skl); p.setGeometry(1104 + GAP, iy + 168, 360, 400)

        # --- Launchers ---
        p, _ = self._make_panel("Launchers", 560, 150, tab_name)
        ll2 = QtWidgets.QGridLayout(); ll2.setContentsMargins(6, 22, 6, 6); ll2.setSpacing(5)
        p._grid_cols = 4  # used by _load_layout to restore positions
        # Panel-wide CfgButton defaults (bg, fg, font_size). _load_layout
        # re-applies these after loading saved buttons so stale saved colors
        # can't override the current visual scheme.
        p._cfg_btn_defaults = ("#2980b9", "#ffffff", 10)
        p._default_btn_specs = []  # populated below — used on load to auto-fill
                                    # any default buttons missing from the user's
                                    # saved list (so new defaults appear for users
                                    # with existing saves).
        launchers = [
            ("ImageJ","/home/beams/USERTXM/Software/ImageJ/ImageJ.sh"),
            ("Detector","/home/beams/USERTXM/epics/synApps/support/32idbSP1/iocBoot/ioc32idbSP1/softioc/32idbSP1.sh medm"),
            ("Blackfly","/home/beams/USERTXM/epics/synApps/support/32idbSP2/iocBoot/ioc32idbSP2/softioc/32idbSP2.sh medm"),
            ("IOCs","medm -x /home/beams/USERTXM/scripts/iocs_start.adl &"),
            ("32ID Main","/home/beams/USERTXM/start_caQtDM_32id"),
            ("Web IOCs","/home/beams/USERTXM/scripts/ioc_page.sh"),
            ("Web Cams","firefox 10.54.102.97 &"),
            ("Shaker","/net/s32dserv/xorApps/epics/synApps_6_3/ioc/32idbShaker/start_MEDM_32idbShaker")]
        bg_l, fg_l, fs_l = p._cfg_btn_defaults
        p._default_btn_specs = list(launchers)
        for i, (lbl, cmd) in enumerate(launchers):
            b = CfgButton(lbl, action_type="shell", action=cmd,
                          bg=bg_l, fg=fg_l, font_size=fs_l, parent=p)
            b.setMinimumHeight(34)
            ll2.addWidget(b, i // 4, i % 4)
            p.custom_buttons.append(b)
        p.setLayout(ll2); p.setGeometry(0, iy + 64, 560, 150)

        # --- Displays ---
        p, _ = self._make_panel("Displays", 560, 150, tab_name)
        dl = QtWidgets.QGridLayout(); dl.setContentsMargins(6, 22, 6, 6); dl.setSpacing(5)
        p._grid_cols = 3  # used by _load_layout to restore positions
        p._cfg_btn_defaults = ("#27ae60", "#ffffff", 10)
        p._default_btn_specs = []  # filled right below
        displays = [
            ("XANES","medm -x -macro 'P=32id:,R=TXMOptics:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/xanes.adl &"),
            ("Furnace","medm -x /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/Furnace.adl &"),
            ("DCM Motors","medm -x -macro 'P=32ida:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/dcm_motors9.adl &"),
            ("IOC Setup","medm -x -macro 'P=32id:,R=TXMOptics:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/txmOptics_extended.adl &"),
            ("TomoScan","medm -x -macro 'P=32id:,R=TomoScan:,BEAMLINE=tomoScan_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("TomoStep","medm -x -macro 'P=32id:,R=TomoScanStep:,BEAMLINE=tomoScanStep_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("TomoStream","medm -x -macro 'P=32id:,R=TomoScanStream:,BEAMLINE=tomoScanStream_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("CSS/BPM","/net/s32dserv/xorApps/epics/synApps_6_0/ioc/32idcBPM/iocBoot/iocbpm/32idcBPM.sh css"),
            ("Softglue","medm -x -macro 'P=32idSoftGlueZynq:' /home/beams19/USERTXM/epics/synApps/support/softGlueZynq/softGlueZynqApp/op/adl/softGlueZynq_top.adl &")]
        bg_d, fg_d, fs_d = p._cfg_btn_defaults
        p._default_btn_specs = list(displays)
        for i, (lbl, cmd) in enumerate(displays):
            b = CfgButton(lbl, action_type="shell", action=cmd,
                          bg=bg_d, fg=fg_d, font_size=fs_d, parent=p)
            b.setMinimumHeight(34)
            p.custom_buttons.append(b)
            dl.addWidget(b, i // 3, i % 3)
        p.setLayout(dl); p.setGeometry(0, iy + 220, 560, 150)

        # --- Schematic ---
        if os.path.isfile(_IMG):
            pix = QtGui.QPixmap(_IMG); pw = min(500, pix.width())
            ph = int(pix.height() * pw / pix.width()) + 24
            p, _ = self._make_panel("Schematic", pw, ph, tab_name)
            il = QtWidgets.QVBoxLayout(); il.setContentsMargins(4, 20, 4, 4)
            img = QtWidgets.QLabel(); img.setPixmap(pix); img.setScaledContents(True); img.setMinimumSize(100, 60)
            il.addWidget(img); p.setLayout(il); p.setGeometry(504, iy + 64, pw, ph)

        # --- ALL STOP (as a movable panel) ---
        p, _ = self._make_panel("ALL STOP", 160, 60, tab_name)
        asl = QtWidgets.QVBoxLayout(); asl.setContentsMargins(4, 20, 4, 4)
        astop = QtWidgets.QPushButton("ALL STOP")
        astop.setStyleSheet("background:#c0392b;color:#fff;font:bold 14pt;border:2px solid #e74c3c;border-radius:4px;padding:4px;")
        astop.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        astop.clicked.connect(lambda: caput_bg("32id:TXMOptics:AllStop", 1))
        asl.addWidget(astop); p.setLayout(asl)
        p.setGeometry(938, 0, 160, 60)

    # ── panel operations ─────────────────────────────────────────────

    def _move_panel_to_tab(self, panel_key, target_tab):
        panel = self._panels.get(panel_key)
        if not panel: return
        target_canvas = self._tab_canvases.get(target_tab)
        if not target_canvas: return
        geo = panel.geometry()
        panel.setParent(target_canvas)
        panel.setGeometry(10, 10, geo.width(), geo.height())
        panel.show(); panel.set_edit(self._edit_mode)
        # Update key mapping
        old_key = panel_key
        new_key = panel_key.rsplit("::", 1)[0] + f"::{target_tab}"
        if new_key in self._panels and new_key != old_key:
            self._next_panel_id += 1
            new_key = panel_key.rsplit("::", 1)[0] + f"#{self._next_panel_id}::{target_tab}"
        self._panels.pop(old_key, None)
        self._panel_tab_map.pop(old_key, None)
        panel.key = new_key
        self._panels[new_key] = panel
        self._panel_tab_map[new_key] = target_tab
        for i in range(self.tab_widget.count()):
            if self.tab_widget.tabText(i) == target_tab:
                self.tab_widget.setCurrentIndex(i); break

    def _duplicate_panel(self, panel_key):
        """Duplicate a panel on the same tab, cloning motor cards and custom buttons.
        The original panel remains untouched; the copy is placed below/right of it."""
        panel = self._panels.get(panel_key)
        if not panel:
            return
        tab_name = self._panel_tab_map.get(panel_key, _DEFAULT_TABS[0])
        canvas = self._tab_canvases.get(tab_name)
        if not canvas:
            return

        # Snapshot everything we need from the original *before* touching it,
        # so any later Qt parent/layout operations cannot affect the source.
        geo = panel.geometry()
        base_title = panel.title_text()
        src_layout = panel.layout()
        is_horizontal = isinstance(src_layout, QtWidgets.QHBoxLayout)

        # Collect original MCs in visual (layout) order, not findChildren order.
        src_mcs = []
        if src_layout is not None:
            for i in range(src_layout.count()):
                item = src_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, MC):
                    src_mcs.append((w._label, w.pv, bool(getattr(w, "_custom_label", False))))
        # Fallback if no MCs found via the layout (e.g. nested):
        if not src_mcs:
            for mc in panel.findChildren(MC):
                src_mcs.append((mc._label, mc.pv, bool(getattr(mc, "_custom_label", False))))

        src_btns = [(cb.text(), cb.action_type, cb.action,
                     getattr(cb, '_bg', None), getattr(cb, '_fg', None),
                     getattr(cb, '_font_size', None))
                    for cb in list(panel.custom_buttons)]

        # Build the new panel — generate a key that doesn't collide with any
        # existing panel OR any key previously recorded in _deleted_panels
        # (those would be silently dropped by the loader).
        while True:
            self._next_panel_id += 1
            new_key = f"{base_title}#{self._next_panel_id}::{tab_name}"
            if new_key not in self._panels and new_key not in self._deleted_panels:
                break
        new_panel = Panel(base_title + " (copy)", new_key, canvas)

        lay = QtWidgets.QHBoxLayout(new_panel) if is_horizontal else QtWidgets.QVBoxLayout(new_panel)
        lay.setContentsMargins(6, 22, 6, 6)
        lay.setSpacing(3)

        for label, pv, custom in src_mcs:
            new_mc = MC(label, pv)
            new_mc._custom_label = custom
            lay.addWidget(new_mc)
            self.mcs.append(new_mc)
            if hasattr(self, '_pve'):
                self._pve.monitor_many(new_mc.get_pvs())

        for text, atype, aval, bg, fg, fs in src_btns:
            new_btn = CfgButton(text, atype, aval, bg, fg, fs, new_panel)
            lay.addWidget(new_btn)
            new_panel.custom_buttons.append(new_btn)

        lay.addStretch()

        # Place below the original if possible, otherwise offset diagonally
        new_x = geo.x() + 20
        new_y = geo.y() + geo.height() + 8
        new_panel.setGeometry(new_x, new_y, geo.width(), geo.height())
        new_panel.show()
        new_panel.set_edit(self._edit_mode)
        self._panels[new_key] = new_panel
        self._panel_tab_map[new_key] = tab_name

    def _remove_panel(self, panel_key, record=True):
        """Remove a panel and clean up references."""
        if record and panel_key not in self._deleted_panels:
            self._deleted_panels.append(panel_key)
        panel = self._panels.pop(panel_key, None)
        self._panel_tab_map.pop(panel_key, None)
        if panel:
            # Remove motor cards that belong to this panel
            panel_mcs = panel.findChildren(MC)
            self.mcs = [m for m in self.mcs if m not in panel_mcs]
            # Remove PVField / ValveField registrations for this panel
            self._pv_fields.pop(panel_key, None)
            panel.deleteLater()

    # ── tab operations ───────────────────────────────────────────────

    def _on_tab_changed(self, idx):
        """Resize window and hide edit controls based on tab."""
        if idx < 0: return
        # Save outgoing tab's window size
        if hasattr(self, '_current_tab'):
            prev = self._current_tab
            if prev:
                self._tab_sizes[prev] = (self.width(), self.height())
        tab_name = self.tab_widget.tabText(idx)
        self._current_tab = tab_name
        # Apply window size for this tab
        size = self._tab_sizes.get(tab_name)
        if size:
            self.showNormal()
            self.resize(size[0], size[1])
        # Edit controls only visible in edit-allowed mode on non-User tabs
        if self._allow_edit:
            is_user = (tab_name == "User Mode")
            self.font_slider.setVisible(not is_user)
            self.font_lbl.setVisible(not is_user)
            self._font_label_widget.setVisible(not is_user)
            self.add_panel_btn.setVisible(not is_user)
            self.add_tab_btn.setVisible(not is_user)

    def _tab_context_menu(self, pos):
        if not self._allow_edit or not self._edit_mode: return
        idx = self.tab_widget.tabBar().tabAt(pos)
        if idx < 0: return
        tab_name = self.tab_widget.tabText(idx)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;} QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Rename Tab...", lambda: self._rename_tab(idx))
        menu.addAction("Tab Label Size...", self._set_tab_label_size)
        menu.addAction("Delete Tab...", lambda: self._delete_tab(idx))
        menu.addSeparator()
        menu.addAction("Restore All Default Panels...", lambda: self._restore_default_panels(tab_name))
        menu.exec_(self.tab_widget.tabBar().mapToGlobal(pos))

    def _restore_default_panels(self, tab_name):
        """Destroy all panels on this tab and rebuild the defaults.
        Warning: custom buttons and position edits on this tab are lost."""
        reply = QtWidgets.QMessageBox.warning(
            self, "Restore Default Panels",
            f"This will delete ALL panels on tab '{tab_name}' and rebuild the defaults.\n\n"
            "Custom buttons and position/size edits on this tab will be LOST.\n\n"
            "Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        # Remove all panels on this tab without recording them as "deleted"
        for key in [k for k, t in self._panel_tab_map.items() if t == tab_name]:
            self._remove_panel(key, record=False)
        # Clear deleted record for this tab
        self._deleted_panels = [k for k in self._deleted_panels
                                if not k.endswith(f"::{tab_name}")]
        self._build_all_panels(tab_name)
        for key, p in self._panels.items():
            if self._panel_tab_map.get(key) == tab_name:
                p.set_edit(self._edit_mode)

    def _set_tab_label_size(self):
        """Let the user set the tab label font size and padding."""
        cur_fs = self._tab_label_font_size
        val, ok = QtWidgets.QInputDialog.getInt(
            self, "Tab Label Size", "Font size (pt):", cur_fs, 8, 30)
        if ok:
            self._tab_label_font_size = val
            self._apply_tab_label_style()

    def _apply_tab_label_style(self):
        """Apply current tab label font size to the tab bar via stylesheet."""
        fs = self._tab_label_font_size
        pad_v = max(4, fs // 2)
        pad_h = max(8, fs)
        self.tab_widget.tabBar().setStyleSheet(
            f"QTabBar::tab{{background:#2d2d2d;color:#e0e0e0;"
            f"padding:{pad_v}px {pad_h}px;border:1px solid #404040;"
            f"border-bottom:none;margin-right:2px;font:bold {fs}pt;}}"
            f"QTabBar::tab:selected{{background:#1e5a8e;color:#fff;}}"
            f"QTabBar::tab:hover{{background:#3a3a3a;}}")

    def _set_tab_size(self, tab_name):
        cur = self._tab_sizes.get(tab_name, (1780, 900))
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle(f"Canvas Size for '{tab_name}'")
        dlg.setStyleSheet("QDialog{background:#000;color:#e0e0e0;}"
            "QLabel{color:#e0e0e0;} QSpinBox{background:#2d2d2d;color:#e0e0e0;padding:4px;border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;border:1px solid #404040;border-radius:3px;}")
        fl = QtWidgets.QFormLayout(dlg); fl.setSpacing(8)
        w_spin = QtWidgets.QSpinBox(); w_spin.setRange(400, 6000); w_spin.setValue(cur[0]); w_spin.setSuffix(" px")
        h_spin = QtWidgets.QSpinBox(); h_spin.setRange(300, 4000); h_spin.setValue(cur[1]); h_spin.setSuffix(" px")
        fl.addRow("Width:", w_spin); fl.addRow("Height:", h_spin)
        hint = QtWidgets.QLabel("Sets the scrollable canvas area.\nSmaller = no scrollbar needed if panels fit.")
        hint.setStyleSheet("color:#888;font:8pt;"); fl.addRow(hint)
        btns = QtWidgets.QHBoxLayout()
        bok = QtWidgets.QPushButton("OK"); bok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        bok.clicked.connect(dlg.accept); btns.addWidget(bok)
        bc = QtWidgets.QPushButton("Cancel"); bc.clicked.connect(dlg.reject); btns.addWidget(bc)
        fl.addRow(btns)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._tab_sizes[tab_name] = (w_spin.value(), h_spin.value())

    def _rename_tab(self, idx):
        old_name = self.tab_widget.tabText(idx)
        text, ok = QtWidgets.QInputDialog.getText(self, "Rename Tab", "New name:", text=old_name)
        if not ok or not text or text == old_name: return
        if text in self._tab_canvases:
            QtWidgets.QMessageBox.warning(self, "Exists", f"Tab '{text}' already exists."); return
        canvas = self._tab_canvases.pop(old_name); self._tab_canvases[text] = canvas
        # Carry over tab size
        if old_name in self._tab_sizes:
            self._tab_sizes[text] = self._tab_sizes.pop(old_name)
        # Update panel keys and tab map
        for old_key in list(self._panels.keys()):
            if self._panel_tab_map.get(old_key) == old_name:
                panel = self._panels.pop(old_key)
                base = old_key.rsplit("::", 1)[0]
                new_key = f"{base}::{text}"
                panel.key = new_key
                self._panels[new_key] = panel
                self._panel_tab_map[new_key] = text
        self.tab_widget.setTabText(idx, text)

    def _delete_tab(self, idx):
        tab_name = self.tab_widget.tabText(idx)
        panels_in_tab = [k for k, v in self._panel_tab_map.items() if v == tab_name]
        other_tabs = [n for n in self._tab_names() if n != tab_name]
        if not other_tabs:
            QtWidgets.QMessageBox.warning(self, "Cannot Delete", "Cannot delete the last tab."); return
        if panels_in_tab:
            reply = QtWidgets.QMessageBox.question(self, "Delete Tab",
                f"Tab '{tab_name}' has {len(panels_in_tab)} panel(s).\nThey will be moved to '{other_tabs[0]}'. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes: return
            for pk in panels_in_tab:
                self._move_panel_to_tab(pk, other_tabs[0])
        self._tab_canvases.pop(tab_name, None); self.tab_widget.removeTab(idx)

    def _add_new_tab(self):
        text, ok = QtWidgets.QInputDialog.getText(self, "New Tab", "Tab name:")
        if not ok or not text: return
        if text in self._tab_canvases:
            QtWidgets.QMessageBox.warning(self, "Exists", f"Tab '{text}' already exists."); return
        self._create_tab(text)

    def _add_new_panel(self):
        text, ok = QtWidgets.QInputDialog.getText(self, "New Panel", "Panel name:")
        if not ok or not text: return
        current_tab = self.tab_widget.tabText(self.tab_widget.currentIndex())
        p, _ = self._make_panel(text, 200, 100, current_tab)
        p.setGeometry(50, 50, 200, 100); p.set_edit(True)
        lay = QtWidgets.QVBoxLayout(); lay.setContentsMargins(6, 22, 6, 6); lay.addStretch(); p.setLayout(lay)

    # ── font scale ───────────────────────────────────────────────────

    def _change_font_scale(self, pct):
        set_font_scale(pct)
        self.font_lbl.setText(f"{pct}%")
        for mc in self.mcs: mc.scale_fonts()

    # ── edit mode ────────────────────────────────────────────────────

    def _toggle_edit(self, on):
        self._edit_mode = on
        for p in self._panels.values():
            p.set_edit(on)
            for w in p.findChildren(QtWidgets.QPushButton):
                if not isinstance(w, CfgButton): w.setEnabled(not on)
            for w in p.findChildren(QtWidgets.QLineEdit): w.setEnabled(not on)
            for w in p.findChildren(QtWidgets.QComboBox): w.setEnabled(not on)
        # Enable "Edit PV..." right-click on each PVField while in edit mode
        for slot in self._pv_fields.values():
            for f in slot.values():
                f.set_edit_mode(on)
        self.add_panel_btn.setVisible(on); self.add_tab_btn.setVisible(on)
        if on:
            self.statusBar().showMessage(
                "EDIT MODE — drag/resize panels, right-click panels/motors/PV fields to edit. "
                "Layout is saved automatically on window close."
            )
            self.statusBar().setStyleSheet("background:#f39c12;color:#000;font:bold 9pt;")
        else:
            self._save_layout()
            self.statusBar().showMessage("Layout saved.", 3000)
            self.statusBar().setStyleSheet("")

    # ── save / load ──────────────────────────────────────────────────

    def _save_layout(self):
        # Capture current tab's window size
        cur_tab = self.tab_widget.tabText(self.tab_widget.currentIndex())
        if cur_tab:
            self._tab_sizes[cur_tab] = (self.width(), self.height())
        data = {"_font_scale": self.font_slider.value(), "_tabs": self._tab_names(),
                "_tab_sizes": {k: list(v) for k, v in self._tab_sizes.items()},
                "_tab_label_fs": self._tab_label_font_size,
                "_deleted_panels": self._deleted_panels,
                "_panels": {}, "_tab_map": {}, "_buttons": {}, "_styles": {},
                "_title_fonts": {}, "_titles": {}, "_mcs": {}, "_pv_fields": {},
                "_custom_rows": self._custom_rows,
                "_io_labels": {fid: labels[0].text()
                               for fid, labels in getattr(self, "_io_labels", {}).items()
                               if labels}}
        # PV-field assignments — save PV per field_id per panel.
        # Widgets that hold >1 PV (ValveField, PVField 'btn_pair') return a dict
        # from get_pvs_dict(); plain single-PV fields save as a bare string.
        for panel_key, slot in self._pv_fields.items():
            out = {}
            for fid, f in slot.items():
                d = f.get_pvs_dict() if hasattr(f, "get_pvs_dict") else None
                out[fid] = d if d is not None else getattr(f, "pv", "")
            data["_pv_fields"][panel_key] = out
        for k, p in self._panels.items():
            g = p.geometry()
            data["_panels"][k] = [g.x(), g.y(), g.width(), g.height()]
            data["_tab_map"][k] = self._panel_tab_map.get(k, "")
            data["_titles"][k] = p.title_text()
            # Save panel title font
            import re
            tm = re.search(r'(\d+)\s*pt', p._title.styleSheet())
            if tm:
                data["_title_fonts"][k] = int(tm.group(1))
            if p.custom_buttons:
                data["_buttons"][k] = [b.to_dict() for b in p.custom_buttons]
            # Save motor cards (pv + label + custom-label flag) by positional index
            panel_mcs = p.findChildren(MC)
            if panel_mcs:
                data["_mcs"][k] = [
                    {"label": mc._label, "pv": mc.pv, "custom": bool(mc._custom_label),
                     "twv": mc.twv.text()}
                    for mc in panel_mcs
                ]
            for btn in p.findChildren(QtWidgets.QPushButton):
                if isinstance(btn, CfgButton): continue
                bg = btn.property("_custom_bg")
                if bg:
                    btn_id = f"{k}|||{btn.text()}"
                    data["_styles"][btn_id] = {"bg": bg, "fg": btn.property("_custom_fg"),
                        "fs": btn.property("_custom_fs"), "w": btn.width(), "h": btn.height()}
        # Always save to the per-user path — never overwrite the shared
        # bundled template. Create ~/.bl_gui/ on demand.
        lay_path = _user_lay_path()
        try:
            os.makedirs(os.path.dirname(lay_path), exist_ok=True)
            # Write via temp + rename + fsync so a crash mid-write can't
            # leave a truncated layout file on disk.
            tmp_path = lay_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, lay_path)
            mc_total = sum(len(v) for v in data["_mcs"].values())
            btn_total = sum(len(v) for v in data["_buttons"].values())
            print(f"[SAVE] wrote {lay_path}  panels={len(data['_panels'])}  mcs={mc_total}  "
                  f"titles={len(data['_titles'])}  deleted={len(data['_deleted_panels'])}  "
                  f"buttons={btn_total}  io_labels={len(data.get('_io_labels', {}))}")
            # Print a sample of what we just wrote so the user can verify
            # their edits actually landed in the file.
            tsample = {k: v for i, (k, v) in enumerate(data["_titles"].items()) if i < 6}
            print(f"[SAVE]   sample titles: {tsample}")
            iolabels = data.get("_io_labels", {})
            if iolabels:
                print(f"[SAVE]   io_labels: {iolabels}")
        except Exception as e:
            print(f"[SAVE] FAILED: {e}")
            import traceback
            traceback.print_exc()

    def _load_layout(self):
        user_path = _user_lay_path()
        bundled_path = _bundled_lay_path()
        lay_path = _lay_path()
        print(f"[LOAD] user_path={user_path} exists={os.path.isfile(user_path)}")
        print(f"[LOAD] bundled_path={bundled_path} exists={os.path.isfile(bundled_path)}")
        print(f"[LOAD] using={lay_path}")
        if not os.path.isfile(lay_path):
            print(f"[LOAD] no file at {lay_path}, using defaults")
            return
        try:
            with open(lay_path) as f: data = json.load(f)
            fs = data.get("_font_scale")
            if fs is not None: self.font_slider.setValue(int(fs))
            # Restore tab label font size
            tlfs = data.get("_tab_label_fs")
            if tlfs is not None:
                self._tab_label_font_size = int(tlfs)
                self._apply_tab_label_style()
            # Restore canvas sizes
            saved_sizes = data.get("_tab_sizes", {})
            for k, v in saved_sizes.items():
                if isinstance(v, list) and len(v) == 2:
                    self._tab_sizes[k] = tuple(v)
            # Restore extra tabs
            saved_tabs = data.get("_tabs")
            if saved_tabs:
                current_tabs = set(self._tab_names())
                for tab_name in saved_tabs:
                    if tab_name not in current_tabs: self._create_tab(tab_name)
                for i, tab_name in enumerate(saved_tabs):
                    for j in range(self.tab_widget.count()):
                        if self.tab_widget.tabText(j) == tab_name:
                            if j != i: self.tab_widget.tabBar().moveTab(j, i)
                            break
            # Recreate duplicated panels (present in saved _panels but not built
            # by _build_all_panels). We need this before the other loops so that
            # their buttons, styles and MC data can target them.
            tab_map = data.get("_tab_map", {})
            mcs_saved = data.get("_mcs", {})
            panels_saved = data.get("_panels", {})
            # Bump _next_panel_id past any "#N" seen in saved panels OR in the
            # deleted-panels list, so future duplications cannot collide with
            # a previously-deleted key (which would then be silently dropped).
            import re
            max_id = self._next_panel_id
            for k in list(panels_saved.keys()) + list(data.get("_deleted_panels", [])):
                m = re.search(r"#(\d+)::", k)
                if m:
                    try:
                        max_id = max(max_id, int(m.group(1)))
                    except ValueError:
                        pass
            self._next_panel_id = max_id
            recreated = 0
            for k, rect in panels_saved.items():
                if k in self._panels:
                    continue    # default panel, already exists
                if k in data.get("_deleted_panels", []):
                    continue    # user had deleted it
                tab_name = tab_map.get(k) or _DEFAULT_TABS[0]
                canvas = self._tab_canvases.get(tab_name)
                if canvas is None:
                    print(f"[LOAD] SKIP (no canvas): panel={k!r}  tab={tab_name!r}  "
                          f"available_tabs={list(self._tab_canvases.keys())}")
                    continue
                # Reconstruct a base title from the key: "Base#N::Tab" -> "Base"
                base = k.split("::")[0].split("#")[0]
                new_p = Panel(base + " (copy)", k, canvas)
                # Use the saved MC list to decide layout orientation + contents
                mc_list = mcs_saved.get(k, [])
                lay = QtWidgets.QHBoxLayout(new_p) if mc_list else QtWidgets.QVBoxLayout(new_p)
                lay.setContentsMargins(6, 22, 6, 6); lay.setSpacing(3)
                for md in mc_list:
                    mc = MC(md.get("label", ""), md.get("pv", ""))
                    mc._custom_label = bool(md.get("custom"))
                    saved_twv = md.get("twv")
                    if saved_twv: mc.twv.setText(saved_twv)
                    lay.addWidget(mc)
                    self.mcs.append(mc)
                lay.addStretch()
                new_p.show()
                self._panels[k] = new_p
                self._panel_tab_map[k] = tab_name
                recreated += 1
                print(f"[LOAD] recreated duplicated panel: {k!r}  tab={tab_name!r}  mcs={len(mc_list)}")
            # Panel positions (now includes the restored duplicates)
            for k, rect in panels_saved.items():
                p = self._panels.get(k)
                if p and isinstance(rect, list) and len(rect) == 4: p.setGeometry(*rect)
            # Remove panels that were explicitly deleted by the user
            deleted = data.get("_deleted_panels", [])
            self._deleted_panels = list(deleted)
            for k in list(self._panels.keys()):
                if k in deleted:
                    self._remove_panel(k, record=False)
            # Restore panel titles (renames)
            titles_saved = data.get("_titles", {})
            for k, title in titles_saved.items():
                p = self._panels.get(k)
                if p and isinstance(title, str) and title:
                    p._title.setText(title)
                    p._title.adjustSize()
            # Restore panel title fonts
            title_fonts = data.get("_title_fonts", {})
            for k, fs in title_fonts.items():
                p = self._panels.get(k)
                if p:
                    p._title.setStyleSheet(
                        f"color: #73dfff; font: bold {fs}pt; background: transparent; padding: 2px 6px;"
                    )
                    p._title.adjustSize()
            # Custom buttons — if the saved layout has buttons for a panel,
            # they REPLACE the default buttons built by _build_all_panels
            # (Launchers / Displays panels seed defaults into
            # p.custom_buttons so the user can edit them; without this
            # replacement we'd end up with duplicates after a save).
            buttons = data.get("_buttons", {})
            for panel_key, btn_list in buttons.items():
                p = self._panels.get(panel_key)
                if not p: continue
                for existing in list(p.custom_buttons):
                    lay = p.layout()
                    if lay is not None:
                        lay.removeWidget(existing)
                    existing.setParent(None); existing.deleteLater()
                p.custom_buttons.clear()
                cols = getattr(p, "_grid_cols", None)
                defaults = getattr(p, "_cfg_btn_defaults", None)
                default_specs = getattr(p, "_default_btn_specs", [])
                # Merge: if the saved list is missing any default button,
                # append it. Each spec is (label, cmd) = shell default,
                # or (label, action_type, action) for e.g. caput presets.
                # Match by label OR action so renames don't duplicate.
                saved_labels = {bd.get("label") for bd in btn_list}
                saved_actions = {bd.get("action") for bd in btn_list}
                merged = list(btn_list)
                for spec in default_specs:
                    if len(spec) == 3:
                        lbl, atype, cmd = spec
                    else:
                        lbl, cmd = spec; atype = "shell"
                    if lbl in saved_labels or cmd in saved_actions:
                        continue
                    merged.append({"label": lbl, "type": atype, "action": cmd,
                                   "bg": defaults[0] if defaults else "#2d2d2d",
                                   "fg": defaults[1] if defaults else "#e0e0e0",
                                   "font_size": defaults[2] if defaults else 9})
                for idx, bd in enumerate(merged):
                    btn = CfgButton.from_dict(bd, p)
                    btn.setMinimumHeight(34)
                    # Only override style when the saved colour is the
                    # stale default "#2d2d2d" from a pre-refresh save —
                    # otherwise respect whatever the user set.
                    if defaults and bd.get("bg") in ("#2d2d2d", None):
                        btn._bg, btn._fg, btn._font_size = defaults
                        btn._apply_style()
                    lay = p.layout()
                    if isinstance(lay, QtWidgets.QGridLayout) and cols:
                        lay.addWidget(btn, idx // cols, idx % cols)
                    elif lay:
                        lay.addWidget(btn)
                    else:
                        btn.move(10, 30)
                    btn.show(); p.custom_buttons.append(btn)
            # Per-button styles
            styles = data.get("_styles", {})
            for btn_id, sty in styles.items():
                # Support both new ||| separator and old :: separator
                if "|||" in btn_id:
                    parts = btn_id.split("|||", 1)
                else:
                    parts = btn_id.split("::", 1)
                if len(parts) != 2: continue
                panel_key, btn_text = parts
                p = self._panels.get(panel_key)
                if not p: continue
                for btn in p.findChildren(QtWidgets.QPushButton):
                    if isinstance(btn, CfgButton): continue
                    if btn.text() == btn_text:
                        bg=sty.get("bg","#2d2d2d"); fg=sty.get("fg","#e0e0e0"); ffs=sty.get("fs",9)
                        btn.setStyleSheet(f"background:{bg};color:{fg};font:{ffs}pt;border:1px solid #404040;border-radius:3px;padding:4px 8px;")
                        btn.setProperty("_custom_bg",bg); btn.setProperty("_custom_fg",fg); btn.setProperty("_custom_fs",ffs)
                        w=sty.get("w"); h=sty.get("h")
                        if w and h: btn.setMinimumSize(w,h); btn.setMaximumSize(w,h)
                        break
            # Motor cards: WIPE the panel's default MCs (built by _build_all_panels)
            # and rebuild the exact list that was saved. This is the only correct
            # strategy because deletes/duplicates can change the MC count — mapping
            # saved entries onto defaults by index causes every motor after the
            # change to shift and show wrong labels/PVs on restart.
            mcs_saved = data.get("_mcs", {})
            for panel_key, mc_list in mcs_saved.items():
                p = self._panels.get(panel_key)
                if not p: continue
                lay = p.layout()
                # Drop every existing MC from this panel and from self.mcs
                for existing_mc in p.findChildren(MC):
                    try: self.mcs.remove(existing_mc)
                    except ValueError: pass
                    if lay is not None:
                        lay.removeWidget(existing_mc)
                    existing_mc.setParent(None)
                    existing_mc.deleteLater()
                # Rebuild from the saved list (preserving order)
                if lay is None: continue
                for idx, mc_data in enumerate(mc_list):
                    mc = MC(mc_data.get("label", ""), mc_data.get("pv", ""))
                    if mc_data.get("custom"):
                        mc._custom_label = True
                    saved_twv = mc_data.get("twv")
                    if saved_twv: mc.twv.setText(saved_twv)
                    lay.insertWidget(idx, mc)
                    self.mcs.append(mc)
            # User-added PV rows (from 'Add PV Row...' in edit mode)
            saved_custom = data.get("_custom_rows", {})
            self._custom_rows = {}   # rebuild from the saved config below
            for panel_key, rows in saved_custom.items():
                p = self._panels.get(panel_key)
                if p is None: continue
                for row_cfg in rows:
                    try:
                        self._add_custom_pv_row(p, row_cfg, record=True)
                    except Exception as ex:
                        print(f"[LOAD] skipping bad custom row on {panel_key}: {ex}")
            # PV-field assignments (Energy, Valves, etc.) — override defaults
            # per field_id. Accept both the simple string form (plain PVField)
            # and the dict form (ValveField).
            pv_fields_saved = data.get("_pv_fields", {})
            for panel_key, fields in pv_fields_saved.items():
                slot = self._pv_fields.get(panel_key)
                if not slot:
                    continue
                for fid, saved in fields.items():
                    f = slot.get(fid)
                    if f is None:
                        continue
                    if isinstance(saved, str) and hasattr(f, "pv"):
                        f.pv = saved.strip()
                    elif isinstance(saved, dict) and hasattr(f, "set_pvs_dict"):
                        f.set_pvs_dict(saved)
            # Restore renamed In/Out panel header labels (all tabs at once).
            io_labels_saved = data.get("_io_labels", {})
            for fid, text in io_labels_saved.items():
                labels = getattr(self, "_io_labels", {}).get(fid) or []
                if isinstance(text, str) and text:
                    for l in labels:
                        l.setText(text)
            mc_total = sum(len(v) for v in mcs_saved.values())
            btn_total = sum(len(v) for v in data.get("_buttons", {}).values())
            iolabels = data.get("_io_labels", {})
            print(f"[LOAD] read {lay_path}  panels={len(data.get('_panels', {}))}  "
                  f"mcs={mc_total}  titles={len(data.get('_titles', {}))}  "
                  f"deleted={len(data.get('_deleted_panels', []))}  "
                  f"buttons={btn_total}  io_labels={len(iolabels)}")
            tsample = {k: v for i, (k, v) in enumerate(
                data.get("_titles", {}).items()) if i < 6}
            print(f"[LOAD]   sample titles: {tsample}")
            if iolabels:
                print(f"[LOAD]   io_labels: {iolabels}")
        except Exception as e:
            print(f"[LOAD] FAILED: {e}")
            import traceback
            traceback.print_exc()

    # ── PV engine ────────────────────────────────────────────────────

    def _start_monitors(self):
        self._pve = PVEngine(self); self._pve.updated.connect(self._on_pv)
        pvs = set()
        for m in self.mcs: pvs.update(m.get_pvs())
        # All non-motor readbacks/setpoints/LEDs/combos come from PVFields now
        for slot in self._pv_fields.values():
            for f in slot.values():
                pvs.update(f.monitored_pvs())
        pv_list = list(pvs)
        print(f"[PV] monitoring {len(pv_list)} PVs...")
        # Do the monitor setup off the GUI thread. Each pvaccess Channel()
        # and startMonitor() call can block on CA discovery (especially if
        # some IOCs are down), and serially subscribing 300+ PVs on the
        # main thread freezes the window for seconds to minutes.
        import threading
        threading.Thread(target=self._pve.monitor_many, args=(pv_list,),
                         daemon=True, name="pv-monitor-setup").start()

    @QtCore.pyqtSlot(str, str)
    def _on_pv(self, pv_name, value):
        # Motors — update all copies
        for m in self.mcs:
            if pv_name.startswith(m.pv + "."):
                field = pv_name[len(m.pv) + 1:]
                m.apply_one(field, value)
            elif pv_name == f"{m.pv}_able":
                # APS convention: 0/"Enable" = enabled, 1/"Disable" = disabled
                enabled = value in ("0", "0.0", "Enable", "Enabled")
                m.set_enabled(enabled)

        # PVField / ValveField rows — fan out to every field bound to this PV
        for slot in self._pv_fields.values():
            for f in slot.values():
                if getattr(f, "pv", None) == pv_name or \
                   getattr(f, "status_pv", None) == pv_name:
                    f.update_value(value)

    def closeEvent(self, event):
        # If we are closing from inside edit mode, save — otherwise every
        # edit done in-session would silently vanish on window-close.
        # In view mode we still do NOT save so a closed-without-Ctrl+S
        # cannot stomp on an externally hand-edited layout.json.
        if getattr(self, "_edit_mode", False):
            try: self._save_layout()
            except Exception as e: print(f"[SAVE] close-event save failed: {e}")
        if hasattr(self, '_pve'): self._pve.stop_all()
        event.accept()

    def _explicit_save_layout(self):
        """User-triggered save — the ONLY way the layout file gets written
        outside of the edit-mode exit. Shows a confirmation in the status
        bar so the user sees it happened."""
        self._save_layout()
        try:
            self.statusBar().showMessage(f"Saved layout to {_user_lay_path()}", 4000)
        except Exception:
            pass

    def _rename_io_label_by_fid(self, fid):
        """Rename every In/Out label that shares this fid across tabs.
        Triggered by right-click on any In/Out header label."""
        labels = getattr(self, "_io_labels", {}).get(fid) or []
        if not labels:
            return
        current = labels[0].text()
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename", f"New label for {fid}:",
            QtWidgets.QLineEdit.Normal, current)
        if ok and text.strip():
            for l in labels:
                l.setText(text.strip())

    def _on_set_energy(self):
        """Go button in the Energy panel. If the EPICS calibration-file PVs
        (EnergyCalibrationFileOne / Two) are both blank, use the local ZP
        calibration table (~/.bl_gui/bl32id_zp_calibration.json) to drive
        the ZP X/Y/Z motors; otherwise let the IOC handle it normally via
        EnergySet. The mono move is triggered in both cases."""
        # Simple rule: Use Calib YES → bl_gui's table is the authority for
        # motor positions; direct-move the motors. Use Calib NO → leave
        # motors alone. EPICS cal-file PVs are IGNORED here (only the
        # Generate Cal Files button touches them).
        use_cal_on = False
        for slot in self._pv_fields.values():
            f = slot.get("energy_usecalib")
            if f is not None and hasattr(f, "_is_open"):
                use_cal_on = bool(f._is_open); break
        print(f"[ENERGY] use_cal_on={use_cal_on}")
        if use_cal_on:
            self._move_motors_from_plugin()
        caput_bg("32id:TXMOptics:EnergySet", 1)

    _CAL_FILE_DIR = "/home/beams/USERTXM/epics/synApps/support/txmoptics/iocBoot/iocTXMOptics"

    def _trigger_qgmax(self):
        """Fire a one-shot QGMax optimization by writing pystream's request
        file. The pystream QGMax background watcher polls that file twice
        a second and runs one optimization cycle."""
        if getattr(self, "_qgmax_running", False):
            print("[QGMAX] cycle already running — ignoring trigger")
            return
        try:
            from .beamlines.bl32id import qgmax_trigger
            ts = qgmax_trigger.trigger()
            # Reflect state immediately — don't wait for the next poll tick.
            self._qgmax_running = True
            for b in getattr(self, "_qgmax_buttons", []):
                self._style_qgmax_button(b, running=True)
            self.statusBar().showMessage(
                f"QGMax trigger sent (ts={ts:.1f}) — optimization running…", 4000)
            print(f"[QGMAX] trigger ts={ts}")
        except Exception as e:
            print(f"[QGMAX] trigger failed: {e}")
            QtWidgets.QMessageBox.warning(self, "QGMax",
                f"Could not write the trigger file:\n{e}")

    def _style_qgmax_button(self, btn, running):
        if running:
            btn.setText("QGMax… running")
            btn.setStyleSheet(
                "background:#f39c12;color:#000;font:bold 10pt;"
                "border:1px solid #f1c40f;border-radius:3px;")
            btn.setToolTip("QGMax is running — wait until it finishes.")
            btn.setEnabled(False)
        else:
            btn.setText("QGMax")
            btn.setStyleSheet(
                "background:#8e44ad;color:#fff;font:bold 10pt;"
                "border:1px solid #9b59b6;border-radius:3px;")
            btn.setToolTip(
                "Trigger a single QGMax image-mean optimization cycle "
                "(pystream must be running).")
            btn.setEnabled(True)

    def _poll_qgmax_status(self):
        from .beamlines.bl32id import qgmax_trigger
        st = qgmax_trigger.read_status()
        running = bool(st and st.get("running"))
        if running != getattr(self, "_qgmax_running", False):
            self._qgmax_running = running
            for b in getattr(self, "_qgmax_buttons", []):
                self._style_qgmax_button(b, running=running)
            if not running:
                self.statusBar().showMessage("QGMax: done.", 3000)

    # Energy range guard (keV) enforced by the GUI on the Energy SP field.
    _ENERGY_MIN_KEV = 6.5
    _ENERGY_MAX_KEV = 12.0

    def _on_energy_sp_return(self, sp_field):
        """Validate Energy SP against [6.5, 12] keV before caput. On
        out-of-range, clamp the text back to the last valid value and
        pop a warning — no caput happens."""
        txt = sp_field._inner.text().strip()
        try:
            val = float(txt)
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self, "Energy invalid", f"{txt!r} is not a number.")
            return
        if val < self._ENERGY_MIN_KEV or val > self._ENERGY_MAX_KEV:
            QtWidgets.QMessageBox.warning(
                self, "Energy out of range",
                f"Energy must be between {self._ENERGY_MIN_KEV} and "
                f"{self._ENERGY_MAX_KEV} keV (you entered {val}).")
            return
        # Normal PVField caput path (same as _on_sp_return does).
        print(f"[SP] {sp_field.field_id}: caput {sp_field.pv} {txt!r}")
        caput_bg(sp_field.pv, txt)

    def _move_motors_from_plugin(self):
        """Linear-interpolate X/Y/Z/QG-V/QG-H at the current Energy SP
        from the ZP calibration table and caput the positions to each
        motor. Used when the EPICS cal-file PVs are empty: bl_gui acts
        as the calibration authority instead of the IOC."""
        sp = None
        for slot in self._pv_fields.values():
            f = slot.get("energy_sp")
            if f is not None:
                sp = f; break
        if sp is None:
            print("[ENERGY] no energy_sp widget found")
            return
        try:
            e_keV = float(sp._inner.text())
        except (ValueError, AttributeError):
            print("[ENERGY] energy setpoint not numeric; aborting")
            return
        e_eV = e_keV * 1000.0

        from .beamlines.bl32id import xanes_calib
        cfg = xanes_calib.load_config()
        pts = [p for p in (cfg.get("points") or []) if p and p[0] is not None]
        pvs = cfg.get("pvs", dict(xanes_calib.DEFAULT_PVS))
        print(f"[ENERGY] plugin config: {len(pts)} cal rows; pvs keys={list(pvs.keys())}")
        if len(pts) < 2:
            print(f"[ENERGY] only {len(pts)} cal point(s) — need >= 2")
            return

        def _interp(col_idx):
            return _polyfit_interp(pts, col_idx, e_eV)

        for name, col, pv_key in (("X", 1, "zp_x_pv"),
                                  ("Y", 2, "zp_y_pv"),
                                  ("Z", 3, "zp_z_pv"),
                                  ("QG V", 4, "qg_v_pv"),
                                  ("QG H", 5, "qg_h_pv")):
            target_pv = pvs.get(pv_key)
            if not target_pv:
                print(f"[ENERGY] {name}: no PV configured (key={pv_key}), skipping")
                continue
            v = _interp(col)
            if v is None:
                print(f"[ENERGY] {name}: no cal data for column {col}, skipping")
                continue
            # Sync-caput to the motor's .VAL so we see success/fail now,
            # not buried in an async worker.
            try:
                r = subprocess.run(["caput", target_pv, f"{float(v):.6f}"],
                                   capture_output=True, timeout=5.0, text=True)
                if r.returncode == 0:
                    print(f"[ENERGY] {name} @ {e_keV:g} keV -> {v:.6f}  "
                          f"caput {target_pv} OK  stdout={r.stdout.strip()!r}")
                else:
                    print(f"[ENERGY] {name} caput {target_pv} rc={r.returncode} "
                          f"stderr={r.stderr.strip()!r}")
            except Exception as ex:
                print(f"[ENERGY] {name} caput {target_pv} EXC: {ex}")

    def _on_cal_range_changed(self, value):
        """Persist the ± energy range into the calibration config JSON as
        soon as it changes, so Go / Generate Cal Files always see the
        current value."""
        from .beamlines.bl32id import xanes_calib
        try:
            cfg = xanes_calib.load_config()
            cfg["range_keV"] = float(value)
            xanes_calib.save_config(cfg)
        except Exception as e:
            print(f"[ENERGY] failed to persist range_keV: {e}")

    def _apply_zp_calib_from_plugin(self):
        """Auto-generate two EPICS cal files at E_target ± range (range
        comes from the ZP calibration tab). Each file holds X/Y/Z motor
        positions INTERPOLATED from the local calibration table at that
        energy."""
        sp_widget = None
        for slot in self._pv_fields.values():
            f = slot.get("energy_sp")
            if f is not None:
                sp_widget = f; break
        if sp_widget is None:
            print("[ENERGY] could not locate energy_sp field")
            return
        try:
            e_keV = float(sp_widget._inner.text())
        except (ValueError, AttributeError):
            print("[ENERGY] energy setpoint not numeric; aborting")
            return

        from .beamlines.bl32id import xanes_calib
        cfg = xanes_calib.load_config()
        pts = [p for p in (cfg.get("points") or []) if p and p[0] is not None]
        pvs = cfg.get("pvs", dict(xanes_calib.DEFAULT_PVS))
        try:
            range_keV = float(cfg.get("range_keV", xanes_calib.DEFAULT_RANGE_KEV))
        except (TypeError, ValueError):
            range_keV = xanes_calib.DEFAULT_RANGE_KEV
        if len(pts) < 2:
            print(f"[ENERGY] only {len(pts)} cal point(s) — need ≥2; aborting")
            return

        def _interp_at(col_idx, e_eV_target):
            return _polyfit_interp(pts, col_idx, e_eV_target)

        e_lo_keV = e_keV - range_keV
        e_hi_keV = e_keV + range_keV
        axis_pvs = [(pvs.get("zp_x_pv"), 1),
                    (pvs.get("zp_y_pv"), 2),
                    (pvs.get("zp_z_pv"), 3),
                    (pvs.get("qg_v_pv"), 4),
                    (pvs.get("qg_h_pv"), 5)]

        try:
            os.makedirs(self._CAL_FILE_DIR, exist_ok=True)
        except Exception as ex:
            print(f"[ENERGY] cal dir not writable: {ex} — aborting")
            return

        def _name(e_keV_val):
            s = f"{e_keV_val:g}".replace(".", "p")
            return f"Energy_{s}keV.txt"

        filenames = []
        for e_target_keV in (e_lo_keV, e_hi_keV):
            e_target_eV = e_target_keV * 1000.0
            fname = _name(e_target_keV)
            fpath = os.path.join(self._CAL_FILE_DIR, fname)
            try:
                with open(fpath, "w") as f:
                    f.write(f"energy {e_target_keV:g}\n")
                    for pv_name, col in axis_pvs:
                        if not pv_name: continue
                        v = _interp_at(col, e_target_eV)
                        if v is None: continue
                        f.write(f"{pv_name} {v:.6f}\n")
                print(f"[ENERGY] wrote {fpath} (E={e_target_keV:g} keV)")
                filenames.append(fname)
            except Exception as ex:
                print(f"[ENERGY] failed to write {fpath}: {ex}")
                return

        # EnergyCalibrationFile* are 40-char stringout records — a full
        # path gets silently truncated. Store ONLY the filename in the PV
        # and rely on the IOC (CWD = iocBoot/iocTXMOptics, or patched to
        # prepend the cal dir) to open it. Sync-caput to avoid the race
        # where EnergySet fires before the PV update lands.
        for pv_name, fname in (
                ("32id:TXMOptics:EnergyCalibrationFileOne", filenames[0]),
                ("32id:TXMOptics:EnergyCalibrationFileTwo", filenames[1])):
            try:
                r = subprocess.run(["caput", pv_name, fname],
                                   capture_output=True, timeout=3.0, text=True)
                if r.returncode != 0:
                    print(f"[ENERGY] caput {pv_name} rc={r.returncode} "
                          f"stderr={r.stderr.strip()!r}")
            except Exception as ex:
                print(f"[ENERGY] caput {pv_name} EXC: {ex}")
        for slot in self._pv_fields.values():
            for fid, fname in (("energy_calfile1", filenames[0]),
                               ("energy_calfile2", filenames[1])):
                f = slot.get(fid)
                if f is not None and hasattr(f, "_inner"):
                    try: f._inner.setText(fname)
                    except Exception: pass

        # Also move the motors directly from bl_gui, using our own
        # interpolation at the TARGET energy. This makes bl_gui
        # self-sufficient: even if the IOC's cal-file interpolation fails
        # or runs late, the motors still land at the right positions. The
        # IOC's later re-application (via EnergySet) will write the same
        # values — idempotent.
        e_target_eV = e_keV * 1000.0
        for pv_name, col in axis_pvs:
            if not pv_name: continue
            v = _interp_at(col, e_target_eV)
            if v is None: continue
            print(f"[ENERGY] direct move {pv_name} -> {v:.6f} (col {col})")
            caput_bg(pv_name, float(v))

    def _apply_cam_binning(self):
        """On Enter in Bin X or Bin Y: caput BinX / BinY / SizeX / SizeY.

        Mirrors pystream detectorcontrol's 'Apply Binning' button. SizeX/Y
        are computed from MaxSizeX_RBV / MaxSizeY_RBV (read live via caget)
        divided by the current BinX / BinY. Without this the driver leaves
        the ROI untouched and binning effectively doesn't take effect."""
        cam_prefix = "32idbSP1:cam1"
        for key, slot in self._pv_fields.items():
            if "cam_binx" in slot and "cam_biny" in slot:
                try:
                    binx = int(slot["cam_binx"]._inner.text() or "1")
                    biny = int(slot["cam_biny"]._inner.text() or "1")
                except ValueError:
                    print("[BIN] non-integer in Bin X / Bin Y — aborting")
                    return
                # Read sensor max — short timeout, must succeed to compute sizes.
                try:
                    max_x = int(float(subprocess.run(
                        ["caget", "-t", f"{cam_prefix}:MaxSizeX_RBV"],
                        capture_output=True, text=True, timeout=2.0,
                    ).stdout.strip()))
                    max_y = int(float(subprocess.run(
                        ["caget", "-t", f"{cam_prefix}:MaxSizeY_RBV"],
                        capture_output=True, text=True, timeout=2.0,
                    ).stdout.strip()))
                except Exception as e:
                    print(f"[BIN] could not read MaxSizeX/Y: {e}")
                    return
                size_x = max_x // max(1, binx)
                size_y = max_y // max(1, biny)
                print(f"[BIN] apply: BinX={binx} BinY={biny} "
                      f"SizeX={size_x} SizeY={size_y} (max={max_x}x{max_y})")
                caput_bg(f"{cam_prefix}:BinX",  binx)
                caput_bg(f"{cam_prefix}:BinY",  biny)
                caput_bg(f"{cam_prefix}:SizeX", size_x)
                caput_bg(f"{cam_prefix}:SizeY", size_y)
                return


class _PressFlash(QtCore.QObject):
    """App-wide filter that paints a semi-transparent yellow overlay on any
    button for ~200 ms when it's pressed, so the user can see whether a click
    registered even if the GUI then stalls (e.g. an MEDM launch grabbing the
    foreground). An overlay child widget is used instead of a graphics effect
    because many buttons in this app have inline stylesheets that suppress
    QGraphicsColorizeEffect rendering."""
    def eventFilter(self, obj, ev):
        if isinstance(obj, QtWidgets.QAbstractButton):
            t = ev.type()
            if t in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.KeyPress):
                if t == QtCore.QEvent.KeyPress and ev.key() not in (
                        QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    return False
                overlay = QtWidgets.QFrame(obj)
                overlay.setObjectName("_pressFlash")
                overlay.setStyleSheet(
                    "#_pressFlash{background-color:rgba(255,234,0,180);"
                    "border:2px solid #ff8800;border-radius:3px;}"
                )
                overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
                overlay.setGeometry(0, 0, obj.width(), obj.height())
                overlay.show()
                overlay.raise_()
                QtCore.QTimer.singleShot(200, overlay.deleteLater)
        return False


def main():
    """Command line:
        bl_gui                          # default layout, view-only
        bl_gui edit                     # default layout, edit mode
        bl_gui <file.json>              # load custom layout
        bl_gui <file.json> edit         # custom layout, edit mode
    <file.json> may be an absolute path, a relative path, or the bare name
    of a file that ships with the package (e.g. 'bl32id.json' resolves to
    the one bundled next to layout.json).
    """
    args = sys.argv[1:]
    allow_edit = "edit" in args
    args = [a for a in args if a != "edit"]

    layout_arg = args[0] if args else None
    if layout_arg:
        # Resolve in this order: absolute, cwd-relative, bundled-with-package.
        pkg_dir = os.path.dirname(os.path.abspath(_theme_mod.__file__))
        candidates = [
            layout_arg,
            os.path.join(os.getcwd(), layout_arg),
            os.path.join(pkg_dir, layout_arg),
            os.path.join(pkg_dir, "layouts", layout_arg),
        ]
        chosen = next((c for c in candidates if os.path.isfile(c)), None)
        if chosen is None:
            print(f"[ERROR] layout file not found: {layout_arg}")
            print("Tried:\n  " + "\n  ".join(candidates))
            sys.exit(2)
        _theme_mod._LAY = os.path.abspath(chosen)
        print(f"[CONFIG] using layout: {_theme_mod._LAY}")

    # HiDPI scaling — makes the GUI adapt to the display DPI so text stays
    # readable when viewing across monitors of very different sizes.
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv); app.setApplicationName("Beamline GUI")
    # App-wide UI font — DejaVu Sans is the crispest readable sans on APS
    # workstations. Liberation Sans / Noto Sans as fallbacks.
    _ui_font = QtGui.QFont("DejaVu Sans", 9)
    _ui_font.setStyleStrategy(QtGui.QFont.PreferAntialias)
    _ui_font.setStyleHint(QtGui.QFont.SansSerif)
    app.setFont(_ui_font)
    _press_flash = _PressFlash(app); app.installEventFilter(_press_flash)
    w = Win(allow_edit=allow_edit); w.show()
    app.aboutToQuit.connect(w.close)
    sys.exit(app.exec_())
