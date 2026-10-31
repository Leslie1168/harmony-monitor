# -*- coding: utf-8 -*-
"""真实设备冒烟：需要三台设备在线时手动运行。

用法（项目 .venv）：
  python -B tests/live_smoke.py [秒数]
默认采样 14 秒，输出 JSON 并断言每台设备都有 CPU/电量样本、停止后全部退出。
"""
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.hdc_client import AdbClient, HdcClient
from app.worker import DeviceWorker

# 用环境变量指定你自己的设备，逗号分隔，格式 通道@地址：
#   set HARMONY_DEVICES=adb@192.168.1.23:5555,adb@192.168.1.24:5555,hdc@192.168.1.25:43891
def _devices():
    raw = os.environ.get("HARMONY_DEVICES", "").strip()
    if not raw:
        return [("adb", "192.168.1.23:5555", ROOT / "tools/adb/adb.exe")]
    out = []
    for part in raw.split(","):
        kind, _, addr = part.strip().partition("@")
        kind = kind.lower()
        exe = ROOT / ("tools/adb/adb.exe" if kind == "adb" else "hdc/hdc.exe")
        out.append((kind, addr, exe))
    return out

DEVICES = _devices()
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 14


def main():
    app = QCoreApplication([])
    records = {}
    workers = []
    for kind, addr, exe in DEVICES:
        client = AdbClient(str(exe)) if kind == "adb" else HdcClient(str(exe))
        worker = DeviceWorker(
            client, addr, lambda: {"cpu", "mem_pct", "battery", "uptime"}, kind
        )
        records[addr] = {
            "states": [],
            "frames": 0,
            "cpu_samples": 0,
            "battery_samples": 0,
        }

        def update(addr=addr, data=None):
            row = records[addr]
            row["frames"] += 1
            if data.get("cpu") is not None:
                row["cpu_samples"] += 1
            if data.get("battery") is not None:
                row["battery_samples"] += 1

        worker.updated.connect(update)
        worker.status.connect(
            lambda addr, state: records[addr]["states"].append(state)
        )
        workers.append(worker)

    for worker in workers:
        worker.start()
    QTimer.singleShot(DURATION * 1000, app.quit)
    app.exec()

    started = time.monotonic()
    for worker in workers:
        worker.stop()
    for worker in workers:
        worker.wait(5000)
    stop_seconds = round(time.monotonic() - started, 3)

    result = {
        "duration_seconds": DURATION,
        "devices": records,
        "stop_seconds": stop_seconds,
        "all_stopped": all(not worker.isRunning() for worker in workers),
        "still_running": [
            worker.addr for worker in workers if worker.isRunning()
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    output = ROOT / "artifacts/LIVE_SMOKE.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result["all_stopped"]
    assert all(
        "online" in row["states"]
        and row["cpu_samples"] > 0
        and row["battery_samples"] > 0
        for row in records.values()
    )


if __name__ == "__main__":
    main()
