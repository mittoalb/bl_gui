"""Property inspector — a dockable side panel that shows the properties
of the currently selected Panel and lets you edit them live.

Selection model: whenever the user clicks a Panel in edit mode, the
window's `_selected_panel_key` is set. The inspector subscribes to
window signals and refreshes.

For now the inspector edits:
    * Panel title (QLineEdit)
    * Panel title font size (QSpinBox)
    * Panel geometry: x, y, w, h (QSpinBoxes)
    * A "Delete panel" action button

Each edit records an undo op via Win._record_op so the history stack
covers both direct manipulation and inspector-driven changes.
"""
from __future__ import annotations

import re

from PyQt5 import QtCore, QtGui, QtWidgets


_STYLE = """
QDockWidget {
    color: #e0e0e0; font-weight: bold; font-size: 9pt;
    titlebar-close-icon: none; titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #1c1c1c; padding: 4px 8px; color: #73dfff;
    border-bottom: 1px solid #2d2d2d;
}
QWidget#InspectorBody {
    background: #1c1c1c; color: #e0e0e0;
}
QLabel { color: #b0b0b0; font-size: 9pt; }
QLabel#SelHeader { color: #73dfff; font-weight: bold; font-size: 10pt;
                   padding: 2px 0; }
QLabel#SelSub    { color: #808080; font-size: 8pt; }
QLineEdit, QSpinBox, QComboBox {
    background: #2c3e50; color: #ecf0f1;
    border: 1px solid #3498db; border-radius: 3px;
    padding: 3px 6px; font-size: 9pt;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    background: #34495e; border-color: #5dade2;
}
QPushButton {
    background: #2d2d2d; color: #e0e0e0; font-size: 9pt;
    border: 1px solid #404040; border-radius: 3px; padding: 5px 10px;
}
QPushButton:hover { background: #3a3a3a; }
QPushButton#DangerBtn {
    background: #7f2417; color: #fff; border-color: #c0392b;
}
QPushButton#DangerBtn:hover { background: #a13023; }
"""


class PropertyInspector(QtWidgets.QDockWidget):
    """Right-side dock widget showing properties of the selected panel."""

    def __init__(self, win, parent=None):
        super().__init__("Properties", parent)
        self._win = win
        self._current_key: str | None = None
        # Guard flag — set while we programmatically populate fields
        # from a newly selected panel, so the field's editingFinished
        # signal doesn't fire a spurious edit op back at the panel.
        self._syncing = False
        self.setObjectName("PropertyInspectorDock")
        self.setStyleSheet(_STYLE)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea
                             | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)

        body = QtWidgets.QWidget()
        body.setObjectName("InspectorBody")
        outer = QtWidgets.QVBoxLayout(body)
        outer.setContentsMargins(10, 10, 10, 10); outer.setSpacing(8)

        self._sel_header = QtWidgets.QLabel("No panel selected")
        self._sel_header.setObjectName("SelHeader")
        outer.addWidget(self._sel_header)
        self._sel_sub = QtWidgets.QLabel("")
        self._sel_sub.setObjectName("SelSub")
        outer.addWidget(self._sel_sub)

        # Property form (hidden until a panel is selected).
        self._form_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self._form_widget)
        form.setContentsMargins(0, 6, 0, 0); form.setSpacing(6)

        self._title_edit = QtWidgets.QLineEdit()
        self._title_edit.editingFinished.connect(self._on_title_committed)
        form.addRow("Title:", self._title_edit)

        self._font_spin = QtWidgets.QSpinBox()
        self._font_spin.setRange(4, 30); self._font_spin.setSuffix(" pt")
        self._font_spin.editingFinished.connect(self._on_font_committed)
        form.addRow("Title font:", self._font_spin)

        # Geometry: two rows, x/y then w/h.
        self._x_spin = QtWidgets.QSpinBox(); self._x_spin.setRange(0, 10000); self._x_spin.setSuffix(" px")
        self._y_spin = QtWidgets.QSpinBox(); self._y_spin.setRange(0, 10000); self._y_spin.setSuffix(" px")
        self._w_spin = QtWidgets.QSpinBox(); self._w_spin.setRange(40, 10000); self._w_spin.setSuffix(" px")
        self._h_spin = QtWidgets.QSpinBox(); self._h_spin.setRange(40, 10000); self._h_spin.setSuffix(" px")
        for s in (self._x_spin, self._y_spin, self._w_spin, self._h_spin):
            s.editingFinished.connect(self._on_geometry_committed)
        xy = QtWidgets.QWidget()
        xy_l = QtWidgets.QHBoxLayout(xy); xy_l.setContentsMargins(0, 0, 0, 0); xy_l.setSpacing(6)
        xy_l.addWidget(QtWidgets.QLabel("x")); xy_l.addWidget(self._x_spin)
        xy_l.addWidget(QtWidgets.QLabel("y")); xy_l.addWidget(self._y_spin)
        form.addRow("Position:", xy)
        wh = QtWidgets.QWidget()
        wh_l = QtWidgets.QHBoxLayout(wh); wh_l.setContentsMargins(0, 0, 0, 0); wh_l.setSpacing(6)
        wh_l.addWidget(QtWidgets.QLabel("w")); wh_l.addWidget(self._w_spin)
        wh_l.addWidget(QtWidgets.QLabel("h")); wh_l.addWidget(self._h_spin)
        form.addRow("Size:", wh)

        self._tab_combo = QtWidgets.QComboBox()
        self._tab_combo.currentTextChanged.connect(self._on_tab_committed)
        form.addRow("Tab:", self._tab_combo)

        outer.addWidget(self._form_widget)
        outer.addStretch(1)

        # Danger zone
        self._delete_btn = QtWidgets.QPushButton("Delete Panel")
        self._delete_btn.setObjectName("DangerBtn")
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        outer.addWidget(self._delete_btn)

        self._form_widget.setVisible(False)
        self._delete_btn.setVisible(False)
        self.setWidget(body)

    # ── public API called by Win when selection changes ─────────────

    def show_panel(self, panel_key: str | None):
        """Point the inspector at the given panel key. `None` shows
        the empty state."""
        self._current_key = panel_key
        if panel_key is None or panel_key not in self._win._panels:
            self._sel_header.setText("No panel selected")
            self._sel_sub.setText("Click a panel while in edit mode to inspect it.")
            self._form_widget.setVisible(False)
            self._delete_btn.setVisible(False)
            return
        p = self._win._panels[panel_key]
        self._sel_header.setText(p.title_text() or panel_key)
        self._sel_sub.setText(panel_key)
        self._form_widget.setVisible(True)
        self._delete_btn.setVisible(True)
        self._syncing = True
        try:
            self._title_edit.setText(p.title_text())
            m = re.search(r'(\d+)\s*pt', p._title.styleSheet())
            self._font_spin.setValue(int(m.group(1)) if m else 12)
            g = p.geometry()
            self._x_spin.setValue(g.x()); self._y_spin.setValue(g.y())
            self._w_spin.setValue(g.width()); self._h_spin.setValue(g.height())
            # Populate tab combo with all current tab names, select current.
            self._tab_combo.blockSignals(True)
            self._tab_combo.clear()
            if hasattr(self._win, "_tab_names"):
                for t in self._win._tab_names():
                    self._tab_combo.addItem(t)
            cur_tab = self._win._panel_tab_map.get(panel_key, "")
            idx = self._tab_combo.findText(cur_tab)
            if idx >= 0:
                self._tab_combo.setCurrentIndex(idx)
            self._tab_combo.blockSignals(False)
        finally:
            self._syncing = False

    def refresh(self):
        """Re-read the current panel's state (used after undo/redo)."""
        self.show_panel(self._current_key)

    # ── field commit handlers ───────────────────────────────────────

    def _panel(self):
        if self._current_key is None:
            return None
        return self._win._panels.get(self._current_key)

    def _on_title_committed(self):
        if self._syncing:
            return
        p = self._panel()
        if p is None:
            return
        new = self._title_edit.text()
        old = p.title_text()
        if new == old or not new:
            return
        p._title.setText(new); p._title.adjustSize()
        self._sel_header.setText(new)
        win = self._win
        if hasattr(win, "_record_op"):
            def _apply(txt, p=p, insp=self):
                p._title.setText(txt); p._title.adjustSize()
                if insp._current_key == p.key:
                    insp._sel_header.setText(txt)
                    insp._title_edit.blockSignals(True)
                    insp._title_edit.setText(txt)
                    insp._title_edit.blockSignals(False)
            win._record_op(
                f"Rename panel {old!r} → {new!r}",
                lambda t=old: _apply(t),
                lambda t=new: _apply(t))

    def _on_font_committed(self):
        if self._syncing:
            return
        p = self._panel()
        if p is None:
            return
        val = self._font_spin.value()
        m = re.search(r'(\d+)\s*pt', p._title.styleSheet())
        cur = int(m.group(1)) if m else 12
        if val == cur:
            return
        def _apply(pt, p=p):
            p._title.setStyleSheet(
                f"color: #73dfff; font-weight:bold;font-size:{pt}pt; "
                f"background: transparent; padding: 2px 6px;")
            p._title.adjustSize()
        _apply(val)
        win = self._win
        if hasattr(win, "_record_op"):
            win._record_op(
                f"Title font {p.title_text()!r}: {cur}pt → {val}pt",
                lambda pt=cur: _apply(pt),
                lambda pt=val: _apply(pt))

    def _on_geometry_committed(self):
        if self._syncing:
            return
        p = self._panel()
        if p is None:
            return
        before = QtCore.QRect(p.geometry())
        new_rect = QtCore.QRect(
            self._x_spin.value(), self._y_spin.value(),
            self._w_spin.value(), self._h_spin.value())
        if before == new_rect:
            return
        p.setGeometry(new_rect)
        win = self._win
        if hasattr(win, "_record_op"):
            win._record_op(
                f"Geometry {p.title_text()!r}",
                lambda p=p, r=QtCore.QRect(before): p.setGeometry(r),
                lambda p=p, r=QtCore.QRect(new_rect): p.setGeometry(r))

    def _on_tab_committed(self, tab_name: str):
        if self._syncing or not tab_name:
            return
        p = self._panel()
        if p is None:
            return
        old_tab = self._win._panel_tab_map.get(self._current_key, "")
        if tab_name == old_tab:
            return
        # Delegate to Win's existing move-to-tab helper (handles key
        # rewrite, canvas reparent, tracking updates). It doesn't
        # currently record undo — wrap the call to do so ourselves.
        win = self._win
        if not hasattr(win, "_move_panel_to_tab"):
            return
        key_before = self._current_key
        win._move_panel_to_tab(key_before, tab_name)
        # After move, the panel key may have changed. Update selection.
        new_key = None
        for k, tp in list(win._panels.items()):
            if tp is p:
                new_key = k; break
        self._current_key = new_key
        if hasattr(win, "_record_op"):
            def _undo(k=new_key, prev=old_tab):
                if k and hasattr(win, "_move_panel_to_tab"):
                    win._move_panel_to_tab(k, prev)
            def _redo(k=key_before, tgt=tab_name):
                if hasattr(win, "_move_panel_to_tab"):
                    win._move_panel_to_tab(k, tgt)
            win._record_op(
                f"Move panel {p.title_text()!r} → tab {tab_name!r}",
                _undo, _redo)

    def _on_delete_clicked(self):
        if self._current_key is None:
            return
        win = self._win
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Panel",
            f"Delete panel '{self._sel_header.text()}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        # _remove_panel already records its own undo op.
        key = self._current_key
        win._remove_panel(key, record=True)
        self.show_panel(None)
