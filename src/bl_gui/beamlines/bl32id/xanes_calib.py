"""Zone-plate energy-calibration table for bl32-ID.

Stores (E_eV, X_mm, Y_mm, Z_mm) tuples — the three ZP positioner axes
at each energy point. Callers (XANES 2D scans, energy setpoint logic,
etc.) can interpolate at run time. The table and the PV config persist
in ~/.bl_gui/bl32id_zp_calibration.json so they survive restarts and
are shared across machines whenever $HOME is on NFS.

Reads/writes PVs through `caget` / `caput` subprocesses to stay
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
    "zp_x_pv":      "32idbTXM:mcs2:c1:m13",
    "zp_y_pv":      "32idbTXM:mcs2:c1:m14",
    "zp_z_pv":      "32idbTXM:mcs2:c1:m15",
    "qg_v_pv":      "32idQG:m1",
    "qg_h_pv":      "32idQG:m2",
}

DEFAULT_RANGE_KEV = 0.5


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
    """Prefer `.RBV`, fall back to the base PV."""
    if not pv:
        return None
    v = _caget(f"{pv}.RBV")
    return v if v is not None else _caget(pv)


def load_config():
    try:
        with open(_CALIB_FILE) as f:
            return json.load(f)
    except Exception:
        return {"pvs": dict(DEFAULT_PVS), "points": []}


def save_config(cfg):
    os.makedirs(os.path.dirname(_CALIB_FILE), exist_ok=True)
    with open(_CALIB_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


class XanesCalibWindow(QtWidgets.QMainWindow):
    """ZP energy calibration table. Independent top-level window — not modal,
    so the main GUI stays live while the table is open. All PVs are
    user-editable; defaults are sane for bl32-ID. File format is
    forward-compatible with the xanes_gui table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt.Window = free-standing top-level, movable across screens;
        # WA_DeleteOnClose so closing it actually frees resources.
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("ZP Energy Calibration — bl32-ID")
        self.resize(820, 600)
        central = QtWidgets.QWidget(); self.setCentralWidget(central)

        cfg = load_config()
        self._pvs = dict(DEFAULT_PVS); self._pvs.update(cfg.get("pvs", {}))

        V = QtWidgets.QVBoxLayout(central)

        # ── PV configuration ────────────────────────────────────────────
        pv_box = QtWidgets.QGroupBox("Sources (PVs)")
        pl = QtWidgets.QFormLayout(pv_box)
        self._pv_edits = {}
        for key, label in [
            ("energy_rb_pv", "Energy RBV PV:"),
            ("zp_x_pv",      "ZP X motor PV:"),
            ("zp_y_pv",      "ZP Y motor PV:"),
            ("zp_z_pv",      "ZP Z motor PV:"),
            ("qg_v_pv",      "Queensgate V PV:"),
            ("qg_h_pv",      "Queensgate H PV:"),
        ]:
            e = QtWidgets.QLineEdit(self._pvs[key])
            pl.addRow(label, e)
            self._pv_edits[key] = e
        self._units_cmb = QtWidgets.QComboBox()
        self._units_cmb.addItems(["keV", "eV"])
        self._units_cmb.setCurrentText(self._pvs.get("energy_units", "keV"))
        pl.addRow("Energy units:", self._units_cmb)
        V.addWidget(pv_box)
        # Note: the cal-file energy range is exposed in the Energy panel of
        # the main GUI, so it's always visible without opening this dialog.
        # It is still stored in this same config JSON under "range_keV".

        # ── Table ───────────────────────────────────────────────────────
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Energy [eV]", "X [mm]", "Y [mm]", "Z [mm]",
             "QG V [mm]", "QG H [mm]"])
        self.table.horizontalHeader().setStretchLastSection(True)
        V.addWidget(self.table, 1)
        for p in cfg.get("points", []):
            # Legacy formats (all folded into 6-column [E, X, Y, Z, QGV, QGH]):
            #   len==4 old:     [E, ZP, X, Z]       (ZP→Z)
            #   len==5 old:     [E, ZP, X, Y, Z]    (ZP→Z)
            #   len==4 recent:  [E, X, Y, Z]
            #   len==6 current: [E, X, Y, Z, QGV, QGH]
            if len(p) == 4 and p[1] is not None and p[2] is not None and p[3] is None:
                p = [p[0], p[2], None, p[3] if p[3] is not None else p[1]]
            elif len(p) == 5:
                e, zp, x, y, z = p
                if z is None:
                    z = zp
                p = [e, x, y, z]
            # Extend to 6 columns.
            while len(p) < 6:
                p = list(p) + [None]
            e, x, y, z, qgv, qgh = p[0], p[1], p[2], p[3], p[4], p[5]
            if e is None:
                continue
            self._append_row(e, x, y, z, qgv, qgh)

        # ── Action buttons ──────────────────────────────────────────────
        H = QtWidgets.QHBoxLayout()
        bn_save = QtWidgets.QPushButton("Save current E + ZP")
        bn_save.setToolTip("Read the current energy RBV and ZP motor RBV via "
                           "caget, append a row.")
        bn_save.clicked.connect(self._on_save_current)
        bn_rm = QtWidgets.QPushButton("Remove selected")
        bn_rm.clicked.connect(self._on_remove)
        bn_clr = QtWidgets.QPushButton("Clear all")
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

    # ── Table helpers ───────────────────────────────────────────────────
    def _append_row(self, e_eV, x_mm=None, y_mm=None, z_mm=None,
                    qgv_mm=None, qgh_mm=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(f"{float(e_eV):.3f}"))
        for col, v in ((1, x_mm), (2, y_mm), (3, z_mm),
                       (4, qgv_mm), (5, qgh_mm)):
            if v is not None:
                self.table.setItem(r, col, QtWidgets.QTableWidgetItem(f"{float(v):.6f}"))

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
            pts.append([e, cell(1), cell(2), cell(3), cell(4), cell(5)])
        return pts

    def _collect_pvs(self):
        pvs = {k: w.text().strip() for k, w in self._pv_edits.items()}
        pvs["energy_units"] = self._units_cmb.currentText()
        return pvs

    # ── Button handlers ─────────────────────────────────────────────────
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
        x_val  = _read_motor_rbv(pvs["zp_x_pv"]) if pvs["zp_x_pv"] else None
        y_val  = _read_motor_rbv(pvs["zp_y_pv"]) if pvs["zp_y_pv"] else None
        z_val  = _read_motor_rbv(pvs["zp_z_pv"]) if pvs["zp_z_pv"] else None
        qgv    = _read_motor_rbv(pvs.get("qg_v_pv")) if pvs.get("qg_v_pv") else None
        qgh    = _read_motor_rbv(pvs.get("qg_h_pv")) if pvs.get("qg_h_pv") else None
        if all(v is None for v in (x_val, y_val, z_val, qgv, qgh)):
            QtWidgets.QMessageBox.warning(self, "Read failed",
                "Could not read any of the configured motor RBVs.")
            return
        self._append_row(e_val, x_val, y_val, z_val, qgv, qgh)

    def _on_remove(self):
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
        """Auto-save on any close (X button, Alt+F4, window-manager close)
        so deleting rows and just closing the dialog actually persists."""
        try:
            if not getattr(self, "_saved_in_session", False):
                self._do_save()
        except Exception as e:
            print(f"[CALIB] auto-save on close failed: {e}")
        super().closeEvent(event)

    def _do_save(self):
        # Preserve range_keV (set from the main GUI's Energy panel) across
        # this dialog's saves.
        prior = load_config()
        cfg = {
            "pvs": self._collect_pvs(),
            "points": self._collect_points(),
            "range_keV": prior.get("range_keV", DEFAULT_RANGE_KEV),
        }
        try:
            save_config(cfg)
            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save failed",
                f"Could not write {_CALIB_FILE}:\n{e}")
            return False


# Keep references on the QApplication so non-modal windows aren't GC'd the
# moment launch() returns.
_open_windows = []


def launch(parent=None):
    """Open the calibration window non-modally (main GUI stays responsive).

    Subsequent calls bring the existing window to the front instead of
    spawning duplicates."""
    # If a window is already open, raise it instead of making a second one.
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
