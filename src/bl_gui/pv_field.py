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
                 placeholder=None, fmt=None,
                 on_pv=None, off_pv=None, on_value=1, off_value=1,
                 parent=None):
        super().__init__(parent)
        self.kind = kind
        self.pv = (pv or "").strip()
        self.field_id = field_id            # stable key for save/load
        self._choices = list(choices or [])
        self._button_value = button_value
        self._button_text = button_text or "Set"
        self._placeholder = placeholder
        self._fmt = fmt
        # For kind 'btn_pair' (two buttons, typically In/Out or Open/Close)
        self.on_pv = (on_pv or "").strip() if on_pv else ""
        self.off_pv = (off_pv or "").strip() if off_pv else ""
        self._on_value = on_value
        self._off_value = off_value
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
        elif self.kind == 'btn_pair':
            # Two buttons (e.g. In/Out, Open/Close). Each writes its own PV+value.
            w = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(w); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(2)
            b_on = QtWidgets.QPushButton(self._button_text.split("/")[0] if "/" in self._button_text else "On")
            b_on.setStyleSheet("background:#27ae60;color:#fff;font:bold 8pt;"
                               "border:1px solid #2ecc71;padding:2px;border-radius:2px;")
            b_on.clicked.connect(lambda: caput_bg(self.on_pv, self._on_value) if self.on_pv else None)
            b_off = QtWidgets.QPushButton(self._button_text.split("/")[1] if "/" in self._button_text else "Off")
            b_off.setStyleSheet("background:#c0392b;color:#fff;font:bold 8pt;"
                                "border:1px solid #e74c3c;padding:2px;border-radius:2px;")
            b_off.clicked.connect(lambda: caput_bg(self.off_pv, self._off_value) if self.off_pv else None)
            hl.addWidget(b_on); hl.addWidget(b_off)
        elif self.kind == 'led':
            # Round LED-style indicator; lights up when subscribed PV is non-zero.
            w = QtWidgets.QLabel("●")
            w.setAlignment(QtCore.Qt.AlignCenter)
            w.setStyleSheet("color:#555;font:bold 14pt;background:transparent;")
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
        elif self.kind == 'led':
            v = str(value).strip().lower()
            on = False
            try:
                on = float(v) != 0.0
            except (ValueError, TypeError):
                on = v in ("on", "open", "true", "high", "yes", "1")
            colour = "#2ecc71" if on else "#555"
            self._inner.setStyleSheet(f"color:{colour};font:bold 14pt;background:transparent;")

    def monitored_pvs(self):
        """PVs the window should subscribe to for this field."""
        if self.kind in ('btn', 'btn_pair'):
            return []
        return [self.pv] if self.pv else []

    # ── Multi-PV save support for kinds that carry > 1 PV ────────────
    def get_pvs_dict(self):
        """Only used by kinds that carry more than one PV (btn_pair).
        Plain kinds (sp/rb/cmb/btn/led) return None → save layer falls
        back to the single-PV string form."""
        if self.kind == 'btn_pair':
            return {"kind": "btn_pair", "on": self.on_pv, "off": self.off_pv}
        return None

    def set_pvs_dict(self, d):
        if self.kind == 'btn_pair' and isinstance(d, dict):
            self.on_pv = (d.get("on") or "").strip()
            self.off_pv = (d.get("off") or "").strip()

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
        menu.addAction("Delete Row", self._delete_row)
        menu.addSeparator()
        menu.addAction("Add PV Row here...", self._add_row_here)
        menu.exec_(e.globalPos())
        e.accept()

    def _add_row_here(self):
        """Open the row-builder dialog on the enclosing Panel."""
        w = self.parent()
        while w is not None and not hasattr(w, "key"):
            w = w.parent()
        win = self.window()
        if w is not None and hasattr(win, "add_pv_row_dialog"):
            win.add_pv_row_dialog(w)

    def _delete_row(self):
        """Ask the window to delete this row from its panel + persistence."""
        win = self.window()
        if hasattr(win, "_delete_custom_row"):
            win._delete_custom_row(self)

    def _edit_pv_dialog(self):
        if self.kind == 'btn_pair':
            # Two action PVs to edit (on_pv, off_pv).
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle(f"Edit PVs — {self.field_id}")
            dlg.setMinimumWidth(420)
            dlg.setStyleSheet(
                "QDialog{background:#000;color:#e0e0e0;}QLabel{color:#e0e0e0;}"
                "QLineEdit{background:#2d2d2d;color:#e0e0e0;padding:4px;"
                "border:1px solid #404040;border-radius:3px;}"
                "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
                "border:1px solid #404040;border-radius:3px;}"
            )
            form = QtWidgets.QFormLayout(dlg); form.setSpacing(6)
            e_on = QtWidgets.QLineEdit(self.on_pv);   form.addRow("On action PV:",  e_on)
            e_of = QtWidgets.QLineEdit(self.off_pv);  form.addRow("Off action PV:", e_of)
            btns = QtWidgets.QHBoxLayout()
            ok_b = QtWidgets.QPushButton("OK"); ok_b.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
            ok_b.clicked.connect(dlg.accept); btns.addWidget(ok_b)
            cb = QtWidgets.QPushButton("Cancel"); cb.clicked.connect(dlg.reject); btns.addWidget(cb)
            form.addRow(btns)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return
            self.on_pv = e_on.text().strip()
            self.off_pv = e_of.text().strip()
            return
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

    def __init__(self, status_pv, on_pv, off_pv, field_id,
                 label_text="", on_text="On", off_text="Off", parent=None):
        super().__init__(parent)
        self.field_id = field_id
        self.status_pv = (status_pv or "").strip()
        self.on_pv = (on_pv or "").strip()
        self.off_pv = (off_pv or "").strip()
        self.label_text = label_text or ""
        self._edit_mode = False

        L = QtWidgets.QHBoxLayout(self)
        L.setContentsMargins(0, 0, 0, 0); L.setSpacing(4)

        # Own label on the left — editable through the 'Edit Valve PVs...' dialog
        self.name_lbl = QtWidgets.QLabel(self.label_text)
        self.name_lbl.setMinimumWidth(70)
        self.name_lbl.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding,
                                    QtWidgets.QSizePolicy.Preferred)
        L.addWidget(self.name_lbl)

        self.status_lbl = QtWidgets.QLabel("---")
        self.status_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.status_lbl.setStyleSheet(
            "background:#404040;color:#e0e0e0;font:bold 9pt;"
            "border:1px solid #606060;border-radius:2px;padding:2px 6px;"
        )
        self.status_lbl.setMinimumWidth(50)
        L.addWidget(self.status_lbl, 1)

        self.btn_on = QtWidgets.QPushButton(on_text); self.btn_on.setFixedWidth(38)
        self.btn_on.setStyleSheet("background:#27ae60;color:#fff;font:8pt;padding:1px;")
        self.btn_on.clicked.connect(lambda: self._fire(self.on_pv))
        L.addWidget(self.btn_on)

        self.btn_off = QtWidgets.QPushButton(off_text); self.btn_off.setFixedWidth(38)
        self.btn_off.setStyleSheet("background:#c0392b;color:#fff;font:8pt;padding:1px;")
        self.btn_off.clicked.connect(lambda: self._fire(self.off_pv))
        L.addWidget(self.btn_off)

    def _fire(self, pv):
        if pv:
            print(f"[VALVE] {self.field_id}: fire -> {pv}")
            caput_bg(pv, 1)
            # Immediate tentative feedback so the user sees the click landed
            # even if the PLC status PV takes a while (or never) to confirm.
            if pv == self.on_pv:
                self._show_pending(True)
            elif pv == self.off_pv:
                self._show_pending(False)

    def _show_pending(self, going_on):
        self.status_lbl.setText("ON?" if going_on else "OFF?")
        self.status_lbl.setStyleSheet(
            "background:#2980b9;color:#fff;font:bold 10pt;"
            "border:1px solid #3a95d8;border-radius:2px;padding:2px 6px;"
        )

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
        print(f"[VALVE] {self.field_id}: status={value!r} -> {'ON' if on else 'OFF'}")
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
        return {
            "kind": "valve",
            "status": self.status_pv, "on": self.on_pv, "off": self.off_pv,
            "label": self.label_text,
            "on_text": self.btn_on.text(), "off_text": self.btn_off.text(),
        }

    def set_pvs_dict(self, d):
        self.status_pv = (d.get("status") or "").strip()
        self.on_pv = (d.get("on") or "").strip()
        self.off_pv = (d.get("off") or "").strip()
        if "label" in d:
            self.label_text = d["label"] or ""
            self.name_lbl.setText(self.label_text)
        if "on_text" in d and d["on_text"]:
            self.btn_on.setText(d["on_text"])
        if "off_text" in d and d["off_text"]:
            self.btn_off.setText(d["off_text"])

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
        menu.addAction("Delete Row", self._delete_row)
        menu.addSeparator()
        menu.addAction("Add PV Row here...", self._add_row_here)
        menu.exec_(e.globalPos())
        e.accept()

    def _add_row_here(self):
        w = self.parent()
        while w is not None and not hasattr(w, "key"):
            w = w.parent()
        win = self.window()
        if w is not None and hasattr(win, "add_pv_row_dialog"):
            win.add_pv_row_dialog(w)

    def _delete_row(self):
        win = self.window()
        if hasattr(win, "_delete_custom_row"):
            win._delete_custom_row(self)

    def _edit_pvs_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Edit Valve — {self.field_id}")
        dlg.setMinimumWidth(460)
        dlg.setStyleSheet(
            "QDialog{background:#000;color:#e0e0e0;}QLabel{color:#e0e0e0;}"
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;padding:4px;"
            "border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
            "border:1px solid #404040;border-radius:3px;}"
        )
        form = QtWidgets.QFormLayout(dlg); form.setSpacing(6)
        e_name = QtWidgets.QLineEdit(self.label_text);      form.addRow("Name/Label:",   e_name)
        e_on_t = QtWidgets.QLineEdit(self.btn_on.text());   form.addRow("On button text:",  e_on_t)
        e_off_t= QtWidgets.QLineEdit(self.btn_off.text());  form.addRow("Off button text:", e_off_t)
        e_st   = QtWidgets.QLineEdit(self.status_pv);       form.addRow("Status PV:",       e_st)
        e_on   = QtWidgets.QLineEdit(self.on_pv);           form.addRow("On action PV:",    e_on)
        e_of   = QtWidgets.QLineEdit(self.off_pv);          form.addRow("Off action PV:",   e_of)
        btns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("OK"); ok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        ok.clicked.connect(dlg.accept); btns.addWidget(ok)
        cancel = QtWidgets.QPushButton("Cancel"); cancel.clicked.connect(dlg.reject); btns.addWidget(cancel)
        form.addRow(btns)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        old_status = self.status_pv
        self.label_text = e_name.text().strip()
        self.name_lbl.setText(self.label_text)
        self.btn_on.setText(e_on_t.text().strip() or "On")
        self.btn_off.setText(e_off_t.text().strip() or "Off")
        self.status_pv = e_st.text().strip()
        self.on_pv = e_on.text().strip()
        self.off_pv = e_of.text().strip()
        self.status_lbl.setText("---")
        win = self.window()
        if hasattr(win, '_pv_field_rebind'):
            win._pv_field_rebind(self, old_status, self.status_pv)


# ── ToggleField: single-button state toggle (ADL shutter style) ───────

class ToggleField(QtWidgets.QWidget):
    """ADL-style shutter / toggle:
         [ editable label on top  ]
         [         big button      ]
       The button text *is* the action that will be performed on click —
       it shows 'Open' when the status reads closed (click opens) and
       'Close' when the status reads open (click closes). Colour reflects
       current state: green=open, red=closed.

       PVs (all editable via right-click dialog):
         status_pv  — monitored; "Open/closed" determined from value
         open_pv    — written with open_value on click when currently closed
         close_pv   — written with close_value on click when currently open
    """

    def __init__(self, status_pv, open_pv, close_pv, field_id,
                 label_text="", open_text="Open", close_text="Close",
                 open_value=1, close_value=1, parent=None):
        super().__init__(parent)
        self.field_id = field_id
        self.status_pv = (status_pv or "").strip()
        self.open_pv = (open_pv or "").strip()
        self.close_pv = (close_pv or "").strip()
        self.label_text = label_text or ""
        self.open_text = open_text or "Open"
        self.close_text = close_text or "Close"
        self._open_value = open_value
        self._close_value = close_value
        self._is_open = False
        self._edit_mode = False

        L = QtWidgets.QVBoxLayout(self)
        L.setContentsMargins(2, 2, 2, 2); L.setSpacing(2)

        self.name_lbl = QtWidgets.QLabel(self.label_text)
        self.name_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.name_lbl.setStyleSheet("font:bold 9pt;color:#73dfff;padding:2px;")
        L.addWidget(self.name_lbl)

        self.btn = QtWidgets.QPushButton(self.open_text)
        self.btn.setMinimumHeight(30)
        self.btn.clicked.connect(self._on_click)
        L.addWidget(self.btn)

        self._restyle()

    # ── Behaviour ────────────────────────────────────────────────────
    def _restyle(self):
        if self._is_open:
            # Currently open → next click will close it.
            self.btn.setText(self.close_text)
            self.btn.setStyleSheet(
                "background:#c0392b;color:#fff;font:bold 11pt;"
                "border:1px solid #e74c3c;border-radius:3px;padding:4px;"
            )
        else:
            self.btn.setText(self.open_text)
            self.btn.setStyleSheet(
                "background:#27ae60;color:#fff;font:bold 11pt;"
                "border:1px solid #2ecc71;border-radius:3px;padding:4px;"
            )

    def _on_click(self):
        if self._is_open and self.close_pv:
            print(f"[SHUTTER] {self.field_id}: fire -> {self.close_pv}")
            caput_bg(self.close_pv, self._close_value)
            self._show_pending("closing")
        elif (not self._is_open) and self.open_pv:
            print(f"[SHUTTER] {self.field_id}: fire -> {self.open_pv}")
            caput_bg(self.open_pv, self._open_value)
            self._show_pending("opening")

    def _show_pending(self, label):
        # Immediate visual feedback — the button turns blue with a '...'
        # marker until the status PV confirms the real state.
        self.btn.setText(f"{label}...")
        self.btn.setStyleSheet(
            "background:#2980b9;color:#fff;font:bold 11pt;"
            "border:1px solid #3a95d8;border-radius:3px;padding:4px;"
        )

    # ── PV interface ─────────────────────────────────────────────────
    def monitored_pvs(self):
        return [self.status_pv] if self.status_pv else []

    def update_value(self, value):
        """Update state from status PV. Most APS shutter 'CLSD_PL' records
        use 1 = closed (i.e. beam NOT present). Users can invert the
        interpretation via the edit dialog later if needed."""
        v = str(value).strip().lower()
        # Heuristic: open/true/1/etc → OPEN; close/closed/0 → CLOSED
        try:
            num = float(v)
            # Treat numeric 0 as "closed" for SBS/FES CLSD_PL semantics too
            open_now = (num == 0.0)
        except (ValueError, TypeError):
            if v in ("open", "on", "true", "high", "1"):
                open_now = True
            elif v in ("closed", "close", "off", "false", "low", "0"):
                open_now = False
            else:
                open_now = False
        self._is_open = bool(open_now)
        self._restyle()
        print(f"[SHUTTER] {self.field_id}: status={value!r} -> "
              f"{'OPEN' if self._is_open else 'CLOSED'}")

    @property
    def pv(self):
        return self.status_pv

    # ── Save / load ──────────────────────────────────────────────────
    def get_pvs_dict(self):
        return {
            "kind": "toggle",
            "status": self.status_pv, "open": self.open_pv, "close": self.close_pv,
            "label": self.label_text,
            "open_text": self.open_text, "close_text": self.close_text,
            "open_value": self._open_value, "close_value": self._close_value,
        }

    def set_pvs_dict(self, d):
        self.status_pv = (d.get("status") or "").strip()
        self.open_pv = (d.get("open") or "").strip()
        self.close_pv = (d.get("close") or "").strip()
        if "label" in d:
            self.label_text = d["label"] or ""
            self.name_lbl.setText(self.label_text)
        if d.get("open_text"):
            self.open_text = d["open_text"]
        if d.get("close_text"):
            self.close_text = d["close_text"]
        if "open_value" in d: self._open_value = d["open_value"]
        if "close_value" in d: self._close_value = d["close_value"]
        self._restyle()

    # ── Edit mode ────────────────────────────────────────────────────
    def set_edit_mode(self, on):
        self._edit_mode = bool(on)

    def contextMenuEvent(self, e):
        if not self._edit_mode:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Edit Toggle...", self._edit_dialog)
        menu.addAction("Delete Row", self._delete_row)
        menu.addSeparator()
        menu.addAction("Add PV Row here...", self._add_row_here)
        menu.exec_(e.globalPos())
        e.accept()

    def _add_row_here(self):
        w = self.parent()
        while w is not None and not hasattr(w, "key"):
            w = w.parent()
        win = self.window()
        if w is not None and hasattr(win, "add_pv_row_dialog"):
            win.add_pv_row_dialog(w)

    def _delete_row(self):
        win = self.window()
        if hasattr(win, "_delete_custom_row"):
            win._delete_custom_row(self)

    def _edit_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Edit Toggle — {self.field_id}")
        dlg.setMinimumWidth(460)
        dlg.setStyleSheet(
            "QDialog{background:#000;color:#e0e0e0;}QLabel{color:#e0e0e0;}"
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;padding:4px;"
            "border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
            "border:1px solid #404040;border-radius:3px;}"
        )
        form = QtWidgets.QFormLayout(dlg); form.setSpacing(6)
        e_name   = QtWidgets.QLineEdit(self.label_text);  form.addRow("Name/Label:",        e_name)
        e_ot     = QtWidgets.QLineEdit(self.open_text);   form.addRow("Open button text:",  e_ot)
        e_ct     = QtWidgets.QLineEdit(self.close_text);  form.addRow("Close button text:", e_ct)
        e_st     = QtWidgets.QLineEdit(self.status_pv);   form.addRow("Status PV:",         e_st)
        e_open   = QtWidgets.QLineEdit(self.open_pv);     form.addRow("Open trigger PV:",   e_open)
        e_close  = QtWidgets.QLineEdit(self.close_pv);    form.addRow("Close trigger PV:",  e_close)
        btns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("OK"); ok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        ok.clicked.connect(dlg.accept); btns.addWidget(ok)
        cancel = QtWidgets.QPushButton("Cancel"); cancel.clicked.connect(dlg.reject); btns.addWidget(cancel)
        form.addRow(btns)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        old_status = self.status_pv
        self.label_text = e_name.text().strip()
        self.name_lbl.setText(self.label_text)
        self.open_text = e_ot.text().strip() or "Open"
        self.close_text = e_ct.text().strip() or "Close"
        self.status_pv = e_st.text().strip()
        self.open_pv = e_open.text().strip()
        self.close_pv = e_close.text().strip()
        self._restyle()
        win = self.window()
        if hasattr(win, '_pv_field_rebind'):
            win._pv_field_rebind(self, old_status, self.status_pv)
