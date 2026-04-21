"""Zone-plate energy-calibration table for bl32-ID.

Stores (E_eV, ZP_mm, ZP_X_mm, ZP_Z_mm) tuples that callers (XANES 2D scans,
energy setpoint logic, etc.) can interpolate at run time. The table and the
PV config persist in ~/.bl_gui/bl32id_zp_calibration.json so they survive
restarts and are shared across machines whenever $HOME is on NFS.

Reads/writes PVs through `caget` / `caput` subprocesses to stay consistent
with the rest of bl_gui and to bound any CA wait with a timeout.
"""
import json
import os
import subprocess

from PyQt5 import QtCore, QtWidgets


_CALIB_FILE = os.path.expanduser("~/.bl_gui/bl32id_zp_calibration.json")

DEFAULT_PVS = {
    "energy_rb_pv": "32ida:BraggERdbkAO",
    "energy_units": "keV",
    "zp_motor_pv":  "32id:m1",
    "zp_x_pv":      "",
    "zp_z_pv":      "",
}


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


class XanesCalibDialog(QtWidgets.QDialog):
    """ZP energy calibration table. All PVs are user-editable — the defaults
    are sane for bl32-ID but any one can be overridden for a different
    setup. File format is forward-compatible with the xanes_gui table so a
    future migration can be a copy-paste."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZP Energy Calibration — bl32-ID")
        self.resize(760, 560)

        cfg = load_config()
        self._pvs = dict(DEFAULT_PVS); self._pvs.update(cfg.get("pvs", {}))

        V = QtWidgets.QVBoxLayout(self)

        # ── PV configuration ────────────────────────────────────────────
        pv_box = QtWidgets.QGroupBox("Sources (PVs)")
        pl = QtWidgets.QFormLayout(pv_box)
        self._pv_edits = {}
        for key, label in [
            ("energy_rb_pv", "Energy RBV PV:"),
            ("zp_motor_pv",  "ZP motor PV:"),
            ("zp_x_pv",      "ZP X motor PV:"),
            ("zp_z_pv",      "ZP Z motor PV:"),
        ]:
            e = QtWidgets.QLineEdit(self._pvs[key])
            pl.addRow(label, e)
            self._pv_edits[key] = e
        self._units_cmb = QtWidgets.QComboBox()
        self._units_cmb.addItems(["keV", "eV"])
        self._units_cmb.setCurrentText(self._pvs.get("energy_units", "keV"))
        pl.addRow("Energy units:", self._units_cmb)
        V.addWidget(pv_box)

        # ── Table ───────────────────────────────────────────────────────
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Energy [eV]", "ZP [mm]", "ZP X [mm]", "ZP Z [mm]"])
        self.table.horizontalHeader().setStretchLastSection(True)
        V.addWidget(self.table, 1)
        for p in cfg.get("points", []):
            e = p[0] if len(p) > 0 else None
            zp = p[1] if len(p) > 1 else None
            if e is None or zp is None:
                continue
            zpx = p[2] if len(p) > 2 else None
            zpz = p[3] if len(p) > 3 else None
            self._append_row(e, zp, zpx, zpz)

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
    def _append_row(self, e_eV, zp_mm, zpx_mm=None, zpz_mm=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(f"{float(e_eV):.3f}"))
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{float(zp_mm):.6f}"))
        if zpx_mm is not None:
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{float(zpx_mm):.6f}"))
        if zpz_mm is not None:
            self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(f"{float(zpz_mm):.6f}"))

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
            e = cell(0); zp = cell(1)
            if e is None or zp is None:
                continue
            pts.append([e, zp, cell(2), cell(3)])
        return pts

    def _collect_pvs(self):
        pvs = {k: w.text().strip() for k, w in self._pv_edits.items()}
        pvs["energy_units"] = self._units_cmb.currentText()
        return pvs

    # ── Button handlers ─────────────────────────────────────────────────
    def _on_save_current(self):
        pvs = self._collect_pvs()
        rb = pvs["energy_rb_pv"]; zp = pvs["zp_motor_pv"]
        if not rb or not zp:
            QtWidgets.QMessageBox.warning(self, "Missing PVs",
                "Set energy RBV PV and ZP motor PV first.")
            return
        e_val = _caget(rb)
        if e_val is None:
            QtWidgets.QMessageBox.warning(self, "Read failed",
                f"caget {rb} failed (timeout or unknown PV).")
            return
        if pvs["energy_units"] == "keV":
            e_val *= 1000.0
        zp_val = _read_motor_rbv(zp)
        if zp_val is None:
            QtWidgets.QMessageBox.warning(self, "Read failed",
                f"Could not read ZP motor RBV from {zp}.")
            return
        zpx_val = _read_motor_rbv(pvs["zp_x_pv"]) if pvs["zp_x_pv"] else None
        zpz_val = _read_motor_rbv(pvs["zp_z_pv"]) if pvs["zp_z_pv"] else None
        self._append_row(e_val, zp_val, zpx_val, zpz_val)

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
        cfg = {"pvs": self._collect_pvs(), "points": self._collect_points()}
        try:
            save_config(cfg)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save failed",
                f"Could not write {_CALIB_FILE}:\n{e}")
            return
        self.accept()


def launch(parent=None):
    """Open the calibration dialog modally."""
    dlg = XanesCalibDialog(parent)
    dlg.exec_()
