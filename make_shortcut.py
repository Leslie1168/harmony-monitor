# -*- coding: utf-8 -*-
"""按 MS-SHLLINK 规范生成 .lnk 快捷方式（不依赖 COM）。"""
import os
import struct


def make_lnk(lnk_path: str, target: str, workdir: str = "", icon: str = "", desc: str = ""):
    workdir = workdir or os.path.dirname(target)
    icon = icon or target

    def ansi(s: str) -> bytes:
        return s.encode("mbcs") + b"\x00"

    def utf16(s: str) -> bytes:
        # CountCharacters 不含终止符，但字符串数据本身必须以 2 字节 null 结尾
        data = s.encode("utf-16-le") + b"\x00\x00"
        return struct.pack("<H", len(s)) + data

    # ---------------- LinkInfo ----------------
    volume_id = struct.pack("<IIII", 17, 3, 0, 0x10) + b"\x00"   # 固定磁盘
    base_path = ansi(target)
    suffix = b"\x00"

    volume_id_offset = 0x1C
    local_base_offset = volume_id_offset + len(volume_id)
    suffix_offset = local_base_offset + len(base_path)
    link_info_size = suffix_offset + len(suffix)

    link_info = (
        struct.pack("<IIIIIII",
                    link_info_size,
                    0x1C,
                    0x01,                 # VolumeIDAndLocalBasePath
                    volume_id_offset,
                    local_base_offset,
                    0,
                    suffix_offset)
        + volume_id + base_path + suffix
    )

    # ---------------- StringData ----------------
    flags = 0x01 | 0x02 | 0x10 | 0x40 | 0x80   # IDList|LinkInfo|WorkingDir|IconLocation|IsUnicode
    strings = utf16(workdir) + utf16(icon)

    # ---------------- LinkTargetIDList（空列表，仅终止符）----------------
    # 资源管理器需要该段存在才会把快捷方式识别为可执行目标
    id_list = struct.pack("<H", 2) + b"\x00\x00"

    # ---------------- Header ----------------
    header = (
        struct.pack("<I", 0x4C)
        + bytes.fromhex("0114020000000000C000000000000046")
        + struct.pack("<I", flags)
        + struct.pack("<I", 0x20)          # FILE_ATTRIBUTE_ARCHIVE
        + b"\x00" * 8 * 3                  # 三个时间戳
        + struct.pack("<I", 0)             # FileSize
        + struct.pack("<i", 0)             # IconIndex
        + struct.pack("<I", 1)             # SW_SHOWNORMAL
        + struct.pack("<H", 0)             # HotKey
        + b"\x00" * 2
        + b"\x00" * 4
        + b"\x00" * 4
    )
    assert len(header) == 76, len(header)

    with open(lnk_path, "wb") as f:
        f.write(header + id_list + link_info + strings)


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        target_arg, lnk_arg = sys.argv[1], sys.argv[2]
        icon_arg = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        target_arg = os.path.join(here, "dist", "HarmonyMonitor", "HarmonyMonitor.exe")
        lnk_arg = os.path.join(os.path.expanduser("~"), "Desktop", "鸿蒙设备监控.lnk")
        icon_arg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_harmony.ico")

    make_lnk(lnk_arg, target_arg, os.path.dirname(target_arg), icon_arg, desc="鸿蒙设备实时监控")
    print(f"created={os.path.exists(lnk_arg)} size={os.path.getsize(lnk_arg)} -> {lnk_arg}")
