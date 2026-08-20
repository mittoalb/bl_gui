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
import subprocess

from PyQt5 import QtCore, QtWidgets
from .pv import caput_bg


# ── Setpoint field styles ───────────────────────────────────────────────
# Clean = the field's value matches what the IOC has (nothing pending).
# Dirty = user has typed a value that hasn't been caput yet. The colour
# swap is the visual reminder that Enter is still required — the display
# no longer tells the truth about the PV until the user commits.
_SP_STYLE_CLEAN = (
    "QLineEdit{background:#2c3e50;color:#ecf0f1;"
    "border:1px solid #3498db;border-radius:3px;"
    "padding:4px 6px;font:10pt 'Liberation Mono','DejaVu Sans Mono',monospace;}"
    "QLineEdit:focus{background:#34495e;border:1px solid #5dade2;}"
)
_SP_STYLE_DIRTY = (
    "QLineEdit{background:#2980b9;color:#fff;"
    "border:1px solid #f39c12;border-radius:3px;"
    "padding:4px 6px;font:bold 10pt 'Liberation Mono','DejaVu Sans Mono',monospace;}"
    "QLineEdit:focus{background:#3498db;border:1px solid #f1c40f;}"
)


def _sp_values_equal(a, b):
    """Compare two setpoint value strings, tolerating numeric formatting
    differences (\"5\" vs \"5.000000\") that show up when the IOC echoes
    a caput back with its own precision."""
    a = "" if a is None else str(a).strip()
    b = "" if b is None else str(b).strip()
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (ValueError, TypeError):
        return False


def _copy_to_clipboard(text):
    if text:
        QtWidgets.QApplication.clipboard().setText(text)


def _add_copy_pv_entries(menu, items):
    """Append `Copy PV: <name>` entries for each (label, pv) tuple where pv
    is non-empty. Returns True if any entry was added."""
    any_added = False
    for label, pv in items:
        if not pv:
            continue
        act = menu.addAction(f"Copy {label}: {pv}")
        act.triggered.connect(lambda _=False, p=pv: _copy_to_clipboard(p))
        any_added = True
    return any_added


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
            # Distinct bluish background so editable fields are obviously
            # different from read-only readbacks / labels. Switches to the
            # DIRTY style whenever the typed text stops matching the PV.
            w.setStyleSheet(_SP_STYLE_CLEAN)
            if self._placeholder:
                w.setPlaceholderText(self._placeholder)
            w.returnPressed.connect(self._on_sp_return)
            # textEdited fires on user keystrokes only (not on our own
            # setText during PV echoes), so it's the right hook to detect
            # a pending edit without recursing on our own updates.
            w.textEdited.connect(lambda _=None: self._update_sp_dirty())
        elif self.kind == 'rb':
            w = QtWidgets.QLabel("---")
            w.setStyleSheet("color:#2ecc71;font:bold 10pt 'Liberation Mono','DejaVu Sans Mono',monospace;")
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
            val = self._inner.text()
            print(f"[SP] {self.field_id}: caput {self.pv} {val!r}")
            caput_bg(self.pv, val)

    def _update_sp_dirty(self):
        """Colour the setpoint field so it's obvious when what you see
        doesn't match what the PV actually has (i.e. you typed but
        haven't hit Enter). Clears itself as soon as the IOC echoes
        your commit back."""
        if self.kind != 'sp' or self._inner is None:
            return
        pv_val = getattr(self, '_pv_value', None)
        dirty = (pv_val is not None
                 and not _sp_values_equal(self._inner.text(), pv_val))
        if getattr(self, '_sp_dirty', None) == dirty:
            return
        self._sp_dirty = dirty
        self._inner.setStyleSheet(_SP_STYLE_DIRTY if dirty else _SP_STYLE_CLEAN)

    def _on_cmb_changed(self, idx):
        if self.pv and idx >= 0:
            # caput the label, not the local index. Local index depends on
            # the order we listed choices in; sending the string lets the
            # IOC (mbbo / bo with ZRST/ONAM etc.) resolve it correctly
            # regardless of how the choices are ordered in the GUI.
            caput_bg(self.pv, self._inner.currentText())

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
            # Apply the field's fmt (if given) so IOC echoes like
            # "6.9999999999" show as "7.000" instead of the raw float.
            # Dirty-check comparison is numeric so the formatted string
            # still matches whatever the user typed.
            text = str(value)
            if self._fmt:
                try:
                    text = format(float(value), self._fmt)
                except (ValueError, TypeError):
                    pass
            # Remember the actual PV value regardless of focus so the
            # dirty check compares against ground truth, not stale state.
            self._pv_value = text
            if not self._inner.hasFocus():
                self._inner.blockSignals(True)
                self._inner.setText(text)
                self._inner.blockSignals(False)
            self._update_sp_dirty()
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
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        items = [("PV", self.pv), ("on PV", self.on_pv), ("off PV", self.off_pv)]
        added = _add_copy_pv_entries(menu, items)
        if self._edit_mode:
            if added:
                menu.addSeparator()
            menu.addAction("Edit PV...", self._edit_pv_dialog)
            menu.addAction("Delete Row", self._delete_row)
            menu.addSeparator()
            menu.addAction("Add PV Row here...", self._add_row_here)
        if menu.isEmpty():
            return super().contextMenuEvent(e)
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
                 label_text="", on_text="On", off_text="Off",
                 status_on_text=None, status_off_text=None,
                 on_value=1, off_value=1,
                 pulse=True, btn_width=38, invert_status=False,
                 vertical=False, highlight_buttons=False, parent=None):
        super().__init__(parent)
        self.field_id = field_id
        self.status_pv = (status_pv or "").strip()
        self.on_pv = (on_pv or "").strip()
        self.off_pv = (off_pv or "").strip()
        self.label_text = label_text or ""
        self._edit_mode = False
        # Edge-triggered PLC bits need a 1,0 pulse so the next click works.
        # .PROC fields and level-driven controls (like uniblitz) want a
        # single write and must not be pulsed. Off by default for shutters.
        self._pulse = bool(pulse)
        # Shutter CLSD_PL records use 1=closed / 0=open (inverse of the
        # valve on/off convention). invert_status flips the interpretation.
        self._invert_status = bool(invert_status)
        # Labels shown on the status indicator (default ON/OFF; shutters
        # want OPEN/CLOSED).
        self._status_on_text = status_on_text or "ON"
        self._status_off_text = status_off_text or "OFF"
        # Values written by the on/off buttons. Default 1/1 matches the
        # traditional "two-trigger" pattern (separate on/off PVs, each
        # triggered with a 1). For a single-PV toggle (e.g. Uniblitz), set
        # on_value=1, off_value=0.
        self._on_value = on_value
        self._off_value = off_value
        # Highlight-mode: hide the status label and instead render the
        # "active" button brightly / the inactive one dim. Useful for a
        # Run/Stop pair where we want to see at a glance which state is
        # current without a separate label.
        self._highlight_buttons = bool(highlight_buttons)

        if vertical:
            # Column layout: name on top, status below, Open/Close buttons
            # side-by-side at the bottom. Used by the shutter panel.
            L = QtWidgets.QVBoxLayout(self)
            L.setContentsMargins(4, 4, 4, 4); L.setSpacing(4)

            self.name_lbl = QtWidgets.QLabel(self.label_text)
            self.name_lbl.setAlignment(QtCore.Qt.AlignCenter)
            self.name_lbl.setStyleSheet("font:bold 10pt;color:#73dfff;padding:2px;")
            L.addWidget(self.name_lbl)

            self.status_lbl = QtWidgets.QLabel("---")
            self.status_lbl.setAlignment(QtCore.Qt.AlignCenter)
            self.status_lbl.setStyleSheet(
                "background:#404040;color:#e0e0e0;font:bold 11pt;"
                "border:1px solid #606060;border-radius:3px;padding:4px;")
            self.status_lbl.setMinimumHeight(28)
            L.addWidget(self.status_lbl)

            btn_row = QtWidgets.QHBoxLayout(); btn_row.setSpacing(4)
            self.btn_on = QtWidgets.QPushButton(on_text)
            self.btn_on.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                      QtWidgets.QSizePolicy.Preferred)
            self.btn_on.setMinimumHeight(32)
            self.btn_on.setStyleSheet(
                "background:#27ae60;color:#fff;font:bold 10pt;padding:4px;"
                "border:1px solid #2ecc71;border-radius:3px;")
            self.btn_on.clicked.connect(lambda: self._fire(self.on_pv, self._on_value))
            btn_row.addWidget(self.btn_on)

            self.btn_off = QtWidgets.QPushButton(off_text)
            self.btn_off.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                       QtWidgets.QSizePolicy.Preferred)
            self.btn_off.setMinimumHeight(32)
            self.btn_off.setStyleSheet(
                "background:#c0392b;color:#fff;font:bold 10pt;padding:4px;"
                "border:1px solid #e74c3c;border-radius:3px;")
            self.btn_off.clicked.connect(lambda: self._fire(self.off_pv, self._off_value))
            btn_row.addWidget(self.btn_off)
            L.addLayout(btn_row)
            return

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

        fs = 8 if btn_width <= 50 else 10
        self.btn_on = QtWidgets.QPushButton(on_text); self.btn_on.setFixedWidth(btn_width)
        self.btn_on.setStyleSheet(
            f"background:#27ae60;color:#fff;font:bold {fs}pt;padding:4px;")
        self.btn_on.clicked.connect(lambda: self._fire(self.on_pv, self._on_value))
        L.addWidget(self.btn_on)

        self.btn_off = QtWidgets.QPushButton(off_text); self.btn_off.setFixedWidth(btn_width)
        self.btn_off.setStyleSheet(
            f"background:#c0392b;color:#fff;font:bold {fs}pt;padding:4px;")
        self.btn_off.clicked.connect(lambda: self._fire(self.off_pv, self._off_value))
        L.addWidget(self.btn_off)

    def _fire(self, pv, value=1):
        if pv:
            if self._pulse:
                print(f"[VALVE] {self.field_id}: fire -> {pv}={value}  (pulse {value},0)")
                caput_bg(pv, value)
                QtCore.QTimer.singleShot(300, lambda p=pv: caput_bg(p, 0))
            else:
                print(f"[VALVE] {self.field_id}: fire -> {pv}={value}")
                caput_bg(pv, value)
            # The "going_on" intent is whichever button was clicked, even
            # if on_pv == off_pv (single-PV toggles like Uniblitz).
            if value == self._on_value and (pv == self.on_pv):
                self._show_pending(True)
            elif value == self._off_value and (pv == self.off_pv):
                self._show_pending(False)

    def _show_pending(self, going_on):
        self.status_lbl.setText("ON?" if going_on else "OFF?")
        self.status_lbl.setStyleSheet(
            "background:#2980b9;color:#fff;font:bold 10pt;"
            "border:1px solid #3a95d8;border-radius:2px;padding:2px 6px;"
        )
        # Safety net so we don't get stuck on "?" forever. Cases where the
        # status monitor doesn't fire after a click:
        #   - Edge-triggered PLC bit clicked in the same direction it was
        #     already in → no state change → no monitor event.
        #   - CA monitor subscription dropped that particular update.
        # After 2s we force a caget and reflect whatever the PV really says.
        if not hasattr(self, "_pending_timer"):
            self._pending_timer = QtCore.QTimer(self)
            self._pending_timer.setSingleShot(True)
            self._pending_timer.timeout.connect(self._resync_status)
        self._pending_timer.start(2000)

    def _resync_status(self):
        """Force-read the status PV via caget. Fallback for when the CA
        monitor doesn't deliver an event after a caput."""
        if not self.status_pv:
            return
        try:
            r = subprocess.run(["caget", "-t", self.status_pv],
                               capture_output=True, timeout=2.0, text=True)
        except Exception as e:
            print(f"[VALVE] {self.field_id}: resync caget failed: {e}")
            return
        if r.returncode != 0:
            print(f"[VALVE] {self.field_id}: resync caget rc={r.returncode} "
                  f"stderr={r.stderr.strip()!r}")
            return
        v = r.stdout.strip()
        if v:
            self.update_value(v)

    # ── PV interface used by the window ──────────────────────────────
    def monitored_pvs(self):
        return [self.status_pv] if self.status_pv else []

    def update_value(self, value):
        """Called by the window when the status PV fires."""
        # A real monitor event arrived — cancel any pending resync so we
        # don't do an unnecessary caget right after a legitimate update.
        t = getattr(self, "_pending_timer", None)
        if t is not None and t.isActive():
            t.stop()
        v = str(value).strip()
        lv = v.lower()
        on = False
        try:
            on = float(v) != 0.0
        except (ValueError, TypeError):
            on = lv in ("on", "open", "true", "high", "yes", "1",
                        "run", "running", "active", "busy", "start",
                        "started", "enable", "enabled",
                        "acquire", "acquiring")
        if self._invert_status:
            on = not on
        print(f"[VALVE] {self.field_id}: status={value!r} -> {'ON' if on else 'OFF'}")
        if self._highlight_buttons:
            # Hide the status label; active button bright, inactive dim.
            self.status_lbl.hide()
            active_on = (
                "background:#27ae60;color:#fff;font:bold 10pt;padding:4px;"
                "border:2px solid #2ecc71;border-radius:3px;")
            inactive_on = (
                "background:#1e3d2a;color:#888;font:10pt;padding:4px;"
                "border:1px solid #2c5e41;border-radius:3px;")
            active_off = (
                "background:#c0392b;color:#fff;font:bold 10pt;padding:4px;"
                "border:2px solid #e74c3c;border-radius:3px;")
            inactive_off = (
                "background:#4a1b15;color:#888;font:10pt;padding:4px;"
                "border:1px solid #7a2a22;border-radius:3px;")
            if on:
                self.btn_on.setStyleSheet(active_on)
                self.btn_off.setStyleSheet(inactive_off)
            else:
                self.btn_on.setStyleSheet(inactive_on)
                self.btn_off.setStyleSheet(active_off)
            return
        if on:
            self.status_lbl.setText(self._status_on_text)
            self.status_lbl.setStyleSheet(
                "background:#27ae60;color:#fff;font:bold 11pt;"
                "border:1px solid #2ecc71;border-radius:3px;padding:4px;"
            )
        else:
            self.status_lbl.setText(self._status_off_text)
            self.status_lbl.setStyleSheet(
                "background:#c0392b;color:#fff;font:bold 11pt;"
                "border:1px solid #e74c3c;border-radius:3px;padding:4px;"
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
        # Accept legacy toggle-style keys (open/close/open_text/close_text)
        # so shutter rows saved before ValveField unification still load.
        self.status_pv = (d.get("status") or "").strip()
        self.on_pv = (d.get("on") or d.get("open") or "").strip()
        self.off_pv = (d.get("off") or d.get("close") or "").strip()
        if "label" in d:
            self.label_text = d["label"] or ""
            self.name_lbl.setText(self.label_text)
        t_on = d.get("on_text") or d.get("open_text")
        if t_on: self.btn_on.setText(t_on)
        t_off = d.get("off_text") or d.get("close_text")
        if t_off: self.btn_off.setText(t_off)

    # ── Edit mode (right-click to change all 3 PVs) ──────────────────
    def set_edit_mode(self, on):
        self._edit_mode = bool(on)

    def contextMenuEvent(self, e):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        added = _add_copy_pv_entries(menu, [
            ("status PV", self.status_pv),
            ("on PV", self.on_pv),
            ("off PV", self.off_pv),
        ])
        if self._edit_mode:
            if added: menu.addSeparator()
            menu.addAction("Edit Valve PVs...", self._edit_pvs_dialog)
            menu.addAction("Delete Row", self._delete_row)
            menu.addSeparator()
            menu.addAction("Add PV Row here...", self._add_row_here)
        if menu.isEmpty():
            return super().contextMenuEvent(e)
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
                 open_value=1, close_value=1,
                 pulse=False, invert_status=False,
                 state_label=False, parent=None):
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
        # Edge-triggered PLCs need a 1,0 pulse so the next click works; normal
        # bi/bo records must not be pulsed (a 0 follow-up immediately reverses
        # the action). Default off — enable explicitly for pulsed outputs.
        self._pulse = bool(pulse)
        # For records like CLSD_PL where 1=closed/0=open, the "state is on"
        # interpretation is inverted. Shutter users set invert_status=True.
        self._invert_status = bool(invert_status)
        # state_label mode: button text is the CURRENT STATE (e.g. "YES" /
        # "NO") rather than the action the click will perform. Colour still
        # follows state (green=on, red=off). When state_label=False (default)
        # the button reads like a shutter: it says "Close" when open, "Open"
        # when closed — i.e. what clicking will do.
        self._state_label = bool(state_label)

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
            # State is ON. In state_label mode the text shows the on-state
            # name (open_text); in action-label mode it shows what clicking
            # does (close_text).
            self.btn.setText(self.open_text if self._state_label else self.close_text)
            bg = "#27ae60" if self._state_label else "#c0392b"
            bd = "#2ecc71" if self._state_label else "#e74c3c"
            self.btn.setStyleSheet(
                f"background:{bg};color:#fff;font:bold 11pt;"
                f"border:1px solid {bd};border-radius:3px;padding:4px;"
            )
        else:
            self.btn.setText(self.close_text if self._state_label else self.open_text)
            bg = "#c0392b" if self._state_label else "#27ae60"
            bd = "#e74c3c" if self._state_label else "#2ecc71"
            self.btn.setStyleSheet(
                f"background:{bg};color:#fff;font:bold 11pt;"
                f"border:1px solid {bd};border-radius:3px;padding:4px;"
            )

    def _on_click(self):
        if self._is_open and self.close_pv:
            self._fire(self.close_pv, self._close_value)
        elif (not self._is_open) and self.open_pv:
            self._fire(self.open_pv, self._open_value)

    def _fire(self, pv, val):
        print(f"[TOGGLE] {self.field_id}: caput {pv} {val}")
        caput_bg(pv, val)
        if self._pulse:
            QtCore.QTimer.singleShot(300, lambda p=pv: caput_bg(p, 0))

    # ── PV interface ─────────────────────────────────────────────────
    def monitored_pvs(self):
        return [self.status_pv] if self.status_pv else []

    def update_value(self, value):
        """Update state from status PV. Standard semantic: truthy (1/Yes/On/
        Open/True/High) = state ON, falsy = OFF. For inverted records like
        CLSD_PL (1=closed), pass invert_status=True to the constructor."""
        v = str(value).strip()
        lv = v.lower()
        on = False
        try:
            on = float(v) != 0.0
        except (ValueError, TypeError):
            on = lv in ("on", "open", "true", "high", "yes", "1",
                        "run", "running", "active", "busy", "start",
                        "started", "enable", "enabled",
                        "acquire", "acquiring")
        if self._invert_status:
            on = not on
        self._is_open = bool(on)
        self._restyle()
        print(f"[TOGGLE] {self.field_id}: status={value!r} -> "
              f"{'ON' if self._is_open else 'OFF'}")

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
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                           "QMenu::item:selected{background:#1e5a8e;}")
        added = _add_copy_pv_entries(menu, [
            ("status PV", self.status_pv),
            ("open PV", self.open_pv),
            ("close PV", self.close_pv),
        ])
        if self._edit_mode:
            if added: menu.addSeparator()
            menu.addAction("Edit Toggle...", self._edit_dialog)
            menu.addAction("Delete Row", self._delete_row)
            menu.addSeparator()
            menu.addAction("Add PV Row here...", self._add_row_here)
        if menu.isEmpty():
            return super().contextMenuEvent(e)
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
