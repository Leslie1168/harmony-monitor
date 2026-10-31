# 第三方组件说明

仓库里的**源代码**（`main.py`、`app/`、`tests/` 等）按根目录 [LICENSE](LICENSE) 的 MIT 协议发布。

以下目录中包含的是第三方官方二进制工具，**不适用**上面的 MIT 协议，仅随仓库分发以便开箱可用：

| 路径 | 来源 | 说明 |
|---|---|---|
| `hdc/hdc.exe`、`hdc/libusb_shared.dll` | OpenHarmony / HarmonyOS SDK 命令行工具（DevEco Studio 组件） | 华为设备调试通道 `hdc` |
| `tools/adb/adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` | Android SDK Platform-Tools（Google） | ADB 客户端 |

- `hdc` 属于 OpenHarmony 开源项目（Apache License 2.0），官方发布见
  <https://gitee.com/openharmony/developtools_hdc>
- Android SDK Platform-Tools 采用 Apache License 2.0，官方发布见
  <https://developer.android.com/tools/releases/platform-tools>

如需更新这两个工具，直接用官方版本覆盖同名文件即可——程序只调用它们的命令行接口。
若所在地区/场景不允许分发这些二进制，可删掉 `hdc/` 与 `tools/adb/` 两个目录，
并自行从官方渠道下载放入相同位置，程序功能不受影响。

Python 依赖（PySide6 / Qt6 / Chromium）遵循各自的 LGPL / BSD 类许可，通过 requirements.txt 安装，不随仓库分发。
