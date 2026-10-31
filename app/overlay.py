"""鸿蒙设备监控 · 悬浮面板（v5 · WebEngine 渲染）

UI 全部由 app/webui.html（HTML/CSS/JS）绘制，QtWebEngine(Chromium) 渲染；
Python 只负责：窗口/托盘/拖动、采集数据推送、模式与配置持久化。
显示模式：full / tabs（默认）/ mini / mini3。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

from PySide6.QtCore import Qt, Signal, QTimer, Slot, QObject, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from . import collectors as C

# ---------------------------------------------------------------- 设计令牌 / 配置
TEXT = '#EDF3FD'
SUBTEXT = '#93A2BB'
DIM = '#5F6E88'
OK = '#3EDBA4'
WARN = '#F2B84B'
BAD = '#FF6D7F'
ACCENT = '#5B8CFF'
ACCENT2 = '#46D8C4'

BAR_COLORS = {
    'cpu': '#5B8CFF',
    'mem_pct': '#A78BFA',
    'storage_pct': '#F0A35E',
    'battery': '#3EDBA4',
    'load': '#F2B84B',
}

DEFAULT_APPEARANCE = {
    'opacity': 92,
    'font': 'Microsoft YaHei UI',
    'card_width': 330,
    'topmost': True,
    'layout': 'horizontal',
    'mode': 'tabs',        # full / tabs / mini / mini3；程序默认打开标签页
    'scale': 100,          # 整体缩放百分比 80-160
    'mini_locked': False,
    'tab_device': '',      # 标签/迷你模式当前设备地址
    'mini_labels': {},     # 迷你模式自定义名称：addr -> 昵称（≤6字）
}

MINI_BASE_W, MINI_BASE_H = 350, 216
# 迷你模式必需的采集字段（详情条需要温度/WiFi/存储/运行时长）
MINI_REQUIRED = {'cpu', 'mem_pct', 'battery', 'batt_state',
                 'batt_temp', 'wifi_rssi', 'storage_pct', 'uptime'}
# 离线时保留最后数值的字段
STICKY_KEYS = ('cpu', 'mem_pct', 'battery')

MODES = [('full', '完整'), ('tabs', '标签'), ('mini', '迷你')]

if getattr(sys, 'frozen', False):
    WEBUI_PATH = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)),
                              'webui.html')
else:
    WEBUI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui.html')


def _bounded_int(value, default, minimum, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


def appearance(config):
    result = dict(DEFAULT_APPEARANCE)
    supplied = config.get('appearance', {}) if isinstance(config, dict) else {}
    if isinstance(supplied, dict):
        result.update(supplied)
    result['opacity'] = _bounded_int(result['opacity'], DEFAULT_APPEARANCE['opacity'], 25, 100)
    result['card_width'] = _bounded_int(result['card_width'], DEFAULT_APPEARANCE['card_width'], 260, 460)
    result['scale'] = _bounded_int(result.get('scale'), DEFAULT_APPEARANCE['scale'], 80, 160)
    if result['mode'] not in ('full', 'tabs', 'mini', 'mini3'):
        result['mode'] = 'tabs'
    if result['layout'] not in ('horizontal', 'vertical'):
        result['layout'] = DEFAULT_APPEARANCE['layout']
    for key in ('topmost', 'mini_locked'):
        if not isinstance(result[key], bool):
            result[key] = DEFAULT_APPEARANCE[key]
    if not isinstance(result['font'], str) or not result['font'].strip():
        result['font'] = DEFAULT_APPEARANCE['font']
    if not isinstance(result['tab_device'], str):
        result['tab_device'] = ''
    if not isinstance(result.get('mini_labels'), dict):
        result['mini_labels'] = {}
    result['mini_labels'] = {
        str(k): str(v)[:6] for k, v in result['mini_labels'].items()
        if isinstance(k, str) and isinstance(v, str) and v.strip()
    }
    return result


def fmt_value(item_id: str, value) -> str:  # 兼容旧导入
    if value is None or value == '':
        return '—'
    item = C.ITEM_MAP.get(item_id)
    if not isinstance(value, (int, float)):
        return str(value)
    dec = item.decimals if item else 1
    unit = item.unit if item else ''
    if unit == '%':
        return f'{value:.{dec}f}%'
    if unit:
        return f'{value:.{dec}f} {unit}'
    return f'{value:.{dec}f}' if isinstance(value, float) else str(value)


# ---------------------------------------------------------------- Web 桥
class _Bridge(QObject):
    """webui.html <-> Python 的唯一通道。"""

    def __init__(self, win: 'OverlayWindow'):
        super().__init__(win)
        self._win = win

    @Slot(str)
    def setMode(self, mode: str):
        self._win.set_mode(mode)

    @Slot(str)
    def switchDevice(self, addr: str):
        self._win._on_switch(addr)

    @Slot(str, str)
    def renameDevice(self, addr: str, name: str):
        self._win._on_rename(addr, name)

    @Slot()
    def openSettings(self):
        self._win.request_settings.emit()

    @Slot()
    def togglePin(self):
        self._win._on_toggle_pin()

    @Slot()
    def minimize(self):
        self._win.hide()

    @Slot()
    def quitApp(self):
        self._win.request_quit.emit()

    @Slot()
    def startMove(self):
        handle = self._win.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @Slot(int, int)
    def reportSize(self, w: int, h: int):
        self._win._apply_size(w, h)


# ---------------------------------------------------------------- 主窗口
class OverlayWindow(QWidget):
    request_settings = Signal()
    request_quit = Signal()
    request_persist = Signal()   # 模式/缩放等变化需要立即落盘

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._preview = None            # 设置对话框实时预览值
        self._data_cache = {}           # addr -> 最新数据
        self._sticky = {}               # addr -> 离线时保留的最后数值
        self._status_cache = {}
        self._push_scheduled = False

        self.setWindowTitle('鸿蒙设备监控')
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.web = QWebEngineView(self)
        self.web.page().setBackgroundColor(QColor(Qt.transparent))
        lay.addWidget(self.web)

        self._bridge = _Bridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject('bridge', self._bridge)
        self.web.page().setWebChannel(self._channel)
        self.web.loadFinished.connect(self._on_loaded)
        self.web.load(QUrl.fromLocalFile(WEBUI_PATH))

        self.resize(400, 340)
        self._restore_geometry()
        self.setContextMenuPolicy(Qt.NoContextMenu)

    # ---------------------------------------------------------- 加载与推送
    def _on_loaded(self, ok: bool):
        if ok:
            self._schedule_push()

    def _appearance_now(self) -> dict:
        a = appearance(self.config)
        if self._preview:
            a.update(self._preview)
        return a

    def _state(self) -> dict:
        a = self._appearance_now()
        devs = []
        for dev in self.config.get('devices', []):
            addr = dev['addr'] if isinstance(dev, dict) else dev
            data = dict(self._data_cache.get(addr, {}))
            status = self._status_cache.get(addr, 'connecting')
            if status != 'online':
                # 离线保留最后数值
                for key, val in self._sticky.get(addr, {}).items():
                    if data.get(key) is None:
                        data[key] = val
            name = str(data.get('name') or addr)
            label = a.get('mini_labels', {}).get(addr) or name.removeprefix('HUAWEI ')
            devs.append({'addr': addr, 'label': label, 'name': name,
                         'status': status, 'data': data})
        items = [{'id': i.id, 'label': i.label, 'unit': i.unit,
                  'decimals': i.decimals} for i in C.ITEMS]
        return {
            'mode': a['mode'],
            'devices': devs,
            'selected': list(self.config.get('selected', [])),
            'items': items,
            'appearance': {'opacity': a['opacity'], 'font': a['font'],
                           'card_width': a['card_width'], 'scale': a['scale'],
                           'topmost': bool(a['topmost']),
                           'tab_device': a.get('tab_device', '')},
        }

    def _schedule_push(self):
        if self._push_scheduled:
            return
        self._push_scheduled = True
        QTimer.singleShot(60, self._do_push)

    def _do_push(self):
        self._push_scheduled = False
        try:
            payload = json.dumps(self._state(), ensure_ascii=False)
        except (TypeError, ValueError):
            return
        self.web.page().runJavaScript(f'window.__applyState({payload});')

    # ---------------------------------------------------------- 外观应用
    def apply_appearance(self, values=None):
        self._preview = dict(values) if values else None
        a = self._appearance_now()
        flag = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        if flag != bool(a['topmost']):
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(a['topmost']))
            if visible:
                self.show()
        self._schedule_push()

    # ---------------------------------------------------------- 模式与交互
    def set_mode(self, mode):
        if mode not in ('full', 'tabs', 'mini', 'mini3'):
            return
        if self._appearance_now()['mode'] == mode:
            return
        self.config.setdefault('appearance', {})['mode'] = mode
        self.request_persist.emit()
        self.apply_appearance()

    def _on_switch(self, addr):
        if addr not in self._data_cache and addr not in [d.get('addr') for d in self.config.get('devices', []) if isinstance(d, dict)]:
            return
        self.config.setdefault('appearance', {})['tab_device'] = addr
        self.request_persist.emit()
        self._schedule_push()

    def _on_rename(self, addr, name):
        name = (name or '').strip()[:6]
        labels = self.config.setdefault('appearance', {}).setdefault('mini_labels', {})
        if name:
            labels[addr] = name
        else:
            labels.pop(addr, None)
        self.request_persist.emit()
        self._schedule_push()

    def _on_toggle_pin(self):
        new = not bool(self._appearance_now()['topmost'])
        self.config.setdefault('appearance', {})['topmost'] = new
        self.request_persist.emit()
        self.apply_appearance()

    # ---------------------------------------------------------- 数据入口
    def rebuild_cards(self):
        addrs = [d['addr'] if isinstance(d, dict) else d
                 for d in self.config.get('devices', [])]
        self._data_cache = {a: self._data_cache.get(a, {}) for a in addrs}
        self._sticky = {a: self._sticky.get(a, {}) for a in addrs}
        self._status_cache = {a: self._status_cache.get(a, 'connecting') for a in addrs}
        self._schedule_push()

    def refresh_selection(self):
        self._schedule_push()

    def card(self, addr):  # 兼容旧调用
        return None

    def on_status(self, addr, status):
        self._status_cache[addr] = status
        self._schedule_push()

    def on_data(self, addr, data):
        cache = self._data_cache.setdefault(addr, {})
        cache.update(data)
        sticky = self._sticky.setdefault(addr, {})
        for key in STICKY_KEYS:
            if cache.get(key) is not None:
                sticky[key] = cache[key]
        self._schedule_push()

    # ---------------------------------------------------------- 窗口
    def _apply_size(self, w, h):
        w = max(80, min(int(w), 4000))
        h = max(60, min(int(h), 4000))
        if (w, h) != (self.width(), self.height()):
            self.setFixedSize(w, h)
            self._clamp_position()

    def _clamp_position(self):
        r = self.screen().availableGeometry()
        self.move(max(r.left(), min(self.x(), r.right() - self.width() + 1)),
                  max(r.top(), min(self.y(), r.bottom() - self.height() + 1)))

    def _restore_geometry(self):
        geo = self.config.get('window', {})
        if geo.get('x') is not None and geo.get('y') is not None:
            self.move(int(geo['x']), int(geo['y']))
        else:
            r = self.screen().availableGeometry()
            self.move(r.right() - self.width() - 24, r.top() + 40)
        self._clamp_position()

    def save_geometry(self):
        self.config.setdefault('window', {}).update(x=self.x(), y=self.y())

    def closeEvent(self, e):
        self.save_geometry()
        self.request_quit.emit()
        e.accept()
