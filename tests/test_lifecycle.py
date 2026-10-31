import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
    ROOT = Path(sys.argv.pop(1)).resolve()
else:
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as M
from app.overlay import appearance
from app.worker import DeviceWorker
from PySide6.QtCore import QCoreApplication, QLockFile, QObject, Signal
from PySide6.QtNetwork import QAbstractSocket, QLocalServer


class DummyWorker(QObject):
    finished = Signal()
    updated = Signal(str, dict)
    status = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.stop = Mock()
        self.wait = Mock()
        self.start = Mock()
        self._finished = False

    def isFinished(self):
        return self._finished

    def finish(self):
        self._finished = True
        self.finished.emit()


def bare_monitor(workers=None):
    monitor = M.MonitorApp.__new__(M.MonitorApp)
    monitor.app = Mock()
    monitor.instance_lock = Mock()
    monitor.cfg = {"devices": [], "selected": [], "window": {}, "appearance": {}}
    monitor.clients = {}
    monitor.workers = workers or {}
    monitor._pending_workers = set()
    monitor._workers_stopping = False
    monitor._restart_after_stop = False
    monitor._quitting = False
    monitor._quit_complete = False
    monitor._settings_dialog = None
    monitor.win = Mock()
    monitor.server = Mock()
    monitor.tray = Mock()
    return monitor


class ConfigTests(unittest.TestCase):
    def test_atomic_save_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            expected = {"devices": [], "selected": ["cpu"], "window": {}, "备注": "保留中文"}
            with patch.object(M, "CONFIG_PATH", str(path)):
                M.save_config(expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
            self.assertEqual(list(Path(directory).glob(".config-*.tmp")), [])

    def test_failed_replace_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            original = '{"devices": [], "sentinel": true}\n'
            path.write_text(original, encoding="utf-8")
            with patch.object(M, "CONFIG_PATH", str(path)), \
                 patch.object(M.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    M.save_config({"replacement": True})
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(Path(directory).glob(".config-*.tmp")), [])

    def test_normalize_devices_rejects_invalid_and_duplicates(self):
        raw = [
            "192.168.1.23:5555",
            {"addr": "host", "type": "adb"},
            {"addr": "host:5555", "type": "adb"},
            {"addr": "bad:0", "type": "adb"},
            {"addr": "bad:abc", "type": "hdc"},
            {"addr": 123, "type": "hdc"},
            {"addr": "bad", "type": "unknown"},
        ]
        self.assertEqual(M.normalize_devices(raw), [
            {"addr": "192.168.1.23:5555", "type": "adb"},
            {"addr": "host:5555", "type": "adb"},
        ])

    def test_damaged_fields_fall_back_independently(self):
        cfg = M.normalize_config({
            "devices": {},
            "selected": "cpu",
            "window": {"x": "bad", "y": 20},
            "appearance": [],
            "future_field": 7,
        })
        self.assertEqual(cfg["devices"], [])
        self.assertIn("cpu", cfg["selected"])
        self.assertEqual(cfg["window"], {"y": 20})
        self.assertEqual(cfg["appearance"], {})
        self.assertEqual(cfg["future_field"], 7)
        values = appearance({"appearance": {
            "opacity": "bad", "scale": None, "layout": "invalid", "topmost": "false"
        }})
        self.assertEqual(values["opacity"], 92)
        self.assertEqual(values["scale"], 100)
        self.assertEqual(values["layout"], "horizontal")
        self.assertIs(values["topmost"], True)


class SingleInstanceTests(unittest.TestCase):
    def test_file_lock_excludes_competing_process_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory, "instance.lock"))
            first, second = QLockFile(path), QLockFile(path)
            self.assertTrue(first.tryLock(0))
            self.assertFalse(second.tryLock(0))
            first.unlock()
            self.assertTrue(second.tryLock(0))
            second.unlock()

    def test_live_ipc_owner_is_never_removed(self):
        server = Mock()
        server.listen.return_value = False
        server.serverError.return_value = QAbstractSocket.SocketError.AddressInUseError
        with patch.object(M, "activate_existing_instance", return_value=True), \
             patch.object(M.QLocalServer, "removeServer") as remove:
            with self.assertRaises(M.InstanceAlreadyRunning):
                M.listen_instance_server(server)
        remove.assert_not_called()

    def test_windows_occupied_ipc_does_not_fall_back_to_second_owner(self):
        server = Mock()
        server.listen.return_value = False
        server.serverError.return_value = QAbstractSocket.SocketError.AddressInUseError
        with patch.object(M, "os", wraps=M.os) as os_mock, \
             patch.object(M, "activate_existing_instance", return_value=False), \
             patch.object(M.QLocalServer, "removeServer") as remove:
            os_mock.name = "nt"
            with self.assertRaises(M.InstanceAlreadyRunning):
                M.listen_instance_server(server)
        remove.assert_not_called()


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def test_restart_is_asynchronous_and_never_waits_on_ui(self):
        worker = DummyWorker()
        monitor = bare_monitor({"old": worker})
        monitor._start_workers = Mock()
        monitor.restart_workers()
        worker.stop.assert_called_once_with()
        worker.wait.assert_not_called()
        monitor._start_workers.assert_not_called()
        worker.finish()
        monitor._start_workers.assert_called_once_with()
        self.assertFalse(monitor._workers_stopping)

    def test_start_workers_does_not_start_services_on_ui_thread(self):
        monitor = bare_monitor()
        monitor.cfg["devices"] = [{"addr": "test:5555", "type": "adb"}]
        client = Mock()
        worker = DummyWorker()
        with patch.object(M, "make_client", return_value=client), \
             patch.object(M, "DeviceWorker", return_value=worker):
            monitor._start_workers()
        client.ensure_server.assert_not_called()
        worker.start.assert_called_once_with()

    def test_quit_finishes_only_after_worker_finished(self):
        worker = DummyWorker()
        monitor = bare_monitor({"old": worker})
        monitor._settings_dialog = Mock()
        with patch.object(M, "save_config"), \
             patch.object(M.QLocalServer, "removeServer"):
            monitor.quit()
            worker.stop.assert_called_once_with()
            worker.wait.assert_not_called()
            monitor._settings_dialog.reject.assert_called_once_with()
            monitor.app.quit.assert_not_called()
            monitor.instance_lock.unlock.assert_not_called()
            worker.finish()
        monitor.app.quit.assert_called_once_with()
        monitor.instance_lock.unlock.assert_called_once_with()

    def test_real_qthread_quit_stays_responsive(self):
        client = Mock()
        client.ensure_server.return_value = True
        client.connect.return_value = (False, "offline")
        client.is_online.return_value = False
        worker = DeviceWorker(client, "test:5555", lambda: set(), "adb")
        monitor = bare_monitor({"test:5555": worker})
        with patch.object(M, "save_config"), \
             patch.object(M.QLocalServer, "removeServer"):
            started = time.monotonic()
            worker.start()
            monitor.quit()
            while not monitor._quit_complete and time.monotonic() - started < 2:
                self.qt.processEvents()
                time.sleep(.005)
        self.assertTrue(monitor._quit_complete)
        self.assertTrue(worker.isFinished())
        self.assertLess(time.monotonic() - started, 2)

    def test_worker_connect_passes_cancellation_event(self):
        client = Mock()
        client.ensure_server.return_value = True

        def cancelled(addr, stop_event=None):
            stop_event.set()
            return False, "cancelled"

        client.connect.side_effect = cancelled
        worker = DeviceWorker(client, "test:5555", lambda: set(), "adb")
        self.assertFalse(worker._connect())
        client.ensure_server.assert_called_once_with(stop_event=worker._stop)
        client.connect.assert_called_once_with("test:5555", stop_event=worker._stop)
        client.is_online.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
