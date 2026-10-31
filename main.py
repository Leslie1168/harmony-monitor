# -*- coding: utf-8 -*-
"""鸿蒙设备监控 · 程序入口。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
# 打包后配置写在 exe 同目录，便于用户保留设置
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "monitor.log")


def _setup_streams():
    """pythonw / --windowed 下 stdout 与 stderr 均为 None，必须及早兜底到日志文件。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def _install_excepthook():
    def hook(exc_type, exc_value, exc_tb):
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n===== 未捕获异常 =====\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


_setup_streams()
_install_excepthook()

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from app import collectors as C
from app.hdc_client import HDC_EXE, HdcClient
from app.overlay import MINI_REQUIRED, OverlayWindow, appearance
from app.settings_dialog import SettingsDialog
from app.worker import DeviceWorker, make_client


ADB_PORTS = {"5555", "5037"}

# 单实例本地 IPC 名称：固定于应用与用户会话，与发布路径无关
_USER = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
INSTANCE_KEY = f"HarmonyMonitor.Overlay.{_USER}"


class InstanceAlreadyRunning(Exception):
    """另一个实例已取得所有权，本进程应静默退出。"""


def make_instance_lock() -> QLockFile:
    """用文件锁消除两个进程同时通过 IPC 探测的启动竞争。"""
    digest = hashlib.sha256(INSTANCE_KEY.encode("utf-8")).hexdigest()[:20]
    return QLockFile(os.path.join(tempfile.gettempdir(), f"HarmonyMonitor-{digest}.lock"))


def activate_existing_instance() -> bool:
    """尝试唤醒已运行的实例。成功返回 True（本进程应直接退出）。"""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if not sock.waitForConnected(400):
        return False
    try:
        sock.write(b"raise")
        sock.flush()
        sock.waitForBytesWritten(400)
    finally:
        sock.disconnectFromServer()
    return True


def listen_instance_server(server: QLocalServer):
    """取得 IPC 监听权；Windows 端口被占用通常意味着实例仍在运行。"""
    if server.listen(INSTANCE_KEY):
        return
    if activate_existing_instance():
        raise InstanceAlreadyRunning
    if (os.name == "nt"
            and server.serverError() == QAbstractSocket.SocketError.AddressInUseError):
        # Windows 的管道随进程消失；这里不删除，避免影响忙于长操作的旧实例。
        raise InstanceAlreadyRunning
    QLocalServer.removeServer(INSTANCE_KEY)
    if server.listen(INSTANCE_KEY):
        return
    if activate_existing_instance():
        raise InstanceAlreadyRunning
    raise RuntimeError(f"无法创建单实例监听：{server.errorString()}")


def normalize_devices(raw) -> list[dict]:
    """兼容旧版纯字符串配置，丢弃非法项并避免地址重复。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen = set()
    for d in raw:
        if isinstance(d, str):
            addr = d.strip()
            port = addr.rsplit(":", 1)[-1] if ":" in addr else ""
            dtype = "adb" if port in ADB_PORTS else "hdc"
        elif isinstance(d, dict):
            addr = d.get("addr", "")
            addr = addr.strip() if isinstance(addr, str) else ""
            dtype = d.get("type", "hdc")
            dtype = dtype.lower() if isinstance(dtype, str) else ""
        else:
            continue
        if not addr or dtype not in ("adb", "hdc"):
            continue
        if ":" not in addr:
            addr += ":5555" if dtype == "adb" else ":8710"
        host, port = addr.rsplit(":", 1)
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535 or addr in seen:
            continue
        seen.add(addr)
        out.append({"addr": addr, "type": dtype})
    return out


def default_config() -> dict:
    return {
        "devices": [],
        "selected": [i.id for i in C.ITEMS if i.default],
        "window": {},
    }


def normalize_config(cfg) -> dict:
    """保留未知扩展字段，同时修复旧配置并隔离损坏字段。"""
    base = default_config()
    if not isinstance(cfg, dict):
        return base
    base.update(cfg)
    base["devices"] = normalize_devices(cfg.get("devices"))

    selected = cfg.get("selected")
    if isinstance(selected, list):
        base["selected"] = list(dict.fromkeys(
            item for item in selected if isinstance(item, str) and item in C.ITEM_MAP
        ))
    else:
        base["selected"] = default_config()["selected"]

    window = cfg.get("window")
    normalized_window = {}
    if isinstance(window, dict):
        for key in ("x", "y"):
            value = window.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                normalized_window[key] = int(value)
    base["window"] = normalized_window
    base["appearance"] = dict(cfg.get("appearance", {})) if isinstance(cfg.get("appearance"), dict) else {}
    return base


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return normalize_config(json.load(f))
        except (OSError, ValueError, TypeError):
            pass
    return default_config()


def save_config(cfg: dict):
    """同目录临时文件 + os.replace，失败时保留上一份完整配置。"""
    directory = os.path.dirname(CONFIG_PATH) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def make_icon() -> QIcon:
    """优先使用随包的鸿蒙风格图标，缺失时回退到内置绘制。"""
    for d in (getattr(sys, "_MEIPASS", ""), APP_DIR, BASE):
        if not d:
            continue
        p = os.path.join(d, "app_harmony.ico")
        if os.path.exists(p):
            ico = QIcon(p)
            if not ico.isNull():
                return ico

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(77, 163, 255))
    p.drawRoundedRect(6, 6, 52, 52, 14, 14)
    p.setBrush(QColor(11, 18, 32))
    p.drawRoundedRect(14, 20, 36, 8, 4, 4)
    p.drawRoundedRect(14, 32, 26, 8, 4, 4)
    p.drawRoundedRect(14, 44, 16, 6, 3, 3)
    p.end()
    return QIcon(pm)


class MonitorApp:
    def __init__(self, app: QApplication, instance_lock: QLockFile | None = None):
        self.app = app
        self.instance_lock = instance_lock
        self.cfg = load_config()
        self.clients: dict[str, object] = {}
        self.workers: dict[str, DeviceWorker] = {}
        self._pending_workers = set()
        self._workers_stopping = False
        self._restart_after_stop = False
        self._quitting = False
        self._quit_complete = False
        self._settings_dialog = None

        # 文件锁负责启动竞争，IPC 负责唤醒已有窗口。
        self.server = QLocalServer(self.app)
        listen_instance_server(self.server)
        self.server.newConnection.connect(self._on_second_instance)

        self.win = OverlayWindow(self.cfg)
        self.win.request_settings.connect(self.open_settings)
        self.win.request_quit.connect(self.quit)
        self.win.request_persist.connect(self.persist)
        self.win.destroyed.connect(lambda *_: None)

        self.tray = self._build_tray()
        self.win.show()

        self.restart_workers()

        # 未配置设备时直接引导
        if not self.cfg.get("devices"):
            self.open_settings()

    def _on_second_instance(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: c.readAll())
            conn.disconnectFromServer()
        if self._quitting:
            return
        # 唤醒：隐藏状态也应恢复并抬到前台
        if not self.win.isVisible():
            self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def _save_config(self) -> bool:
        try:
            save_config(self.cfg)
            return True
        except OSError as exc:
            QMessageBox.warning(self.win, "配置保存失败", f"原配置未被覆盖，请检查目录写入权限。\n\n{exc}")
            return False

    def persist(self):
        """模式/缩放/置顶等窗口侧改动立即落盘。"""
        self.win.save_geometry()
        self._save_config()

    # ------------------------------------------------------------------
    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(make_icon(), self.app)
        tray.setToolTip("鸿蒙设备监控")
        menu = QMenu()
        act_show = QAction("显示 / 隐藏", menu)
        act_show.triggered.connect(self._toggle_visible)
        act_cfg = QAction("设置…", menu)
        act_cfg.triggered.connect(self.open_settings)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_show)
        menu.addAction(act_cfg)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._toggle_visible()
            if reason == QSystemTrayIcon.Trigger else None
        )
        tray.show()
        return tray

    def _toggle_visible(self):
        if self.win.isVisible():
            self.win.hide()
        else:
            self.win.show()
            self.win.raise_()

    # ------------------------------------------------------------------
    def _selected_ids(self) -> set[str]:
        ids = set(self.cfg.get("selected", []))
        # 迷你/迷你扩展模式必须持续采集 CPU/内存/电量/充电状态与详情条字段，
        # 即使用户在监控项设置里取消勾选也不能断流
        if appearance(self.cfg).get('mode') in ('mini', 'mini3'):
            ids |= MINI_REQUIRED
        return ids

    def restart_workers(self):
        """请求停止旧线程；完成信号回到 UI 线程后再启动新线程。"""
        if self._quitting:
            return
        self._restart_after_stop = True
        if self.workers:
            self._request_stop_workers()
        elif not self._workers_stopping:
            self._restart_after_stop = False
            self._start_workers()

    def _request_stop_workers(self):
        if self._workers_stopping or not self.workers:
            return
        self._workers_stopping = True
        self._pending_workers = set(self.workers.values())
        for worker in self._pending_workers:
            worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        for worker in list(self._pending_workers):
            worker.stop()
            if worker.isFinished():
                self._on_worker_finished(worker)

    def _on_worker_finished(self, worker):
        if worker not in self._pending_workers:
            return
        self._pending_workers.remove(worker)
        worker.deleteLater()
        if self._pending_workers:
            return
        self.workers.clear()
        self._workers_stopping = False
        if self._quitting:
            self._finish_quit()
        elif self._restart_after_stop:
            self._restart_after_stop = False
            self._start_workers()

    def _start_workers(self):
        if self._quitting:
            return
        self.workers.clear()
        for dev in self.cfg.get("devices", []):
            addr = dev["addr"]
            dtype = dev.get("type", "hdc")
            client = self.clients.get(dtype)
            if client is None:
                client = make_client(dtype)
                self.clients[dtype] = client
            w = DeviceWorker(client, addr, self._selected_ids, dtype, self.win)
            w.updated.connect(self.win.on_data)
            w.status.connect(self.win.on_status)
            w.start()
            self.workers[addr] = w

    def open_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dlg = SettingsDialog(self.cfg, self.win)
        self._settings_dialog = dlg
        dlg.appearance_changed.connect(self.win.apply_appearance)
        accepted = dlg.exec()
        self._settings_dialog = None
        if accepted:
            new = dlg.result_config()
            new_devices = normalize_devices(new["devices"])
            devices_changed = new_devices != self.cfg.get("devices", [])
            updated = dict(self.cfg)
            updated["devices"] = new_devices
            updated["selected"] = new["selected"]
            # 合并而非整体替换：保留设置页不管理的字段（mode/mini_locked/tab_device）
            updated["appearance"] = dict(self.cfg.get("appearance", {}))
            updated["appearance"].update(new["appearance"])
            previous = self.cfg
            self.cfg = updated
            if self._save_config():
                self.win.config = self.cfg
                if devices_changed:
                    self.win.rebuild_cards()
                    self.restart_workers()
                else:
                    self.win.refresh_selection()
            else:
                self.cfg = previous
        if not self._quitting:
            self.win.apply_appearance()
        dlg.deleteLater()

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        self._restart_after_stop = False
        self.win.hide()
        if self._settings_dialog is not None:
            self._settings_dialog.reject()
        self.win.save_geometry()
        self._save_config()
        self._request_stop_workers()
        if not self._workers_stopping:
            self._finish_quit()

    def _finish_quit(self):
        if self._quit_complete:
            return
        self._quit_complete = True
        self.server.close()
        QLocalServer.removeServer(INSTANCE_KEY)
        self.tray.hide()
        if self.instance_lock is not None:
            self.instance_lock.unlock()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("鸿蒙设备监控")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(make_icon())

    if not os.path.exists(HDC_EXE):
        QMessageBox.critical(None, "缺少组件", "未找到 hdc\\hdc.exe，程序无法运行。")
        return 1

    # 先走快速唤醒，再用文件锁消除同时启动时的探测空窗。
    if activate_existing_instance():
        return 0
    instance_lock = make_instance_lock()
    if not instance_lock.tryLock(100):
        activate_existing_instance()
        return 0

    try:
        monitor = MonitorApp(app, instance_lock)
    except InstanceAlreadyRunning:
        instance_lock.unlock()
        return 0
    except Exception as exc:
        instance_lock.unlock()
        QMessageBox.critical(None, "启动失败", str(exc))
        return 1
    monitor.win.destroyed.connect(monitor.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
