from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox,QDialog,QDialogButtonBox,QGridLayout,QGroupBox,QHBoxLayout,QLabel,QLineEdit,QListWidget,QListWidgetItem,QPushButton,QScrollArea,QVBoxLayout,QWidget,QTabWidget,QSlider,QFontComboBox,QComboBox,QFormLayout,QSpinBox)
from . import collectors as C
from .overlay import appearance
BG = "#10151F"
FG = "#EDF3FD"
SUB = "#93A2BB"
ACC = "#5B8CFF"

QSS = f"""
QDialog {{ background:{BG}; }}
QLabel {{ color:{FG}; font-size:12px; }}
QLabel#hint {{ color:{SUB}; font-size:11px; }}
QGroupBox {{
    color:{FG}; font-size:12px; font-weight:600;
    border:1px solid rgba(255,255,255,20); border-radius:10px;
    margin-top:10px; padding:12px 10px 10px 10px;
}}
QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}
QLineEdit {{
    background:#1A212F; color:{FG}; border:1px solid rgba(255,255,255,24);
    border-radius:7px; padding:6px 8px; font-size:12px;
}}
QLineEdit:focus {{ border:1px solid {ACC}; }}
QListWidget {{
    background:#1A212F; color:{FG}; border:1px solid rgba(255,255,255,20);
    border-radius:7px; font-size:12px; padding:4px;
}}
QListWidget::item {{ padding:5px 4px; }}
QListWidget::item:selected {{ background:{ACC}; color:#0b1220; border-radius:4px; }}
QCheckBox {{ color:{FG}; font-size:12px; spacing:6px; }}
QCheckBox::indicator {{
    width:14px; height:14px; border-radius:4px;
    border:1px solid rgba(255,255,255,60); background:#1A212F;
}}
QCheckBox::indicator:checked {{ background:{ACC}; border:1px solid {ACC}; }}
QPushButton {{
    background:#232B3C; color:{FG}; border:1px solid rgba(255,255,255,22);
    border-radius:7px; padding:6px 14px; font-size:12px;
}}
QPushButton:hover {{ background:#2E3850; }}
QPushButton#primary {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5B8CFF,stop:1 #3FA7C9);
    color:#FFFFFF; font-weight:700; border:none; }}
QPushButton#primary:hover {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #6FA0FF,stop:1 #4FBCD8); }}
QScrollArea {{ border:none; background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:8px; }}
QScrollBar::handle:vertical {{ background:rgba(255,255,255,46); border-radius:4px; }}
"""



QSS += """
QTabWidget::pane {border:1px solid rgba(255,255,255,20);border-radius:12px;background:#151C29;}
QTabBar::tab {background:#1C2434;color:#93A2BB;padding:12px 24px;margin-right:5px;border-top-left-radius:8px;border-top-right-radius:8px;}
QTabBar::tab:selected {background:#2A3A5C;color:#FFFFFF;}
QComboBox,QSpinBox {background:#1E283A;color:#EDF3FD;border:1px solid #3A4A66;border-radius:6px;padding:8px;min-height:20px;}
QComboBox QAbstractItemView {background:#1E283A;color:#EDF3FD;selection-background-color:#3D5580;}
QSlider::groove:horizontal {height:6px;background:#2A3852;border-radius:3px;}
QSlider::sub-page:horizontal {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #5B8CFF,stop:1 #46D8C4);border-radius:3px;}
QSlider::handle:horizontal {background:#EAF3FF;width:16px;margin:-5px 0;border-radius:8px;}
QWidget#page {background:#151C29;}
QFontComboBox {background:#1E283A;color:#EDF3FD;border:1px solid #3A4A66;border-radius:6px;padding:8px;}
"""

class SettingsDialog(QDialog):
    appearance_changed=Signal(dict)
    def __init__(self,config,parent=None):
        super().__init__(parent)
        self.setWindowTitle('设备监控 · 设置')
        self.setWindowFlag(Qt.WindowStaysOnTopHint,True)
        self.setStyleSheet(QSS)
        self.resize(620,620)
        self.config=config
        self.a=appearance(config)
        root=QVBoxLayout(self)
        root.setContentsMargins(22,20,22,20)
        root.setSpacing(16)
        title=QLabel('让监控融入你的桌面')
        title.setStyleSheet('font-size:22px;font-weight:600;color:#EDF3FD;')
        root.addWidget(title)
        subtitle=QLabel('外观即时预览，保存后下次启动仍会保留。')
        subtitle.setObjectName('hint')
        root.addWidget(subtitle)
        self.tabs=QTabWidget()
        root.addWidget(self.tabs,1)

        visual=QWidget();visual.setObjectName('page')
        form=QFormLayout(visual)
        form.setContentsMargins(22,24,22,20)
        form.setVerticalSpacing(20)
        self.opacity=QSlider(Qt.Horizontal)
        self.opacity.setRange(25,100)
        self.opacity.setValue(self.a['opacity'])
        self.opacity_label=QLabel()
        opacity_row=QHBoxLayout()
        opacity_row.addWidget(self.opacity,1);opacity_row.addWidget(self.opacity_label)
        form.addRow('背景不透明度',opacity_row)
        self.scale_slider=QSlider(Qt.Horizontal)
        self.scale_slider.setRange(80,160)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setValue(self.a['scale'])
        self.scale_label=QLabel()
        scale_row=QHBoxLayout()
        scale_row.addWidget(self.scale_slider,1);scale_row.addWidget(self.scale_label)
        form.addRow('整体缩放',scale_row)
        self.font_picker=QFontComboBox()
        self.font_picker.setCurrentFont(QFont(self.a['font']))
        form.addRow('显示字体',self.font_picker)
        self.width_picker=QSpinBox()
        self.width_picker.setRange(260,460);self.width_picker.setSingleStep(10)
        self.width_picker.setSuffix(' px');self.width_picker.setValue(self.a['card_width'])
        form.addRow('每台设备显示宽度',self.width_picker)
        self.layout_picker=QComboBox()
        self.layout_picker.addItem('横向并排 · 桌面宽面板','horizontal')
        self.layout_picker.addItem('纵向排列 · 桌面侧边栏','vertical')
        self.layout_picker.setCurrentIndex(max(0,self.layout_picker.findData(self.a['layout'])))
        form.addRow('排列方式',self.layout_picker)
        self.topmost=QCheckBox('始终显示在其他窗口上方')
        self.topmost.setChecked(self.a['topmost'])
        form.addRow('窗口置顶',self.topmost)
        hint=QLabel('透明度只影响面板背景，文字保持清晰。\n「整体缩放」会同步放大字体、间距与卡片；与「显示宽度」相互独立。\n迷你模式为全透明背景，不受不透明度影响。')
        hint.setObjectName('hint');hint.setWordWrap(True)
        form.addRow(hint)
        self.tabs.addTab(visual,'外观')

        metrics=QWidget();metrics.setObjectName('page')
        ml=QVBoxLayout(metrics)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        inner=QWidget();inner.setObjectName('page')
        il=QVBoxLayout(inner)
        self.checks={}
        for group in C.GROUPS:
            box=QGroupBox(group);grid=QGridLayout(box)
            for i,item in enumerate([it for it in C.ITEMS if it.group==group]):
                cb=QCheckBox('系统平均负载' if item.id=='load' else item.label)
                cb.setChecked(item.id in config.get('selected',[]))
                self.checks[item.id]=cb
                grid.addWidget(cb,i//3,i%3)
            il.addWidget(box)
        il.addStretch();scroll.setWidget(inner);ml.addWidget(scroll)
        quick=QHBoxLayout()
        for title,slot in [('全选',lambda:self._set_all(True)),('全不选',lambda:self._set_all(False)),('恢复默认',self._restore_default)]:
            b=QPushButton(title);b.clicked.connect(slot);quick.addWidget(b)
        ml.addLayout(quick)
        self.tabs.addTab(metrics,'监控项')

        devices=QWidget();devices.setObjectName('page')
        dl=QVBoxLayout(devices);dl.setContentsMargins(18,20,18,20)
        dl.addWidget(QLabel('已连接设备'))
        self.list_widget=QListWidget()
        for d in config.get('devices',[]):
            if isinstance(d,str):d={'addr':d,'type':'adb' if d.endswith(':5555') else 'hdc'}
            self._append_device(dict(d))
        dl.addWidget(self.list_widget)
        row=QHBoxLayout()
        self.input=QLineEdit();self.input.setPlaceholderText('IP:端口，如 192.168.1.23:5555')
        self.protocol=QComboBox();self.protocol.addItem('ADB','adb');self.protocol.addItem('HDC','hdc')
        add=QPushButton('添加');add.clicked.connect(self._add_device)
        row.addWidget(self.input,1);row.addWidget(self.protocol);row.addWidget(add)
        dl.addLayout(row)
        remove=QPushButton('移除选中设备');remove.clicked.connect(self._del_device);dl.addWidget(remove)
        self.error=QLabel('');self.error.setStyleSheet('color:#FF6D7F;');dl.addWidget(self.error)
        hint=QLabel('HarmonyOS 4.x 使用 ADB；HarmonyOS NEXT 使用 HDC。\n4.x 设备重启后，需要通过 USB 重新开启无线连接。')
        hint.setObjectName('hint');hint.setWordWrap(True);dl.addWidget(hint)
        self.tabs.addTab(devices,'设备')

        buttons=QDialogButtonBox()
        ok=QPushButton('保存设置');ok.setObjectName('primary');ok.clicked.connect(self.accept)
        cancel=QPushButton('取消');cancel.clicked.connect(self.reject)
        buttons.addButton(cancel,QDialogButtonBox.RejectRole);buttons.addButton(ok,QDialogButtonBox.AcceptRole)
        root.addWidget(buttons)
        self.opacity.valueChanged.connect(self._preview)
        self.scale_slider.valueChanged.connect(self._preview)
        self.font_picker.currentFontChanged.connect(self._preview)
        self.width_picker.valueChanged.connect(self._preview)
        self.layout_picker.currentIndexChanged.connect(self._preview)
        self.topmost.toggled.connect(self._preview)
        self.opacity_label.setText(f"{self.opacity.value()}%")
        self.scale_label.setText(f"{self.scale_slider.value()}%")

    def _appearance(self):
        return dict(opacity=self.opacity.value(),scale=self.scale_slider.value(),
                    font=self.font_picker.currentFont().family(),card_width=self.width_picker.value(),
                    layout=self.layout_picker.currentData(),topmost=self.topmost.isChecked())
    def _preview(self,*_):
        self.opacity_label.setText(f'{self.opacity.value()}%')
        self.scale_label.setText(f'{self.scale_slider.value()}%')
        self.appearance_changed.emit(self._appearance())
    def _append_device(self,d):
        item=QListWidgetItem(f"{d['addr']}   ·   {d['type'].upper()}")
        item.setData(Qt.UserRole,d)
        self.list_widget.addItem(item)
    def _add_device(self):
        addr=self.input.text().strip()
        if not addr:return
        dtype=self.protocol.currentData()
        if ':' not in addr:addr+=':5555' if dtype=='adb' else ':8710'
        host,port=addr.rsplit(':',1)
        if not host or not port.isdigit() or not 1<=int(port)<=65535:
            self.error.setText('请输入有效的 IP 或主机名及端口（1–65535）。');return
        if any(self.list_widget.item(i).data(Qt.UserRole)['addr']==addr for i in range(self.list_widget.count())):
            self.error.setText('该设备已经在列表中。');return
        self._append_device(dict(addr=addr,type=dtype));self.input.clear();self.error.clear()
    def _del_device(self):
        for it in self.list_widget.selectedItems():self.list_widget.takeItem(self.list_widget.row(it))
    def _set_all(self,on):
        for cb in self.checks.values():cb.setChecked(on)
    def _restore_default(self):
        for item in C.ITEMS:self.checks[item.id].setChecked(item.default)
    def result_config(self):
        return dict(devices=[self.list_widget.item(i).data(Qt.UserRole) for i in range(self.list_widget.count())],selected=[k for k,cb in self.checks.items() if cb.isChecked()],appearance=self._appearance())
