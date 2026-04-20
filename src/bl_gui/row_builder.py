"""Dialog used in edit mode to add a new PV-backed row to any panel.

Produces a config dict that the main window turns into a PVField / ValveField:
  {kind, label, field_id, pv, opts:{...}, on_pv?, off_pv?}
"""
from PyQt5 import QtWidgets


_KIND_LABEL = [
    ("sp",       "Setpoint (editable)"),
    ("rb",       "Readback (live label)"),
    ("cmb",      "Combo / enum"),
    ("btn",      "Action button"),
    ("led",      "LED indicator"),
    ("btn_pair", "In/Out button pair"),
    ("valve",    "Valve (status + On/Off)"),
    ("toggle",   "Shutter toggle (single button, state-aware)"),
]


class AddPVRowDialog(QtWidgets.QDialog):
    def __init__(self, existing_field_ids, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add PV Row")
        self.setMinimumWidth(500)
        self.setStyleSheet(
            "QDialog{background:#000;color:#e0e0e0;}QLabel{color:#e0e0e0;}"
            "QLineEdit,QComboBox,QSpinBox{background:#2d2d2d;color:#e0e0e0;padding:4px;"
            "border:1px solid #404040;border-radius:3px;}"
            "QPushButton{background:#2d2d2d;color:#e0e0e0;padding:6px 12px;"
            "border:1px solid #404040;border-radius:3px;}"
        )
        self._existing = set(existing_field_ids or [])
        self.cfg = None

        L = QtWidgets.QFormLayout(self); L.setSpacing(6)

        self.kind_box = QtWidgets.QComboBox()
        for k, desc in _KIND_LABEL:
            self.kind_box.addItem(desc, k)
        self.kind_box.currentIndexChanged.connect(self._on_kind_changed)
        L.addRow("Kind:", self.kind_box)

        self.label_edit = QtWidgets.QLineEdit()
        self.label_edit.setPlaceholderText("shown in the GUI (e.g. 'Pressure:')")
        L.addRow("Label:", self.label_edit)

        self.pv_edit = QtWidgets.QLineEdit()
        self.pv_edit.setPlaceholderText("PV name")
        L.addRow("PV:", self.pv_edit)

        self.on_pv_edit = QtWidgets.QLineEdit()
        self.on_pv_edit.setPlaceholderText("PV written when 'On' / 'In' is clicked")
        L.addRow("On action PV:", self.on_pv_edit)

        self.off_pv_edit = QtWidgets.QLineEdit()
        self.off_pv_edit.setPlaceholderText("PV written when 'Off' / 'Out' is clicked")
        L.addRow("Off action PV:", self.off_pv_edit)

        self.opt_edit = QtWidgets.QLineEdit()
        self.opt_edit.setPlaceholderText("context-specific (see below)")
        L.addRow("Options:", self.opt_edit)

        hint = QtWidgets.QLabel()
        hint.setStyleSheet("color:#888;font:8pt;")
        hint.setWordWrap(True)
        L.addRow("", hint)
        self._hint = hint

        btns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("Add"); ok.setStyleSheet("background:#1e5a8e;color:#fff;font:bold 9pt;")
        ok.clicked.connect(self._accept); btns.addWidget(ok)
        cancel = QtWidgets.QPushButton("Cancel"); cancel.clicked.connect(self.reject); btns.addWidget(cancel)
        L.addRow(btns)

        self._on_kind_changed()   # initial hint + field visibility

    def _on_kind_changed(self, *_):
        k = self.kind_box.currentData()
        # Show PV / On PV / Off PV per kind
        has_primary = k in ("sp", "rb", "cmb", "btn", "led", "valve", "toggle")
        has_on_off  = k in ("btn_pair", "valve", "toggle")
        self.pv_edit.setVisible(has_primary)
        self.on_pv_edit.setVisible(has_on_off)
        self.off_pv_edit.setVisible(has_on_off)
        # (Labels stay visible — Qt QFormLayout rows are tied to widgets; clearing
        # visibility on the line edit is enough to suggest which fields apply.)
        hints = {
            "sp":       "Options: placeholder=<text>  (e.g. placeholder=sec)",
            "rb":       "Options: fmt=<python-format-spec>  (e.g. fmt=.3f for 3 decimals)",
            "cmb":      "Options: choices=Off,On,Auto   (comma-separated; leave empty for dynamic mbbo)",
            "btn":      "Options: text=<label>, value=<caput-value>  (e.g. text=Go, value=1)",
            "led":      "No options — colour reflects PV (non-zero = green).",
            "btn_pair": "Options: text=In/Out   (slash-separated button labels)",
            "valve":    "No options — PV-pair 'On action' + 'Off action' + status PV (primary).",
            "toggle":   "Shutter style: label on top, button shows Open/Close based on status PV. "
                        "Primary = status PV; On action = open trigger PV; Off action = close trigger PV.",
        }
        self._hint.setText(hints.get(k, ""))

    @staticmethod
    def _parse_opts(text, kind):
        """Turn the one-line 'options' text into a dict based on kind."""
        out = {}
        text = text.strip()
        if not text:
            return out
        # split on ',' but keep 'choices=a,b,c' intact
        if kind == "cmb":
            if text.lower().startswith("choices="):
                out["choices"] = [c.strip() for c in text[len("choices="):].split(",") if c.strip()]
            else:
                out["choices"] = [c.strip() for c in text.split(",") if c.strip()]
            return out
        for pair in text.split(","):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            k = k.strip(); v = v.strip()
            if not k:
                continue
            if kind == "sp" and k == "placeholder":
                out["placeholder"] = v
            elif kind == "rb" and k == "fmt":
                out["fmt"] = v
            elif kind == "btn":
                if k == "text":
                    out["button_text"] = v
                elif k == "value":
                    # try numeric
                    try:
                        out["button_value"] = int(v) if v.isdigit() else float(v)
                    except Exception:
                        out["button_value"] = v
            elif kind == "btn_pair" and k == "text":
                out["button_text"] = v   # e.g. "In/Out" or "Open/Close"
        return out

    def _accept(self):
        label = self.label_edit.text().strip()
        kind = self.kind_box.currentData()
        pv = self.pv_edit.text().strip()
        on_pv = self.on_pv_edit.text().strip()
        off_pv = self.off_pv_edit.text().strip()
        if kind in ("sp", "rb", "cmb", "led") and not pv:
            QtWidgets.QMessageBox.warning(self, "Missing PV", "This kind needs a PV name.")
            return
        if kind in ("btn_pair", "valve", "toggle") and not (on_pv or off_pv):
            QtWidgets.QMessageBox.warning(self, "Missing PVs", "Set at least one of the two action PVs.")
            return
        # Auto-generate a field_id unique within this panel
        base = (label.lower().replace(" ", "_").strip(":") or kind)
        fid = base; n = 1
        while fid in self._existing:
            n += 1
            fid = f"{base}_{n}"
        opts = self._parse_opts(self.opt_edit.text(), kind)
        self.cfg = {
            "kind": kind,
            "label": label,
            "field_id": fid,
            "pv": pv,
            "on_pv": on_pv,
            "off_pv": off_pv,
            "opts": opts,
        }
        self.accept()
