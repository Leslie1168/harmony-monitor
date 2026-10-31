# -*- coding: utf-8 -*-
"""HDC 命令封装：负责连接管理与命令执行。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

def _candidate_hdc() -> str:
    """按优先级定位 hdc.exe：exe 同目录 > 打包资源目录 > 源码目录。"""
    cands: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        cands.append(os.path.join(exe_dir, "hdc", "hdc.exe"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(os.path.join(meipass, "hdc", "hdc.exe"))
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cands.append(os.path.join(root, "hdc", "hdc.exe"))
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0] if cands else "hdc"


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDC_EXE = _candidate_hdc()
HDC_DIR = os.path.dirname(HDC_EXE)

# Windows 下隐藏子进程控制台窗口
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run_process(cmd, timeout, stop_event=None):
    """Drain both pipes while allowing a worker to cancel its own child only."""
    if stop_event is not None and stop_event.is_set():
        return -4, "", "cancelled"
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=_NO_WINDOW) as process:
            deadline = time.monotonic() + timeout
            while True:
                cancelled = stop_event is not None and stop_event.is_set()
                remaining = deadline - time.monotonic()
                if cancelled or remaining <= 0:
                    process.kill()
                    process.communicate()
                    return (-4, "", "cancelled") if cancelled else (-1, "", "timeout")
                try:
                    out, err = process.communicate(timeout=min(.1, remaining))
                    return process.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")
                except subprocess.TimeoutExpired:
                    continue
    except FileNotFoundError:
        return -2, "", f"executable not found: {cmd[0]}"
    except OSError as exc:
        return -3, "", str(exc)


class HdcError(Exception):
    pass


class HdcClient:
    """全局唯一的 hdc 调用入口，内部串行化 server 启动。"""

    _lock = threading.Lock()
    _server_started = False

    def __init__(self, exe: str = HDC_EXE):
        self.exe = exe if os.path.exists(exe) else "hdc"

    # ------------------------------------------------------------------ 基础
    def run(self, args, timeout=15.0, stop_event=None):
        return _run_process([self.exe] + args, timeout, stop_event)

    def ensure_server(self, stop_event=None) -> bool:
        if stop_event is not None and stop_event.is_set():
            return False
        with self._lock:
            if HdcClient._server_started:
                return True
            rc, out, _ = self.run(["list", "targets"], timeout=10, stop_event=stop_event)
            HdcClient._server_started = rc == 0
            return HdcClient._server_started

    # ------------------------------------------------------------------ 连接
    def list_targets(self, stop_event=None) -> list[str]:
        rc, out, _ = self.run(["list", "targets"], timeout=10, stop_event=stop_event)
        if rc != 0:
            return []
        targets = []
        for line in out.splitlines():
            line = line.strip()
            if not line or "Empty" in line or line.startswith("["):
                continue
            targets.append(line)
        return targets

    def connect(self, addr: str, timeout: float = 8.0, stop_event=None) -> tuple[bool, str]:
        """连接无线设备。返回 (是否成功, 原始消息)。"""
        addr = addr.strip()
        if not addr:
            return False, "地址为空"
        if ":" not in addr:
            addr = f"{addr}:8710"
        rc, out, err = self.run(["tconn", addr], timeout=timeout, stop_event=stop_event)
        msg = (out or err).strip()
        ok = rc == 0 and ("Connect OK" in msg or "connected" in msg.lower())
        return ok, msg

    def disconnect(self, addr: str) -> None:
        self.run(["tconn", addr, "-remove"], timeout=6)

    def is_online(self, addr: str, stop_event=None) -> bool:
        return addr in self.list_targets(stop_event=stop_event)

    # ------------------------------------------------------------------ 执行
    def shell(self, addr: str, command: str, timeout: float = 12.0, stop_event=None) -> str:
        rc, out, err = self.run(["-t", addr, "shell", command], timeout=timeout, stop_event=stop_event)
        if rc != 0:
            raise HdcError(err.strip() or f"rc={rc}")
        if not out.strip():
            raise HdcError("empty shell response")
        return out.replace("\r", "")

    def model_info(self, addr: str) -> dict[str, str]:
        """一次取回设备静态信息。"""
        cmd = (
            "param get const.product.name;"
            "param get const.product.model;"
            "param get const.product.brand;"
            "param get const.ohos.fullname;"
            "param get const.ohos.apiversion;"
            "param get const.product.devicetype;"
            "bm get --udid"
        )
        out = self.shell(addr, cmd, timeout=20)
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        vals = [x for x in lines if not x.startswith("udid of")]
        info = {
            "name": vals[0] if len(vals) > 0 else "",
            "model": vals[1] if len(vals) > 1 else "",
            "brand": vals[2] if len(vals) > 2 else "",
            "os": vals[3] if len(vals) > 3 else "",
            "api": vals[4] if len(vals) > 4 else "",
            "devicetype": vals[5] if len(vals) > 5 else "",
            "udid": "",
        }
        m = re.search(r"([0-9A-Fa-f]{32,})", out)
        if m:
            info["udid"] = m.group(1)
        return info


def wait_for_online(client: HdcClient, addr: str, retries: int = 3) -> bool:
    for i in range(retries):
        ok, _ = client.connect(addr)
        if ok:
            time.sleep(0.6)
            if client.is_online(addr):
                return True
        time.sleep(0.8)
    return client.is_online(addr)


class AdbClient:
    """ADB 后端，接口与 HdcClient 保持一致，供鸿蒙 4.x 设备使用。"""

    _lock = threading.Lock()
    _server_started = False

    def __init__(self, exe: str = ""):
        self.exe = exe or _candidate_adb()

    def run(self, args, timeout=15.0, stop_event=None):
        return _run_process([self.exe] + args, timeout, stop_event)

    def ensure_server(self, stop_event=None) -> bool:
        if stop_event is not None and stop_event.is_set():
            return False
        with self._lock:
            if AdbClient._server_started:
                return True
            rc, _, _ = self.run(["start-server"], timeout=15, stop_event=stop_event)
            AdbClient._server_started = rc == 0
            return AdbClient._server_started

    def list_targets(self, stop_event=None) -> list[str]:
        rc, out, _ = self.run(["devices"], stop_event=stop_event)
        if rc != 0:
            return []
        result = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                result.append(parts[0])
        return result

    def connect(self, addr: str, timeout: float = 8.0, stop_event=None) -> tuple[bool, str]:
        if not addr or ":" not in addr:
            return False, "地址需包含端口"
        rc, out, err = self.run(["connect", addr], timeout=timeout, stop_event=stop_event)
        msg = (out or err).strip()
        ok = rc == 0 and bool(re.match(r"^(?:already )?connected to\s", msg, re.I))
        return ok, msg

    def disconnect(self, addr: str) -> None:
        self.run(["disconnect", addr], timeout=6)

    def is_online(self, addr: str, stop_event=None) -> bool:
        return addr in self.list_targets(stop_event=stop_event)

    def shell(self, addr: str, command: str, timeout: float = 12.0, stop_event=None) -> str:
        rc, out, err = self.run(["-s", addr, "shell", command], timeout=timeout, stop_event=stop_event)
        if rc != 0:
            raise HdcError(err.strip() or f"rc={rc}")
        if not out.strip():
            raise HdcError("empty shell response")
        return out.replace("\r", "")


def _candidate_adb() -> str:
    cands = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        cands.append(os.path.join(exe_dir, "tools", "adb", "adb.exe"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(os.path.join(meipass, "tools", "adb", "adb.exe"))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands.append(os.path.join(root, "tools", "adb", "adb.exe"))
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]


if __name__ == "__main__":
    c = HdcClient()
    print("hdc:", c.exe)
    print("server:", c.ensure_server())
    print("targets:", c.list_targets())
    for t in sys.argv[1:]:
        print(f"\n== {t} ==")
        print(c.connect(t))
        print(c.model_info(t))
