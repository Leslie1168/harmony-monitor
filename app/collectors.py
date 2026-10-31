# -*- coding: utf-8 -*-
"""指标定义与设备数据解析。

设计原则：
- 指标以 id 标识，UI 通过 id 勾选，采集层按 id 组装输出。
- 分三个采集频率档：fast(1s) / mid(5s) / slow(30s)，避免无谓的 shell 往返。
- 所有解析均对缺失字段容错，返回 None 表示该项在当前设备上不可用。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 指标元数据
@dataclass
class Item:
    id: str
    label: str
    group: str          # 分组：设备 / 性能 / 电池 / 网络 / 系统
    unit: str = ""      # 单位后缀
    tier: str = "fast"  # 采集频率档：fast / mid / slow / static
    decimals: int = 0
    default: bool = True  # 默认是否勾选


ITEMS: list[Item] = [
    # --- 设备信息（静态，连接时取一次）---
    Item("name",        "设备名称",   "设备", tier="static", default=False),
    Item("model",       "型号",       "设备", tier="static", default=False),
    Item("os",          "系统版本",   "设备", tier="static", default=False),
    Item("api",         "API 版本",   "设备", tier="static", default=False),
    Item("udid",        "UDID",       "设备", tier="static", default=False),

    # --- 性能（每秒）---
    Item("cpu",         "CPU 使用率", "性能", "%",  "fast", 1),
    Item("load",        "系统平均负载", "性能", "",   "fast", 2),
    Item("cpu_freq",    "CPU 当前频率", "性能", "GHz", "mid", 2, default=False),
    Item("cpu_temp",    "CPU 温度",   "性能", "℃", "slow", 1, default=False),
    Item("mem_pct",     "内存使用率", "性能", "%",  "fast", 1),
    Item("mem_used",    "已用内存",   "性能", "GB", "fast", 1, default=False),
    Item("mem_free",    "可用内存",   "性能", "GB", "fast", 1, default=False),
    Item("storage_pct", "存储使用率", "性能", "%",  "fast", 1),
    Item("storage_free", "剩余存储",  "性能", "GB", "fast", 1, default=False),
    Item("procs",       "进程数",     "性能", "",   "fast", 0, default=False),

    # --- 电池（5 秒）---
    Item("battery",     "电量",       "电池", "%",  "mid", 0),
    Item("batt_temp",   "电池温度",   "电池", "℃", "mid", 1),
    Item("batt_volt",   "电压",       "电池", "V",  "mid", 2, default=False),
    Item("batt_cur",    "电流",       "电池", "mA", "mid", 0, default=False),
    Item("batt_state",  "充电状态",   "电池", "",   "mid", 0),
    Item("batt_health", "电池健康",   "电池", "",   "mid", 0, default=False),
    Item("batt_left",   "充满剩余",   "电池", "min", "mid", 0, default=False),

    # --- 网络（5 秒）---
    Item("wifi_ssid",   "WiFi 名称",  "网络", "",   "mid", 0, default=False),
    Item("wifi_rssi",   "信号强度",   "网络", "dBm", "mid", 0),
    Item("wifi_speed",  "链路速率",   "网络", "Mbps", "mid", 0, default=False),
    Item("wifi_ip",     "本机 IP",    "网络", "",   "mid", 0, default=False),

    # --- 系统（30 秒）---
    Item("uptime",      "运行时长",   "系统", "",   "slow", 0),
    Item("brightness",  "屏幕亮度",   "系统", "",   "slow", 0, default=False),
    Item("resolution",  "分辨率",     "系统", "",   "static", 0, default=False),
]

ITEM_MAP: dict[str, Item] = {i.id: i for i in ITEMS}
GROUPS: list[str] = ["设备", "性能", "电池", "网络", "系统"]


# ---------------------------------------------------------------- 采集命令
# 重要：hidumper 的服务正文只在 TTY 下输出，一旦接入管道即被丢弃。
# 因此这些命令一律不做管道裁剪，全量取回后在本地解析。
CMD_FAST = (
    "hidumper --cpuusage;"
    "echo '@@MEM@@';"
    "cat /proc/meminfo;"
    "echo '@@DF@@';"
    "df -h /data;"
    "echo '@@CORES@@';"
    "nproc;"
    "echo '@@PS@@';"
    "ps -ef | wc -l"
)

CMD_MID = (
    "hidumper -s BatteryService -a -i;"
    "echo '@@WIFI@@';"
    "hidumper -s WifiDevice -a -a;"
    "echo '@@IP@@';"
    "ifconfig"
)

CMD_SLOW = (
    "uptime;"
    "hidumper -s DisplayPowerManagerService -a -a"
)

CMD_STATIC = (
    "param get const.product.name;"
    "param get const.product.model;"
    "param get const.ohos.fullname;"
    "param get const.ohos.apiversion;"
    "echo '@@UDID@@';"
    "bm get --udid;"
    "echo '@@DISP@@';"
    "hidumper -s DisplayManagerService -a -a"
)

# 不同频率档对应的命令
TIER_CMD = {"fast": CMD_FAST, "mid": CMD_MID, "slow": CMD_SLOW}
TIER_INTERVAL = {"fast": 1.0, "mid": 5.0, "slow": 30.0}


# ---------------------------------------------------------------- 解析工具
def _f(v: str) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _gb_from_df(size: str) -> float | None:
    """'441G' / '512M' / '1.2T' -> GB"""
    m = re.match(r"([\d.]+)\s*([KMGTP]?)", size.strip(), re.I)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").upper()
    factor = {"": 1 / 1024, "K": 1 / 1024 ** 2, "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024 ** 2}
    return round(val * factor.get(unit, 1), 2)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}天{h}时"
    if h:
        return f"{h}时{m}分"
    return f"{m}分"


CHARGE_STATE = {0: "未充电", 1: "充电中", 2: "已充满", 3: "充电中", 4: "放电中"}
HEALTH_STATE = {1: "良好", 2: "一般", 3: "较差", 0: "未知"}


# ---------------------------------------------------------------- 解析入口
def _segments(text: str, marks: list[str]) -> dict[str, str]:
    """按 '@@NAME@@' 标记切分文本，返回 {段名: 内容}。第一个标记之前归入 'head'。"""
    result: dict[str, str] = {}
    positions = []
    for m in marks:
        idx = text.find(f"@@{m}@@")
        positions.append((idx, m))
    positions.sort()
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


def parse_fast(text: str, state: dict | None = None) -> dict:
    """解析 fast 档输出（state 供 ADB 后端计算 CPU 差值，此处未使用）。"""
    out: dict = {}
    seg = _segments(text, ["MEM", "DF", "CORES", "PS"])
    cpu_part = seg.get("head", "")

    m = re.search(r"Total:\s*([\d.]+)%", cpu_part)
    if m:
        out["cpu"] = _f(m.group(1))

    m = re.search(r"Load average:\s*([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)", cpu_part)
    if m:
        out["load"] = _f(m.group(1))

    # 内存以 MemAvailable 为准：free -m 的 used 含页缓存，会虚高 30 多个百分点
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

    m = re.search(
        r"\s(\d+(?:\.\d+)?[KMGT])\s+(\d+(?:\.\d+)?[KMGT])\s+(\d+(?:\.\d+)?[KMGT])\s+(\d+)%",
        seg.get("DF", ""),
    )
    if m:
        out["storage_total"] = _gb_from_df(m.group(1))
        out["storage_used"] = _gb_from_df(m.group(2))
        out["storage_free"] = _gb_from_df(m.group(3))
        out["storage_pct"] = _f(m.group(4))

    m = re.fullmatch(r"\s*(\d+)\s*", seg.get("CORES", ""))
    if m:
        out["cores"] = int(m.group(1))

    m = re.fullmatch(r"\s*(\d+)\s*", seg.get("PS", ""))
    if m:
        out["procs"] = max(0, int(m.group(1)) - 1)

    return out


def parse_mid(text: str) -> dict:
    """解析 mid 档输出（电池 + WiFi + IP）。"""
    out: dict = {}
    batt_part, _, rest = text.partition("@@WIFI@@")
    wifi_part, _, ip_part = rest.partition("@@IP@@")

    for key, field_name, scale in [
        ("capacity", "battery", 1),
        ("temperature", "batt_temp", 0.1),
        ("voltage", "batt_volt", 1e-6),
        ("nowCurrent", "batt_cur", 1),
        ("chargingStatus", "batt_state", 1),
        ("healthState", "batt_health", 1),
        ("remainingChargeTime", "batt_left", 1),
    ]:
        m = re.search(rf"^\s*{key}:\s*(-?\d+)", batt_part, re.M)
        if m:
            raw = int(m.group(1))
            if key == "chargingStatus":
                out[field_name] = CHARGE_STATE.get(raw, str(raw))
            elif key == "healthState":
                out[field_name] = HEALTH_STATE.get(raw, str(raw))
            else:
                out[field_name] = round(raw * scale, 2) if scale != 1 else raw

    m = re.search(r"Connection\.ssid:\s*(.+)", wifi_part)
    if m:
        out["wifi_ssid"] = m.group(1).strip()
    m = re.search(r"Connection\.rssi:\s*(-?\d+)", wifi_part)
    if m:
        out["wifi_rssi"] = int(m.group(1))
    m = re.search(r"Connection\.linkSpeed:\s*(\d+)", wifi_part)
    if m:
        out["wifi_speed"] = int(m.group(1))

    m = re.search(r"inet addr:(192\.168\.[\d.]+)", ip_part)
    if not m:
        m = re.search(r"inet addr:(?!127\.)([\d.]+)", ip_part)
    if m:
        out["wifi_ip"] = m.group(1)

    return out


def parse_slow(text: str) -> dict:
    out: dict = {}
    m = re.search(r"up\s+(.+?),\s+\d+\s+user", text)
    if m:
        raw = m.group(1)
        # "4 days, 18:52" 或 "3:21" 或 "18:52"
        days = 0
        md = re.search(r"(\d+)\s+day", raw)
        if md:
            days = int(md.group(1))
        mt = re.search(r"(\d+):(\d+)", raw)
        hours = int(mt.group(1)) if mt else 0
        mm = re.search(r"(\d+)\s+min", raw)
        mins = int(mt.group(2)) if mt else (int(mm.group(1)) if mm else 0)
        out["uptime"] = fmt_duration(days * 86400 + hours * 3600 + mins * 60)

    m = re.search(r"Brightness=(\d+)", text)
    if m:
        out["brightness"] = int(m.group(1))
    return out


def parse_static(text: str) -> dict:
    out: dict = {}
    seg = _segments(text, ["UDID", "DISP"])
    param_part = seg.get("head", "")
    vals = [x.strip() for x in param_part.splitlines() ]
    if len(vals) > 0:
        out["name"] = vals[0]
    if len(vals) > 1:
        out["model"] = vals[1]
    if len(vals) > 2:
        out["os"] = vals[2]
    if len(vals) > 3:
        out["api"] = vals[3]

    m = re.search(r"([0-9A-Fa-f]{32,})", seg.get("UDID", ""))
    if m:
        out["udid"] = m.group(1)

    disp = seg.get("DISP", "")
    m = re.search(r"VirtualWidth:\s*(\d+)", disp)
    m2 = re.search(r"VirtualHeight:\s*(\d+)", disp)
    if m and m2:
        out["resolution"] = f"{m.group(1)}x{m2.group(1)}"
    return out


PARSERS = {"fast": parse_fast, "mid": parse_mid, "slow": parse_slow, "static": parse_static}


def needs_tier(selected: set[str], tier: str) -> bool:
    return any(ITEM_MAP[i].tier == tier for i in selected if i in ITEM_MAP)
