"""Edit-mode input guard and drag-to-reorder for motor cards and buttons.

While edit mode is on, `EditModeGuard` (an application event filter)
owns every left click, key press and wheel event aimed at anything
inside a Panel, so no widget can fire a caput / shell action while the
user is arranging the layout. A left press on a motor card or a saved
CfgButton starts a drag; a press anywhere else in a panel is forwarded
to the Panel's own move/resize handling. Right clicks pass through so
the edit context menus keep working.

Motor cards can be dropped into any panel; buttons only reorder within
their own panel. Items always snap into the panel's existing layout.
"""
from PyQt5 import QtCore, QtWidgets

from .motor import MC


def enclosing_panel(w):
    from .widgets import Panel
    while w is not None:
        if isinstance(w, Panel):
            return w
        w = w.parentWidget()
    return None


# ── layout helpers ──────────────────────────────────────────────────────

def layout_containing(lay, w):
    """The layout (possibly nested) whose direct item is widget `w`."""
    if lay is None:
        return None
    for i in range(lay.count()):
        it = lay.itemAt(i)
        cw = it.widget()
        if cw is w:
            return lay
        sub = it.layout()
        if sub is not None:
            found = layout_containing(sub, w)
            if found is not None:
                return found
        elif (cw is not None and not isinstance(cw, MC)
              and cw.layout() is not None and cw.isAncestorOf(w)):
            found = layout_containing(cw.layout(), w)
            if found is not None:
                return found
    return None


def slot_of(lay, w):
    """Position of `w` in `lay`, in the unit `insert_at` takes
    (item index for box layouts, row for form / grid layouts)."""
    if isinstance(lay, QtWidgets.QFormLayout):
        return lay.getWidgetPosition(w)[0]
    if isinstance(lay, QtWidgets.QGridLayout):
        return lay.getItemPosition(lay.indexOf(w))[0]
    return lay.indexOf(w)


def detach(lay, w):
    """Take `w` out of `lay` without deleting it."""
    if isinstance(lay, QtWidgets.QFormLayout):
        lay.takeRow(w)
    else:
        lay.removeWidget(w)


def _end_index(box):
    """Index just before any trailing stretch / spacer items."""
    n = box.count()
    while n > 0 and box.itemAt(n - 1).spacerItem() is not None:
        n -= 1
    return n


def insert_at(lay, pos, w):
    """Insert `w` into `lay` at `pos` (None = end, before trailing
    stretch). Grid layouts always append a full-width row."""
    if isinstance(lay, QtWidgets.QBoxLayout):
        lay.insertWidget(_end_index(lay) if pos is None else pos, w)
    elif isinstance(lay, QtWidgets.QFormLayout):
        if pos is None or pos >= lay.rowCount():
            lay.addRow(w)
        else:
            lay.insertRow(pos, w)
    elif isinstance(lay, QtWidgets.QGridLayout):
        lay.addWidget(w, lay.rowCount(), 0, 1, max(1, lay.columnCount()))
    else:
        lay.addWidget(w)


def _is_horizontal(lay):
    return (isinstance(lay, QtWidgets.QBoxLayout)
            and lay.direction() in (QtWidgets.QBoxLayout.LeftToRight,
                                    QtWidgets.QBoxLayout.RightToLeft))


def _rect_in(panel, w):
    return QtCore.QRect(w.mapTo(panel, QtCore.QPoint(0, 0)), w.size())


# ── motor cards ─────────────────────────────────────────────────────────

def mcs_in_order(panel):
    """Motor cards of `panel` in on-screen (layout) order."""
    out = []

    def walk(lay):
        for i in range(lay.count()):
            it = lay.itemAt(i)
            w = it.widget()
            if isinstance(w, MC):
                out.append(w)
            elif it.layout() is not None:
                walk(it.layout())
            elif w is not None and w.layout() is not None:
                walk(w.layout())

    if panel.layout() is not None:
        walk(panel.layout())
    seen = {id(m) for m in out}
    out.extend(m for m in panel.findChildren(MC) if id(m) not in seen)
    return out


def _find_mc_container(panel, exclude=None):
    """Layout holding the panel's motor cards, or its top layout. Pure."""
    for m in mcs_in_order(panel):
        if m is exclude:
            continue
        lay = layout_containing(panel.layout(), m)
        if lay is not None:
            return lay
    return panel.layout()


def mc_container(panel, exclude=None):
    """Like _find_mc_container, but makes the panel ready to take a motor
    card: installs a row layout when there is none, and turns an empty
    "+ Panel" column into a row (the loader rebuilds a motor-only panel
    as a row, so this keeps runtime and reload identical)."""
    lay = _find_mc_container(panel, exclude)
    if lay is not None and lay is not panel.layout():
        return lay
    if lay is None or (isinstance(lay, QtWidgets.QVBoxLayout) and not any(
            lay.itemAt(i).widget() is not None for i in range(lay.count()))):
        if lay is not None:
            QtWidgets.QWidget().setLayout(lay)   # discard the empty layout
        lay = QtWidgets.QHBoxLayout()
        lay.setContentsMargins(6, 22, 6, 6)
        lay.setSpacing(3)
        lay.addStretch()
        panel.setLayout(lay)
    return lay


def _mc_siblings(panel, lay, exclude):
    return [m for m in mcs_in_order(panel)
            if m is not exclude and layout_containing(panel.layout(), m) is lay]


def mc_index(panel, mc):
    """Index of `mc` among the motor cards of its own container."""
    lay = layout_containing(panel.layout(), mc)
    sibs = [m for m in mcs_in_order(panel)
            if layout_containing(panel.layout(), m) is lay]
    return sibs.index(mc) if mc in sibs else len(sibs)


def _drop_index(panel, lay, items, gpos):
    """How many of `items` come before the cursor along `lay`'s axis."""
    p = panel.mapFromGlobal(gpos)
    k = 0
    for w in items:
        r = _rect_in(panel, w)
        if isinstance(lay, QtWidgets.QGridLayout):
            before = r.bottom() < p.y() or (
                r.top() <= p.y() <= r.bottom() and r.center().x() < p.x())
        elif _is_horizontal(lay):
            before = r.center().x() < p.x()
        else:
            before = r.center().y() < p.y()
        k += bool(before)
    return k


def _hint_for(panel, lay, items, k):
    """Drop-indicator line (panel coords) before items[k] / after last."""
    if not items:
        return ("panel",)
    horizontal = _is_horizontal(lay)
    if isinstance(lay, QtWidgets.QGridLayout):
        horizontal = True
    if k < len(items):
        r = _rect_in(panel, items[k])
        edge = r.left() - 2 if horizontal else r.top() - 2
    else:
        r = _rect_in(panel, items[-1])
        edge = r.right() + 2 if horizontal else r.bottom() + 2
    if horizontal:
        return ("line", edge, r.top(), edge, r.bottom())
    return ("line", r.left(), edge, r.right(), edge)


def move_mc(win, mc, target, k):
    """Move `mc` into `target` as its k-th motor card."""
    src = enclosing_panel(mc)
    if src is not None:
        src_lay = layout_containing(src.layout(), mc)
        if src_lay is not None:
            detach(src_lay, mc)
    lay = mc_container(target, exclude=mc)
    sibs = _mc_siblings(target, lay, mc)
    if k < len(sibs):
        pos = slot_of(lay, sibs[k])
    elif sibs:
        pos = slot_of(lay, sibs[-1]) + 1
    else:
        pos = None
    insert_at(lay, pos, mc)
    mc.set_edit_mode(bool(getattr(win, "_edit_mode", False)))
    mc.show()
    touched = getattr(win, "_mc_touched", None)
    if touched is not None:
        for p in (src, target):
            if p is not None:
                touched.add(p.key)


# ── buttons ─────────────────────────────────────────────────────────────

def _button_group(panel, btn):
    """(layout, saved buttons sharing btn's layout, in on-screen order)."""
    lay = layout_containing(panel.layout(), btn)
    group = [b for b in panel.custom_buttons
             if layout_containing(panel.layout(), b) is lay]
    if isinstance(lay, QtWidgets.QGridLayout):
        group.sort(key=lambda b: lay.getItemPosition(lay.indexOf(b))[:2])
    elif lay is not None:
        group.sort(key=lambda b: slot_of(lay, b))
    return lay, group


def apply_button_order(panel, order):
    """Rearrange `order` (buttons sharing one layout) into the slots they
    currently occupy, and mirror the order into panel.custom_buttons so
    the layout save/restore keeps it. Returns False if unsupported."""
    if not order:
        return False
    lay = layout_containing(panel.layout(), order[0])
    if isinstance(lay, QtWidgets.QGridLayout):
        cells = sorted(lay.getItemPosition(lay.indexOf(b))[:2] for b in order)
        for b in order:
            lay.removeWidget(b)
        for b, (r, c) in zip(order, cells):
            lay.addWidget(b, r, c)
    elif isinstance(lay, QtWidgets.QBoxLayout):
        slots = sorted(lay.indexOf(b) for b in order)
        for b in order:
            lay.removeWidget(b)
        for b, i in zip(order, slots):
            lay.insertWidget(i, b)
    else:
        return False
    ids = {id(b) for b in order}
    seq = iter(order)
    panel.custom_buttons = [next(seq) if id(b) in ids else b
                            for b in panel.custom_buttons]
    return True


# ── the guard ───────────────────────────────────────────────────────────

_MOUSE = (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
          QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.MouseMove)
_BLOCK = (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease,
          QtCore.QEvent.Wheel, QtCore.QEvent.MouseButtonDblClick)


class EditModeGuard(QtCore.QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._ghost = None
        self._reset()

    def _reset(self):
        self._mode = None          # None | "panel" | "item"
        self._panel = None
        self._item = None
        self._press = None
        self._grab = QtCore.QPoint()
        self._dragging = False
        self._hint_panel = None

    def cancel(self):
        if self._mode == "panel" and self._panel is not None:
            self._panel._drag = self._panel._resize = False
        self._clear_feedback()
        self._reset()

    # ── filter ──────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in _MOUSE and t not in _BLOCK:
            return False
        if self._mode is not None:
            if t == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_Escape:
                self.cancel()
                return True
            if t in (QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonRelease):
                return self._continue(ev)
        if t == QtCore.QEvent.MouseMove:
            return False           # hover; nothing pressed
        if not obj.isWidgetType() or obj.window() is not self.win:
            return False
        panel = enclosing_panel(obj)
        if panel is None or obj is panel or self._always_enabled(obj, panel):
            return False
        if t in _BLOCK:
            return True
        if t == QtCore.QEvent.MouseButtonRelease:
            return ev.button() != QtCore.Qt.RightButton
        # MouseButtonPress
        if ev.button() == QtCore.Qt.RightButton:
            return False           # context menus stay available
        if ev.button() != QtCore.Qt.LeftButton:
            return True
        item = self._draggable(obj, panel)
        self._panel = panel
        if item is not None:
            self._mode = "item"
            self._item = item
            self._press = ev.globalPos()
            self._grab = item.mapFromGlobal(ev.globalPos())
        else:
            self._mode = "panel"
            panel.mousePressEvent(self._mapped(ev, panel))
        return True

    @staticmethod
    def _always_enabled(obj, panel):
        w = obj
        while w is not None and w is not panel:
            if w.property("_bl_gui_always_enabled"):
                return True
            w = w.parentWidget()
        return False

    @staticmethod
    def _draggable(obj, panel):
        from .widgets import CfgButton
        w = obj
        while w is not None and w is not panel:
            if isinstance(w, MC):
                return w
            if isinstance(w, CfgButton):
                return w if any(b is w for b in panel.custom_buttons) else None
            w = w.parentWidget()
        return None

    @staticmethod
    def _mapped(ev, w):
        return type(ev)(ev.type(), w.mapFromGlobal(ev.globalPos()),
                        ev.globalPos(), ev.button(), ev.buttons(),
                        ev.modifiers())

    def _continue(self, ev):
        t = ev.type()
        try:
            if self._mode == "panel":
                if t == QtCore.QEvent.MouseMove:
                    self._panel.mouseMoveEvent(self._mapped(ev, self._panel))
                    return True
                if ev.button() != QtCore.Qt.LeftButton:
                    return True
                self._panel.mouseReleaseEvent(self._mapped(ev, self._panel))
                self._reset()
                return True
            # item
            if t == QtCore.QEvent.MouseMove:
                if not self._dragging and (ev.globalPos() - self._press).manhattanLength() \
                        >= QtWidgets.QApplication.startDragDistance():
                    self._start_drag()
                if self._dragging:
                    self._track(ev.globalPos())
                return True
            if ev.button() != QtCore.Qt.LeftButton:
                return True
            if self._dragging:
                self._drop(ev.globalPos())
            else:
                self._click()
            self._clear_feedback()
            self._reset()
            return True
        except RuntimeError:       # a widget died mid-gesture
            self._clear_feedback()
            self._reset()
            return True

    # ── click / drag ────────────────────────────────────────────────
    def _click(self):
        if isinstance(self._item, MC) and hasattr(self.win, "_select_widget"):
            self.win._select_widget(self._item)
        elif hasattr(self.win, "_select_panel"):
            self.win._select_panel(self._panel.key)

    def _start_drag(self):
        self._dragging = True
        ghost = QtWidgets.QLabel(self.win)
        ghost.setPixmap(self._item.grab())
        ghost.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        ghost.setStyleSheet("border:2px dashed #f39c12;background:transparent;")
        ghost.adjustSize()
        ghost.show()
        ghost.raise_()
        self._ghost = ghost
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.ClosedHandCursor)

    def _target_panel(self, gpos):
        """Topmost visible panel under the cursor on the current tab."""
        from .widgets import Panel
        canvas = self._panel.parentWidget() if self._panel is not None else None
        if canvas is None:
            return None
        local = canvas.mapFromGlobal(gpos)
        for child in reversed(canvas.children()):
            if isinstance(child, Panel) and child.isVisible() \
                    and child.geometry().contains(local):
                return child
        return None

    def _plan(self, gpos):
        """(target panel, items-in-container, layout, index) or None."""
        target = self._target_panel(gpos)
        if target is None:
            return None
        if isinstance(self._item, MC):
            lay = _find_mc_container(target, exclude=self._item)
            items = _mc_siblings(target, lay, self._item) if lay is not None else []
        else:
            if target is not self._panel:
                return None
            lay, group = _button_group(target, self._item)
            items = [b for b in group if b is not self._item]
        return target, items, lay, _drop_index(target, lay, items, gpos)

    def _track(self, gpos):
        if self._ghost is not None:
            self._ghost.move(self.win.mapFromGlobal(gpos) - self._grab)
        plan = self._plan(gpos)
        hint_panel = plan[0] if plan else None
        if self._hint_panel is not None and self._hint_panel is not hint_panel:
            self._hint_panel.set_drop_hint(None)
        self._hint_panel = hint_panel
        if plan:
            target, items, lay, k = plan
            target.set_drop_hint(_hint_for(target, lay, items, k))

    def _drop(self, gpos):
        plan = self._plan(gpos)
        if plan is None:
            if not isinstance(self._item, MC):
                QtWidgets.QToolTip.showText(
                    gpos, "Buttons can only be reordered within their own panel.")
            return
        target, items, lay, k = plan
        win = self.win
        if isinstance(self._item, MC):
            mc, src = self._item, self._panel
            old_k = mc_index(src, mc)
            if target is src and k == old_k:
                return
            move_mc(win, mc, target, k)
            if hasattr(win, "_record_op"):
                win._record_op(f"Move motor {mc._label!r}",
                               lambda: move_mc(win, mc, src, old_k),
                               lambda: move_mc(win, mc, target, k))
            return
        btn, panel = self._item, self._panel
        _, before = _button_group(panel, btn)
        after = items[:k] + [btn] + items[k:]
        if after == before or not apply_button_order(panel, after):
            return
        if hasattr(win, "_record_op"):
            win._record_op(f"Reorder button {btn.text()!r}",
                           lambda: apply_button_order(panel, before),
                           lambda: apply_button_order(panel, after))

    def _clear_feedback(self):
        if self._ghost is not None:
            self._ghost.hide()
            self._ghost.deleteLater()
            self._ghost = None
            QtWidgets.QApplication.restoreOverrideCursor()
        if self._hint_panel is not None:
            try:
                self._hint_panel.set_drop_hint(None)
            except RuntimeError:
                pass
            self._hint_panel = None
