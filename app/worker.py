"""Per-device polling with reconnects, isolated tiers and complete metric frames."""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time as _time
from PySide6.QtCore import QThread, Signal
from . import adb_collectors
from . import collectors as C
from .hdc_client import AdbClient, HdcClient, HdcError

_APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_logger = logging.getLogger('harmony.worker')
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    handler = RotatingFileHandler(os.path.join(_APP_DIR, 'worker.log'),
                                 maxBytes=2097152, backupCount=2, encoding='utf-8', delay=True)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    _logger.addHandler(handler)

def _log(message):
    _logger.info(message)

class DeviceWorker(QThread):
    updated = Signal(str, dict)
    status = Signal(str, str)

    def __init__(self, client, addr, get_selected, device_type='hdc', parent=None):
        super().__init__(parent)
        self.client, self.addr = client, addr
        self.device_type = device_type
        self.mod = adb_collectors if device_type == 'adb' else C
        self._get_selected = get_selected
        self._stop = threading.Event()
        self._static, self._state = {}, {}
        self._last = dict.fromkeys(('fast', 'mid', 'slow'), float('-inf'))
        self._load_zero_streak = 0

    def stop(self):
        self._stop.set()

    def _frame(self, tier, values):
        # UI merges dictionaries; omissions would preserve stale readings.
        frame = {i.id: None for i in C.ITEMS if i.tier == tier}
        if tier == 'fast':
            frame.update(dict.fromkeys(('mem_total', 'storage_total', 'storage_used', 'cores')))
            frame['load_invalid'] = False
        frame.update(values)
        return frame

    def _clear_dynamic(self):
        for tier in self._last:
            self.updated.emit(self.addr, self._frame(tier, {}))

    def _collect_static(self):
        try:
            raw = self.client.shell(self.addr, self.mod.CMD_STATIC, timeout=25, stop_event=self._stop)
            self._static = self.mod.parse_static(raw)
            self.updated.emit(self.addr, self._frame('static', self._static))
            return any(v not in (None, '') for v in self._static.values())
        except Exception as exc:
            self._static = {}
            self.updated.emit(self.addr, self._frame('static', {}))
            _log(f'{self.addr} static ERROR {type(exc).__name__}: {exc}')
            return False

    def run(self):
        connected = False
        failures = dict.fromkeys(self._last, 0)
        last_probe = float('-inf')
        while not self._stop.is_set():
            if not connected:
                self.status.emit(self.addr, 'connecting')
                if not self._connect():
                    self.status.emit(self.addr, 'offline')
                    self._clear_dynamic()
                    if self._stop.wait(3):
                        break
                    continue
                if self._stop.is_set():
                    break
                self._reset_sampling()
                failures = dict.fromkeys(self._last, 0)
                connected = True
                self.status.emit(self.addr, 'online')
                self._collect_static()
                last_probe = _time.monotonic()
            start = _time.monotonic()
            selected = set(self._get_selected() or ())
            active = [tier for tier in self._last if C.needs_tier(selected, tier)]
            # Empty/static-only selections still require liveness monitoring.
            if not active and start - last_probe >= 5:
                connected = self.client.is_online(self.addr, stop_event=self._stop)
                last_probe = _time.monotonic()
                if not connected:
                    self.status.emit(self.addr, 'offline')
                    self._clear_dynamic()
                    continue
            for tier in self._last:
                if self._stop.is_set():
                    break
                if tier not in active:
                    self._last[tier] = float('-inf')
                    failures[tier] = 0
                    if tier == 'fast':
                        self._state.clear()
                    continue
                if not self._due(tier):
                    continue
                self._last[tier] = _time.monotonic()
                try:
                    raw = self.client.shell(self.addr, self._command(tier, selected),
                                            timeout=15 if tier == 'fast' else 18, stop_event=self._stop)
                    parser = getattr(self.mod, 'parse_' + tier)
                    values = parser(raw, self._state) if tier == 'fast' else parser(raw)
                    if not any(value is not None for value in values.values()) and not (tier == 'fast' and 'cpu_stat' in self._state):
                        raise HdcError(f'{tier}: no recognised metrics')
                    data = self._frame(tier, values)
                    if tier == 'fast':
                        self._check_load_validity(data)
                    self.updated.emit(self.addr, data)
                    failures[tier] = 0
                except Exception as exc:
                    failures[tier] += 1
                    self.updated.emit(self.addr, self._frame(tier, {}))
                    if tier == 'fast':
                        self._state.clear()
                        self._load_zero_streak = 0
                    _log(f'{self.addr} {tier} ERROR {type(exc).__name__}: {exc}')
                    # Unsupported commands must not suppress other tiers or disconnect healthy devices.
                    if failures[tier] >= 3:
                        connected = self.client.is_online(self.addr, stop_event=self._stop)
                        if not connected:
                            self.status.emit(self.addr, 'offline')
                            self._clear_dynamic()
                            break
            self._sleep_rest(1, start)

    def _command(self, tier, selected):
        command = getattr(self.mod, 'CMD_' + tier.upper())
        if tier == 'fast':
            # Keep section markers, but avoid expensive process enumeration unless requested.
            if 'procs' not in selected:
                command = command.replace('ps -A | wc -l', ':').replace('ps -ef | wc -l', ':')
            if not selected.intersection({'storage_pct', 'storage_free'}):
                command = command.replace('df -h /data;', ':;')
            if 'load' not in selected:
                command = command.replace('nproc;', ':;')
        return command

    def _due(self, tier):
        return _time.monotonic() - self._last[tier] >= C.TIER_INTERVAL[tier]

    def _sleep_rest(self, interval, start):
        self._stop.wait(max(0, interval - (_time.monotonic() - start)))

    def _connect(self):
        if self._stop.is_set():
            return False
        try:
            if not self.client.ensure_server(stop_event=self._stop):
                return False
        except Exception as exc:
            _log(f'{self.addr} server ERROR {type(exc).__name__}: {exc}')
            return False
        for _ in range(3):
            if self._stop.is_set():
                return False
            try:
                ok, _ = self.client.connect(self.addr, stop_event=self._stop)
                if ok:
                    if self._stop.wait(.5):
                        return False
                    if self.client.is_online(self.addr, stop_event=self._stop):
                        return True
            except Exception as exc:
                _log(f'{self.addr} connect ERROR {type(exc).__name__}: {exc}')
            if self._stop.wait(.7):
                return False
        return not self._stop.is_set() and self.client.is_online(self.addr, stop_event=self._stop)

    def _check_load_validity(self, data):
        load, cpu = data.get('load'), data.get('cpu')
        if isinstance(load, (int, float)) and load == 0 and isinstance(cpu, (int, float)) and cpu > 1:
            self._load_zero_streak += 1
        else:
            self._load_zero_streak = 0
        data['load_invalid'] = self._load_zero_streak >= 5
        if self._load_zero_streak == 5:
            _log(f'{self.addr} load repeatedly zero while CPU active; marking unavailable')

    def _reset_sampling(self):
        self._state.clear()
        self._load_zero_streak = 0
        self._last = dict.fromkeys(self._last, float('-inf'))

    def _reconnect(self):
        # Never disconnect a shared ADB/HDC target used by another app.
        self._reset_sampling()
        return self._connect()


def make_client(device_type):
    return AdbClient() if device_type == 'adb' else HdcClient()
