"""Style sheets and file-path constants."""
import os

_IMG = "/home/beams19/USERTXM/epics/synApps/support/txmoptics/txmOpticsApp/op/adl/txm2.gif"

# Layout file lives inside the package so it travels with the source tree
# (can be committed to git and deployed to other machines).
# For editable installs (`pip install -e .`) this writes directly into the
# git repo's src/bl_gui/layout.json.
_LAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "layouts", "bl32id.json")

_SS = """
QMainWindow { background-color: #000000; }
QWidget { background-color: #000000; color: #e0e0e0; }
QPushButton {
    background: #2d2d2d; color: #e0e0e0; padding: 3px 8px;
    border: 1px solid #404040; border-radius: 3px; font-size: 9pt;
}
QPushButton:hover { background: #3a3a3a; border-color: #505050; }
QPushButton:pressed { background: #222; }
QLineEdit {
    background: #454545; color: #e0e0e0; padding: 2px 5px;
    border: 1px solid #606060; border-radius: 3px; font: 9pt monospace;
}
QLineEdit:focus { background: #555; border: 1px solid #2980b9; }
QComboBox {
    background: #2d2d2d; color: #e0e0e0; padding: 2px 5px;
    border: 1px solid #404040; border-radius: 3px; font-size: 8pt;
}
QComboBox QAbstractItemView { background: #2d2d2d; color: #e0e0e0; }
QScrollArea { border: none; background: #000000; }
QTabWidget::pane { border: 1px solid #404040; background: #000000; }
QTabBar { qproperty-expanding: 0; }
"""

_PANEL_SS = """
    background: #323232; border: 1px solid #484848; border-radius: 3px;
    font: bold 9pt; color: #e0e0e0;
"""
_PANEL_SS_EDIT = """
    background: #323232; border: 2px dashed #f39c12; border-radius: 3px;
    font: bold 9pt; color: #e0e0e0;
"""
