"""Main application window (beamline optics GUI) and entry point."""
import json
import os
import sys
from typing import Dict, List
from functools import partial

from PyQt5 import QtCore, QtGui, QtWidgets

from .motor import MC, GROUPS, _DEFAULT_TABS, _fs, _rb, _act, set_font_scale
from .pv import PVEngine, caput_bg
from . import theme as _theme_mod
from .theme import _IMG, _PANEL_SS, _PANEL_SS_EDIT, _SS


def _lay_path():
    """Return the current layout-file path (may be overridden by main() CLI)."""
    return _theme_mod._LAY
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
        self.shs: List[tuple] = []          # (pv_name_or_None, QLabel)
        self._rb: Dict[str, List[QtWidgets.QLabel]] = {}   # pv -> [labels...]
        self._special: Dict[str, List[QtWidgets.QLabel]] = {}  # special indicators
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

        self.edit_btn = QtWidgets.QPushButton("Edit Layout"); self.edit_btn.setFixedSize(100, 28); self.edit_btn.setCheckable(True)
        self.edit_btn.setStyleSheet("QPushButton{background:#2d2d2d;color:#e0e0e0;font:9pt;border:1px solid #404040;border-radius:3px;}"
                                    "QPushButton:checked{background:#f39c12;color:#000;font:bold 9pt;}")
        self.edit_btn.toggled.connect(self._toggle_edit); top.addWidget(self.edit_btn)

        self.add_panel_btn = QtWidgets.QPushButton("+ Panel"); self.add_panel_btn.setFixedSize(70, 28)
        self.add_panel_btn.setStyleSheet("background:#2d2d2d;color:#e0e0e0;font:9pt;border:1px solid #404040;border-radius:3px;")
        self.add_panel_btn.clicked.connect(self._add_new_panel); self.add_panel_btn.setVisible(False); top.addWidget(self.add_panel_btn)

        self.add_tab_btn = QtWidgets.QPushButton("+ Tab"); self.add_tab_btn.setFixedSize(60, 28)
        self.add_tab_btn.setStyleSheet("background:#2d2d2d;color:#e0e0e0;font:9pt;border:1px solid #404040;border-radius:3px;")
        self.add_tab_btn.clicked.connect(self._add_new_tab); self.add_tab_btn.setVisible(False); top.addWidget(self.add_tab_btn)

        # Hide all edit controls unless edit mode allowed
        if not self._allow_edit:
            self._font_label_widget.setVisible(False)
            self.font_slider.setVisible(False)
            self.font_lbl.setVisible(False)
            self.edit_btn.setVisible(False)
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
        QtCore.QTimer.singleShot(300, self._start_monitors)

    # ── helpers ───────────────────────────────────────────────────────

    def _unique_key(self, base, tab_name):
        """Generate unique panel key: base::tab_name, with dedup."""
        key = f"{base}::{tab_name}"
        if key not in self._panels:
            return key
        self._next_panel_id += 1
        return f"{base}#{self._next_panel_id}::{tab_name}"

    def _register_rb(self, pv, label):
        """Register a readback label for a PV (supports multiple labels per PV)."""
        self._rb.setdefault(pv, []).append(label)

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

        # --- Shutters ---
        p, _ = self._make_panel("Shutters", 380, 80, tab_name)
        lay = QtWidgets.QHBoxLayout(); lay.setContentsMargins(6, 20, 6, 4); lay.setSpacing(4)
        for a in [("A-Stn","32idb:rshtrA:Open.PROC","32idb:rshtrA:Close.PROC","PB:32ID:STA_A_FES_CLSD_PL"),
                  ("B-Stn","32idb:rshtrB:Open.PROC","32idb:rshtrB:Close.PROC","PB:32ID:STA_B_SBS_CLSD_PL"),
                  ("Uniblitz","32idbTXM:uniblitz:control","32idbTXM:uniblitz:control",None)]:
            sf = QtWidgets.QFrame(); sf.setStyleSheet("background:transparent;")
            sl = QtWidgets.QVBoxLayout(sf); sl.setContentsMargins(0,0,0,0); sl.setSpacing(2)
            st = QtWidgets.QLabel("---"); st.setAlignment(QtCore.Qt.AlignCenter); st.setFixedHeight(20)
            st.setStyleSheet("background:#2d2d2d;border:1px solid #404040;border-radius:3px;font:bold 9pt;")
            sl.addWidget(st)
            br = QtWidgets.QHBoxLayout(); br.setSpacing(2)
            bo = QtWidgets.QPushButton("Open"); bo.setStyleSheet("background:#27ae60;color:#fff;font:8pt;padding:2px;")
            bo.clicked.connect(partial(caput_bg, a[1], 1)); br.addWidget(bo)
            bc = QtWidgets.QPushButton("Close"); bc.setStyleSheet("background:#c0392b;color:#fff;font:8pt;padding:2px;")
            bc.clicked.connect(partial(caput_bg, a[2], 1)); br.addWidget(bc)
            sl.addLayout(br); lay.addWidget(sf)
            self.shs.append((a[3], st))
        p.setLayout(lay); p.setGeometry(x, y, 380, 80); x += 384

        # --- Beam Info ---
        p, _ = self._make_panel("Beam", 350, 80, tab_name)
        bl = QtWidgets.QGridLayout(); bl.setContentsMargins(6, 20, 6, 4); bl.setSpacing(3)
        for i, (lbl, pv) in enumerate([("I (mA):","S-DCCT:CurrentM"),("Life:","S-DCCT:LifetimeM"),
                                        ("Mode:","S:ActualMode"),("Und E:","S32ID:USID:EnergyM.VAL")]):
            bl.addWidget(QtWidgets.QLabel(lbl), i//2, (i%2)*2)
            v = _rb(); self._register_rb(pv, v); bl.addWidget(v, i//2, (i%2)*2+1)
        p.setLayout(bl); p.setGeometry(x, y, 350, 80); x += 354

        # --- Presets ---
        p, _ = self._make_panel("Presets", 200, 80, tab_name)
        pl = QtWidgets.QHBoxLayout(); pl.setContentsMargins(6, 20, 6, 4); pl.setSpacing(4)
        bn = QtWidgets.QPushButton("Nano"); bn.setStyleSheet("background:#27ae60;color:#fff;font:bold 11pt;")
        bn.clicked.connect(lambda: caput_bg("32id:TXMOptics:MoveAllIn",1)); pl.addWidget(bn)
        bm = QtWidgets.QPushButton("Micro"); bm.setStyleSheet("background:#27ae60;color:#fff;font:bold 11pt;")
        bm.clicked.connect(lambda: caput_bg("32id:TXMOptics:MoveAllOut",1)); pl.addWidget(bm)
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
        p, _ = self._make_panel("In / Out", 700, 60, tab_name)
        iol = QtWidgets.QHBoxLayout(); iol.setContentsMargins(6, 18, 6, 4); iol.setSpacing(6)
        for lbl, pvp in [("Sample","Sample"),("PhRing","PhaseRing"),("ZP","ZonePlate"),
                         ("Pinhole","Pinhole"),("Cond","Condenser"),("BS","Beamstop"),("Diff","Diffuser")]:
            sub = QtWidgets.QVBoxLayout(); sub.setSpacing(1)
            t = QtWidgets.QLabel(lbl); t.setAlignment(QtCore.Qt.AlignCenter); t.setStyleSheet("font:8pt;"); sub.addWidget(t)
            br2 = QtWidgets.QHBoxLayout(); br2.setSpacing(2)
            bi = QtWidgets.QPushButton("In"); bi.setFixedSize(28,18); bi.setStyleSheet("background:#27ae60;color:#fff;font:7pt;padding:0;")
            bi.clicked.connect(partial(caput_bg,f"32id:TXMOptics:Move{pvp}In",1)); br2.addWidget(bi)
            boo = QtWidgets.QPushButton("Out"); boo.setFixedSize(28,18); boo.setStyleSheet("background:#c0392b;color:#fff;font:7pt;padding:0;")
            boo.clicked.connect(partial(caput_bg,f"32id:TXMOptics:Move{pvp}Out",1)); br2.addWidget(boo)
            sub.addLayout(br2); iol.addLayout(sub)
        iol.addStretch()
        bp = QtWidgets.QPushButton("PyStream"); bp.setFixedSize(90,28)
        bp.setStyleSheet("background:#27ae60;color:#fff;font:bold 10pt;border-radius:3px;")
        bp.clicked.connect(lambda: subprocess.Popen(["/home/beams/USERTXM/scripts/start_pystream.sh"],start_new_session=True))
        iol.addWidget(bp); p.setLayout(iol); p.setGeometry(0, iy, 700, 60)

        # --- Energy ---
        p, _ = self._make_panel("Energy", 340, 280, tab_name)
        el = QtWidgets.QFormLayout(); el.setContentsMargins(6, 22, 6, 6); el.setSpacing(4)
        e_sp = QtWidgets.QLineEdit(); e_sp.setPlaceholderText("keV"); el.addRow("Energy (keV):", e_sp)
        e_rbv = _rb(); self._register_rb("32ida:BraggERdbkAO", e_rbv); el.addRow("Bragg RBV:", e_rbv)
        e_det = QtWidgets.QLineEdit(); el.addRow("Detune (eV):", e_det)
        e_cal = QtWidgets.QComboBox(); e_cal.addItems(["No", "Yes"])
        e_cal.currentIndexChanged.connect(lambda i: caput_bg("32id:TXMOptics:EnergyUseCalibration", i))
        el.addRow("Use Calib:", e_cal)
        e_calfile1 = QtWidgets.QLineEdit(); e_calfile1.setPlaceholderText("calib file 1")
        e_calfile1.returnPressed.connect(lambda: caput_bg("32id:TXMOptics:EnergyCalibrationFileOne", e_calfile1.text()))
        el.addRow("Cal File 1:", e_calfile1)
        e_calfile2 = QtWidgets.QLineEdit(); e_calfile2.setPlaceholderText("calib file 2")
        e_calfile2.returnPressed.connect(lambda: caput_bg("32id:TXMOptics:EnergyCalibrationFileTwo", e_calfile2.text()))
        el.addRow("Cal File 2:", e_calfile2)
        v = _rb(); self._register_rb("S32ID:USID:EnergyM.VAL", v); el.addRow("Und E (keV):", v)
        e_busy = QtWidgets.QLabel("\u25CF"); e_busy.setStyleSheet("color:#555;font:12pt;")
        self._special.setdefault("32id:TXMOptics:EnergyBusy", []).append(e_busy)
        el.addRow("Busy:", e_busy)
        def _set_energy(sp=e_sp, dt=e_det):
            vv = sp.text().strip()
            if not vv: return
            caput_bg("32id:TXMOptics:Energy", vv)
            dd = dt.text().strip()
            if dd: caput_bg("32id:TXMOptics:EnergyDetune", dd)
            caput_bg("32id:TXMOptics:EnergySet", 1)
        el.addRow(_act("Set Energy", _set_energy))
        p.setLayout(el); p.setGeometry(700 + GAP, 84 + GAP, 340, 280)

        # --- Camera ---
        p, _ = self._make_panel("Camera", 340, 250, tab_name)
        cl = QtWidgets.QFormLayout(); cl.setContentsMargins(6, 22, 6, 6); cl.setSpacing(3)
        ar = QtWidgets.QHBoxLayout()
        ar.addWidget(_act("Acquire", lambda: caput_bg("32idbSP1:cam1:Acquire",1)))
        acq = QtWidgets.QLabel("\u25CF"); acq.setStyleSheet("color:#555;font:12pt;")
        self._special.setdefault("32idbSP1:cam1:Acquire", []).append(acq)
        ar.addWidget(acq); ar.addStretch(); cl.addRow(ar)
        er = QtWidgets.QHBoxLayout()
        exp_entry = QtWidgets.QLineEdit(); exp_entry.setPlaceholderText("sec")
        exp_entry.returnPressed.connect(lambda: caput_bg("32idbSP1:cam1:AcquireTime", exp_entry.text()))
        er.addWidget(exp_entry)
        v = _rb(); self._register_rb("32idbSP1:cam1:AcquireTime_RBV", v); er.addWidget(v); cl.addRow("Exp:", er)
        for lbl, pv in [("SzX:","32idbSP1:cam1:SizeX_RBV"),("SzY:","32idbSP1:cam1:SizeY_RBV")]:
            v = _rb(); self._register_rb(pv, v); cl.addRow(lbl, v)
        nr = QtWidgets.QHBoxLayout()
        nf_entry = QtWidgets.QLineEdit(); nf_entry.setPlaceholderText("N")
        nf_entry.returnPressed.connect(lambda: caput_bg("32idbSP1:Proc1:NumFilter", nf_entry.text()))
        nr.addWidget(nf_entry)
        v = _rb(); self._register_rb("32idbSP1:Proc1:NumFiltered_RBV", v); nr.addWidget(v); cl.addRow("Filter:", nr)
        v = _rb(); self._register_rb("32idbSP1:Proc1:ValidFlatField_RBV", v); cl.addRow("Flat Valid:", v)
        pr = QtWidgets.QHBoxLayout()
        pr.addWidget(_act("Reset", lambda: caput_bg("32idbSP1:Proc1:ResetFilter",1)))
        pr.addWidget(_act("Save Flat", lambda: caput_bg("32idbSP1:Proc1:SaveFlatField",1)))
        cl.addRow(pr); p.setLayout(cl); p.setGeometry(700 + GAP + 344, 84 + GAP, 340, 250)

        # --- Crop ---
        p, _ = self._make_panel("Crop", 300, 60, tab_name)
        crl = QtWidgets.QHBoxLayout(); crl.setContentsMargins(6, 18, 6, 4); crl.setSpacing(4)
        crop = {}
        for n in ("L","R","T","B"):
            e = QtWidgets.QLineEdit(); e.setFixedWidth(45); e.setPlaceholderText(n)
            crl.addWidget(e); crop[{"L":"Left","R":"Right","T":"Top","B":"Bottom"}[n]] = e
        def _apply_crop(cr=crop):
            for nn, ee in cr.items():
                tt = ee.text().strip()
                if tt: caput_bg(f"32id:TXMOptics:Crop{nn}", tt)
            caput_bg("32id:TXMOptics:Crop", 1)
        crl.addWidget(_act("Apply", _apply_crop)); p.setLayout(crl)
        p.setGeometry(700 + GAP, iy, 300, 60)

        # --- Valves ---
        p, _ = self._make_panel("Valves", 320, 110, tab_name)
        vl = QtWidgets.QGridLayout(); vl.setContentsMargins(6, 22, 6, 4); vl.setSpacing(3)
        vl.setColumnMinimumWidth(0, 90)   # enough for "Granite X / Y"
        for i,(lbl,on,off,st) in enumerate([
            ("all","32idbSoft:PLC1:oC21","32idbSoft:PLC1:oC31","32idbSoft:PLC1:C1"),
            ("Granite X","32idbSoft:PLC1:oC22","32idbSoft:PLC1:oC32","32idbSoft:PLC1:C2"),
            ("Granite Y","32idbSoft:PLC1:oC23","32idbSoft:PLC1:oC33","32idbSoft:PLC1:C3")]):
            name_lbl = QtWidgets.QLabel(lbl)
            name_lbl.setMinimumWidth(90)
            name_lbl.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Preferred)
            vl.addWidget(name_lbl, i, 0)
            v = _rb(); self._register_rb(st, v); vl.addWidget(v,i,1)
            bo = QtWidgets.QPushButton("On"); bo.setFixedWidth(30); bo.setStyleSheet("background:#27ae60;color:#fff;font:8pt;padding:1px;")
            bo.clicked.connect(partial(caput_bg,on,1)); vl.addWidget(bo,i,2)
            bf = QtWidgets.QPushButton("Off"); bf.setFixedWidth(30); bf.setStyleSheet("background:#c0392b;color:#fff;font:8pt;padding:1px;")
            bf.clicked.connect(partial(caput_bg,off,1)); vl.addWidget(bf,i,3)
        p.setLayout(vl); p.setGeometry(1004 + GAP, iy, 320, 110)

        # --- BPM/EPID ---
        p, _ = self._make_panel("BPM/EPID", 400, 100, tab_name)
        epl = QtWidgets.QGridLayout(); epl.setContentsMargins(6, 22, 6, 4); epl.setSpacing(3)
        epl.addWidget(QtWidgets.QLabel(""), 0, 0); epl.addWidget(QtWidgets.QLabel("Setpoint"), 0, 1)
        epl.addWidget(QtWidgets.QLabel("Current"), 0, 2); epl.addWidget(QtWidgets.QLabel("FB"), 0, 3)
        for i, (ax, pv) in enumerate([("Horiz", "32idbSoft:epidH"), ("Vert", "32idbSoft:epidV")], 1):
            epl.addWidget(QtWidgets.QLabel(f"{ax}:"), i, 0)
            e = QtWidgets.QLineEdit(); e.setFixedWidth(70)
            e.returnPressed.connect(partial(lambda pp, w: caput_bg(f"{pp}.VAL", w.text()), pv, e)); epl.addWidget(e, i, 1)
            v = _rb(); self._register_rb(f"{pv}.CVAL", v); epl.addWidget(v, i, 2)
            fb = QtWidgets.QComboBox(); fb.addItems(["Off", "On"]); fb.setFixedWidth(55)
            fb.currentIndexChanged.connect(partial(lambda pp, idx: caput_bg(f"{pp}:on", idx), pv)); epl.addWidget(fb, i, 3)
        p.setLayout(epl); p.setGeometry(700 + GAP, iy + 64, 400, 100)

        # --- PV Save/Load ---
        p, _ = self._make_panel("PV Save/Load", 220, 50, tab_name)
        pvl = QtWidgets.QHBoxLayout(); pvl.setContentsMargins(6, 18, 6, 4)
        pvl.addWidget(_act("Save", lambda: caput_bg("32id:TXMOptics:SaveAllPVs",1)))
        pvl.addWidget(_act("Load", lambda: caput_bg("32id:TXMOptics:LoadAllPVs",1)))
        p.setLayout(pvl); p.setGeometry(1104 + GAP, iy + 64, 220, 50)

        # --- Beam Status ---
        p, _ = self._make_panel("Beam Status", 400, 100, tab_name)
        bsl = QtWidgets.QFormLayout(); bsl.setContentsMargins(6, 22, 6, 4); bsl.setSpacing(3)
        for lbl, pv in [("Desired Mode:", "S:DesiredMode"), ("Actual Mode:", "S:ActualMode"),
                        ("Inj Period:", "S-INJ:InjectionPeriodCounterM")]:
            v = _rb(); self._register_rb(pv, v); bsl.addRow(lbl, v)
        p.setLayout(bsl); p.setGeometry(700 + GAP, iy + 168, 400, 100)

        # --- OPS Messages ---
        p, _ = self._make_panel("OPS Messages", 400, 90, tab_name)
        opl = QtWidgets.QVBoxLayout(); opl.setContentsMargins(6, 20, 6, 4); opl.setSpacing(1)
        for pv in ["OPS:message1","OPS:message2","OPS:message3","OPS:message4","OPS:message5","OPS:message6"]:
            v = QtWidgets.QLabel(""); v.setStyleSheet("font:8pt;color:#cc0;"); self._register_rb(pv, v); opl.addWidget(v)
        p.setLayout(opl); p.setGeometry(700 + GAP, iy + 272, 400, 90)

        # --- Shaker ---
        p, _ = self._make_panel("Shaker", 300, 200, tab_name)
        skl = QtWidgets.QGridLayout(); skl.setContentsMargins(6, 22, 6, 4); skl.setSpacing(3)
        for i, (lbl, pv) in enumerate([
            ("Freq:", "32idbShaker:shaker:frequency.VAL"), ("Time/Pt:", "32idbShaker:shaker:timePerPoint.VAL"),
            ("Num Pts:", "32idbShaker:shaker:numPoints.VAL"), ("Amp:", "32idbShaker:shaker:ampMult.VAL"),
            ("Offset:", "32idbShaker:shaker:ampOffset.VAL"), ("Phase:", "32idbShaker:shaker:phaseShift.VAL")]):
            skl.addWidget(QtWidgets.QLabel(lbl), i, 0)
            e = QtWidgets.QLineEdit(); e.setFixedWidth(70)
            e.returnPressed.connect(partial(lambda pp, w: caput_bg(pp, w.text()), pv, e)); skl.addWidget(e, i, 1)
        shk_run = QtWidgets.QComboBox(); shk_run.addItems(["Off", "On"])
        shk_run.currentIndexChanged.connect(lambda i: caput_bg("32idbShaker:shaker:run", i))
        skl.addWidget(QtWidgets.QLabel("Run:"), 6, 0); skl.addWidget(shk_run, 6, 1)
        p.setLayout(skl); p.setGeometry(1104 + GAP, iy + 168, 300, 200)

        # --- Launchers ---
        p, _ = self._make_panel("Launchers", 500, 110, tab_name)
        ll2 = QtWidgets.QGridLayout(); ll2.setContentsMargins(6, 22, 6, 4); ll2.setSpacing(3)
        launchers = [
            ("ImageJ","/home/beams/USERTXM/Software/ImageJ/ImageJ.sh"),
            ("Detector","/home/beams/USERTXM/epics/synApps/support/32idbSP1/iocBoot/ioc32idbSP1/softioc/32idbSP1.sh medm"),
            ("Blackfly","/home/beams/USERTXM/epics/synApps/support/32idbSP2/iocBoot/ioc32idbSP2/softioc/32idbSP2.sh medm"),
            ("IOCs","medm -x /home/beams/USERTXM/scripts/iocs_start.adl &"),
            ("32ID Main","/home/beams/USERTXM/start_caQtDM_32id"),
            ("Web IOCs","/home/beams/USERTXM/scripts/ioc_page.sh"),
            ("Web Cams","firefox 10.54.102.97 &"),
            ("Shaker","/net/s32dserv/xorApps/epics/synApps_6_3/ioc/32idbShaker/start_MEDM_32idbShaker")]
        for i, (lbl, cmd) in enumerate(launchers):
            b = QtWidgets.QPushButton(lbl)
            b.setStyleSheet("background:#2d2d2d;color:#e0e0e0;font:9pt;padding:3px 6px;border:1px solid #404040;border-radius:3px;")
            b.clicked.connect(partial(lambda c: subprocess.Popen(c, shell=True, start_new_session=True), cmd))
            ll2.addWidget(b, i // 4, i % 4)
        p.setLayout(ll2); p.setGeometry(0, iy + 64, 500, 110)

        # --- Displays ---
        p, _ = self._make_panel("Displays", 500, 80, tab_name)
        dl = QtWidgets.QGridLayout(); dl.setContentsMargins(6, 22, 6, 4); dl.setSpacing(3)
        displays = [
            ("XANES","medm -x -macro 'P=32id:,R=TXMOptics:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/xanes.adl &"),
            ("Furnace","medm -x /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/Furnace.adl &"),
            ("DCM Motors","medm -x -macro 'P=32ida:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/dcm_motors9.adl &"),
            ("IOC Setup","medm -x -macro 'P=32id:,R=TXMOptics:' /home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/txmOptics_extended.adl &"),
            ("TomoScan","medm -x -macro 'P=32id:,R=TomoScan:,BEAMLINE=tomoScan_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("TomoStep","medm -x -macro 'P=32id:,R=TomoScanStep:,BEAMLINE=tomoScanStep_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("TomoStream","medm -x -macro 'P=32id:,R=TomoScanStream:,BEAMLINE=tomoScanStream_32ID' /home/beams19/USERTXM/epics/synApps/support/tomoscan/tomoScanApp/op/adl/tomoScan_32ID_main.adl &"),
            ("CSS/BPM","/net/s32dserv/xorApps/epics/synApps_6_0/ioc/32idcBPM/iocBoot/iocbpm/32idcBPM.sh css")]
        for i, (lbl, cmd) in enumerate(displays):
            b = QtWidgets.QPushButton(lbl)
            b.setStyleSheet("background:#1e5a8e;color:#fff;font:9pt;padding:3px 6px;border:1px solid #2980b9;border-radius:3px;")
            b.clicked.connect(partial(lambda c: subprocess.Popen(c, shell=True, start_new_session=True), cmd))
            dl.addWidget(b, i // 3, i % 3)
        p.setLayout(dl); p.setGeometry(0, iy + 178, 500, 80)

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
            # Remove shutter labels
            panel_labels = set(id(w) for w in panel.findChildren(QtWidgets.QLabel))
            self.shs = [(pv, lbl) for pv, lbl in self.shs if id(lbl) not in panel_labels]
            # Remove readback labels
            for pv in list(self._rb.keys()):
                self._rb[pv] = [lbl for lbl in self._rb[pv] if id(lbl) not in panel_labels]
                if not self._rb[pv]: del self._rb[pv]
            # Remove special labels
            for pv in list(self._special.keys()):
                self._special[pv] = [lbl for lbl in self._special[pv] if id(lbl) not in panel_labels]
                if not self._special[pv]: del self._special[pv]
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
            self.edit_btn.setVisible(not is_user)
            self.font_slider.setVisible(not is_user)
            self.font_lbl.setVisible(not is_user)
            self._font_label_widget.setVisible(not is_user)

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
        self.add_panel_btn.setVisible(on); self.add_tab_btn.setVisible(on)
        if on:
            self.statusBar().showMessage(
                "EDIT MODE: drag/resize panels, right-click to add buttons, duplicate, delete, or move panels between tabs. "
                "Right-click tab bar to rename/delete tabs.")
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
                "_title_fonts": {}, "_titles": {}, "_mcs": {}}
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
                    {"label": mc._label, "pv": mc.pv, "custom": bool(mc._custom_label)}
                    for mc in panel_mcs
                ]
            for btn in p.findChildren(QtWidgets.QPushButton):
                if isinstance(btn, CfgButton): continue
                bg = btn.property("_custom_bg")
                if bg:
                    btn_id = f"{k}|||{btn.text()}"
                    data["_styles"][btn_id] = {"bg": bg, "fg": btn.property("_custom_fg"),
                        "fs": btn.property("_custom_fs"), "w": btn.width(), "h": btn.height()}
        lay_path = _lay_path()
        try:
            with open(lay_path, "w") as f: json.dump(data, f, indent=2)
            mc_total = sum(len(v) for v in data["_mcs"].values())
            print(f"[SAVE] wrote {lay_path}  panels={len(data['_panels'])}  mcs={mc_total}  "
                  f"titles={len(data['_titles'])}  deleted={len(data['_deleted_panels'])}")
        except Exception as e:
            print(f"[SAVE] FAILED: {e}")
            import traceback
            traceback.print_exc()

    def _load_layout(self):
        lay_path = _lay_path()
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
            # Custom buttons
            buttons = data.get("_buttons", {})
            for panel_key, btn_list in buttons.items():
                p = self._panels.get(panel_key)
                if not p: continue
                for bd in btn_list:
                    btn = CfgButton.from_dict(bd, p)
                    lay = p.layout()
                    if lay: lay.addWidget(btn)
                    else: btn.move(10, 30)
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
                    lay.insertWidget(idx, mc)
                    self.mcs.append(mc)
            mc_total = sum(len(v) for v in mcs_saved.values())
            print(f"[LOAD] read {lay_path}  panels={len(data.get('_panels', {}))}  "
                  f"mcs={mc_total}  titles={len(data.get('_titles', {}))}  "
                  f"deleted={len(data.get('_deleted_panels', []))}")
        except Exception as e:
            print(f"[LOAD] FAILED: {e}")
            import traceback
            traceback.print_exc()

    # ── PV engine ────────────────────────────────────────────────────

    def _start_monitors(self):
        self._pve = PVEngine(self); self._pve.updated.connect(self._on_pv)
        pvs = set()
        for spv, _ in self.shs:
            if spv: pvs.add(spv)
        for m in self.mcs: pvs.update(m.get_pvs())
        for pv in self._rb: pvs.add(pv)
        pvs.add("32id:TXMOptics:EnergyBusy"); pvs.add("32idbSP1:cam1:Acquire")
        print(f"[PV] monitoring {len(pvs)} PVs...")
        self._pve.monitor_many(list(pvs))

    @QtCore.pyqtSlot(str, str)
    def _on_pv(self, pv_name, value):
        # Shutters — fan out to all labels
        for spv, st_lbl in self.shs:
            if spv == pv_name:
                if value in ("1", "1.0"):
                    st_lbl.setText("CLOSED")
                    st_lbl.setStyleSheet("background:#c0392b;border:1px solid #e74c3c;border-radius:3px;font:bold 9pt;color:#fff;")
                else:
                    st_lbl.setText("OPEN")
                    st_lbl.setStyleSheet("background:#27ae60;border:1px solid #2ecc71;border-radius:3px;font:bold 9pt;color:#fff;")
                # Don't return — there may be duplicate labels on other tabs

        # Motors — update all copies
        for m in self.mcs:
            if pv_name.startswith(m.pv + "."):
                field = pv_name[len(m.pv) + 1:]
                m.apply_one(field, value)
            elif pv_name == f"{m.pv}_able":
                # APS convention: 0/"Enable" = enabled, 1/"Disable" = disabled
                enabled = value in ("0", "0.0", "Enable", "Enabled")
                m.set_enabled(enabled)

        # Readback labels — update all copies
        labels = self._rb.get(pv_name)
        if labels:
            for lbl in labels: lbl.setText(value)

        # Special indicators
        specials = self._special.get(pv_name)
        if specials:
            if pv_name == "32id:TXMOptics:EnergyBusy":
                ss = "color:#f39c12;font:12pt;" if value in ("1","1.0") else "color:#2ecc71;font:12pt;"
            elif pv_name == "32idbSP1:cam1:Acquire":
                ss = "color:#2ecc71;font:12pt;" if value in ("1","1.0") else "color:#555;font:12pt;"
            else:
                ss = ""
            for lbl in specials:
                if ss: lbl.setStyleSheet(ss)

    def closeEvent(self, event):
        self._save_layout()
        if hasattr(self, '_pve'): self._pve.stop_all()
        event.accept()


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

    app = QtWidgets.QApplication(sys.argv); app.setApplicationName("Beamline GUI")
    w = Win(allow_edit=allow_edit); w.show()
    app.aboutToQuit.connect(w.close)
    sys.exit(app.exec_())
