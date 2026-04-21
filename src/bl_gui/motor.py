"""Motor card widget (MC), motor groups, and small layout helpers."""
from PyQt5 import QtCore, QtWidgets

from .pv import caput_bg


_font_scale = 1.0


def _fs(base_pt):
    return max(6, int(base_pt * _font_scale + 0.5))


def set_font_scale(pct):
    """Update the global font scale (0-200% typical). Call mc.scale_fonts() after."""
    global _font_scale
    _font_scale = pct / 100.0


def get_font_scale():
    return _font_scale


class MC(QtWidgets.QFrame):
    def __init__(self, label, pv):
        super().__init__()
        self.pv = pv; self._label = label
        self._panel_edit_mode = False
        self._custom_label = False   # set True once user picks a name so .DESC doesn't clobber it
        self._movn = "0"; self._dmov = "0"; self._hls = "0"; self._lls = "0"; self._lvio = "0"
        self.setMinimumWidth(100); self.setMinimumHeight(140)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._ss_idle = "MC{background:#2e2e2e;border:1px solid #484848;border-radius:3px;}"
        self._ss_flash = "MC{background:#f39c12;border:2px solid #f1c40f;border-radius:3px;}"
        self.setStyleSheet(self._ss_idle)
        self._flash_on = False
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.setInterval(400)
        self._flash_timer.timeout.connect(self._toggle_flash)
        L = QtWidgets.QVBoxLayout(self); L.setContentsMargins(3, 3, 3, 2); L.setSpacing(2)
        self.desc = QtWidgets.QLabel(label); self.desc.setAlignment(QtCore.Qt.AlignCenter)
        self.desc.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred); L.addWidget(self.desc)
        self.egu = QtWidgets.QLabel(""); self.egu.setAlignment(QtCore.Qt.AlignCenter)
        self.egu.setStyleSheet("color:#888; font:7pt;"); self.egu.setFixedHeight(12); L.addWidget(self.egu)
        self.rbv = QtWidgets.QLabel("---"); self.rbv.setAlignment(QtCore.Qt.AlignCenter)
        self.rbv.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred); L.addWidget(self.rbv)
        self.val = QtWidgets.QLineEdit(); self.val.setAlignment(QtCore.Qt.AlignCenter)
        self.val.setPlaceholderText("go to")
        self.val.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.val.returnPressed.connect(lambda: caput_bg(f"{self.pv}.VAL", self.val.text())); L.addWidget(self.val)
        tw = QtWidgets.QHBoxLayout(); tw.setSpacing(2); tw.setContentsMargins(0, 0, 0, 0)
        self.btn_twr = QtWidgets.QPushButton("\u25C0")
        self.btn_twr.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        self.btn_twr.clicked.connect(lambda: caput_bg(f"{self.pv}.TWR", 1)); tw.addWidget(self.btn_twr)
        self.twv = QtWidgets.QLineEdit(""); self.twv.setAlignment(QtCore.Qt.AlignCenter)
        self.twv.setPlaceholderText("step")
        self.twv.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.twv.returnPressed.connect(self._on_twv_return); tw.addWidget(self.twv)
        self.btn_twf = QtWidgets.QPushButton("\u25B6")
        self.btn_twf.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        self.btn_twf.clicked.connect(lambda: caput_bg(f"{self.pv}.TWF", 1)); tw.addWidget(self.btn_twf)
        L.addLayout(tw)
        self.stat = QtWidgets.QLabel(""); self.stat.setAlignment(QtCore.Qt.AlignCenter)
        self.stat.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred); L.addWidget(self.stat)
        self.btn_stop = QtWidgets.QPushButton("STOP")
        self.btn_stop.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.btn_stop.clicked.connect(lambda: caput_bg(f"{self.pv}.STOP", 1)); L.addWidget(self.btn_stop)
        # Enable / Disable toggle (uses APS _able PV convention)
        self._enabled = True
        self.btn_able = QtWidgets.QPushButton("Enabled")
        self.btn_able.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.btn_able.clicked.connect(self._toggle_enable)
        L.addWidget(self.btn_able)
        self.lim = QtWidgets.QFrame(); self.lim.setFixedHeight(3); self.lim.setStyleSheet("background:#404040;"); L.addWidget(self.lim)
        self._apply_fonts()

    def _on_twv_return(self):
        # Push the new step to the IOC. Do NOT touch the layout file — any
        # layout save must be triggered explicitly by the user so we don't
        # stomp on hand-edited bl32id.json.
        caput_bg(f"{self.pv}.TWV", self.twv.text())

    def _toggle_enable(self):
        # Flip state locally and write to <pv>_able (APS convention: 0=Enable, 1=Disable)
        new_val = 1 if self._enabled else 0
        caput_bg(f"{self.pv}_able", new_val)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if self._enabled:
            self.btn_able.setText("Enabled")
            self.btn_able.setStyleSheet(
                f"background:#27ae60;color:#fff;font:bold {_fs(8)}pt;padding:1px;"
                "border:1px solid #2ecc71;border-radius:2px;"
            )
        else:
            self.btn_able.setText("Disabled")
            self.btn_able.setStyleSheet(
                f"background:#7f8c8d;color:#fff;font:bold {_fs(8)}pt;padding:1px;"
                "border:1px solid #95a5a6;border-radius:2px;"
            )

    def _apply_fonts(self):
        sd=_fs(9); sr=_fs(12); sv=_fs(10); st=_fs(9); sb=_fs(8); stw=_fs(9)
        seg=_fs(8)
        self.desc.setStyleSheet(f"background:#1e5a8e;color:#fff;font:bold {sd}pt;padding:2px;border-radius:2px;")
        self.rbv.setStyleSheet(f"background:#000000;color:#2ecc71;font:bold {sr}pt 'Liberation Mono','DejaVu Sans Mono',monospace;padding:3px;border:1px solid #333;border-radius:2px;")
        self.val.setStyleSheet(f"font:{sv}pt;"); self.twv.setStyleSheet(f"font:bold {st}pt;")
        # Units label (egu) — now scales with the font slider too.
        self.egu.setStyleSheet(f"color:#888;font:{seg}pt;")
        self.egu.setFixedHeight(max(12, seg + 4))
        self.btn_twr.setStyleSheet(f"font:{stw}pt;padding:0 2px;"); self.btn_twf.setStyleSheet(f"font:{stw}pt;padding:0 2px;")
        self.btn_twr.setFixedWidth(max(18, _fs(18))); self.btn_twf.setFixedWidth(max(18, _fs(18)))
        self.stat.setStyleSheet(f"font:{_fs(8)}pt;")
        self.btn_stop.setStyleSheet(f"background:#c0392b;color:#fff;font:bold {sb}pt;padding:1px;border:1px solid #e74c3c;border-radius:2px;")
        # Refresh the enable/disable button colour + font
        self.set_enabled(self._enabled)

    def scale_fonts(self):
        self._apply_fonts()

    def get_pvs(self):
        base = [f"{self.pv}.{f}" for f in ("RBV","DMOV","MOVN","DESC","EGU","HLS","LLS","LVIO")]
        base.append(f"{self.pv}_able")
        return base

    def apply_one(self, field, value):
        if field == "RBV":
            try: self.rbv.setText(f"{float(value):.4f}")
            except (ValueError, TypeError): self.rbv.setText(str(value))
        elif field == "DESC":
            if not self._custom_label:
                self.desc.setText(value)
                self._label = value
        elif field == "EGU": self.egu.setText(value)
        elif field == "MOVN": self._movn = value; self._update_status()
        elif field == "DMOV": self._dmov = value; self._update_status()
        elif field in ("HLS", "LLS", "LVIO"):
            setattr(self, f"_{field.lower()}", value)
            hit = any(getattr(self, f"_{f.lower()}", "0") in ("1", "1.0") for f in ("HLS", "LLS", "LVIO"))
            self.lim.setStyleSheet("background:#e74c3c;" if hit else "background:#2ecc71;")

    def _update_status(self):
        ss = _fs(8)
        moving = self._movn in ("1", "1.0")
        if moving:
            self.stat.setText("Moving"); self.stat.setStyleSheet(f"font:bold {ss}pt;color:#e74c3c;")
            if not self._flash_timer.isActive():
                self._flash_timer.start()
        elif self._dmov in ("1", "1.0"):
            self.stat.setText("Done"); self.stat.setStyleSheet(f"font:{ss}pt;color:#2ecc71;")
        else:
            self.stat.setText(""); self.stat.setStyleSheet(f"font:{ss}pt;")
        if not moving and self._flash_timer.isActive():
            self._flash_timer.stop()
            self._flash_on = False
            self.setStyleSheet(self._ss_idle)

    def _toggle_flash(self):
        self._flash_on = not self._flash_on
        self.setStyleSheet(self._ss_flash if self._flash_on else self._ss_idle)

    def set_edit_mode(self, on):
        """Called by the owning Panel to enable/disable per-motor editing."""
        self._panel_edit_mode = bool(on)

    def contextMenuEvent(self, e):
        """Right-click menu. 'Motor Details...' is always available (view + edit
        modes); the edit-only entries only show when the panel is in edit mode."""
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        if self.pv:
            act = menu.addAction(f"Copy PV: {self.pv}")
            act.triggered.connect(
                lambda _=False, p=self.pv:
                    QtWidgets.QApplication.clipboard().setText(p))
            menu.addSeparator()
        menu.addAction("Motor Details...", self._open_debug)
        if self._panel_edit_mode:
            from .widgets import _edit_widget, _duplicate_widget, _change_font_size, _delete_widget
            menu.addSeparator()
            menu.addAction("Edit This Motor...", lambda: _edit_widget(self))
            menu.addAction("Duplicate This Motor", lambda: _duplicate_widget(self, None))
            menu.addAction("Font Size...", lambda: _change_font_size(self))
            menu.addSeparator()
            menu.addAction("Delete This Motor...", lambda: _delete_widget(self))
            menu.addSeparator()
            menu.addAction("Add PV Row here...", self._add_pv_row_to_panel)
        menu.exec_(e.globalPos())
        e.accept()

    def _add_pv_row_to_panel(self):
        """Locate the enclosing Panel and trigger the row-builder dialog."""
        w = self.parent()
        while w is not None and not hasattr(w, "key"):
            w = w.parent()
        win = self.window()
        if w is not None and hasattr(win, "add_pv_row_dialog"):
            win.add_pv_row_dialog(w)

    def mouseDoubleClickEvent(self, e):
        """Double-click anywhere on the card opens the full motor details dialog."""
        self._open_debug()
        super().mouseDoubleClickEvent(e)

    def _open_debug(self):
        from .motor_debug import MotorDetailsDialog
        dlg = MotorDetailsDialog(self, parent=self)
        dlg.setModal(False)
        dlg.show()


# ── Motor groups ──────────────────────────────────────────────────────────

GROUPS = [
    ("Condenser",     [("X","32idbTXM:mcs2:c1:m1"),("Y","32idbTXM:mcs2:c1:m2"),("Z","32idbTXM:mcs2:c1:m3"),
                       ("X-L","32idbTXM:mcs2:c1:m4"),("Y-L","32idbTXM:mcs2:c1:m5")]),
    ("Zone Plate",    [("X","32idbTXM:mcs2:c1:m6"),("Y","32idbTXM:mcs2:c1:m7"),("Z","32idbTXM:mcs2:c1:m8")]),
    ("Phase Ring",    [("X","32idbTXM:mcs2:c2:m1"),("Y","32idbTXM:mcs2:c2:m2"),("Z","32idbTXM:mcs2:c2:m3")]),
    ("Detector",      [("X","32idbTXM:mcs2:c1:m9"),("Z","32idbTXM:mcs2:c1:m10")]),
    ("Sample",        [("X","32idbTXM:mcs2:c1:m11"),("Z","32idbTXM:mcs2:c1:m12")]),
    ("Pinhole",       [("X","32idbTXM:mcs2:c1:m13"),("Y","32idbTXM:mcs2:c1:m14")]),
    ("Beamstop",      [("X","32idbTXM:mcs2:c1:m15"),("Y","32idbTXM:mcs2:c3:m1")]),
    ("Diffuser",      [("X","32idbTXM:mcs2:c3:m2")]),
    ("Bertrand Lens", [("X","32idbTXM:mcs:c2:m1"),("Y","32idbTXM:mcs:c2:m2")]),
    ("Furnace",       [("X","32idbSoft:m3"),("Y","32idbSoft:m4"),("Z","32idbSoft:m5")]),
    ("Nano Focus",    [("Y","32idbTXM:nf:m2"),("Z","32idbTXM:nf:m4")]),
    ("Queensgate",    [("V","32idQG:m1"),("H","32idQG:m2")]),
    ("Ensemble",      [("E","32idbTXM:ens:c1:m1")]),
    ("Other",         [("Ring","32idbSoft:m1"),("m6","32idbSoft:m6")]),
]


_DEFAULT_TABS = ["User Mode", "Expert Mode"]


def _rb():
    lbl = QtWidgets.QLabel("---")
    lbl.setStyleSheet("color:#2ecc71;font:bold 10pt 'Liberation Mono','DejaVu Sans Mono',monospace;")
    return lbl


def _act(text, cb):
    b = QtWidgets.QPushButton(text)
    b.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;border:1px solid #2980b9;padding:4px 10px;")
    b.clicked.connect(cb)
    return b
