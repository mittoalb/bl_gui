"""Native bl_gui port of the mctOptics MEDM screen.

Reconstructs the controls exposed by the mctOptics IOC template
(``mctOptics.template``) as a tabbed QWidget built from bl_gui's own
``PVField`` primitives, so the panel behaves like the rest of the GUI
(save/load, snapshot, dirty-check, edit-mode wiring) instead of
launching MEDM.

PV names are formed as ``PREFIX + suffix`` — default prefix is
``32id:MCTOptics:`` (P=32id:, R=MCTOptics: from the IOC substitution).
Change ``MCTOpticsView.DEFAULT_PREFIX`` if the same widget is dropped
into a GUI that talks to a different mctOptics instance.
"""
from PyQt5 import QtCore, QtWidgets

from ...pv_field import PVField


DEFAULT_PREFIX = "32id:MCTOptics:"


# mbbo choices — hardcoded from mctOptics.template ZRST/ONST/... so the
# combo shows the right labels even before the first PV update.
CAMERA_SELECT_CHOICES = ["Camera 0", "Camera 1"]
LENS_SELECT_CHOICES = ["Lens 0", "Lens 1", "Lens 2"]
CAMERA_BINNING_CHOICES = ["1x1", "2x2", "3x3", "4x4"]


class MCTOpticsView(QtWidgets.QWidget):
    """Composite widget presenting the mctOptics IOC controls in tabs.

    Every leaf control is a ``PVField`` whose ``field_id`` is unique
    across the widget, so bl_gui's ``_add_widget_from_registry`` walks
    the descendants and registers each one for PV monitoring and
    save/load with no extra wiring here.
    """

    DEFAULT_PREFIX = DEFAULT_PREFIX

    def __init__(self, prefix: str = None, parent=None):
        super().__init__(parent)
        self._prefix = (prefix or self.DEFAULT_PREFIX).strip()
        self._next_fid = 0

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(3)

        header = QtWidgets.QLabel(f"mctOptics — {self._prefix}")
        header.setStyleSheet(
            "color:#73dfff;font:bold 10pt;padding:2px 4px;"
            "background:#1c1c1c;border:1px solid #2d2d2d;border-radius:2px;")
        outer.addWidget(header)

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setStyleSheet(
            "QTabBar::tab{background:#2d2d2d;color:#e0e0e0;padding:4px 10px;}"
            "QTabBar::tab:selected{background:#1e5a8e;color:#fff;}")
        outer.addWidget(self._tabs, 1)

        self._build_setup_tab()
        self._build_camera_lens_tab()
        self._build_focus_rot_tab()
        self._build_cut_angles_tab()
        self._build_status_tab()
        self._build_ioc_config_tab()

    # ── helpers ──────────────────────────────────────────────────────

    def _fid(self, name: str) -> str:
        """Namespaced, unique field id (survives multi-instance drops)."""
        self._next_fid += 1
        return f"mct_{name}_{self._next_fid}"

    def _pv(self, suffix: str) -> str:
        return self._prefix + suffix

    def _sp(self, suffix: str, name: str, fmt: str = None,
            placeholder: str = None) -> PVField:
        return PVField('sp', self._pv(suffix), self._fid(name),
                       fmt=fmt, placeholder=placeholder, parent=self)

    def _rb(self, suffix: str, name: str, fmt: str = None) -> PVField:
        return PVField('rb', self._pv(suffix), self._fid(name),
                       fmt=fmt, parent=self)

    def _cmb(self, suffix: str, name: str, choices=None) -> PVField:
        return PVField('cmb', self._pv(suffix), self._fid(name),
                       choices=choices, parent=self)

    def _btn(self, suffix: str, name: str, text: str, value=1) -> PVField:
        return PVField('btn', self._pv(suffix), self._fid(name),
                       button_text=text, button_value=value, parent=self)

    def _led(self, suffix: str, name: str) -> PVField:
        return PVField('led', self._pv(suffix), self._fid(name),
                       parent=self)

    @staticmethod
    def _form_tab() -> "tuple[QtWidgets.QWidget, QtWidgets.QFormLayout]":
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        return page, form

    # ── tab builders ─────────────────────────────────────────────────

    def _build_setup_tab(self):
        page, form = self._form_tab()
        form.addRow("Scintillator type:",
                    self._sp("ScintillatorType", "scint_type"))
        form.addRow("Scint. thickness (µm):",
                    self._sp("ScintillatorThickness", "scint_thick", fmt=".3f"))
        form.addRow("Image pixel size (µm):",
                    self._sp("ImagePixelSize", "img_px", fmt=".4f"))
        form.addRow("Detector pixel size (µm):",
                    self._sp("DetectorPixelSize", "det_px", fmt=".4f"))
        form.addRow("Camera objective (x):",
                    self._sp("CameraObjective", "obj"))
        form.addRow("Tube length (mm):",
                    self._sp("CameraTubeLength", "tube_len", fmt=".2f"))
        form.addRow("Camera binning:",
                    self._cmb("CameraBinning", "cam_bin",
                              CAMERA_BINNING_CHOICES))
        self._tabs.addTab(page, "Setup")

    def _build_camera_lens_tab(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Camera group
        cam_box = QtWidgets.QGroupBox("Camera")
        cam_box.setStyleSheet("QGroupBox{color:#73dfff;font:bold 9pt;"
                              "border:1px solid #2d2d2d;margin-top:8px;}"
                              "QGroupBox::title{subcontrol-origin:margin;"
                              "left:8px;padding:0 4px;}")
        cf = QtWidgets.QFormLayout(cam_box)
        cf.setContentsMargins(8, 14, 8, 8); cf.setSpacing(4)
        cf.addRow("Select:", self._cmb("CameraSelect", "cam_sel",
                                       CAMERA_SELECT_CHOICES))
        cf.addRow("Selected:", self._rb("CameraSelected", "cam_seld"))
        cf.addRow("Camera 0 name:", self._sp("Camera0Name", "cam0_name"))
        cf.addRow("Camera 1 name:", self._sp("Camera1Name", "cam1_name"))
        cf.addRow("Camera 0 pos:", self._sp("CameraPos0", "cam0_pos", fmt=".6f"))
        cf.addRow("Camera 1 pos:", self._sp("CameraPos1", "cam1_pos", fmt=".6f"))
        cf.addRow("Sync:", self._btn("Sync", "sync", "Sync now", 1))
        v.addWidget(cam_box)

        # Lens group
        lens_box = QtWidgets.QGroupBox("Lens")
        lens_box.setStyleSheet(cam_box.styleSheet())
        lf = QtWidgets.QFormLayout(lens_box)
        lf.setContentsMargins(8, 14, 8, 8); lf.setSpacing(4)
        lf.addRow("Select:", self._cmb("LensSelect", "lens_sel",
                                       LENS_SELECT_CHOICES))
        lf.addRow("Lens 0 name:", self._sp("Lens0Name", "lens0_name"))
        lf.addRow("Lens 1 name:", self._sp("Lens1Name", "lens1_name"))
        lf.addRow("Lens 2 name:", self._sp("Lens2Name", "lens2_name"))
        v.addWidget(lens_box)

        v.addStretch(1)
        self._tabs.addTab(page, "Camera / Lens")

    def _build_focus_rot_tab(self):
        page = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(page)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(10); grid.setVerticalSpacing(4)

        headers = ["", "Camera 0", "Camera 1"]
        for c, txt in enumerate(headers):
            lbl = QtWidgets.QLabel(txt)
            lbl.setStyleSheet("color:#73dfff;font:bold 9pt;")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(lbl, 0, c)

        row = 1
        for lens_idx in range(3):
            grid.addWidget(QtWidgets.QLabel(f"Focus lens {lens_idx + 1}:"),
                           row, 0)
            grid.addWidget(self._sp(f"Camera0Lens{lens_idx}Focus",
                                    f"c0_l{lens_idx}_foc", fmt=".4f"),
                           row, 1)
            grid.addWidget(self._sp(f"Camera1Lens{lens_idx}Focus",
                                    f"c1_l{lens_idx}_foc", fmt=".4f"),
                           row, 2)
            row += 1
            grid.addWidget(QtWidgets.QLabel(f"Rotation lens {lens_idx + 1}:"),
                           row, 0)
            grid.addWidget(self._sp(f"Camera0Lens{lens_idx}Rotation",
                                    f"c0_l{lens_idx}_rot", fmt=".4f"),
                           row, 1)
            grid.addWidget(self._sp(f"Camera1Lens{lens_idx}Rotation",
                                    f"c1_l{lens_idx}_rot", fmt=".4f"),
                           row, 2)
            row += 1

        # Lens 1 & 2 sample-XYZ offsets (lens 0 has no offset in template)
        for lens_idx in (1, 2):
            for axis in ("X", "Y", "Z"):
                grid.addWidget(
                    QtWidgets.QLabel(f"Lens {lens_idx + 1} sample {axis}:"),
                    row, 0)
                grid.addWidget(
                    self._sp(f"Camera0Lens{lens_idx}{axis}Offset",
                             f"c0_l{lens_idx}_{axis.lower()}_off", fmt=".4f"),
                    row, 1)
                grid.addWidget(
                    self._sp(f"Camera1Lens{lens_idx}{axis}Offset",
                             f"c1_l{lens_idx}_{axis.lower()}_off", fmt=".4f"),
                    row, 2)
                row += 1

        grid.setRowStretch(row, 1)
        self._tabs.addTab(page, "Focus / Rotation")

    def _build_cut_angles_tab(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8); v.setSpacing(6)

        cut_box = QtWidgets.QGroupBox("Cut / ROI (pixels)")
        cut_box.setStyleSheet("QGroupBox{color:#73dfff;font:bold 9pt;"
                              "border:1px solid #2d2d2d;margin-top:8px;}"
                              "QGroupBox::title{subcontrol-origin:margin;"
                              "left:8px;padding:0 4px;}")
        cf = QtWidgets.QFormLayout(cut_box)
        cf.setContentsMargins(8, 14, 8, 8); cf.setSpacing(4)
        cf.addRow("Left:",   self._sp("CutLeft",   "cut_L"))
        cf.addRow("Right:",  self._sp("CutRight",  "cut_R"))
        cf.addRow("Top:",    self._sp("CutTop",    "cut_T"))
        cf.addRow("Bottom:", self._sp("CutBottom", "cut_B"))
        cf.addRow("Apply:",  self._btn("Cut", "cut_apply", "Apply cut", 1))
        v.addWidget(cut_box)

        ang_box = QtWidgets.QGroupBox("Suggested angles")
        ang_box.setStyleSheet(cut_box.styleSheet())
        af = QtWidgets.QFormLayout(ang_box)
        af.setContentsMargins(8, 14, 8, 8); af.setSpacing(4)
        af.addRow("Angles:",     self._sp("SuggestedAngles",    "sug_ang", fmt=".2f"))
        af.addRow("Angle step:", self._sp("SuggestedAngleStep", "sug_step", fmt=".4f"))
        v.addWidget(ang_box)

        v.addStretch(1)
        self._tabs.addTab(page, "Cut / Angles")

    def _build_status_tab(self):
        page, form = self._form_tab()
        form.addRow("Server running:", self._led("ServerRunning", "srv_led"))
        form.addRow("Watchdog:",       self._rb("Watchdog", "wdog"))
        form.addRow("MCT status:",     self._rb("MCTStatus", "mct_status"))
        form.addRow("Camera 0 bit:",   self._rb("Camera0Bit", "c0_bit"))
        form.addRow("Camera 1 bit:",   self._rb("Camera1Bit", "c1_bit"))
        self._tabs.addTab(page, "Status")

    def _build_ioc_config_tab(self):
        """Config strings the IOC uses to locate the actual motors and
        camera plugins. Same fields as the `_setup` MEDM sub-screens.
        Wide setpoint fields — these hold full PV names, not values."""
        page = QtWidgets.QWidget()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)
        form = QtWidgets.QFormLayout(inner)
        form.setContentsMargins(8, 8, 8, 8); form.setSpacing(4)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        def note(text: str):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet("color:#73dfff;font:bold 9pt;"
                              "background:#1c1c1c;padding:3px 6px;"
                              "border:1px solid #2d2d2d;border-radius:2px;")
            form.addRow(lbl)

        note("Motor PV names (record name of the motor, e.g. 32idc02:m9)")
        form.addRow("Camera motor:",  self._sp("CameraMotorPVName", "cam_mot_pv"))
        form.addRow("Lens motor:",    self._sp("LensMotorPVName",   "lens_mot_pv"))
        form.addRow("Lens sample X:", self._sp("LensSampleXPVName", "smpl_x_pv"))
        form.addRow("Lens sample Y:", self._sp("LensSampleYPVName", "smpl_y_pv"))
        form.addRow("Lens sample Z:", self._sp("LensSampleZPVName", "smpl_z_pv"))
        form.addRow("Lens 0 focus:",  self._sp("Lens0FocusPVName",  "l0_foc_pv"))
        form.addRow("Lens 1 focus:",  self._sp("Lens1FocusPVName",  "l1_foc_pv"))
        form.addRow("Lens 2 focus:",  self._sp("Lens2FocusPVName",  "l2_foc_pv"))
        form.addRow("Camera 0 rotation:",
                    self._sp("Camera0RotationPVName", "c0_rot_pv"))
        form.addRow("Camera 1 rotation:",
                    self._sp("Camera1RotationPVName", "c1_rot_pv"))

        note("areaDetector prefixes (trailing colon required, e.g. 32idK1:)")
        form.addRow("Camera 0:",        self._sp("Camera0PVPrefix", "c0_pref"))
        form.addRow("Camera 1:",        self._sp("Camera1PVPrefix", "c1_pref"))
        form.addRow("Overlay plugin 0:", self._sp("OverlayPlugin0PVPrefix", "ov0_pref"))
        form.addRow("Overlay plugin 1:", self._sp("OverlayPlugin1PVPrefix", "ov1_pref"))
        form.addRow("File plugin 0:",   self._sp("FilePlugin0PVPrefix", "fp0_pref"))
        form.addRow("File plugin 1:",   self._sp("FilePlugin1PVPrefix", "fp1_pref"))
        form.addRow("PVA plugin 0:",    self._sp("PvaPlugin0PVPrefix", "pva0_pref"))
        form.addRow("PVA plugin 1:",    self._sp("PvaPlugin1PVPrefix", "pva1_pref"))
        form.addRow("ROI plugin 0:",    self._sp("RoiPlugin0PVPrefix", "roi0_pref"))
        form.addRow("ROI plugin 1:",    self._sp("RoiPlugin1PVPrefix", "roi1_pref"))
        form.addRow("Circular buffer 0:",
                    self._sp("CbPlugin0PVPrefix", "cb0_pref"))
        form.addRow("Circular buffer 1:",
                    self._sp("CbPlugin1PVPrefix", "cb1_pref"))

        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(scroll)
        self._tabs.addTab(page, "IOC Config")
