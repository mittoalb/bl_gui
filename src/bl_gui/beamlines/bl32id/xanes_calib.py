"""ZP energy-calibration table for bl32-ID.

Stores an (E_eV, motor_1, motor_2, …) row per measured calibration
point, plus a user-editable **motor list** that names each column and
carries an ``include`` checkbox. Callers (XANES 2D scans, energy
setpoint logic, etc.) iterate the motor list and interpolate only the
motors flagged ``include``. The table and the config persist in
``~/.bl_gui/bl32id_zp_calibration.json`` so they survive restarts and
are shared across machines whenever ``$HOME`` is on NFS.

Reads/writes PVs through ``caget`` / ``caput`` subprocesses to stay
consistent with the rest of bl_gui and to bound any CA wait with a
timeout.
"""
import json
import os
import subprocess

from PyQt5 import QtCore, QtWidgets


_CALIB_FILE = os.path.expanduser("~/.bl_gui/bl32id_zp_calibration.json")

DEFAULT_PVS = {
    "energy_rb_pv": "32ida:BraggERdbkAO",
    "energy_units": "keV",
}

# One entry per calibration column. `include=True` = interpolate and
# write to the EPICS cal files / direct-caput. `include=False` = keep
# the column in the table for reference but do not drive the motor.
_DEFAULT_MOTORS = [
    {"label": "ZP X",  "pv": "32idbTXM:mcs2:c1:m13", "include": True},
    {"label": "ZP Y",  "pv": "32idbTXM:mcs2:c1:m14", "include": True},
    {"label": "ZP Z",  "pv": "32idbTXM:mcs2:c1:m15", "include": True},
    {"label": "QG V",  "pv": "32idQG:m1",             "include": True},
    {"label": "QG H",  "pv": "32idQG:m2",             "include": True},
]

DEFAULT_RANGE_KEV = 0.5

# ── Legacy PV keys used by the pre-motors-list schema. Kept only for
# migration; not written to new saves.
_LEGACY_PV_KEYS = [
    ("zp_x_pv", "ZP X"),
    ("zp_y_pv", "ZP Y"),
    ("zp_z_pv", "ZP Z"),
    ("qg_v_pv", "QG V"),
    ("qg_h_pv", "QG H"),
]


def _caget(pv, timeout=2.0):
    if not pv:
        return None
    try:
        r = subprocess.run(["caget", "-t", pv],
                           capture_output=True, timeout=timeout, text=True)
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        return None


def _read_motor_rbv(pv):
    """Prefer ``.RBV``, fall back to the base PV."""
    if not pv:
        return None
    v = _caget(f"{pv}.RBV")
    return v if v is not None else _caget(pv)


def _migrate(cfg):
    """Fold a legacy config (with zp_x_pv / zp_y_pv / etc. in ``pvs``
    and 6-column points) into the current schema with a ``motors``
    list. Idempotent — a new-schema config is returned unchanged."""
    cfg = dict(cfg or {})
    if "motors" in cfg and isinstance(cfg["motors"], list):
        return cfg
    old_pvs = dict(cfg.get("pvs") or {})
    motors = []
    for key, label in _LEGACY_PV_KEYS:
        pv = (old_pvs.pop(key, "") or "").strip()
        motors.append({"label": label, "pv": pv, "include": True})
    if not motors:
        motors = [dict(m) for m in _DEFAULT_MOTORS]
    cfg["motors"] = motors
    # Keep only the non-motor keys in pvs (energy_rb_pv, energy_units).
    cfg["pvs"] = {k: v for k, v in old_pvs.items()
                  if k in DEFAULT_PVS or k == "energy_units"}
    for k, v in DEFAULT_PVS.items():
        cfg["pvs"].setdefault(k, v)
    return cfg


def load_config():
    """Load the calibration config. Returns a dict with keys ``pvs``,
    ``motors``, ``points`` and ``range_keV``. Missing keys are filled
    with defaults; legacy schemas are migrated on the fly."""
    try:
        with open(_CALIB_FILE) as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    cfg = _migrate(raw)
    cfg.setdefault("pvs", dict(DEFAULT_PVS))
    cfg.setdefault("motors", [dict(m) for m in _DEFAULT_MOTORS])
    cfg.setdefault("points", [])
    cfg.setdefault("range_keV", DEFAULT_RANGE_KEV)
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(_CALIB_FILE), exist_ok=True)
    with open(_CALIB_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def included_motors(cfg=None):
    """Convenience for consumers: return only the motors flagged
    ``include=True`` from the current (or given) config. Each entry is
    the full dict so callers can look at label + pv together."""
    if cfg is None:
        cfg = load_config()
    return [m for m in (cfg.get("motors") or []) if m.get("include", True)
            and (m.get("pv") or "").strip()]


class XanesCalibWindow(QtWidgets.QMainWindow):
    """ZP energy calibration table with a dynamic motor list. Independent
    top-level window — not modal, so the main GUI stays live while it's
    open."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("ZP Energy Calibration — bl32-ID")
        self.resize(900, 700)
        central = QtWidgets.QWidget(); self.setCentralWidget(central)

        cfg = load_config()
        self._pvs = dict(cfg.get("pvs") or DEFAULT_PVS)
        # Deep-copy the motors list so edits in the UI don't mutate the
        # loaded dict until we explicitly save.
        self._motors = [dict(m) for m in (cfg.get("motors") or _DEFAULT_MOTORS)]

        V = QtWidgets.QVBoxLayout(central)

        # ── Non-motor PV configuration ─────────────────────────────
        pv_box = QtWidgets.QGroupBox("Energy source")
        pl = QtWidgets.QFormLayout(pv_box)
        self._pv_edits = {}
        self._e_edit = QtWidgets.QLineEdit(self._pvs.get("energy_rb_pv", ""))
        self._pv_edits["energy_rb_pv"] = self._e_edit
        pl.addRow("Energy RBV PV:", self._e_edit)
        self._units_cmb = QtWidgets.QComboBox()
        self._units_cmb.addItems(["keV", "eV"])
        self._units_cmb.setCurrentText(self._pvs.get("energy_units", "keV"))
        pl.addRow("Energy units:", self._units_cmb)
        V.addWidget(pv_box)

        # ── Motor list ─────────────────────────────────────────────
        mot_box = QtWidgets.QGroupBox(
            "Motors (columns) — check Include to interpolate")
        ml = QtWidgets.QVBoxLayout(mot_box)
        self.motor_table = QtWidgets.QTableWidget(0, 3)
        self.motor_table.setHorizontalHeaderLabels(["Label", "PV", "Include"])
        self.motor_table.horizontalHeader().setStretchLastSection(False)
        self.motor_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Interactive)
        self.motor_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch)
        self.motor_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeToContents)
        self.motor_table.setMaximumHeight(180)
        ml.addWidget(self.motor_table)

        mb = QtWidgets.QHBoxLayout()
        bn_madd = QtWidgets.QPushButton("+ Motor")
        bn_madd.clicked.connect(self._on_add_motor)
        bn_mrm  = QtWidgets.QPushButton("− Remove selected")
        bn_mrm.clicked.connect(self._on_remove_motor)
        mb.addWidget(bn_madd); mb.addWidget(bn_mrm); mb.addStretch()
        ml.addLayout(mb)
        V.addWidget(mot_box)

        # Populate the motor list. Signal-blocked so the initial fill
        # doesn't recurse into _on_motors_changed → auto-save loop.
        self._suppress_autosave = True
        for m in self._motors:
            self._append_motor_row(m)
        self._suppress_autosave = False

        # ── Data table ─────────────────────────────────────────────
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.horizontalHeader().setStretchLastSection(True)
        V.addWidget(self.table, 1)
        self._rebuild_data_table_columns()

        # Auto-save on any change (with 250 ms debounce so a burst of
        # edits collapses into one write).
        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(250)
        self._autosave_timer.timeout.connect(self._do_save)

        self.motor_table.itemChanged.connect(self._on_motor_changed)
        self.motor_table.model().rowsInserted.connect(
            lambda *_: (not self._suppress_autosave) and self._autosave_timer.start())
        self.motor_table.model().rowsRemoved.connect(
            lambda *_: (not self._suppress_autosave) and self._autosave_timer.start())

        self.table.itemChanged.connect(
            lambda *_: (not self._suppress_autosave) and self._autosave_timer.start())
        self.table.model().rowsInserted.connect(
            lambda *_: (not self._suppress_autosave) and self._autosave_timer.start())
        self.table.model().rowsRemoved.connect(
            lambda *_: (not self._suppress_autosave) and self._autosave_timer.start())

        # Load the saved data-point rows into the (now dynamic) table.
        self._suppress_autosave = True
        for p in (cfg.get("points") or []):
            self._append_row_from_list(p)
        self._suppress_autosave = False

        # ── Action buttons ─────────────────────────────────────────
        H = QtWidgets.QHBoxLayout()
        bn_save = QtWidgets.QPushButton("Save current E + motors")
        bn_save.setToolTip("Read the current energy RBV and each motor's RBV "
                           "via caget, append a row.")
        bn_save.clicked.connect(self._on_save_current)
        bn_rm = QtWidgets.QPushButton("Remove selected row")
        bn_rm.clicked.connect(self._on_remove_row)
        bn_clr = QtWidgets.QPushButton("Clear all rows")
        bn_clr.clicked.connect(self._on_clear)
        for b in (bn_save, bn_rm, bn_clr):
            H.addWidget(b)
        H.addStretch()
        bn_close = QtWidgets.QPushButton("Save && Close")
        bn_close.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;"
                               "padding:4px 10px;border-radius:3px;")
        bn_close.clicked.connect(self._save_and_close)
        H.addWidget(bn_close)
        V.addLayout(H)

    # ── Motor-list helpers ─────────────────────────────────────────
    def _append_motor_row(self, m):
        r = self.motor_table.rowCount()
        self.motor_table.insertRow(r)
        self.motor_table.setItem(
            r, 0, QtWidgets.QTableWidgetItem(m.get("label", "")))
        self.motor_table.setItem(
            r, 1, QtWidgets.QTableWidgetItem(m.get("pv", "")))
        chk = QtWidgets.QTableWidgetItem()
        chk.setFlags(chk.flags() | QtCore.Qt.ItemIsUserCheckable)
        chk.setCheckState(QtCore.Qt.Checked if m.get("include", True)
                          else QtCore.Qt.Unchecked)
        chk.setTextAlignment(QtCore.Qt.AlignCenter)
        self.motor_table.setItem(r, 2, chk)

    def _on_add_motor(self):
        self._suppress_autosave = True
        self._append_motor_row({"label": "New", "pv": "", "include": True})
        self._suppress_autosave = False
        self._motors = self._collect_motors()
        self._rebuild_data_table_columns()
        self._autosave_timer.start()

    def _on_remove_motor(self):
        rows = sorted({i.row() for i in self.motor_table.selectedIndexes()},
                      reverse=True)
        if not rows:
            return
        # Ask before removing — data columns for those motors disappear
        # too, and undo isn't cheap.
        if QtWidgets.QMessageBox.question(
                self, "Remove motor(s)",
                f"Remove {len(rows)} motor(s) and drop their data columns "
                "from every row?") != QtWidgets.QMessageBox.Yes:
            return
        self._suppress_autosave = True
        for r in rows:
            self.motor_table.removeRow(r)
        self._suppress_autosave = False
        self._motors = self._collect_motors()
        self._rebuild_data_table_columns()
        self._autosave_timer.start()

    def _on_motor_changed(self, item):
        if self._suppress_autosave:
            return
        # Label change → update the data table's header for this column.
        # PV change → nothing structural to redraw.
        # Include change → nothing structural, but affects downstream.
        new_motors = self._collect_motors()
        if len(new_motors) == len(self._motors):
            # Just header labels might have changed.
            self._motors = new_motors
            self._sync_data_headers()
        else:
            # Shouldn't happen from an item edit, but guard anyway.
            self._motors = new_motors
            self._rebuild_data_table_columns()
        self._autosave_timer.start()

    def _collect_motors(self):
        out = []
        for r in range(self.motor_table.rowCount()):
            lbl_it = self.motor_table.item(r, 0)
            pv_it  = self.motor_table.item(r, 1)
            chk_it = self.motor_table.item(r, 2)
            label = (lbl_it.text() if lbl_it else "").strip() or f"m{r+1}"
            pv    = (pv_it.text()  if pv_it  else "").strip()
            include = bool(chk_it and chk_it.checkState() == QtCore.Qt.Checked)
            out.append({"label": label, "pv": pv, "include": include})
        return out

    # ── Data-table helpers ─────────────────────────────────────────
    def _headers(self):
        cols = ["Energy [eV]"]
        for m in self._motors:
            cols.append(f"{m['label']} [mm]")
        return cols

    def _sync_data_headers(self):
        self.table.setHorizontalHeaderLabels(self._headers())

    def _rebuild_data_table_columns(self):
        """Called when the motor list gains or loses a row. Preserves
        existing data by column INDEX — dropped motors take their
        column with them; new motors get empty cells in every row."""
        prior_rows = []
        for r in range(self.table.rowCount()):
            row = []
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                row.append(it.text() if it and it.text().strip() else "")
            prior_rows.append(row)

        want = 1 + len(self._motors)
        self._suppress_autosave = True
        self.table.setColumnCount(want)
        self.table.setHorizontalHeaderLabels(self._headers())
        # Re-fill preserved cells up to the new column count.
        for r, row in enumerate(prior_rows):
            for c in range(min(len(row), want)):
                if row[c]:
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem(row[c]))
        self._suppress_autosave = False

    def _append_row_from_list(self, values):
        """Append a data row from a saved [E, v1, v2, …] list. Missing
        trailing values become empty cells; extras are dropped."""
        if not values or values[0] is None:
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        try:
            self.table.setItem(
                r, 0, QtWidgets.QTableWidgetItem(f"{float(values[0]):.3f}"))
        except (ValueError, TypeError):
            return
        for c in range(1, min(len(values), 1 + len(self._motors))):
            v = values[c]
            if v is None or v == "":
                continue
            try:
                self.table.setItem(
                    r, c, QtWidgets.QTableWidgetItem(f"{float(v):.6f}"))
            except (ValueError, TypeError):
                pass

    def _append_row_live(self, e_eV, motor_values):
        """Append a data row from live caget results (one value per motor
        in the current motor order). None-valued cells are left empty."""
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(
            r, 0, QtWidgets.QTableWidgetItem(f"{float(e_eV):.3f}"))
        for c, v in enumerate(motor_values, start=1):
            if v is None:
                continue
            self.table.setItem(
                r, c, QtWidgets.QTableWidgetItem(f"{float(v):.6f}"))

    def _collect_points(self):
        pts = []
        for r in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(r, c)
                if it is None or not it.text().strip():
                    return None
                try:
                    return float(it.text())
                except ValueError:
                    return None
            e = cell(0)
            if e is None:
                continue
            row = [e] + [cell(c) for c in range(1, 1 + len(self._motors))]
            pts.append(row)
        return pts

    def _collect_pvs(self):
        pvs = {
            "energy_rb_pv": self._e_edit.text().strip(),
            "energy_units": self._units_cmb.currentText(),
        }
        return pvs

    # ── Button handlers ────────────────────────────────────────────
    def _on_save_current(self):
        pvs = self._collect_pvs()
        rb = pvs["energy_rb_pv"]
        if not rb:
            QtWidgets.QMessageBox.warning(self, "Missing PV",
                "Set the Energy RBV PV first.")
            return
        e_val = _caget(rb)
        if e_val is None:
            QtWidgets.QMessageBox.warning(self, "Read failed",
                f"caget {rb} failed (timeout or unknown PV).")
            return
        if pvs["energy_units"] == "keV":
            e_val *= 1000.0
        # Snapshot the current motor list so any UI edit mid-caget
        # doesn't misalign the columns we're about to write.
        motors = self._collect_motors()
        self._motors = motors
        vals = [_read_motor_rbv(m["pv"]) if m["pv"] else None for m in motors]
        if all(v is None for v in vals):
            QtWidgets.QMessageBox.warning(self, "Read failed",
                "Could not read any of the configured motor RBVs.")
            return
        self._suppress_autosave = True
        self._append_row_live(e_val, vals)
        self._suppress_autosave = False
        self._autosave_timer.start()

    def _on_remove_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _on_clear(self):
        if self.table.rowCount() == 0:
            return
        if QtWidgets.QMessageBox.question(
                self, "Clear calibration table", "Delete all rows?"
        ) == QtWidgets.QMessageBox.Yes:
            self.table.setRowCount(0)

    def _save_and_close(self):
        if self._do_save():
            self._saved_in_session = True
            self.close()

    def closeEvent(self, event):
        try:
            if not getattr(self, "_saved_in_session", False):
                self._do_save()
        except Exception as e:
            print(f"[CALIB] auto-save on close failed: {e}")
        super().closeEvent(event)

    def _do_save(self):
        # Force any in-progress cell edit to commit so a value typed but
        # not yet Enter-committed still lands on disk.
        for tbl in (self.motor_table, self.table):
            if tbl.state() == QtWidgets.QAbstractItemView.EditingState:
                editor = QtWidgets.QApplication.focusWidget()
                if editor is not None:
                    delegate = tbl.itemDelegate(tbl.currentIndex())
                    if delegate is not None:
                        try:
                            delegate.commitData.emit(editor)
                            delegate.closeEditor.emit(editor)
                        except Exception:
                            pass
                try:
                    tbl.setCurrentCell(-1, -1)
                except Exception:
                    pass

        # Preserve range_keV (set from the main GUI's Energy panel).
        prior = load_config()
        self._motors = self._collect_motors()
        cfg = {
            "pvs":     self._collect_pvs(),
            "motors":  self._motors,
            "points":  self._collect_points(),
            "range_keV": prior.get("range_keV", DEFAULT_RANGE_KEV),
        }
        try:
            save_config(cfg)
            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save failed",
                f"Could not write {_CALIB_FILE}:\n{e}")
            return False


# Keep references on the QApplication so non-modal windows aren't GC'd
# the moment launch() returns.
_open_windows = []


def launch(parent=None):
    for w in list(_open_windows):
        if w.isVisible():
            w.raise_(); w.activateWindow()
            return w
        _open_windows.remove(w)
    win = XanesCalibWindow(parent)
    _open_windows.append(win)
    win.destroyed.connect(lambda _=None, w=win: _open_windows.remove(w)
                          if w in _open_windows else None)
    win.show()
    return win
