"""PV layer: real-time monitors over pvaccess CA plus a background caput pool."""
import subprocess
import threading
from typing import Dict
from concurrent.futures import ThreadPoolExecutor
from PyQt5 import QtCore
from pvaccess import Channel, CA


class PVEngine(QtCore.QObject):
    updated = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: Dict[str, Channel] = {}
        self._lock = threading.Lock()

    def monitor(self, pv_name: str):
        with self._lock:
            if pv_name in self._channels:
                return
            try:
                ch = Channel(pv_name, CA)
                self._channels[pv_name] = ch
                ch.subscribe(pv_name, lambda pv, n=pv_name: self._on_change(n, pv))
                ch.startMonitor()
            except Exception as e:
                print(f"[PV] failed to monitor {pv_name}: {e}")

    def monitor_many(self, pvs):
        for pv in pvs:
            self.monitor(pv)

    @staticmethod
    def _extract(pv_obj):
        v = pv_obj['value']
        if isinstance(v, dict):
            if 'index' in v and 'choices' in v:
                idx = v['index']
                choices = v['choices']
                if isinstance(choices, (list, tuple)) and 0 <= idx < len(choices):
                    return choices[idx]
                return str(idx)
            return str(v)
        if isinstance(v, (list, tuple)):
            if len(v) > 0 and isinstance(v[0], int):
                ba = bytes(b for b in v if b != 0)
                return ba.decode('utf-8', errors='replace')
            return str(v)
        return str(v)

    def get(self, pv_name: str):
        ch = self._channels.get(pv_name)
        if ch is None:
            try:
                ch = Channel(pv_name, CA)
            except Exception:
                return None
        try:
            return self._extract(ch.get())
        except Exception:
            return None

    def _on_change(self, pv_name, pv_obj):
        try:
            val = self._extract(pv_obj)
            self.updated.emit(pv_name, val)
        except Exception:
            pass

    def stop_all(self):
        with self._lock:
            for name, ch in self._channels.items():
                try:
                    ch.stopMonitor()
                    ch.unsubscribe(name)
                except Exception:
                    pass
            self._channels.clear()


_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="ca")
_put_channels: Dict[str, Channel] = {}
_put_pv_locks: Dict[str, threading.Lock] = {}
_put_locks_guard = threading.Lock()


def _get_put_channel(pv: str):
    # Reuse one Channel per PV: creating a new pvaccess Channel on every caput
    # leaks CA resources and, under heavy use, wedges the CA client — which
    # appears to the user as the GUI getting stuck. Per-PV locks so a slow
    # Channel() call for a sluggish IOC (e.g. PLC) can't block puts to every
    # other PV.
    ch = _put_channels.get(pv)
    if ch is not None:
        return ch
    with _put_locks_guard:
        lock = _put_pv_locks.get(pv)
        if lock is None:
            lock = threading.Lock()
            _put_pv_locks[pv] = lock
    with lock:
        ch = _put_channels.get(pv)
        if ch is None:
            ch = Channel(pv, CA)
            _put_channels[pv] = ch
        return ch


def caput_bg(pv, val, t=10.0):
    def _do():
        try:
            _get_put_channel(pv).put(val)
        except Exception:
            try:
                subprocess.run(["caput", pv, str(val)],
                               capture_output=True, timeout=t)
            except Exception:
                pass
    _pool.submit(_do)
