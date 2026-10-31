# -*- coding: utf-8 -*-
"""采集链路自测：连接设备 -> 三档采集 -> 打印解析结果。"""
import json
import sys
import time

from app.hdc_client import HdcClient, wait_for_online
from app import collectors as C


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.23:5555"
    c = HdcClient()
    c.ensure_server()
    print("hdc:", c.exe)
    print("targets:", c.list_targets())

    if not wait_for_online(c, addr):
        print("连接失败")
        return

    print(f"\n=== 静态信息 {addr} ===")
    raw = c.shell(addr, C.CMD_STATIC, timeout=25)
    static = C.parse_static(raw)
    for k, v in static.items():
        print(f"  {k:12s} = {v}")

    print("\n=== fast 档（3 次，测延迟）===")
    for i in range(3):
        t0 = time.perf_counter()
        raw = c.shell(addr, C.CMD_FAST)
        dt = (time.perf_counter() - t0) * 1000
        data = C.parse_fast(raw)
        print(f"  #{i+1} {dt:6.0f}ms  cpu={data.get('cpu')}% load={data.get('load')} "
              f"mem={data.get('mem_pct')}% used={data.get('mem_used')}GB "
              f"stor={data.get('storage_pct')}% free={data.get('storage_free')}GB "
              f"procs={data.get('procs')}")

    print("\n=== mid 档（电池 + 网络）===")
    t0 = time.perf_counter()
    raw = c.shell(addr, C.CMD_MID, timeout=20)
    dt = (time.perf_counter() - t0) * 1000
    mid = C.parse_mid(raw)
    print(f"  {dt:.0f}ms")
    for k, v in mid.items():
        print(f"  {k:12s} = {v}")

    print("\n=== slow 档 ===")
    raw = c.shell(addr, C.CMD_SLOW, timeout=20)
    slow = C.parse_slow(raw)
    for k, v in slow.items():
        print(f"  {k:12s} = {v}")

    print("\n=== 汇总 ===")
    all_data = {**static, **mid, **slow}
    all_data.update(C.parse_fast(c.shell(addr, C.CMD_FAST)))
    print(json.dumps(all_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
