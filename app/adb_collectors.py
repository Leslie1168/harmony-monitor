# -*- coding: utf-8 -*-
"""ADB 后端的采集命令与解析（用于鸿蒙 4.x 等无 hdc 无线入口的设备）。

字段 id 与 collectors.ITEMS 完全一致，UI 层无需区分设备类型。
注意：鸿蒙 4.x 上 `hidumper` 的服务查询在 shell 权限下不可用（SELinux），
但 Android 兼容层的 `dumpsys` / sysfs 可以正常读取，因此改走 Android 接口。
"""
from __future__ import annotations

import re

from .collectors import _f, _gb_from_df, fmt_duration

# ---------------------------------------------------------------- 命令
# 一次往返取回整档数据，段间用 @@XX@@ 分隔，避免解析歧义
CMD_FAST = (
    "cat /proc/stat;"
    "echo '@@LOAD@@';"
    "cat /proc/loadavg;"
    "echo '@@MEM@@';"
    "cat /proc/meminfo;"
    "echo '@@DF@@';"
    "df -h /data;"
    "echo '@@CORES@@';"
    "nproc;"
    "echo '@@PS@@';"
    "ps -A | wc -l"
)

CMD_MID = (
    "dumpsys battery;"
    "echo '@@WIFI@@';"
    "cmd wifi status;"
    "echo '@@IP@@';"
    "ip addr show wlan0"
)

CMD_SLOW = (
    "cat /proc/uptime;"
    "echo '@@TEMP@@';"
    "cat /sys/class/thermal/thermal_zone0/temp;"
    "echo '@@BR@@';"
    "settings get system screen_brightness"
)

CMD_STATIC = (
    "getprop ro.product.model;"
    "getprop ro.product.brand;"
    "getprop ro.build.display.id;"
    "getprop ro.build.version.sdk;"
    "getprop ro.serialno;"
    "getprop ro.config.marketing_name"
)

TIER_CMD = {"fast": CMD_FAST, "mid": CMD_MID, "slow": CMD_SLOW, "static": CMD_STATIC}

CHARGE_STATE = {1: "未知", 2: "充电中", 3: "放电中", 4: "未充电", 5: "已充满"}
HEALTH_STATE = {1: "未知", 2: "良好", 3: "过热", 4: "损坏", 5: "过压", 6: "故障", 7: "过冷"}


def _segments(text: str, marks: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    positions = sorted((text.find(f"@@{m}@@"), m) for m in marks)
    prev_idx, prev_name = 0, "head"
    for idx, name in positions:
        if idx < 0:
            result[name] = ""
            continue
        result[prev_name] = text[prev_idx:idx]
        prev_idx = idx + len(name) + 4
        prev_name = name
    result[prev_name] = text[prev_idx:]
    return result


# ---------------------------------------------------------------- 解析
def parse_static(text: str) -> dict:
    vals = [x.strip() for x in text.splitlines() ]
    def g(i):
        return vals[i] if len(vals) > i else ""

    model = g(0)
    brand = g(1)
    display_id = g(2)
    sdk = g(3)
    serial = g(4)
    market = g(5)

    os_name = display_id
    m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", display_id)
    if m:
        os_name = f"HarmonyOS {m.group(1)}"

    name = market or model
    if brand and brand.upper() not in name.upper():
        name = f"{brand} {name}"

    return {
        "name": name,
        "model": model,
        "os": os_name,
        "api": sdk,
        "udid": serial,
    }


def parse_fast(text: str, state: dict | None = None) -> dict:
    out: dict = {}
    seg = _segments(text, ["LOAD", "MEM", "DF", "CORES", "PS"])

    # ---- CPU：/proc/stat 两次采样差值 ----
    out["cpu"] = None  # 不沿用上一轮数据冒充当前读数
    m = re.search(r"^cpu\s+([\d ]+)", seg.get("head", ""), re.M)
    if m:
        nums = [int(n) for n in m.group(1).split()][:8]
        nums += [0] * (8 - len(nums))
        idle = nums[3] + nums[4]          # idle + iowait
        total = sum(nums)
        if state is not None:
            prev = state.get("cpu_stat")
            if prev:
                dt = total - prev[0]
                di = idle - prev[1]
                if dt > 0 and 0 <= di <= dt:
                    out["cpu"] = round((dt - di) / dt * 100, 1)
            state["cpu_stat"] = (total, idle)
    elif state is not None:
        state.pop("cpu_stat", None)

    # ---- 负载 ----
    m = re.search(r"^([\d.]+)\s+[\d.]+\s+[\d.]+", seg.get("LOAD", ""), re.M)
    if m:
        out["load"] = _f(m.group(1))

    # ---- 内存：以 MemAvailable 为准（free 的 used 含页缓存，严重虚高）----
    mem = seg.get("MEM", "")
    mt = re.search(r"MemTotal:\s+(\d+)", mem)
    ma = re.search(r"MemAvailable:\s+(\d+)", mem)
    mf = re.search(r"MemFree:\s+(\d+)", mem)
    if mt and ma and 0 < int(mt.group(1)) and 0 <= int(ma.group(1)) <= int(mt.group(1)):
        total_kb = int(mt.group(1))
        avail_kb = int(ma.group(1))
        total_gb = total_kb / 1048576
        avail_gb = avail_kb / 1048576
        used_gb = max(0.0, total_gb - avail_gb)
        out["mem_total"] = round(total_gb, 2)
        out["mem_used"] = round(used_gb, 2)
        out["mem_free"] = round(avail_gb, 2)
        out["mem_pct"] = round(used_gb / total_gb * 100, 1) if total_gb else None

    # ---- 存储 ----
    m = re.search(
        r"\s(\d+(?:\.\d+)?[KMGT])\s+(\d+(?:\.\d+)?[KMGT])\s+(\d+(?:\.\d+)?[KMGT])\s+(\d+)%",
        seg.get("DF", ""),
    )
    if m:
        out["storage_total"] = _gb_from_df(m.group(1))
        out["storage_used"] = _gb_from_df(m.group(2))
        out["storage_free"] = _gb_from_df(m.group(3))
        out["storage_pct"] = _f(m.group(4))

    # ---- 核数 / 进程数 ----
    m = re.fullmatch(r"\s*(\d+)\s*", seg.get("CORES", ""))
    if m:
        out["cores"] = int(m.group(1))

    m = re.fullmatch(r"\s*(\d+)\s*", seg.get("PS", ""))
    if m:
        out["procs"] = max(0, int(m.group(1)) - 1)

    return out


def parse_mid(text: str) -> dict:
    out: dict = {}
    seg = _segments(text, ["WIFI", "IP"])
    batt = seg.get("head", "")

    m = re.search(r"^\s*level:\s*(\d+)", batt, re.M)
    if m:
        level = int(m.group(1))
        scale_match = re.search(r"^\s*scale:\s*(\d+)", batt, re.M)
        scale = int(scale_match.group(1)) if scale_match else 100
        if scale > 0 and 0 <= level <= scale:
            out["battery"] = round(level / scale * 100)
    m = re.search(r"^\s*temperature:\s*(-?\d+)", batt, re.M)
    if m:
        out["batt_temp"] = round(int(m.group(1)) / 10, 1)
    m = re.search(r"^\s*voltage:\s*(\d+)", batt, re.M)
    if m:
        out["batt_volt"] = round(int(m.group(1)) / 1000, 2)
    m = re.search(r"^\s*status:\s*(\d+)", batt, re.M)
    if m:
        out["batt_state"] = CHARGE_STATE.get(int(m.group(1)), m.group(1))
    m = re.search(r"^\s*health:\s*(\d+)", batt, re.M)
    if m:
        out["batt_health"] = HEALTH_STATE.get(int(m.group(1)), m.group(1))

    wifi = seg.get("WIFI", "")
    m = re.search(r'SSID:\s*"([^"]*)"', wifi)
    if m:
        out["wifi_ssid"] = m.group(1)
    m = re.search(r"RSSI:\s*(-?\d+)", wifi)
    if m:
        out["wifi_rssi"] = int(m.group(1))
    m = re.search(r"Link speed:\s*(\d+)Mbps", wifi)
    if m:
        out["wifi_speed"] = int(m.group(1))

    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", seg.get("IP", ""))
    if m:
        out["wifi_ip"] = m.group(1)

    return out


def parse_slow(text: str) -> dict:
    out: dict = {}
    seg = _segments(text, ["TEMP", "BR"])

    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s+\d", seg.get("head", ""))
    if m:
        out["uptime"] = fmt_duration(float(m.group(1)))

    m = re.fullmatch(r"\s*(-?\d+)\s*", seg.get("TEMP", ""))
    if m:
        out["cpu_temp"] = round(int(m.group(1)) / 1000, 1)

    m = re.fullmatch(r"\s*(\d+)\s*", seg.get("BR", ""))
    if m:
        out["brightness"] = int(m.group(1))

    return out


PARSERS = {"fast": parse_fast, "mid": parse_mid, "slow": parse_slow, "static": parse_static}
