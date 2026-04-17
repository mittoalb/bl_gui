"""Small PV-bound widget used in non-motor panels (Energy, BPM/EPID, etc.).

Supports four kinds:
  'sp'  setpoint  — QLineEdit,  caput on return
  'rb'  readback  — QLabel,     subscribed to PV
  'cmb' combo     — QComboBox,  caput index on change
  'btn' action    — QPushButton, caput fixed value on click

A related 'ValveField' class handles 3-PV rows (status + On action + Off action)
as used by the Valves panel; it also supports right-click PV editing for all
three PVs and participates in the same save/load machinery.

Right-click (in panel edit mode) exposes 'Edit PV...' so each field's PV
can be reassigned at runtime and saved with the layout.
"""
from PyQt5 import QtCore, QtWidgets
from .pv import caput_bg


class PVField(QtWidgets.QWidget):
    def __init__(self, kind, pv, field_id,
                 choices=None, button_text=None, button_value=None,
                 placeholder=None, fmt=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.pv = (pv or "").strip()
        self.field_id = field_id            # stable key for save/load
        self._choices = list(choices or [])
        self._button_value = button_value
        self._button_text = button_text or "Set"
        self._placeholder = placeholder
        self._fmt = fmt                     # e.g. ".3f" for 3-decimal readbacks
        self._edit_mode = False
        L = QtWidgets.QHBoxLayout(self)
        L.setContentsMargins(0, 0, 0, 0); L.setSpacing(0)
        self._inner = None
        self._build_inner()

    # ── Inner widget construction ────────────────────────────────────
    def _build_inner(self):
        # Clear any previous widget
        while self.layout().count():
            item = self.layout().takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()

        if self.kind == 'sp':
            w = QtWidgets.QLineEdit()
            if self._placeholder:
                w.setPlaceholderText(self._placeholder)
            w.returnPressed.connect(self._on_sp_return)
        elif self.kind == 'rb':
            w = QtWidgets.QLabel("---")
            w.setStyleSheet("color:#2ecc71;font:bold 10pt monospace;")
        elif self.kind == 'cmb':
            w = QtWidgets.QComboBox()
            if self._choices:
                w.addItems(self._choices)
            else:
                # No preset choices — auto-populate from PV updates.
                w.setEditable(True)
                w.lineEdit().returnPressed.connect(
                    lambda ww=w: caput_bg(self.pv, ww.currentText()) if self.pv else None
                )
            w.currentIndexChanged.connect(self._on_cmb_changed)
        elif self.kind == 'btn':
            w = QtWidgets.QPushButton(self._button_text)
            # Green for "go" actions (value != 0), red for "stop"-ish actions.
            if str(self._button_value) in ("0", "0.0"):
                w.setStyleSheet("background:#c0392b;color:#fff;font:bold 9pt;"
                                "border:1px solid #e74c3c;padding:4px 10px;border-radius:3px;")
            else:
                w.setStyleSheet("background:#27ae60;color:#fff;font:bold 9pt;"
                                "border:1px solid #2ecc71;padding:4px 10px;border-radius:3px;")
            w.clicked.connect(self._on_btn_clicked)
        else:
            w = QtWidgets.QLabel(f"?? {self.kind}")
        self._inner = w
        self.layout().addWidget(w)

    # ── Signals to PV ────────────────────────────────────────────────
    def _on_sp_return(self):
        if self.pv:
            caput_bg(self.pv, self._inner.text())

    def _on_cmb_changed(self, idx):
        if self.pv and idx >= 0:
            caput_bg(self.pv, idx)

    def _on_btn_clicked(self):
        if self.pv and self._button_value is not None:
            caput_bg(self.pv, self._button_value)

    # ── Incoming PV updates (called by the main window) ──────────────
    def update_value(self, value):
        if self._inner is None:
            return
        if self.kind == 'rb':
            text = str(value)
            if self._fmt:
                try:
                    text = format(float(value), self._fmt)
                except (ValueError, TypeError):
                    pass
            self._inner.setText(text)
        elif self.kind == 'cmb':
            # Block signals for the ENTIRE update so addItem/setCurrentIndex
            # don't fire currentIndexChanged back through _on_cmb_changed
            # (which would caput and create a PV echo loop).
            self._inner.blockSignals(True)
            try:
                idx = self._inner.findText(value)
                if idx < 0:
                    # A string we haven't seen — remember it so it's selectable.
                    try:
                        float(value)   # numeric fallback handled below
                        numeric = True
                    except (ValueError, TypeError):
                        numeric = False
                    if not numeric:
                        self._inner.addItem(str(value))
                        idx = self._inner.findText(value)
                if idx < 0:
                    try:
                        idx = int(float(value))
                    except (ValueError, TypeError):
                        idx = -1
                if 0 <= idx < self._inner.count():
                    self._inner.setCurrentIndex(idx)
                if self._inner.isEditable() and self._inner.lineEdit() is not None:
                    if not self._inner.lineEdit().hasFocus():
                        self._inner.lineEdit().setText(str(value))
            finally:
                self._inner.blockSignals(False)
        elif self.kind == 'sp':
            if not self._inner.hasFocus():
                self._inner.blockSignals(True)
                self._inner.setText(str(value))
                self._inner.blockSignals(False)

    def monitored_pvs(self):
        """PVs the window should subscribe to for this field."""
        return [self.pv] if self.pv and self.kind != 'btn' else []

    # ── Edit-mode: right-click to change the PV assignment ───────────
    def set_edit_mode(self, on):
        self._edit_mode = bool(on)

    def contextMenuEvent(self, e):
        if not self._edit_mode:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Edit PV...", self._edit_pv_dialog)
        menu.exec_(e.globalPos())
        e.accept()

    def _edit_pv_dialog(self):
        new_pv, ok = QtWidgets.QInputDialog.getText(
            self, f"Edit PV — {self.field_id}",
            f"PV name for '{self.field_id}':\n(leave empty to unbind)",
            text=self.pv,
        )
        if not ok:
            return
        old_pv = self.pv
        self.pv = new_pv.strip()
        # Reset the display so stale values don't linger
        if self.kind == 'rb':
            self._inner.setText("---")
        elif self.kind == 'sp':
            self._inner.clear()
        # Ask the window to rebind
        win = self.window()
        if hasattr(win, '_pv_field_rebind'):
            win._pv_field_rebind(self, old_pv, self.pv)


# ── Valve field: status + On action + Off action ──────────────────────

class ValveField(QtWidgets.QWidget):
    """A single row for a binary-valve control:
         [status label: ON/OFF, colour-coded]  [On btn]  [Off btn]
       The status PV is monitored and formatted as ON/OFF (accepts numeric
       non-zero, or strings like 'On'/'Open'/'True'/'High'/'Yes' etc.).
       On/Off buttons caput 1 to their respective action PVs.
       Right-click (in edit mode) exposes 'Edit PVs...' to change all three
       PVs at once. Persisted via the same `_pv_fields` save/load path.
    """

    def __init__(self, status_pv, on_pv, off_pv, field_id, parent=None):
        super().__init__(parent)
        self.field_id = field_id
        self.status_pv = (status_pv or "").strip()
        self.on_pv = (on_pv or "").strip()
        self.off_pv = (off_pv or "").strip()
        self._edit_mode = False

        L = QtWidgets.QHBoxLayout(self)
        L.setContentsMargins(0, 0, 0, 0); L.setSpacing(4)

        self.status_lbl = QtWidgets.QLabel("---")
        self.status_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.status_lbl.setStyleSheet(
            "background:#404040;color:#e0e0e0;font:bold 9pt;"
            "border:1px solid #606060;border-radius:2px;padding:2px 6px;"
        )
        self.status_lbl.setMinimumWidth(50)
        L.addWidget(self.status_lbl, 1)

        self.btn_on = QtWidgets.QPushButton("On"); self.btn_on.setFixedWidth(34)
        self.btn_on.setStyleSheet("background:#27ae60;color:#fff;font:8pt;padding:1px;")
        self.btn_on.clicked.connect(lambda: self._fire(self.on_pv))
        L.addWidget(self.btn_on)

        self.btn_off = QtWidgets.QPushButton("Off"); self.btn_off.setFixedWidth(34)
        self.btn_off.setStyleSheet("background:#c0392b;color:#fff;font:8pt;padding:1px;")
        self.btn_off.clicked.connect(lambda: self._fire(self.off_pv))
        L.addWidget(self.btn_off)

    def _fire(self, pv):
        if pv:
            caput_bg(pv, 1)

    # ── PV interface used by the window ──────────────────────────────
    def monitored_pvs(self):
        return [self.status_pv] if self.status_pv else []

    def update_value(self, value):
        """Called by the window when the status PV fires."""
        v = str(value).strip()
        lv = v.lower()
        on = False
        try:
            on = float(v) != 0.0
        except (ValueError, TypeError):
            on = lv in ("on", "open", "true", "high", "yes", "1")
        if on:
            self.status_lbl.setText("ON")
            self.status_lbl.setStyleSheet(
                "background:#27ae60;color:#fff;font:bold 10pt;"
                "border:1px solid #2ecc71;border-radius:2px;padding:2px 6px;"
            )
        else:
            self.status_lbl.setText("OFF")
            self.status_lbl.setStyleSheet(
                "background:#c0392b;color:#fff;font:bold 10pt;"
                "border:1px solid #e74c3c;border-radius:2px;padding:2px 6px;"
            )

    # ── Save / load support ──────────────────────────────────────────
    @property
    def pv(self):
        """Fallback used only for log messages; not for routing."""
        return self.status_pv

    def get_pvs_dict(self):
        return {"kind": "valve", "status": self.status_pv,
                "on": self.on_pv, "off": self.off_pv}

    def set_pvs_dict(self, d):
        self.status_pv = (d.get("status") or "").strip()
        self.on_pv = (d.get("on") or "").strip()
        self.off_pv = (d.get("off") or "").strip()

    # ── Edit mode (right-click to change all 3 PVs) ──────────────────
    def set_edit_mode(self, on):
        self._edit_mode = bool(on)

    def contextMenuEvent(self, e):
        if not self._edit_mode:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Edit Valve PVs...", self._edit_pvs_dialog)
        menu.exec_(e.globalPos())
        e.accept()

    def _edit_pvs_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Edit Valve PVs — {self.field_id}")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(
            "QDialog{background:#000;color:#e0e0e0;}QLabel{color:#e0e0e0;}"
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;padding:4px;"
            "border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
            "border:1px solid #404040;border-radius:3px;}"
        )
        form = QtWidgets.QFormLayout(dlg); form.setSpacing(6)
        e_st = QtWidgets.QLineEdit(self.status_pv); form.addRow("Status PV:", e_st)
        e_on = QtWidgets.QLineEdit(self.on_pv);     form.addRow("On action PV:", e_on)
        e_of = QtWidgets.QLineEdit(self.off_pv);    form.addRow("Off action PV:", e_of)
        btns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("OK"); ok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        ok.clicked.connect(dlg.accept); btns.addWidget(ok)
        cancel = QtWidgets.QPushButton("Cancel"); cancel.clicked.connect(dlg.reject); btns.addWidget(cancel)
        form.addRow(btns)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        old_status = self.status_pv
        self.status_pv = e_st.text().strip()
        self.on_pv = e_on.text().strip()
        self.off_pv = e_of.text().strip()
        self.status_lbl.setText("---")
        win = self.window()
        if hasattr(win, '_pv_field_rebind'):
            win._pv_field_rebind(self, old_status, self.status_pv)
