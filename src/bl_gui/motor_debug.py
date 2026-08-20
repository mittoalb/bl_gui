"""Motor debug dialog: exposes the full EPICS motor record for one MC,
mimicking the motorx_all.adl screen from synApps/motor.

Opened from the MC context menu ("Motor Details...") and from double-click
on the motor card description label. Live RBVs update via the window's
PVEngine; edits do caput directly to the corresponding field.
"""
from PyQt5 import QtCore, QtWidgets
from .pv import caput_bg


# (pv-suffix, label, kind)  where kind in {"rw", "ro", "btn:N", "cmb:choices"}
_SECTIONS = [
    ("Identity", [
        ("DESC", "Description",       "rw"),
        ("EGU",  "Engineering units", "rw"),
        ("PREC", "Precision",         "rw"),
    ]),
    ("Position", [
        ("RBV",  "Readback (user)",   "ro"),
        ("VAL",  "Setpoint (user)",   "rw"),
        ("DVAL", "Setpoint (dial)",   "rw"),
        ("RVAL", "Raw value",         "ro"),
        ("OFF",  "User offset",       "rw"),
        ("DIR",  "Direction",         "cmb:Pos,Neg"),
    ]),
    ("Motion", [
        ("VELO", "Velocity",          "rw"),
        ("VBAS", "Base velocity",     "rw"),
        ("ACCL", "Accel time (s)",    "rw"),
        ("BDST", "Backlash dist",     "rw"),
        ("BVEL", "Backlash vel",      "rw"),
        ("BACC", "Backlash accel",    "rw"),
    ]),
    ("Soft limits", [
        ("HLM",  "High (user)",       "rw"),
        ("LLM",  "Low (user)",        "rw"),
        ("DHLM", "High (dial)",       "rw"),
        ("DLLM", "Low (dial)",        "rw"),
    ]),
    ("Scaling", [
        ("MRES", "Motor resolution",  "rw"),
        ("ERES", "Encoder resolution","rw"),
        ("UEIP", "Use encoder (1/0)", "rw"),
    ]),
    ("Status", [
        ("MOVN", "Moving",            "ro"),
        ("DMOV", "Done moving",       "ro"),
        ("LVIO", "Soft-limit violation", "ro"),
        ("HLS",  "Hardware high limit",  "ro"),
        ("LLS",  "Hardware low limit",   "ro"),
        ("MSTA", "Status (raw mask)",    "ro"),
    ]),
    ("Control", [
        ("SET",  "Set/Use mode",         "cmb:Use,Set"),
        # CNEN — standard motor record torque/coil enable. Stays "---"
        # on drivers that don't implement it (e.g. some Aerotech
        # rotaries), which is why the second row exists.
        ("CNEN", "Torque enable (CNEN)", "cmb:Disable,Enable"),
        # <pv>_able — APS convention used by many drivers (Delta Tau,
        # Aerotech Ensemble, IMS via wrapper). Note the inverted
        # semantics: 0=enable, 1=disable. Combo order matches the
        # index → written value mapping.
        ("_able", "APS torque enable",   "cmb:Enable,Disable"),
        ("DISP", "Disable puts",         "rw"),
        ("STOP", "STOP",                 "btn:1"),
        ("HOMF", "Home forward",         "btn:1"),
        ("HOMR", "Home reverse",         "btn:1"),
    ]),
]


def _pv_for(base, sfx):
    """Compose a full PV name for a sfx entry from _SECTIONS.

    - "CNEN"   → "<base>.CNEN"   (standard motor-record field)
    - "_able"  → "<base>_able"   (APS-convention external PV)
    - ":x"     → "<base>:x"      (colon-separated sibling PV)
    """
    if sfx.startswith(("_", ":")):
        return f"{base}{sfx}"
    return f"{base}.{sfx}"


def _sfx_from_pv(base, pv_name):
    """Inverse of _pv_for — recover the sfx key so _on_pv can look up
    the widget that owns this PV in self._fields."""
    if not pv_name.startswith(base):
        return None
    tail = pv_name[len(base):]
    if tail.startswith("."):
        return tail[1:]
    if tail.startswith(("_", ":")):
        return tail
    return None


class MotorDetailsDialog(QtWidgets.QDialog):
    def __init__(self, mc, parent=None):
        super().__init__(parent)
        self.mc = mc
        self.pv = mc.pv
        self.setWindowTitle(f"Motor details — {self.pv}  ({mc._label})")
        self.setMinimumSize(680, 760)
        self.setStyleSheet(
            "QDialog{background:#000;color:#e0e0e0;}"
            "QGroupBox{background:#0e0e0e;color:#73dfff;font:bold 10pt;"
            "border:1px solid #383838;border-radius:3px;margin-top:10px;padding:12px 6px 6px 6px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}"
            "QLabel{color:#e0e0e0;background:transparent;}"
            "QLineEdit,QComboBox{background:#2d2d2d;color:#e0e0e0;padding:2px 5px;"
            "border:1px solid #404040;border-radius:3px;font:9pt 'Liberation Mono','DejaVu Sans Mono',monospace;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:4px 10px;"
            "border:1px solid #404040;border-radius:3px;}"
            "QPushButton:hover{background:#3a3a3a;}"
            "QScrollArea{border:none;background:#000;}"
        )

        # Build UI
        root = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QLabel(f"<b>{self.pv}</b>  — live motor record fields")
        header.setStyleSheet("font:bold 11pt;color:#73dfff;")
        root.addWidget(header)

        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget(); vbox = QtWidgets.QVBoxLayout(inner)
        vbox.setSpacing(6); vbox.setContentsMargins(4, 4, 4, 4)

        self._fields = {}   # suffix -> QLineEdit|QComboBox|QLabel
        self._pvs = []      # full PVs to subscribe

        for section_name, rows in _SECTIONS:
            gb = QtWidgets.QGroupBox(section_name)
            gl = QtWidgets.QGridLayout(gb)
            gl.setHorizontalSpacing(6); gl.setVerticalSpacing(3)
            gl.setContentsMargins(8, 14, 8, 6)
            gl.setColumnStretch(1, 1)
            for row, (sfx, lbl, kind) in enumerate(rows):
                gl.addWidget(QtWidgets.QLabel(lbl + ":"), row, 0)
                w = self._make_field(sfx, kind)
                gl.addWidget(w, row, 1)
                # Suffix column shows the actual PV tail (e.g. ".CNEN"
                # for motor-record fields, "_able" for external PVs).
                display = sfx if sfx.startswith(("_", ":")) else f".{sfx}"
                gl.addWidget(QtWidgets.QLabel(display), row, 2)
                self._pvs.append(_pv_for(self.pv, sfx))
            vbox.addWidget(gb)

        vbox.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # Close button
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        close = QtWidgets.QPushButton("Close"); close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        root.addLayout(btn_row)

        # Hook into the main PVEngine
        win = parent.window() if parent else self.window()
        self._win = win
        self._pve = getattr(win, "_pve", None)
        if self._pve is not None:
            self._pve.monitor_many(self._pvs)
            self._pve.updated.connect(self._on_pv)
            # PVEngine.monitor() is a no-op if the PV is already being
            # watched (which most motor fields are, from the main
            # window's MC subscription). In that case no fresh update
            # fires, so the dialog would sit empty until a value
            # happened to change. Pull each PV's current value once
            # here so every field is populated immediately.
            for pv in self._pvs:
                v = self._pve.get(pv)
                if v is not None:
                    self._on_pv(pv, v)

    def _make_field(self, sfx, kind):
        if kind == "ro":
            w = QtWidgets.QLabel("---")
            w.setStyleSheet("color:#2ecc71;font:bold 10pt 'Liberation Mono','DejaVu Sans Mono',monospace;padding:2px 4px;"
                            "background:#000;border:1px solid #333;border-radius:2px;")
            self._fields[sfx] = w
            return w
        if kind.startswith("btn:"):
            val = kind.split(":", 1)[1]
            b = QtWidgets.QPushButton(sfx)
            b.setStyleSheet("background:#c0392b;color:#fff;font:bold 9pt;padding:4px;")
            b.clicked.connect(
                lambda _=False, s=sfx, v=val:
                    caput_bg(_pv_for(self.pv, s), v))
            self._fields[sfx] = b
            return b
        if kind.startswith("cmb:"):
            choices = kind.split(":", 1)[1].split(",")
            c = QtWidgets.QComboBox(); c.addItems(choices)
            c.currentIndexChanged.connect(
                lambda idx, s=sfx: caput_bg(_pv_for(self.pv, s), idx)
            )
            self._fields[sfx] = c
            return c
        # rw
        le = QtWidgets.QLineEdit()
        le.returnPressed.connect(
            lambda s=sfx, e=le: caput_bg(_pv_for(self.pv, s), e.text())
        )
        self._fields[sfx] = le
        return le

    @QtCore.pyqtSlot(str, str)
    def _on_pv(self, pv_name, value):
        sfx = _sfx_from_pv(self.pv, pv_name)
        if sfx is None:
            return
        w = self._fields.get(sfx)
        if w is None:
            return
        if isinstance(w, QtWidgets.QLabel):
            w.setText(str(value))
        elif isinstance(w, QtWidgets.QComboBox):
            # Accept either index or choice string
            idx = w.findText(value)
            if idx >= 0:
                w.blockSignals(True); w.setCurrentIndex(idx); w.blockSignals(False)
            else:
                try:
                    i = int(float(value))
                    if 0 <= i < w.count():
                        w.blockSignals(True); w.setCurrentIndex(i); w.blockSignals(False)
                except (ValueError, TypeError):
                    pass
        elif isinstance(w, QtWidgets.QLineEdit):
            # Only replace the field text if the user is not editing
            if not w.hasFocus():
                w.blockSignals(True); w.setText(str(value)); w.blockSignals(False)

    def closeEvent(self, e):
        if self._pve is not None:
            try:
                self._pve.updated.disconnect(self._on_pv)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(e)
