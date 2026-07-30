"""Snapshot browser — pick a past state and restore PVs from it.

Left panel: list of snapshots (timestamp + PV count). Select one
and the right panel populates with every PV in that snapshot.
Check the ones you want to restore, hit "Restore selected", confirm,
and each PV gets a bounded caput to its saved value.

Nothing is written to EPICS unless you explicitly click Restore.
"""
import os
import time
from typing import Dict, Optional

from PyQt5 import QtCore, QtWidgets

from . import snapshot as snap
from .pv import caput_bg


class SnapshotWindow(QtWidgets.QMainWindow):
    """Non-modal snapshot browser. One instance at a time — main
    window keeps a reference and reuses it on subsequent open calls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Snapshots — bl_gui")
        self.resize(1100, 720)

        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        L = QtWidgets.QHBoxLayout(central)
        L.setContentsMargins(6, 6, 6, 6); L.setSpacing(8)

        # ── Left: snapshot list ─────────────────────────────────────
        left = QtWidgets.QVBoxLayout(); left.setSpacing(4)
        left.addWidget(QtWidgets.QLabel(
            "<b>Snapshots</b> (newest first) — click to preview"))
        self.snap_table = QtWidgets.QTableWidget(0, 3)
        self.snap_table.setHorizontalHeaderLabels(["When", "PVs", "File"])
        self.snap_table.horizontalHeader().setStretchLastSection(True)
        self.snap_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        self.snap_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection)
        self.snap_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self.snap_table.itemSelectionChanged.connect(self._on_snap_selected)
        left.addWidget(self.snap_table, 1)

        row = QtWidgets.QHBoxLayout()
        bn_refresh = QtWidgets.QPushButton("Refresh")
        bn_refresh.clicked.connect(self._reload_snap_list)
        bn_take = QtWidgets.QPushButton("Take snapshot now")
        bn_take.setToolTip("Force an immediate snapshot regardless of the "
                           "hourly timer / change detection.")
        bn_take.clicked.connect(self._on_take_now)
        bn_delete = QtWidgets.QPushButton("Delete selected")
        bn_delete.setStyleSheet("color:#c0392b;")
        bn_delete.clicked.connect(self._on_delete)
        for b in (bn_refresh, bn_take, bn_delete):
            row.addWidget(b)
        left.addLayout(row)
        L.addLayout(left, 1)

        # ── Right: PV preview + restore ────────────────────────────
        right = QtWidgets.QVBoxLayout(); right.setSpacing(4)
        self.header_lbl = QtWidgets.QLabel(
            "<b>Preview</b> — select a snapshot on the left")
        right.addWidget(self.header_lbl)

        # Filter box so a snapshot with 400+ PVs stays browsable.
        flt_row = QtWidgets.QHBoxLayout()
        flt_row.addWidget(QtWidgets.QLabel("Filter:"))
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("substring on PV name…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        flt_row.addWidget(self.filter_edit, 1)
        chk_all = QtWidgets.QPushButton("Check visible")
        chk_all.clicked.connect(self._check_visible)
        flt_row.addWidget(chk_all)
        uncheck_all = QtWidgets.QPushButton("Uncheck all")
        uncheck_all.clicked.connect(self._uncheck_all)
        flt_row.addWidget(uncheck_all)
        right.addLayout(flt_row)

        self.pv_table = QtWidgets.QTableWidget(0, 3)
        self.pv_table.setHorizontalHeaderLabels(
            ["Restore", "PV", "Value at snapshot"])
        self.pv_table.horizontalHeader().setStretchLastSection(True)
        self.pv_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        self.pv_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Interactive)
        self.pv_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        right.addWidget(self.pv_table, 1)

        # Also restore the ancillary files. Off by default — restoring
        # the calibration JSON in particular can silently overwrite
        # in-progress work.
        aux = QtWidgets.QHBoxLayout()
        self.cb_regime = QtWidgets.QCheckBox("Restore regime file (nano/micro)")
        self.cb_calib = QtWidgets.QCheckBox(
            "Restore calibration JSON (WARNING: overwrites current)")
        aux.addWidget(self.cb_regime); aux.addWidget(self.cb_calib)
        aux.addStretch()
        right.addLayout(aux)

        act = QtWidgets.QHBoxLayout()
        act.addStretch()
        self.bn_restore = QtWidgets.QPushButton("Restore selected")
        self.bn_restore.setStyleSheet(
            "background:#1e5a8e;color:#fff;font:bold 10pt;"
            "padding:6px 14px;border-radius:3px;")
        self.bn_restore.clicked.connect(self._on_restore)
        act.addWidget(self.bn_restore)
        right.addLayout(act)

        L.addLayout(right, 2)

        self._current_snap: Optional[Dict] = None
        self._reload_snap_list()

    # ── Snapshot list ─────────────────────────────────────────────
    def _reload_snap_list(self):
        entries = snap.list_snapshots()
        self.snap_table.setRowCount(0)
        for path, offset, ts_epoch, n in entries:
            r = self.snap_table.rowCount()
            self.snap_table.insertRow(r)
            when = time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(ts_epoch)) if ts_epoch \
                   else f"{os.path.basename(path)}@{offset}"
            self.snap_table.setItem(r, 0, QtWidgets.QTableWidgetItem(when))
            self.snap_table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(n)))
            it_path = QtWidgets.QTableWidgetItem(
                f"{os.path.basename(path)} @ {offset}")
            # Stash (path, offset) so restore / delete / preview can
            # find the exact record inside the log file.
            it_path.setData(QtCore.Qt.UserRole, (path, offset))
            self.snap_table.setItem(r, 2, it_path)
        if self.snap_table.rowCount():
            self.snap_table.selectRow(0)

    def _selected_record(self) -> Optional[tuple]:
        r = self.snap_table.currentRow()
        if r < 0:
            return None
        it = self.snap_table.item(r, 2)
        return it.data(QtCore.Qt.UserRole) if it else None

    def _on_snap_selected(self):
        rec = self._selected_record()
        if not rec:
            self.pv_table.setRowCount(0)
            self._current_snap = None
            self.header_lbl.setText(
                "<b>Preview</b> — select a snapshot on the left")
            return
        path, offset = rec
        d = snap.load_snapshot_at(path, offset)
        if d is None:
            self.header_lbl.setText(
                f"<b>Preview</b> — failed to load {os.path.basename(path)}@{offset}")
            self.pv_table.setRowCount(0)
            self._current_snap = None
            return
        self._current_snap = d
        pvs = d.get("pvs") or {}
        self.header_lbl.setText(
            f"<b>Preview</b> — {os.path.basename(path)}@{offset} — {len(pvs)} PVs")
        self.pv_table.setRowCount(0)
        for pv in sorted(pvs.keys()):
            r = self.pv_table.rowCount()
            self.pv_table.insertRow(r)
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(chk.flags() | QtCore.Qt.ItemIsUserCheckable)
            chk.setCheckState(QtCore.Qt.Unchecked)
            self.pv_table.setItem(r, 0, chk)
            self.pv_table.setItem(r, 1, QtWidgets.QTableWidgetItem(pv))
            val_item = QtWidgets.QTableWidgetItem(str(pvs[pv]))
            self.pv_table.setItem(r, 2, val_item)
        self._apply_filter(self.filter_edit.text())

    # ── PV filter ─────────────────────────────────────────────────
    def _apply_filter(self, text):
        text = (text or "").strip().lower()
        for r in range(self.pv_table.rowCount()):
            it = self.pv_table.item(r, 1)
            visible = True if not text else (text in (it.text().lower() if it else ""))
            self.pv_table.setRowHidden(r, not visible)

    def _check_visible(self):
        for r in range(self.pv_table.rowCount()):
            if self.pv_table.isRowHidden(r):
                continue
            chk = self.pv_table.item(r, 0)
            if chk:
                chk.setCheckState(QtCore.Qt.Checked)

    def _uncheck_all(self):
        for r in range(self.pv_table.rowCount()):
            chk = self.pv_table.item(r, 0)
            if chk:
                chk.setCheckState(QtCore.Qt.Unchecked)

    # ── Actions ───────────────────────────────────────────────────
    def _on_take_now(self):
        """Force an immediate snapshot using the parent window's PVEngine."""
        win = self.parent()
        if win is None or not hasattr(win, "_pve"):
            QtWidgets.QMessageBox.warning(
                self, "Cannot snapshot", "Parent window has no PVEngine.")
            return
        pv_names = list(win._pve._channels.keys())
        data = snap.take_snapshot(pv_names, win._pve.get)
        path, offset = snap.save_snapshot(data)
        print(f"[SNAPSHOT] manual save -> {path}@{offset}  "
              f"({len(data.get('pvs') or {})} PVs)")
        self._reload_snap_list()

    def _on_delete(self):
        rec = self._selected_record()
        if not rec:
            return
        path, offset = rec
        if QtWidgets.QMessageBox.question(
                self, "Delete snapshot",
                f"Delete record at {os.path.basename(path)}@{offset} ?\n"
                "(Other records in this log file stay intact.)"
                ) != QtWidgets.QMessageBox.Yes:
            return
        if not snap.delete_snapshot(path, offset):
            QtWidgets.QMessageBox.warning(
                self, "Delete failed",
                f"Could not remove record at {os.path.basename(path)}@{offset}.")
        self._reload_snap_list()

    def _on_restore(self):
        if self._current_snap is None:
            return
        pvs = self._current_snap.get("pvs") or {}
        to_restore = []
        for r in range(self.pv_table.rowCount()):
            chk = self.pv_table.item(r, 0)
            if chk and chk.checkState() == QtCore.Qt.Checked:
                pv = self.pv_table.item(r, 1).text()
                val = pvs.get(pv)
                if val is not None:
                    to_restore.append((pv, val))
        aux_msg = []
        if self.cb_regime.isChecked() and self._current_snap.get("regime"):
            aux_msg.append("regime file")
        if self.cb_calib.isChecked() and self._current_snap.get("calib"):
            aux_msg.append("calibration JSON")
        if not to_restore and not aux_msg:
            QtWidgets.QMessageBox.information(
                self, "Nothing to restore",
                "Check at least one PV row (or one auxiliary file) first.")
            return
        parts = [f"{len(to_restore)} PV(s)"]
        parts.extend(aux_msg)
        if QtWidgets.QMessageBox.question(
                self, "Confirm restore",
                "About to restore: " + ", ".join(parts) + ".\n\n"
                "Motor / setpoint caputs may cause hardware moves.\n"
                "Continue?") != QtWidgets.QMessageBox.Yes:
            return
        # Fire the caputs. caput_bg is non-blocking; each has its own
        # subprocess timeout so a bad PV doesn't stall the loop.
        for pv, val in to_restore:
            print(f"[SNAPSHOT] restore caput {pv} = {val!r}")
            caput_bg(pv, val)
        if "regime file" in aux_msg:
            snap.restore_regime_file(self._current_snap.get("regime"))
            print("[SNAPSHOT] restored regime file")
        if "calibration JSON" in aux_msg:
            snap.restore_calib_file(self._current_snap.get("calib"))
            print("[SNAPSHOT] restored calibration JSON")
        QtWidgets.QMessageBox.information(
            self, "Restore fired",
            f"Sent {len(to_restore)} caput(s) + {len(aux_msg)} file "
            "restore(s). Check the terminal for per-PV log lines.")


_win_ref = []


def launch(parent=None) -> "SnapshotWindow":
    """Open (or raise) the snapshot browser as a non-modal window."""
    for w in list(_win_ref):
        if w.isVisible():
            w.raise_(); w.activateWindow()
            return w
        _win_ref.remove(w)
    w = SnapshotWindow(parent=parent)
    _win_ref.append(w)
    w.destroyed.connect(lambda _=None, x=w:
                        _win_ref.remove(x) if x in _win_ref else None)
    w.show()
    return w
