# -*- coding: utf-8 -*-
"""界面预览：用真实设备数据渲染悬浮窗并保存截图。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.overlay import OverlayWindow
from app.worker import DeviceWorker, make_client


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    win = OverlayWindow(cfg)
    win.show()

    clients = {}
    workers = []
    for dev in cfg["devices"]:
        addr = dev["addr"] if isinstance(dev, dict) else dev
        dtype = dev.get("type", "hdc") if isinstance(dev, dict) else "hdc"
        client = clients.get(dtype)
        if client is None:
            client = make_client(dtype)
            client.ensure_server()
            clients[dtype] = client
        w = DeviceWorker(client, addr, lambda: set(cfg["selected"]), dtype)
        w.updated.connect(win.on_data)
        w.status.connect(win.on_status)
        w.start()
        workers.append(w)

    def shot():
        win.grab().save("preview.png")
        print("saved preview.png", win.width(), "x", win.height())
        for w in workers:
            w.stop()
            w.wait(800)
        app.quit()

    QTimer.singleShot(9000, shot)
    app.exec()


if __name__ == "__main__":
    main()
