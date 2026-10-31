#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸿蒙设备 HDC 探测脚本
用途：验证无线连接、枚举可采集字段、导出样例数据供设计监控项使用。

用法：
    python probe_device.py 192.168.1.100:5555
    python probe_device.py --list
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
HDC = os.path.join(BASE, "hdc", "hdc.exe")
if not os.path.exists(HDC):
    HDC = "hdc"

# 强制 UTF-8，避免中文与 emoji 输出解码失败
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(args, timeout=25):
    """执行一条 hdc 命令，返回 (rc, stdout, stderr)"""
    cmd = [HDC] + args
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout, env=ENV,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = p.stdout.decode("utf-8", errors="replace")
        err = p.stderr.decode("utf-8", errors="replace")
        return p.returncode, out.strip(), err.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -2, "", str(e)


def shell(key, command, timeout=25):
    """在指定设备上执行 shell 命令"""
    return run(["-t", key, "shell", command], timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="设备地址 ip:port")
    ap.add_argument("--list", action="store_true", help="仅列出已连接设备")
    ap.add_argument("--out", default="probe_result", help="输出文件前缀")
    a = ap.parse_args()

    print(f"[HDC] {HDC}")
    rc, out, err = run(["version"])
    print(f"[版本] rc={rc} {out} {err}".strip())

    if a.target:
        print(f"\n[连接] tconn {a.target} ...")
        rc, out, err = run(["tconn", a.target], timeout=30)
        print(f"  rc={rc} out={out!r} err={err!r}")
        time.sleep(1)

    rc, out, err = run(["list", "targets"])
    print(f"\n[设备列表] {out!r}")
    targets = [x for x in re.split(r"[\r\n]+", out) if x.strip() and "Empty" not in x]
    for t in targets:
        print(f"  - {t}")

    if a.list or not targets:
        return

    key = a.target if a.target else targets[0]
    report = {"target": key, "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"), "probes": {}}

    print(f"\n[探测目标] {key}")

    # ---- 1. 系统参数 ----
    rc, out, err = shell(key, "param get")
    params = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            params[k.strip()] = v.strip()
    report["probes"]["param_count"] = len(params)
    report["probes"]["param_sample"] = {k: params[k] for k in list(params)[:15]}
    print(f"\n[系统参数] 共 {len(params)} 项")
    for k in ["const.product.model", "const.product.brand", "const.product.name",
              "const.ohos.apiversion", "const.ohos.fullname", "const.build.utdversion"]:
        print(f"  {k:32s} = {params.get(k, '<缺失>')}")

    # ---- 2. 关键服务探测 ----
    probes = {
        "battery": "hidumper -s BatteryService -a -i",
        "cpu_usage": "hidumper --cpuusage",
        "cpu_freq": "hidumper --cpufreq",
        "mem": "hidumper --mem",
        "storage": "hidumper --storage",
        "system_base": "hidumper -c base",
        "display": "hidumper -s DisplayManagerService -a -a",
        "net": "hidumper --net",
        "uptime": "cat /proc/uptime",
        "df": "df -h /data",
        "wm_size": "wm size",
        "thermal": "hidumper -s ThermalService -a -a",
        "sensors": "hidumper -s SensorService -a -a",
        "wifi": "hidumper -s WifiDeviceService -a -a",
    }
    for name, cmd in probes.items():
        rc, out, err = shell(key, cmd)
        head = out[:300].replace("\n", " | ")
        report["probes"][name] = {
            "cmd": cmd, "rc": rc, "len": len(out), "head": head, "err": err[:200],
        }
        status = "OK " if len(out) > 10 else "EMPTY"
        print(f"\n[{status}] {name:12s} len={len(out):5d}  {cmd}")
        if out:
            print(f"        {head[:220]}")

    # ---- 3. 保存 ----
    out_json = f"{a.out}_{key.replace(':', '_')}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[已保存] {out_json}")


if __name__ == "__main__":
    main()
