"""Panel outliner — a dockable tree view listing every tab and its
panels. Complements the property inspector on the left side of the
main window: click a panel here to select it (and populate the
inspector), see the whole layout at a glance when the canvas is
crowded.

The outliner rebuilds itself from self._panels + self._panel_tab_map
whenever Win.refresh_outliner() is called (after add/remove/move/
rename ops or after undo/redo replay).
"""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets


_STYLE = """
QDockWidget {
    color: #e0e0e0; font-weight: bold; font-size: 9pt;
}
QDockWidget::title {
    background: #1c1c1c; padding: 4px 8px; color: #73dfff;
    border-bottom: 1px solid #2d2d2d;
}
QWidget#OutlinerBody { background: #1c1c1c; color: #e0e0e0; }
QTreeWidget {
    background: #1c1c1c; color: #e0e0e0;
    border: none; font-size: 9pt;
    show-decoration-selected: 1;
}
QTreeWidget::item { padding: 2px 4px; }
QTreeWidget::item:selected {
    background: #1e5a8e; color: #ffffff;
}
QTreeWidget::branch:has-children { }
QHeaderView::section {
    background: #2d2d2d; color: #b0b0b0; padding: 4px;
    border: none; border-bottom: 1px solid #404040;
    font-size: 9pt;
}
"""


# UserRole payloads on tree items:
_ROLE_KIND = QtCore.Qt.UserRole + 1   # 'tab' | 'panel'
_ROLE_KEY  = QtCore.Qt.UserRole + 2   # panel key (only for 'panel' items)


class PanelOutliner(QtWidgets.QDockWidget):
    """Left-side dock: tab → panels tree. Selecting a panel here
    marks it as selected in Win (same as clicking the panel itself)."""

    def __init__(self, win, parent=None):
        super().__init__("Panels", parent)
        self._win = win
        self.setObjectName("PanelOutlinerDock")
        self.setStyleSheet(_STYLE)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea
                             | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)

        body = QtWidgets.QWidget()
        body.setObjectName("OutlinerBody")
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(["Panel"])
        self._tree.setColumnCount(1)
        self._tree.setIndentation(14)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_tree_selected)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        lay.addWidget(self._tree)

        self.setWidget(body)
        self.refresh()

    # ── build/refresh ────────────────────────────────────────────────

    def refresh(self):
        """Rebuild the tree from Win's current state. Cheap enough
        to call after any layout mutation."""
        # Preserve current selection through the rebuild.
        selected_key = self._current_selected_key()
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            win = self._win
            tab_names = (win._tab_names() if hasattr(win, "_tab_names")
                         else list({v for v in win._panel_tab_map.values()}))
            # Group panels by tab in the tab order.
            panels_by_tab: dict[str, list[str]] = {t: [] for t in tab_names}
            for key, tab in win._panel_tab_map.items():
                if tab in panels_by_tab:
                    panels_by_tab[tab].append(key)
                else:
                    # Orphan tab — still show it.
                    panels_by_tab.setdefault(tab, []).append(key)
            for tab in tab_names + [t for t in panels_by_tab if t not in tab_names]:
                keys = panels_by_tab.get(tab, [])
                tab_item = QtWidgets.QTreeWidgetItem(
                    self._tree, [f"{tab}  ({len(keys)})"])
                tab_item.setData(0, _ROLE_KIND, "tab")
                tab_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#73dfff")))
                fnt = tab_item.font(0); fnt.setBold(True); tab_item.setFont(0, fnt)
                # Sort panel keys alphabetically for consistent ordering.
                for key in sorted(keys):
                    p = win._panels.get(key)
                    if p is None:
                        continue
                    title = p.title_text() or key
                    child = QtWidgets.QTreeWidgetItem(tab_item, [title])
                    child.setData(0, _ROLE_KIND, "panel")
                    child.setData(0, _ROLE_KEY, key)
                    # Tooltip shows the panel key so a rename doesn't
                    # hide which underlying node this row represents.
                    child.setToolTip(0, key)
                    if key == selected_key:
                        self._tree.setCurrentItem(child)
                tab_item.setExpanded(True)
        finally:
            self._tree.blockSignals(False)

    def _current_selected_key(self):
        items = self._tree.selectedItems()
        if not items:
            return None
        it = items[0]
        if it.data(0, _ROLE_KIND) != "panel":
            return None
        return it.data(0, _ROLE_KEY)

    def select_key(self, panel_key: str | None):
        """Highlight the row for a given panel key. `None` clears
        selection. Called by Win after selection changes elsewhere
        (panel click, inspector delete, undo/redo)."""
        if panel_key is None:
            self._tree.clearSelection()
            return
        it = self._find_item(panel_key)
        if it is not None:
            self._tree.blockSignals(True)
            try:
                self._tree.setCurrentItem(it)
            finally:
                self._tree.blockSignals(False)

    def _find_item(self, panel_key: str):
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            tab_it = root.child(i)
            for j in range(tab_it.childCount()):
                ch = tab_it.child(j)
                if ch.data(0, _ROLE_KEY) == panel_key:
                    return ch
        return None

    # ── interaction handlers ────────────────────────────────────────

    def _on_tree_selected(self):
        key = self._current_selected_key()
        if key is None:
            return
        win = self._win
        # Switch to the tab that hosts this panel so it's actually
        # visible when the user clicks it in the outliner.
        tab = win._panel_tab_map.get(key)
        if tab:
            for i in range(win.tab_widget.count()):
                if win.tab_widget.tabText(i) == tab:
                    if win.tab_widget.currentIndex() != i:
                        win.tab_widget.setCurrentIndex(i)
                    break
        # Raise the panel so it's obvious which one got selected.
        p = win._panels.get(key)
        if p is not None:
            p.raise_()
        # Update inspector.
        if hasattr(win, "_select_panel"):
            win._select_panel(key)

    def _on_item_double_clicked(self, item, _column):
        """Double-click on a panel row: same as single-click select
        (already handled), but also brings focus back to the canvas
        by activating the tab widget."""
        if item.data(0, _ROLE_KIND) == "panel":
            self._win.tab_widget.setFocus()
