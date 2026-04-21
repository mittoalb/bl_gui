"""Interactive widgets: Panel, CfgButton/CfgDialog, WidgetEditor, and helpers.

The Panel carries its children (motor cards, custom buttons, labels, etc.) and
supports drag/resize in edit mode. The WidgetEditor is the dialog that edits
a single widget's style/size/PV or, for an MC, just its label + PV.
"""
import subprocess
import sys
from functools import partial
from typing import List

from PyQt5 import QtCore, QtGui, QtWidgets

from .motor import MC
from .pv import caput_bg
from .theme import _PANEL_SS, _PANEL_SS_EDIT


# ── Event filter: redirect right-clicks ──────────────────────────────────

class _ButtonEditFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.ContextMenu:
            # If the right-click landed inside a motor card (MC), let the MC's
            # own contextMenuEvent handle it (per-motor menu with PV edit, etc.).
            w = obj
            while w is not None:
                if isinstance(w, MC):
                    return False   # do not consume — MC.contextMenuEvent will fire
                w = w.parent()
            # Non-MC button: show the standalone edit menu for this button only.
            menu = QtWidgets.QMenu(obj)
            menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;}"
                               "QMenu::item:selected{background:#1e5a8e;}")
            menu.addAction("Font Size...", lambda: _change_font_size(obj))
            menu.addAction("Edit...", lambda: _edit_widget(obj))
            menu.addAction("Duplicate", lambda: _duplicate_widget(obj, None))
            menu.exec_(event.globalPos())
            return True
        return False


# ── Panel ────────────────────────────────────────────────────────────────

class Panel(QtWidgets.QFrame):
    HANDLE = 12

    def __init__(self, title, key, parent=None):
        super().__init__(parent)
        self.key = key
        self._edit = False
        self._drag = False
        self._resize = False
        self._mstart = None
        self._geo0 = None
        self.custom_buttons: List["CfgButton"] = []
        self._btn_filter = _ButtonEditFilter(self)
        self._title = QtWidgets.QLabel(title, self)
        self._title.setStyleSheet(
            "color: #73dfff; font: bold 12pt; background: transparent; padding: 2px 6px;"
        )
        self._title.adjustSize()         # width to fit the text
        self._title.move(6, 2)
        self._title.raise_()             # keep above any later siblings
        self.setStyleSheet(_PANEL_SS)

    def title_text(self):
        return self._title.text()

    def set_edit(self, on):
        self._edit = on
        self.setStyleSheet(_PANEL_SS_EDIT if on else _PANEL_SS)
        self.setCursor(QtCore.Qt.OpenHandCursor if on else QtCore.Qt.ArrowCursor)
        for b in self.custom_buttons:
            b.set_edit_mode(on)
        for child in self.findChildren(QtWidgets.QWidget):
            if isinstance(child, CfgButton):
                continue
            if isinstance(child, MC):
                child.set_edit_mode(on)
                # Do NOT install the filter on MCs themselves — MC.contextMenuEvent
                # handles per-motor editing natively.
                continue
            if isinstance(child, QtWidgets.QPushButton):
                if on:
                    child.installEventFilter(self._btn_filter)
                else:
                    child.removeEventFilter(self._btn_filter)
        self.update()

    def contextMenuEvent(self, e):
        if not self._edit:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;} QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Add Button...", self._add_button)
        menu.addAction("Add PV Row...", self._add_pv_row)
        menu.addAction("Rename Panel...", self._rename_panel)
        menu.addAction("Panel Title Font...", self._change_title_font)
        menu.addAction("Duplicate Panel", self._duplicate_panel)
        menu.addAction("Delete Panel...", self._delete_panel)
        # "Move to Tab..." submenu
        win = self.window()
        if hasattr(win, '_tab_names'):
            move_menu = menu.addMenu("Move to Tab...")
            move_menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;} QMenu::item:selected{background:#1e5a8e;}")
            current_tab = self._current_tab_name()
            for tab_name in win._tab_names():
                if tab_name == current_tab:
                    continue
                move_menu.addAction(tab_name, partial(self._move_to_tab, tab_name))
        menu.exec_(e.globalPos())

    def _current_tab_name(self):
        win = self.window()
        if hasattr(win, '_get_panel_tab'):
            return win._get_panel_tab(self.key)
        return ""

    def _move_to_tab(self, tab_name):
        win = self.window()
        if hasattr(win, '_move_panel_to_tab'):
            win._move_panel_to_tab(self.key, tab_name)

    def _duplicate_panel(self):
        win = self.window()
        if hasattr(win, '_duplicate_panel'):
            win._duplicate_panel(self.key)

    def _delete_panel(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Panel", f"Delete panel '{self.title_text()}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            win = self.window()
            if hasattr(win, '_remove_panel'):
                win._remove_panel(self.key)

    def _add_button(self):
        dlg = CfgDialog(parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            btn = CfgButton(dlg.label, dlg.atype, dlg.aval, parent=self)
            btn.set_edit_mode(self._edit)
            lay = self.layout()
            if lay:
                lay.addWidget(btn)
            else:
                btn.move(10, 30)
            btn.show()
            self.custom_buttons.append(btn)

    def _add_pv_row(self):
        win = self.window()
        if hasattr(win, 'add_pv_row_dialog'):
            win.add_pv_row_dialog(self)

    def _rename_panel(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Panel", "New title:", text=self._title.text())
        if ok and text:
            self._title.setText(text)
            self._title.adjustSize()

    def _change_title_font(self):
        import re
        ss = self._title.styleSheet()
        cur = 12
        m = re.search(r'(\d+)\s*pt', ss)
        if m: cur = int(m.group(1))
        val, ok = QtWidgets.QInputDialog.getInt(
            self, "Panel Title Font", "Font size (pt):", cur, 4, 30)
        if ok:
            self._title.setStyleSheet(
                f"color: #73dfff; font: bold {val}pt; background: transparent; padding: 2px 6px;"
            )
            self._title.adjustSize()

    def _in_handle(self, pos):
        return pos.x() > self.width() - self.HANDLE and pos.y() > self.height() - self.HANDLE

    def mousePressEvent(self, e):
        if not self._edit or e.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(e)
        self._geo0 = self.geometry()
        self._mstart = e.globalPos()
        if self._in_handle(e.pos()):
            self._resize = True
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        else:
            self._drag = True
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        self.raise_()

    def mouseMoveEvent(self, e):
        if not self._edit:
            return super().mouseMoveEvent(e)
        if self._drag and self._mstart:
            d = e.globalPos() - self._mstart
            self.move(self._geo0.topLeft() + d)
        elif self._resize and self._mstart:
            d = e.globalPos() - self._mstart
            self.resize(max(80, self._geo0.width() + d.x()),
                        max(40, self._geo0.height() + d.y()))
        elif self._in_handle(e.pos()):
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        else:
            self.setCursor(QtCore.Qt.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        if not self._edit:
            return super().mouseReleaseEvent(e)
        self._drag = self._resize = False
        self._mstart = None
        self.setCursor(QtCore.Qt.OpenHandCursor if not self._in_handle(e.pos())
                       else QtCore.Qt.SizeFDiagCursor)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._edit:
            p = QtGui.QPainter(self)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#f39c12"))
            p.drawRect(self.width() - self.HANDLE, self.height() - self.HANDLE,
                       self.HANDLE, self.HANDLE)
            p.end()


# ── Configurable action button ───────────────────────────────────────────

class CfgButton(QtWidgets.QPushButton):
    def __init__(self, label="Button", action_type="shell", action="",
                 bg="#2d2d2d", fg="#e0e0e0", font_size=9, parent=None):
        super().__init__(label, parent)
        self.action_type = action_type
        self.action = action
        self._bg = bg
        self._fg = fg
        self._font_size = font_size
        self._edit_mode = False
        self._apply_style()
        self.clicked.connect(self._execute)
        self.setToolTip(f"{action_type}: {action}")

    def _apply_style(self):
        self.setStyleSheet(
            f"background:{self._bg};color:{self._fg};font:{self._font_size}pt;"
            f"border:1px solid #404040;border-radius:3px;padding:4px 8px;")

    def set_edit_mode(self, on):
        self._edit_mode = on

    def _execute(self):
        if self._edit_mode:
            return
        if not self.action:
            return
        if self.action_type == "shell":
            subprocess.Popen(self.action, shell=True, start_new_session=True)
        elif self.action_type == "caput":
            parts = self.action.split(None, 1)
            if len(parts) == 2:
                caput_bg(parts[0], parts[1])
        elif self.action_type == "url":
            import webbrowser
            webbrowser.open(self.action)
        elif self.action_type == "script":
            subprocess.Popen([sys.executable, self.action], start_new_session=True)

    def contextMenuEvent(self, e):
        if not self._edit_mode:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("QMenu{background:#2d2d2d;color:#e0e0e0;} QMenu::item:selected{background:#1e5a8e;}")
        menu.addAction("Configure...", self._configure)
        menu.addAction("Edit...", self._full_edit)
        menu.addAction("Duplicate", lambda: _duplicate_widget(self, None))
        menu.addAction("Delete", self._delete)
        menu.exec_(e.globalPos())

    def _configure(self):
        dlg = CfgDialog(self.text(), self.action_type, self.action, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.setText(dlg.label)
            self.action_type = dlg.atype
            self.action = dlg.aval
            self.setToolTip(f"{self.action_type}: {self.action}")

    def _full_edit(self):
        dlg = WidgetEditor(self, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            d = dlg.result_data
            self._bg = d['bg']
            self._fg = d['fg']
            self._font_size = d['fs']
            self._apply_style()
            self.setMinimumSize(d['w'], d['h'])
            self.setMaximumSize(d['w'], d['h'])
            self.resize(d['w'], d['h'])
            pv = d.get('pv', '')
            if pv and self.action_type == 'caput':
                parts = self.action.split(None, 1)
                val = parts[1] if len(parts) == 2 else "1"
                self.action = f"{pv} {val}"
                self.setToolTip(f"{self.action_type}: {self.action}")

    def _delete(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Button", f"Delete '{self.text()}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            parent = self.parent()
            if hasattr(parent, 'custom_buttons'):
                parent.custom_buttons = [b for b in parent.custom_buttons if b is not self]
            self.deleteLater()

    def to_dict(self):
        return {"label": self.text(), "type": self.action_type, "action": self.action,
                "bg": self._bg, "fg": self._fg, "font_size": self._font_size}

    @staticmethod
    def from_dict(d, parent=None):
        return CfgButton(d.get("label", "Button"),
                         d.get("type", "shell"),
                         d.get("action", ""),
                         d.get("bg", "#2d2d2d"),
                         d.get("fg", "#e0e0e0"),
                         d.get("font_size", 9),
                         parent)


class CfgDialog(QtWidgets.QDialog):
    def __init__(self, label="", atype="shell", aval="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Button")
        self.setMinimumWidth(400)
        self.setStyleSheet(
            "QDialog{background:#000000;color:#e0e0e0;}"
            "QLabel{color:#e0e0e0;}"
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;padding:4px;border:1px solid #404040;border-radius:3px;}"
            "QComboBox{background:#2d2d2d;color:#e0e0e0;padding:4px;border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 16px;border:1px solid #404040;border-radius:3px;}")
        L = QtWidgets.QFormLayout(self); L.setSpacing(8)
        self.label_edit = QtWidgets.QLineEdit(label); L.addRow("Label:", self.label_edit)
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["shell", "caput", "url", "script"])
        idx = self.type_combo.findText(atype)
        if idx >= 0: self.type_combo.setCurrentIndex(idx)
        L.addRow("Action type:", self.type_combo)
        self.action_edit = QtWidgets.QLineEdit(aval)
        self.action_edit.setPlaceholderText("e.g. /path/to/script.sh  or  PV_NAME value")
        L.addRow("Action:", self.action_edit)
        help_lbl = QtWidgets.QLabel(
            "shell: run command in shell\n"
            "caput: 'PV_NAME value' (e.g. 32id:TXMOptics:MoveAllIn 1)\n"
            "url: open URL in browser\nscript: run Python script")
        help_lbl.setStyleSheet("color:#888;font:8pt;"); help_lbl.setWordWrap(True); L.addRow(help_lbl)
        btns = QtWidgets.QHBoxLayout()
        bok = QtWidgets.QPushButton("OK")
        bok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        bok.clicked.connect(self.accept); btns.addWidget(bok)
        bcancel = QtWidgets.QPushButton("Cancel"); bcancel.clicked.connect(self.reject); btns.addWidget(bcancel)
        L.addRow(btns)

    @property
    def label(self): return self.label_edit.text()

    @property
    def atype(self): return self.type_combo.currentText()

    @property
    def aval(self): return self.action_edit.text()


# ── Widget editor dialog ─────────────────────────────────────────────────

class WidgetEditor(QtWidgets.QDialog):
    _DLG_SS = ("QDialog{background:#000;color:#e0e0e0;}"
               "QLabel{color:#e0e0e0;background:transparent;}"
               "QLineEdit,QSpinBox{background:#2d2d2d;color:#e0e0e0;padding:4px;"
               "border:1px solid #404040;border-radius:3px;}"
               "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
               "border:1px solid #404040;border-radius:3px;}"
               "QTabWidget::pane{border:1px solid #404040;background:#000;}"
               "QTabBar::tab{background:#2d2d2d;color:#e0e0e0;padding:6px 14px;"
               "border:1px solid #404040;margin-right:2px;}"
               "QTabBar::tab:selected{background:#1e5a8e;}")

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.w = widget
        self.setWindowTitle(f"Edit: {self._widget_text()}")
        self.setMinimumWidth(400); self.setStyleSheet(self._DLG_SS)
        self.result_data = {}
        import re
        ss = widget.styleSheet()
        self._bg = "#2d2d2d"; self._fg = "#e0e0e0"; self._fs = 9
        m = re.search(r'background[:\-color]*\s*([#\w]+)', ss)
        if m: self._bg = m.group(1)
        m = re.search(r'(?<!background[:\-])color\s*:\s*([#\w]+)', ss)
        if m: self._fg = m.group(1)
        m = re.search(r'font[:\s]*(?:bold\s+)?(\d+)\s*pt', ss)
        if m: self._fs = int(m.group(1))
        L = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        style_w = QtWidgets.QWidget(); sl = QtWidgets.QFormLayout(style_w); sl.setSpacing(6)
        self._bg_btn = QtWidgets.QPushButton(self._bg)
        self._bg_btn.setStyleSheet(self._color_ss(self._bg))
        self._bg_btn.clicked.connect(self._pick_bg); sl.addRow("Background:", self._bg_btn)
        self._fg_btn = QtWidgets.QPushButton(self._fg)
        self._fg_btn.setStyleSheet(self._color_ss(self._fg))
        self._fg_btn.clicked.connect(self._pick_fg); sl.addRow("Text color:", self._fg_btn)
        self._fs_spin = QtWidgets.QSpinBox(); self._fs_spin.setRange(6, 30)
        self._fs_spin.setValue(self._fs); self._fs_spin.setSuffix(" pt"); sl.addRow("Font size:", self._fs_spin)
        self._preview = QtWidgets.QPushButton(self._widget_text()); self._update_preview()
        sl.addRow("Preview:", self._preview); tabs.addTab(style_w, "Style")
        pos_w = QtWidgets.QWidget(); pl = QtWidgets.QFormLayout(pos_w); pl.setSpacing(6)
        geo = widget.geometry()
        self._x_spin = QtWidgets.QSpinBox(); self._x_spin.setRange(-9999, 9999); self._x_spin.setValue(geo.x()); pl.addRow("X:", self._x_spin)
        self._y_spin = QtWidgets.QSpinBox(); self._y_spin.setRange(-9999, 9999); self._y_spin.setValue(geo.y()); pl.addRow("Y:", self._y_spin)
        self._w_spin = QtWidgets.QSpinBox(); self._w_spin.setRange(10, 9999); self._w_spin.setValue(geo.width()); pl.addRow("Width:", self._w_spin)
        self._h_spin = QtWidgets.QSpinBox(); self._h_spin.setRange(10, 9999); self._h_spin.setValue(geo.height()); pl.addRow("Height:", self._h_spin)
        tabs.addTab(pos_w, "Position / Size")
        cur_label = getattr(widget, "_label", None) or self._widget_text()

        # For CfgButton widgets, show an "Action" tab instead of a PV tab —
        # these buttons don't talk to a PV directly, they run a shell
        # command / caput / URL / script.
        self._is_cfg = isinstance(widget, CfgButton)
        if self._is_cfg:
            act_w = QtWidgets.QWidget(); al = QtWidgets.QFormLayout(act_w); al.setSpacing(6)
            self._label_edit = QtWidgets.QLineEdit(cur_label)
            self._label_edit.setPlaceholderText("Button label")
            al.addRow("Label:", self._label_edit)
            self._type_combo = QtWidgets.QComboBox()
            self._type_combo.addItems(["shell", "caput", "url", "script"])
            idx = self._type_combo.findText(getattr(widget, "action_type", "shell"))
            if idx >= 0: self._type_combo.setCurrentIndex(idx)
            al.addRow("Action type:", self._type_combo)
            self._action_edit = QtWidgets.QLineEdit(getattr(widget, "action", ""))
            self._action_edit.setPlaceholderText(
                "e.g. /path/to/script.sh   or   PV_NAME value")
            al.addRow("Action:", self._action_edit)
            help_lbl = QtWidgets.QLabel(
                "shell : run a shell command\n"
                "caput : 'PV_NAME value' (writes value to PV)\n"
                "url   : open URL in default browser\n"
                "script: run Python script")
            help_lbl.setStyleSheet("color:#888;font:8pt;"); help_lbl.setWordWrap(True)
            al.addRow(help_lbl)
            tabs.addTab(act_w, "Action")
            # Keep a blank _pv_edit so accept() can still reference it.
            self._pv_edit = QtWidgets.QLineEdit("")
        else:
            pv_name = ""
            if hasattr(widget, 'pv'): pv_name = widget.pv
            pv_w = QtWidgets.QWidget(); pvl = QtWidgets.QFormLayout(pv_w); pvl.setSpacing(6)
            self._label_edit = QtWidgets.QLineEdit(cur_label)
            self._label_edit.setPlaceholderText("Displayed name (e.g. X, Y-L, Sample X)")
            pvl.addRow("Name/Label:", self._label_edit)
            self._pv_edit = QtWidgets.QLineEdit(pv_name)
            self._pv_edit.setPlaceholderText("PV name (e.g. 32idbTXM:mcs2:c1:m1)")
            pvl.addRow("PV:", self._pv_edit)
            pvl.addRow(QtWidgets.QLabel(
                "For motor cards: changes the motor PV prefix and label.\n"
                "Leave empty to keep current."))
            tabs.addTab(pv_w, "PV")
        L.addWidget(tabs)
        btns = QtWidgets.QHBoxLayout()
        bok = QtWidgets.QPushButton("OK"); bok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        bok.clicked.connect(self.accept); btns.addWidget(bok)
        bc = QtWidgets.QPushButton("Cancel"); bc.clicked.connect(self.reject); btns.addWidget(bc)
        L.addLayout(btns)

    def _widget_text(self):
        if hasattr(self.w, 'text') and callable(self.w.text): return self.w.text() or type(self.w).__name__
        if hasattr(self.w, '_label'): return self.w._label
        return type(self.w).__name__

    @staticmethod
    def _contrast(c):
        try:
            q = QtGui.QColor(c)
            return "#000" if (0.299*q.red()+0.587*q.green()+0.114*q.blue()) > 128 else "#fff"
        except Exception:
            return "#fff"

    def _color_ss(self, c):
        return f"background:{c};color:{self._contrast(c)};font:bold 10pt;padding:6px;"

    def _update_preview(self):
        fs = self._fs_spin.value()
        self._preview.setStyleSheet(f"background:{self._bg};color:{self._fg};font:{fs}pt;border:1px solid #404040;border-radius:3px;padding:6px 12px;")

    def _pick_bg(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._bg), self, "Background")
        if c.isValid():
            self._bg = c.name(); self._bg_btn.setText(self._bg); self._bg_btn.setStyleSheet(self._color_ss(self._bg)); self._update_preview()

    def _pick_fg(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._fg), self, "Text Color")
        if c.isValid():
            self._fg = c.name(); self._fg_btn.setText(self._fg); self._fg_btn.setStyleSheet(self._color_ss(self._fg)); self._update_preview()

    def accept(self):
        self.result_data = {"bg": self._bg, "fg": self._fg, "fs": self._fs_spin.value(),
            "x": self._x_spin.value(), "y": self._y_spin.value(),
            "w": self._w_spin.value(), "h": self._h_spin.value(),
            "pv": self._pv_edit.text().strip(),
            "label": self._label_edit.text().strip()}
        if self._is_cfg:
            self.result_data["action_type"] = self._type_combo.currentText()
            self.result_data["action"] = self._action_edit.text()
        super().accept()


StyleDialog = WidgetEditor   # backward-compat alias


# ── Helpers invoked by the context menus ─────────────────────────────────

def _change_font_size(widget):
    """Quick font size change for any widget."""
    import re
    ss = widget.styleSheet()
    cur = 9
    m = re.search(r'font[:\s]*(?:bold\s+)?(\d+)\s*pt', ss)
    if m:
        cur = int(m.group(1))
    val, ok = QtWidgets.QInputDialog.getInt(
        widget, "Font Size", "Font size (pt):", cur, 4, 40)
    if not ok or val == cur:
        return
    new_ss = re.sub(r'(font[:\s]*(?:bold\s+)?)(\d+)(\s*pt)', lambda mm: mm.group(1) + str(val) + mm.group(3), ss)
    if new_ss == ss:
        new_ss = ss.rstrip(';') + f";font-size:{val}pt;"
    widget.setStyleSheet(new_ss)
    widget.setProperty("_custom_fs", val)
    if not widget.property("_custom_bg"):
        m_bg = re.search(r'background[:\-color]*\s*([#\w]+)', new_ss)
        if m_bg:
            widget.setProperty("_custom_bg", m_bg.group(1))
    if not widget.property("_custom_fg"):
        m_fg = re.search(r'(?<!background[:\-])color\s*:\s*([#\w]+)', new_ss)
        if m_fg:
            widget.setProperty("_custom_fg", m_fg.group(1))


def _edit_widget(widget):
    dlg = WidgetEditor(widget, widget)
    if dlg.exec_() != QtWidgets.QDialog.Accepted: return
    d = dlg.result_data

    if isinstance(widget, MC):
        # Motor cards: apply label + PV only. Do NOT overwrite the card's
        # custom frame stylesheet or force a fixed size.
        new_label = d.get('label', '').strip()
        old_label = widget._label
        old_pv = widget.pv
        if new_label:
            # Any non-empty label entered via the dialog is treated as an
            # intentional user choice — lock it so the motor record's
            # .DESC PV does not overwrite it on update.
            widget._custom_label = True
            if new_label != widget._label:
                widget._label = new_label
                widget.desc.setText(new_label)
        pv = d.get('pv', '').strip()
        if pv and pv != widget.pv:
            widget.pv = pv
            for attr in ('_movn', '_dmov', '_hls', '_lls', '_lvio'):
                if hasattr(widget, attr):
                    setattr(widget, attr, "0")
            widget.rbv.setText("---"); widget.egu.setText("")
            widget.stat.setText(""); widget.val.clear()
            win = widget.window()
            if hasattr(win, '_pve'):
                win._pve.monitor_many(widget.get_pvs())
        print(f"[EDIT MC] label: {old_label!r} -> {widget._label!r}  "
              f"pv: {old_pv!r} -> {widget.pv!r}  custom={widget._custom_label}")
        return

    # Non-MC widgets: apply the full style / size / PV edits as before
    widget.setStyleSheet(f"background:{d['bg']};color:{d['fg']};font:{d['fs']}pt;border:1px solid #404040;border-radius:3px;padding:4px 8px;")
    widget.setProperty("_custom_bg", d['bg']); widget.setProperty("_custom_fg", d['fg']); widget.setProperty("_custom_fs", d['fs'])
    widget.setMinimumSize(d['w'], d['h']); widget.setMaximumSize(d['w'], d['h']); widget.resize(d['w'], d['h'])

    # CfgButton: update label + action_type + action from the Action tab.
    if isinstance(widget, CfgButton):
        new_label = d.get('label', '').strip()
        if new_label:
            widget.setText(new_label)
        atype = d.get('action_type')
        if atype is not None:
            widget.action_type = atype
        action = d.get('action')
        if action is not None:
            widget.action = action
        widget.setToolTip(f"{widget.action_type}: {widget.action}")
        return

    pv = d.get('pv', '')
    if pv and hasattr(widget, 'pv'):
        widget.pv = pv


def _delete_widget(widget):
    """Confirm + remove a single MC (or any widget) from its panel."""
    name = getattr(widget, "_label", None) or (widget.text() if hasattr(widget, "text") else "widget")
    reply = QtWidgets.QMessageBox.question(
        widget, "Delete",
        f"Delete '{name}'?",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
    )
    if reply != QtWidgets.QMessageBox.Yes:
        return
    win = widget.window()
    if isinstance(widget, MC) and hasattr(win, "mcs"):
        try:
            win.mcs.remove(widget)
        except ValueError:
            pass
    parent = widget.parent()
    lay = parent.layout() if parent is not None else None
    if lay is not None:
        lay.removeWidget(widget)
    widget.setParent(None)
    widget.deleteLater()


def _duplicate_widget(widget, canvas):
    if isinstance(widget, CfgButton):
        parent = widget.parent()
        new = CfgButton(widget.text() + " (copy)", widget.action_type, widget.action,
                        widget._bg, widget._fg, widget._font_size, parent)
        new.set_edit_mode(True)
        lay = parent.layout() if parent else None
        if lay: lay.addWidget(new)
        new.show()
        if hasattr(parent, 'custom_buttons'): parent.custom_buttons.append(new)
    elif isinstance(widget, MC):
        parent = widget.parent()
        new = MC(widget._label + " (copy)", widget.pv)
        if parent:
            lay = parent.layout()
            if lay: lay.addWidget(new)
            new.show()
        win = widget.window()
        if hasattr(win, 'mcs'):
            win.mcs.append(new)
            if hasattr(win, '_pve'): win._pve.monitor_many(new.get_pvs())
        panel = widget.parent()
        panel_key = getattr(panel, 'key', '?') if panel is not None else '?'
        total_in_panel = len(panel.findChildren(MC)) if panel is not None else -1
        print(f"[DUP MC] source={widget._label!r}  new={new._label!r}  "
              f"panel={panel_key!r}  mcs_in_panel_after={total_in_panel}  "
              f"mcs_total={len(win.mcs) if hasattr(win, 'mcs') else -1}")
