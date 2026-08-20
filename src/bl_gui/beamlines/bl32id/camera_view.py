"""Embedded web view widget (camera stream, any web page).

Loads a URL into a ``QWebEngineView`` inside a bl_gui panel. Address
bar at the top lets you retype the URL and press Enter to navigate —
same widget can be used for a camera live view, a status dashboard, a
wiki page, etc. Uses PyQtWebEngine (Chromium under the hood).

Per-panel URL persistence: each panel's current URL is stored in
``~/.bl_gui/web_views.json`` keyed by the panel key so it survives a
restart. Panels created without a key (or with no saved entry) fall
back to the default URL.
"""
import json
import os
from typing import Optional

from PyQt5 import QtCore, QtWidgets


DEFAULT_URL = "http://10.54.102.89/live/index.html?Language=0&ViewMode=pull"

_URL_STORE = os.path.expanduser("~/.bl_gui/web_views.json")
# Per-host credential store. PLAINTEXT — acceptable for a beamline
# workstation where the file sits in $HOME (mode 0600 recommended),
# not for anything shared. For proper secrecy swap to `keyring`.
_CRED_STORE = os.path.expanduser("~/.bl_gui/web_credentials.json")


def _load_json(path: str) -> dict:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2)
        # Tighten permissions on the credential file so at least
        # random other users on the box can't grep the password.
        if path == _CRED_STORE:
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
    except Exception:
        pass


def _load_all_urls() -> dict:
    return _load_json(_URL_STORE)


def _save_url(panel_key: str, url: str) -> None:
    if not panel_key:
        return
    urls = _load_all_urls()
    urls[panel_key] = url
    _save_json(_URL_STORE, urls)


def _load_creds(host: str):
    d = _load_json(_CRED_STORE).get(host)
    if isinstance(d, dict) and "user" in d and "password" in d:
        return d["user"], d["password"]
    return None


def _save_creds(host: str, user: str, password: str) -> None:
    if not host:
        return
    all_ = _load_json(_CRED_STORE)
    all_[host] = {"user": user, "password": password}
    _save_json(_CRED_STORE, all_)


class CameraView(QtWidgets.QWidget):
    """Wrapping widget for a QWebEngineView. Class name kept for
    backward compatibility with existing code — it's really a general
    web view now, usable for any URL."""

    def __init__(self, url: Optional[str] = None,
                 panel_key: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._panel_key = panel_key or ""
        # Load the saved URL for this panel, if any; otherwise use the
        # explicit `url` argument, otherwise the module default. Order
        # so an explicit URL always wins over a stale saved one only
        # when explicitly passed by the caller.
        stored = _load_all_urls().get(self._panel_key) if self._panel_key else None
        self._url = url or stored or DEFAULT_URL
        self._view = None

        # Explicit expanding policy + a real minimum so a fresh panel
        # never shrinks the widget to 0 (which would render the URL
        # bar and the web view as invisible slivers).
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(240, 140)

        L = QtWidgets.QVBoxLayout(self)
        L.setContentsMargins(0, 0, 0, 0)
        L.setSpacing(2)

        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineView
        except Exception as e:
            msg = QtWidgets.QLabel(
                "QtWebEngine not installed — web view unavailable.\n\n"
                "In the pystream env, run:\n"
                "    pip install PyQtWebEngine\n\n"
                f"Import error: {e}"
            )
            msg.setWordWrap(True)
            msg.setAlignment(QtCore.Qt.AlignCenter)
            msg.setStyleSheet(
                "color:#e74c3c;background:#0e0e0e;padding:12px;"
                "border:1px solid #383838;border-radius:3px;")
            L.addWidget(msg)
            return

        # Toolbar at the TOP so a short panel can't push it out of
        # sight (the web view below has Expanding size policy).
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(2, 2, 2, 2); bar.setSpacing(4)
        self._url_edit = QtWidgets.QLineEdit(self._url)
        self._url_edit.setMinimumHeight(22)
        self._url_edit.returnPressed.connect(self._on_url_changed)
        self._url_edit.setToolTip(
            "URL — press Enter to load (camera stream, web page, dashboard, …)")
        # Exempt this line-edit from the blanket _toggle_edit disable so
        # the URL can still be typed while the panel is being dragged
        # into place. Read by main_window._toggle_edit.
        self._url_edit.setProperty("_bl_gui_always_enabled", True)
        self._url_edit.setStyleSheet(
            "background:#2d2d2d;color:#e0e0e0;font:8pt "
            "'Liberation Mono','DejaVu Sans Mono',monospace;"
            "border:1px solid #404040;border-radius:2px;padding:1px 4px;")
        btn_reload = QtWidgets.QPushButton("↻")
        btn_reload.setFixedSize(24, 22)
        btn_reload.setToolTip("Reload")
        btn_reload.clicked.connect(self.reload)
        btn_reload.setStyleSheet(
            "background:#2d2d2d;color:#e0e0e0;font:bold 10pt;"
            "border:1px solid #404040;border-radius:2px;padding:0;")
        bar.addWidget(self._url_edit, 1)
        bar.addWidget(btn_reload, 0)
        L.addLayout(bar)

        self._view = QWebEngineView(self)
        # HTTP Basic Auth: cameras (and many appliances) return 401
        # and expect a login prompt. QWebEngineView doesn't show one
        # by default, so we hook the page's authenticationRequired
        # signal and pop our own username/password dialog. Creds are
        # cached per-host for the session so page navigation inside
        # the same host doesn't re-prompt on every request.
        self._auth_cache = {}
        self._view.page().authenticationRequired.connect(self._on_auth)
        self._view.setUrl(QtCore.QUrl(self._url))
        L.addWidget(self._view, 1)

    # ── Public API ────────────────────────────────────────────────────
    def set_url(self, url: str):
        self._url = url or self._url
        if hasattr(self, "_url_edit"):
            self._url_edit.setText(self._url)
        if self._view is not None:
            self._view.setUrl(QtCore.QUrl(self._url))
        # Persist for next session so users don't have to re-type on
        # every launch. Silently no-op if this instance has no panel
        # key (e.g. constructed manually outside the plugin registry).
        _save_url(self._panel_key, self._url)

    def reload(self):
        if self._view is not None:
            self._view.reload()

    def _on_url_changed(self):
        text = self._url_edit.text().strip()
        if text:
            self.set_url(text)

    # ── Auth ──────────────────────────────────────────────────────────
    def _on_auth(self, url, authenticator):
        """Handle an HTTP-Basic-Auth challenge from the loaded page.
        Reuses saved credentials on-disk if the user checked
        'Remember' the last time this host prompted; otherwise (or if
        the disk credential was rejected) prompts. Session cache
        avoids re-reading disk on every embedded request."""
        host = url.host() or "?"
        # 1) in-memory cache from this session
        cached = self._auth_cache.get(host)
        if cached is not None:
            user, pw = cached
            authenticator.setUser(user)
            authenticator.setPassword(pw)
            return
        # 2) on-disk credentials (from a previous session's 'Remember')
        stored = _load_creds(host)
        if stored is not None:
            user, pw = stored
            self._auth_cache[host] = (user, pw)
            authenticator.setUser(user)
            authenticator.setPassword(pw)
            return
        # 3) prompt
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Login required — {host}")
        form = QtWidgets.QFormLayout(dlg)
        u_edit = QtWidgets.QLineEdit()
        p_edit = QtWidgets.QLineEdit()
        p_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        remember = QtWidgets.QCheckBox("Remember on this machine "
                                       "(stored in ~/.bl_gui/web_credentials.json)")
        remember.setChecked(True)
        form.addRow("User:", u_edit)
        form.addRow("Password:", p_edit)
        form.addRow(remember)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        user = u_edit.text()
        pw = p_edit.text()
        self._auth_cache[host] = (user, pw)
        if remember.isChecked():
            _save_creds(host, user, pw)
        authenticator.setUser(user)
        authenticator.setPassword(pw)
