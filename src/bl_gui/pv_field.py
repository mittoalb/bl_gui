"""Small PV-bound widget used in non-motor panels (Energy, BPM/EPID, etc.).

Supports four kinds:
  'sp'  setpoint  — QLineEdit,  caput on return
  'rb'  readback  — QLabel,     subscribed to PV
  'cmb' combo     — QComboBox,  caput index on change
  'btn' action    — QPushButton, caput fixed value on click

Right-click (in panel edit mode) exposes 'Edit PV...' so each field's PV
can be reassigned at runtime and saved with the layout.
"""
from PyQt5 import QtCore, QtWidgets
from .pv import caput_bg


class PVField(QtWidgets.QWidget):
    def __init__(self, kind, pv, field_id,
                 choices=None, button_text=None, button_value=None,
                 placeholder=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.pv = (pv or "").strip()
        self.field_id = field_id            # stable key for save/load
        self._choices = list(choices or [])
        self._button_value = button_value
        self._button_text = button_text or "Set"
        self._placeholder = placeholder
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
            w.addItems(self._choices)
            w.currentIndexChanged.connect(self._on_cmb_changed)
        elif self.kind == 'btn':
            w = QtWidgets.QPushButton(self._button_text)
            w.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;"
                            "border:1px solid #2980b9;padding:4px 10px;border-radius:3px;")
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
            self._inner.setText(str(value))
        elif self.kind == 'cmb':
            idx = self._inner.findText(value)
            if idx < 0:
                try:
                    idx = int(float(value))
                except (ValueError, TypeError):
                    idx = -1
            if 0 <= idx < self._inner.count():
                self._inner.blockSignals(True)
                self._inner.setCurrentIndex(idx)
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
