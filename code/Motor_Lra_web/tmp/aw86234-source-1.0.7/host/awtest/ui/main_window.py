# -*- coding: utf-8 -*-
"""主窗口：工具栏 + 降级横幅 + 常驻通道矩阵 + 标签页 + 状态栏 + 安全行为。

规格书 §4 / §7 的落地点：

* 工具栏：串口下拉 / 刷新 / 连接断开 · **全部停止**（始终可用且显眼）/ 软复位全部 / TRIG 静默；
* 常驻通道矩阵在任何页面之上，所有页面操作都以它的勾选为准；
* 串口 I/O 全在工作线程；界面只收发 Qt 信号（``DeviceBridge`` + ``TaskRunner``）；
* 断线 → 状态栏变红 + 提示"固件将在 5 秒内自动停止全部通道"；
* ``EVT_SAFETY_TRIP`` / ``EVT_PLAY_DONE(reason=2)`` → 红色告警条；
* 关闭串口与退出程序前**尽力**发 ``STOP_ALL`` + ``TRIG_PARK``（预算 500 ms，不阻塞退出）；
* 心跳定时器连接后自动启动，**界面上不提供关闭选项**；
* 最后一次连接的串口用 ``QSettings`` 记住。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QScrollArea,
                               QTabWidget, QVBoxLayout, QWidget)

from awtest.device import AWTestDevice, ConnectionInfo
from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.protocol.transport import PortInfo

from . import (HAVE_SCRIPT, HAVE_WAVEFORM, LOG_COLORS, UiContext, cap_reason,
               mask_to_text)
from .page_control import PageControl
from .page_tactile import PageTactile
from .page_channels import PageChannels
from .page_connection import PageConnection
from .page_console import PageConsole
from .page_measure import PageMeasure
from .page_mode_params import PageModeParams
from .page_register import PageRegister
from .page_testscript import PageTestScript
from .page_waveform import PageWaveform
from .widgets import ChannelMatrix

ORG_NAME = "Glovity"
APP_NAME = "AW86234-Testbench"
KEY_LAST_PORT = "connection/last_port"

_STOP_BUDGET_S = 0.5      # 退出/关闭串口前的停止指令预算（规格书 §7）


class MainWindow(QMainWindow):
    """上位机主窗口。"""

    def __init__(self, device: Optional[AWTestDevice] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AW86234 八路振动测试板 · PC 上位机（PySide6）")
        self.resize(1120, 800)
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.ctx = UiContext(device if device is not None else AWTestDevice(), self)
        self.ctx.bridge.attach(self.ctx.device)
        self._connected = False
        self._alert_active = False

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._wire_signals()
        self.stop_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.stop_shortcut.activated.connect(lambda: self.emergency_stop(announce=True))

        self._port_timer = QTimer(self)
        self._port_timer.setInterval(5000)
        self._port_timer.timeout.connect(self._auto_refresh_ports)
        self._port_timer.start()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._poll_status)

        self.refresh_ports()
        self.ctx.log("上位机已启动。流程提示：选择串口 → 连接 → 用通道矩阵勾选 → "
                     "在『快捷控制』选择模式 → 下发并启动。", "info")
        self._update_toolbar_state(False)
        self._check_optional_modules()

    # ==================================================================
    # 构造
    # ==================================================================

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("主工具栏")
        bar.setMovable(False)

        bar.addWidget(QLabel(" 串口："))
        self.cmb_port = QComboBox()
        # ★ 原来 280 px：这是工具栏里最占宽度的控件，窗口变窄时会把后面几个按钮
        #   挤进 QToolBar 的 "»" 溢出菜单（用户反馈"有些菜单看不到打不开"）。
        #   降到 200 后工具栏最小宽度明显下降，下拉列表本身仍按内容自适应。
        self.cmb_port.setMinimumWidth(200)
        self.cmb_port.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_port.setMinimumContentsLength(18)
        self.cmb_port.setToolTip("列出全部串口（Espressif USB-JTAG 优先）。显示的 VID:PID 与描述"
                                 "来自串口枚举。")
        bar.addWidget(self.cmb_port)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        bar.addWidget(self.btn_refresh)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setStyleSheet("font-weight:bold;")
        self.btn_connect.setToolTip("按 05 文档 §3.3：MODE BIN → PING → GET_CAPS → GET_STATUS → "
                                    "BUS_PROBE → CH_PROBE_ALL → 启动心跳")
        self.btn_connect.clicked.connect(self.toggle_connection)
        bar.addWidget(self.btn_connect)

        bar.addWidget(QLabel("  │  "))

        self.btn_stop_all = QPushButton("■ 全部停止")
        self.btn_stop_all.setStyleSheet(
            "font-weight:bold; color:white; background-color:#a01818; padding:4px 10px;")
        self.btn_stop_all.setToolTip("STOP_ALL(immediate=1) + TRIG_PARK —— 安全第一，始终可用")
        self.btn_stop_all.clicked.connect(lambda: self.emergency_stop(announce=True))
        bar.addWidget(self.btn_stop_all)

        self.btn_advanced = QPushButton("高级调试")
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.toggled.connect(self._set_advanced)
        bar.addWidget(self.btn_advanced)

        self.btn_softrst_all = QPushButton("全部软复位")
        self.btn_softrst_all.setToolTip("对所有通道执行 AW_SOFTRST（二次确认；软复位会清配置寄存器）")
        self.btn_softrst_all.clicked.connect(self._softrst_all)
        self._advanced_actions = [bar.addWidget(self.btn_softrst_all)]

        self.btn_trig_park = QPushButton("TRIG 静默")
        self.btn_trig_park.setToolTip("TRIG_PARK：把所有 TRIG 输出拉低")
        self.btn_trig_park.clicked.connect(self._trig_park)
        self._advanced_actions.append(bar.addWidget(self.btn_trig_park))

        self.btn_clear_err = QPushButton("清错")
        self.btn_clear_err.setToolTip("CLEAR_ERRORS：退出 safety_state=1 安全态")
        self.btn_clear_err.clicked.connect(self._clear_errors)
        self._advanced_actions.append(bar.addWidget(self.btn_clear_err))

    def _build_central(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # 降级横幅（CAP_INTERNAL_PULLUP=1 / CAP_TCA_ONLINE=0）
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#4a3b00; color:#ffd75f; padding:6px; border:1px solid #e6b422;")
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        # 告警条（EVT_SAFETY_TRIP / 断线）
        alert_row = QWidget()
        ar = QHBoxLayout(alert_row)
        ar.setContentsMargins(0, 0, 0, 0)
        self.alert_label = QLabel("")
        self.alert_label.setWordWrap(True)
        self.alert_label.setStyleSheet(
            "background:#5a0000; color:#ff8080; padding:6px; border:1px solid #ff5555;"
            "font-weight:bold;")
        ar.addWidget(self.alert_label, 1)
        self.btn_alert_ack = QPushButton("知道了")
        self.btn_alert_ack.clicked.connect(self._dismiss_alert)
        ar.addWidget(self.btn_alert_ack)
        self.alert_bar = alert_row
        self.alert_bar.setVisible(False)
        root.addWidget(self.alert_bar)

        # 常驻通道矩阵
        self.matrix = ChannelMatrix()
        root.addWidget(self.matrix)

        # 标签页（六个主页面 + 连接 / 通道 两个辅助页）
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab {font-size:16px;font-weight:600;min-height:28px;padding:10px 24px;margin-right:4px;border:1px solid #cbd5e1;border-radius:6px;background:#edf2f7;color:#334155;} QTabBar::tab:selected {background:#1769aa;color:white;border:1px solid #12568c;} QTabBar::tab:hover {background:#d6eaff;color:#163e60;}")
        root.addWidget(self.tabs, 1)
        self.page_control = PageControl(self.ctx)
        self.page_live = self.page_control.live_control
        self.page_tactile = PageTactile(self.ctx)
        self.page_tactile.running_changed.connect(self._tactile_running)
        self.page_mode_params = PageModeParams(self.ctx)
        self.page_waveform = PageWaveform(self.ctx)
        self.page_measure = PageMeasure(self.ctx)
        self.page_register = PageRegister(self.ctx)
        self.page_testscript = PageTestScript(self.ctx)
        self.page_console = PageConsole(self.ctx)
        self.page_connection = PageConnection(self.ctx)
        self.page_channels = PageChannels(self.ctx, self.matrix)
        self.pages = {
            "control": self.page_control,
            "tactile": self.page_tactile,
            "mode_params": self.page_mode_params,
            "waveform": self.page_waveform,
            "measure": self.page_measure,
            "register": self.page_register,
            "testscript": self.page_testscript,
            "console": self.page_console,
            "connection": self.page_connection,
            "channels": self.page_channels,
        }
        self.tabs.addTab(self._scrollable(self.page_control), "快捷控制")
        self.tabs.addTab(self._scrollable(self.page_tactile), "触觉反馈")
        self.tabs.addTab(self._scrollable(self.page_connection), "连接")
        self.tabs.addTab(self._scrollable(self.page_channels), "通道")
        self.tabs.addTab(self._scrollable(self.page_mode_params), "模式与参数")
        self.tabs.addTab(self._scrollable(self.page_waveform), "波形编辑")
        self.tabs.addTab(self._scrollable(self.page_measure), "测量")
        self.tabs.addTab(self._scrollable(self.page_register), "寄存器")
        self.tabs.addTab(self._scrollable(self.page_testscript), "测试脚本")
        self.tabs.addTab(self._scrollable(self.page_console), "日志")
        self.tabs.setCurrentWidget(self.tabs.widget(0))

        self.setCentralWidget(central)
        self._set_advanced(False)

    def _tactile_running(self, running: bool) -> None:
        # One motor-control owner; emergency stop and connection toolbar remain usable.
        for page in self.pages.values():
            if page not in (self.page_tactile, self.page_console):
                page.setEnabled(not running)

    def _set_advanced(self, enabled: bool) -> None:
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index).widget()
            visible = enabled or page in (self.page_control, self.page_live, self.page_tactile, self.page_waveform, self.page_console)
            self.tabs.setTabVisible(index, visible)
        for action in self._advanced_actions:
            action.setVisible(enabled)
        self.matrix.set_compact(not enabled)
        if not self.tabs.isTabVisible(self.tabs.currentIndex()):
            self.tabs.setCurrentIndex(0)

    def _navigate(self, key: str) -> None:
        page = self.pages[key]
        if key == "mode_params":
            page._sync_ui(self.page_control.profile())
        if page not in (self.page_control, self.page_live, self.page_waveform, self.page_console):
            self.btn_advanced.setChecked(True)
        for index in range(self.tabs.count()):
            if self.tabs.widget(index).widget() is page:
                self.tabs.setCurrentIndex(index)
                return

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """把页面包进滚动区（★ 修复"底部内容看不到、也滚不到"）。

        每个页面各自的最小高度（测量 878、模式与参数 803…）会累加到窗口上：
        顶栏 + 通道矩阵 + 标签栏 + 页面 ≈ **最小 1294 px**。在 1080p 或带缩放的
        屏幕上窗口放不下，Qt 又不会让窗口小于 minimumSizeHint —— 结果就是底部
        内容被切掉，而当时 5 个页面根本没有滚动区，用户既看不到也滚不到。

        包一层 QScrollArea 后，窗口最小尺寸与页面内容解耦（滚动区自身的最小
        约 70×70），窗口可以任意缩小，超出的内容靠滚动条访问。

        注：连接 / 模式与参数 / 波形编辑 三页内部已有自己的滚动区，这里会形成
        嵌套 —— 这是良性的：外层视口够高时只有内层滚动，不够高时外层滚动而内层
        正好处于自己的最小高度，同一时刻只会有**一个**滚动条是可用的。
        """
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sa.setWidget(page)
        return sa

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_conn = QLabel("未连接")
        self.lbl_bus = QLabel("总线 —")
        self.lbl_safety = QLabel("安全状态 —")
        self.lbl_err = QLabel("上次错误 —")
        self.lbl_rtt = QLabel("往返 —")
        self.lbl_hb = QLabel("心跳：未启动")
        for lbl in (self.lbl_conn, self.lbl_bus, self.lbl_safety, self.lbl_err,
                    self.lbl_rtt, self.lbl_hb):
            lbl.setStyleSheet("padding:0 8px;")
            sb.addPermanentWidget(lbl)
        self.lbl_busy = QLabel("")
        sb.addWidget(self.lbl_busy)

    def _wire_signals(self) -> None:
        ctx = self.ctx
        ctx.log_line.connect(self._on_log)
        self.page_control.navigate.connect(self._navigate)
        self.page_control.stop_requested.connect(lambda: self.emergency_stop(announce=True))
        ctx.bridge.disconnected.connect(self._on_disconnected)
        ctx.bridge.event_received.connect(self._on_event)
        ctx.bridge.frame_received.connect(self._on_frame)
        ctx.runner.busy_changed.connect(self._on_busy)
        ctx.selection_changed.connect(self._on_selection)
        ctx.mode_changed.connect(lambda ch, mode: self.matrix.set_channel_mode(ch, mode))
        ctx.playing_changed.connect(lambda ch: self.matrix.set_playing(
            ch, bool(self.ctx.device.playing.get(ch)) if self.ctx.device else False))
        self.matrix.selection_changed.connect(self._on_matrix_selection)
        self.matrix.probe_requested.connect(self._probe_one)
        self.matrix.probe_all_requested.connect(self.page_channels.probe_all)
        self.matrix.info_message.connect(lambda text: self.ctx.log(text, "warn"))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.ctx.set_selection(self.matrix.selected_mask())

    def _check_optional_modules(self) -> None:
        if not HAVE_WAVEFORM:
            self.ctx.log("波形模块 awtest.waveform 尚未就绪：波形编辑页只做提示，"
                         "生成/下发按钮不可用。", "warn")
        if not HAVE_SCRIPT:
            self.ctx.log("测试脚本模块 awtest.script（engine/report）尚未就绪："
                         "测试脚本页只做提示，运行按钮不可用。", "warn")

    # ==================================================================
    # 串口枚举与连接
    # ==================================================================

    def refresh_ports(self) -> None:
        def job(progress):
            return AWTestDevice.list_ports(False)

        def done(ports: List[PortInfo]) -> None:
            previous = self.cmb_port.currentData()
            self.cmb_port.blockSignals(True)
            self.cmb_port.clear()
            for info in ports:
                vid_pid = (f" [{info.vid:04X}:{info.pid:04X}]"
                           if info.vid is not None and info.pid is not None else "")
                self.cmb_port.addItem(f"{info.label()}{vid_pid}", info.device)
                tip = f"{info.label()}{vid_pid}"
                if info.hwid:
                    tip += f"\n{info.hwid}"
                self.cmb_port.setItemData(self.cmb_port.count() - 1, tip, Qt.ToolTipRole)
            if not ports:
                self.cmb_port.addItem("（未发现串口）", "")
            last = str(previous or self.settings.value(KEY_LAST_PORT, "") or "")
            idx = self.cmb_port.findData(last)
            if idx >= 0:
                self.cmb_port.setCurrentIndex(idx)
            self.cmb_port.blockSignals(False)
            self.ctx.log(f"串口枚举完成：{len(ports)} 个端口"
                         + (f"，当前选择 {self.cmb_port.currentData() or '（无）'}"), "debug")

        self.ctx.task(job, on_done=done, name="枚举串口")

    def _auto_refresh_ports(self) -> None:
        """自动刷新串口下拉（已连接时不打扰用户）。"""
        if self._connected:
            return
        self.refresh_ports()

    def toggle_connection(self) -> None:
        if self._connected:
            self.disconnect_device()
        else:
            self.connect_device()

    def connect_device(self) -> None:
        port = self.cmb_port.currentData()
        if not port:
            self.ctx.warn(self, "没有可用串口",
                          "未选择串口。请插好 USB 后点『刷新』重新枚举。")
            return
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("连接中…")
        self.lbl_conn.setText(f"正在连接 {port} …")
        self.ctx.log(f"开始连接 {port}（MODE BIN → PING → GET_CAPS → GET_STATUS → "
                     f"BUS_PROBE → CH_PROBE_ALL → 心跳）", "user")
        dev = self.ctx.device

        def job(progress):
            def step(msg: str) -> None:
                progress(msg)
            return dev.connect(port, 115200, K.BusProbeResp.BUS_AUTO,
                               probe_channels=True, heartbeat=True, on_step=step)

        def on_progress(msg) -> None:
            self.lbl_conn.setText(f"连接中：{msg}")
            self.ctx.log(f"[连接] {msg}", "debug")

        def done(info: ConnectionInfo) -> None:
            self._connected = True
            self.ctx.info = info
            self.settings.setValue(KEY_LAST_PORT, info.port)
            self.ctx.set_caps(info.caps)
            self.ctx.info_changed.emit(info)
            self.page_connection.update_info(info)
            self.page_connection.update_status(info.status)
            self.page_connection.update_safety(self._read_safety_silent())
            self._apply_probe_result(info)
            self._apply_caps()
            self._update_toolbar_state(True)
            self.lbl_conn.setText(info.summary())
            self.lbl_conn.setStyleSheet("color:#8fbf6f; font-weight:bold;")
            self.ctx.log("连接成功：" + info.summary(), "info")
            self.ctx.connection_changed.emit(True)
            self._status_timer.start()
            if info.caps and info.caps.has(C.CAP_INTERNAL_PULLUP):
                self._set_banner(
                    "降级运行：上游 I2C 上拉不足，固件正在使用内部弱上拉 + 降速运行。"
                    "建议补焊 4.7 kΩ 上拉到 3.3 V（CAP_INTERNAL_PULLUP=1）。")
            if info.caps and not info.caps.has(C.CAP_TCA_ONLINE):
                self._set_banner("高位示警：TCA9548A（0x70）无应答，只有单路直连模式可用。")

        def failed(msg: str) -> None:
            self.btn_connect.setEnabled(True)
            self.btn_connect.setText("连接")
            self.lbl_conn.setText("未连接")
            self.lbl_conn.setStyleSheet("color:#ff5555;")
            detail = msg.splitlines()[0]
            if not isinstance(self.ctx.device, AWTestDevice):
                return
            hint = ("请确认：① 串口未被 idf.py monitor 等工具占用；"
                    "② 固件已烧录并上电；③ 若设备已在二进制模式，上位机会自动识别（先试 PING，"
                    "再试 MODE BIN）。")
            self.ctx.error(self, "连接失败", "无法连接设备：\n" + detail, hint)

        self.ctx.task(job, on_done=done, on_error=failed, on_progress=on_progress,
                      name="连接设备")

    def _read_safety_silent(self):
        try:
            cfg = self.ctx.device.get_safety()
            self.ctx.safety = cfg
            self.ctx.heartbeat_period_s = max(0.2, cfg.heartbeat_timeout_ms / 3000.0)
            return cfg
        except Exception:
            return None

    def _apply_probe_result(self, info: ConnectionInfo) -> None:
        if info.channels is None:
            self.matrix.set_all_unknown()
            return
        chipids: Dict[int, Optional[int]] = {}
        for chan, hid, lid, _st in info.channels.entries:
            chipids[chan] = (hid << 8) | lid
        self.matrix.apply_probe_all(info.channels.present_mask, chipids)
        n = bin(info.channels.present_mask).count("1")
        self.ctx.log(f"通道探测：present_mask=0x{info.channels.present_mask:02X}"
                     f"（{n}/8 在线）", "info")
        for ch in range(C.CHANNEL_COUNT):
            cid = chipids.get(ch)
            if cid is not None and cid != C.AW_CHIPID_EXPECTED:
                self.ctx.log(f"CH{ch} CHIPID = 0x{cid:04X}，与期望的 0x2340 不符："
                             "可能是 AW86234AFCR 之外的变体或通道映射接错。", "warn")

    def disconnect_device(self, ask: bool = True) -> None:
        if ask and not self.ctx.confirm(self, "断开连接",
                                        "确认断开串口？",
                                        "断开前会尽力发送 STOP_ALL + TRIG_PARK；"
                                        "断开后固件将在心跳超时（默认 5 秒）后自动停止全部通道。"):
            return
        self.emergency_stop(announce=True)
        dev = self.ctx.device

        def job(progress):
            dev.disconnect()

        def done(_r) -> None:
            self._after_disconnect("已按用户要求断开。已发送停止指令（STOP_ALL + TRIG_PARK）。")

        self.ctx.task(job, on_done=done, name="断开连接")

    def _after_disconnect(self, reason: str) -> None:
        self._connected = False
        self._status_timer.stop()
        self._update_toolbar_state(False)
        self.lbl_conn.setText("已断开")
        self.lbl_conn.setStyleSheet("color:#ff5555; font-weight:bold;")
        self.lbl_bus.setText("总线 —")
        self.lbl_safety.setText("安全状态 —")
        self.lbl_rtt.setText("往返 —")
        self.lbl_hb.setText("心跳：已停止（固件将在 5 秒内自动停止全部通道）")
        self.matrix.set_all_playing(0)
        self.matrix.set_all_unknown()
        self.ctx.info = None
        self.ctx.set_caps(None)
        self.ctx.info_changed.emit(None)
        self._set_banner("")
        self.ctx.connection_changed.emit(False)
        self.ctx.log(f"串口已断开：{reason}", "error")
        self._show_alert("串口已断开。固件将在 5 秒内自动停止全部通道。")

    def _update_toolbar_state(self, connected: bool) -> None:
        self.btn_connect.setText("断开" if connected else "连接")
        self.btn_connect.setEnabled(True)
        self.cmb_port.setEnabled(not connected)
        self.btn_refresh.setEnabled(not connected)
        for w in (self.btn_softrst_all, self.btn_trig_park, self.btn_clear_err):
            w.setEnabled(connected)
        # 全部停止永远可用（没连接时点它会提示）
        self.btn_stop_all.setEnabled(True)
        if not connected:
            self.lbl_conn.setStyleSheet("color:#ff5555; font-weight:bold;")
            self.lbl_hb.setText("心跳：未启动")

    # ==================================================================
    # 安全行为
    # ==================================================================

    def emergency_stop(self, announce: bool = False, synchronous: bool = False) -> None:
        self.page_tactile.cancel_pending()
        """尽力发送 STOP_ALL + TRIG_PARK；总预算 500 ms，绝不阻塞退出（规格书 §7）。"""
        self.page_control.cancel_pending()
        self.page_live.cancel_pending()
        dev = self.ctx.device
        if not synchronous and dev is not None and dev.is_open:
            self.page_channels.stop_all_and_park(announce=announce)
            return
        dev = self.ctx.device
        if dev is None or not dev.is_open:
            if announce:
                self.ctx.log("未连接设备，无需停止。", "info")
            return
        sent: List[str] = []
        t0 = time.perf_counter()
        try:
            dev.t.request(C.CMD_STOP_ALL, bytes([1]), timeout=0.2)
            sent.append("STOP_ALL")
        except Exception as exc:
            self.ctx.log(f"STOP_ALL 未获确认（{type(exc).__name__}），继续尝试 TRIG_PARK", "warn")
        if time.perf_counter() - t0 < _STOP_BUDGET_S - 0.2:
            try:
                dev.t.request(C.CMD_TRIG_PARK, b"", timeout=0.2)
                sent.append("TRIG_PARK")
            except Exception as exc:
                self.ctx.log(f"TRIG_PARK 未获确认（{type(exc).__name__}）", "warn")
        self.matrix.set_all_playing(0)
        if sent:
            text = "已发送停止指令：" + " + ".join(sent)
            if announce:
                self.ctx.log(text + f"（耗时 {(time.perf_counter() - t0) * 1000:.0f} ms）", "user")
            else:
                self.ctx.log(text, "info")
        else:
            self.ctx.log("停止指令未能送达（串口可能已断开）。固件会在心跳超时后自行停止。", "warn")

    def _softrst_all(self) -> None:
        if not self.ctx.require_device(self):
            return
        if not self.ctx.confirm(self, "全部软复位",
                                "确认对全部 8 路执行软复位（写 0x00 = 0xAA）？",
                                "软复位会清掉全部配置寄存器（只保留 SRAM），固件会重新下发 "
                                "Profile；请先停止播放。", danger=True):
            return
        self.emergency_stop(announce=False)
        self.page_connection.softrst(True)

    def _trig_park(self) -> None:
        if not self.ctx.require_device(self):
            return
        self.page_channels.trig_park()

    def _clear_errors(self) -> None:
        if not self.ctx.require_device(self):
            return
        self.page_connection.clear_errors()

    def _set_banner(self, text: str) -> None:
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    def _show_alert(self, text: str) -> None:
        self.alert_label.setText(text)
        self.alert_bar.setVisible(True)
        self._alert_active = True

    def _dismiss_alert(self) -> None:
        self.alert_bar.setVisible(False)
        self._alert_active = False

    # ==================================================================
    # 事件与状态
    # ==================================================================

    def _on_log(self, level: str, text: str) -> None:
        if level == "error":
            self.lbl_err.setText("上次错误：" + text[:60])
            self.lbl_err.setStyleSheet(f"color:{LOG_COLORS['error']}; padding:0 8px;")

    def _on_busy(self, busy: int) -> None:
        self.lbl_busy.setText(f"后台任务：{busy}" if busy else "")

    def _on_frame(self, frame, direction: str) -> None:
        if direction == "rx" and self.ctx.device is not None:
            rtt = self.ctx.device.stats.last_rtt_ms
            if rtt:
                avg = self.ctx.device.stats.rtt_avg_ms
                self.lbl_rtt.setText(f"往返：{rtt:.1f} ms（均 {avg:.1f} ms）")

    def _on_selection(self, mask: int) -> None:
        self.ctx.log(f"选中通道：{mask_to_text(mask)}", "debug")

    def _on_matrix_selection(self, mask: int) -> None:
        self.ctx.set_selection(mask)

    def _on_tab_changed(self, index: int) -> None:
        page = self.tabs.widget(index)
        if page is None:
            return
        self.ctx.log(f"切换到页面：{self.tabs.tabText(index)}", "debug")

    def _on_event(self, frame) -> None:
        dev = self.ctx.device
        evt = K.decode_event(frame)
        if frame.cmd == C.EVT_PLAY_STARTED and isinstance(evt, K.EvtPlayStarted):
            if frame.chan in range(C.CHANNEL_COUNT):
                dev.playing[frame.chan] = True
                dev.modes[frame.chan] = evt.mode
                self.matrix.set_playing(frame.chan, True)
                self.ctx.playing_changed.emit(frame.chan)
            self.ctx.log(f"播放开始：模式 {C.mode_name(evt.mode)}，"
                         f"安全超时 {evt.timeout_ms} ms", "info")
            return
        if frame.cmd == C.EVT_PLAY_DONE and isinstance(evt, K.EvtPlayDone):
            if frame.chan not in (0xFE, 0xFF):
                dev.playing[frame.chan] = False
                self.matrix.set_playing(frame.chan, False)
                self.ctx.playing_changed.emit(frame.chan)
            if evt.reason == C.PLAY_DONE_SAFETY_TIMEOUT:
                self._show_alert(
                    f"播放因安全超时被强制停止（EVT_PLAY_DONE reason=2）。"
                    f"实际超时值 {evt.elapsed_ms} ms —— 固件已 STOP(immediate=1) + TRIG_PARK。")
                self.ctx.log(f"★ 安全超时：播放被强制停止（{evt.elapsed_ms} ms）", "error")
            elif evt.reason == C.PLAY_DONE_HEARTBEAT_LOST:
                self._show_alert("播放因心跳丢失被停止（reason=4）。请检查串口连接。")
            else:
                self.ctx.log(f"播放结束：{evt.reason_name}（{evt.elapsed_ms} ms）", "info")
            return
        if frame.cmd == C.EVT_SAFETY_TRIP and isinstance(evt, K.EvtSafetyTrip):
            self._show_alert(
                f"★ 安全机制触发（EVT_SAFETY_TRIP）：{evt.reason_name}；"
                f"受影响通道掩码 0x{evt.affected_mask:02X}。固件已强制停止并 TRIG_PARK。")
            self.matrix.set_all_playing(0)
            self.ctx.log(f"安全机制触发：{evt.reason_name} "
                         f"（掩码 0x{evt.affected_mask:02X}）", "error")
            return
        if frame.cmd == C.EVT_ERROR and isinstance(evt, K.EvtError):
            self._show_alert(f"设备上报错误：{C.err_name(evt.errcode)}"
                             f"（{C.err_cn(evt.errcode)}）"
                             + (f" —— {evt.text}" if evt.text else ""))
            return
        if frame.cmd == C.EVT_SELFTEST_RESULT and isinstance(evt, K.EvtSelftestResult):
            self.ctx.log(f"自检结果：{evt.overall_name} · 在线掩码 "
                         f"0x{evt.present_mask:02X} · TCA={evt.tca} · "
                         f"VREG 掩码 0x{evt.vreg_mask:02X} · 问题 {evt.issue_count} 条"
                         + (f" · {evt.text}" if evt.text else ""), "info")

    def _poll_status(self) -> None:
        if not self.ctx.connected:
            return
        dev = self.ctx.device

        def job(progress):
            return dev.get_status()

        def done(status: K.Status) -> None:
            self.page_connection.update_status(status)
            self.lbl_safety.setText(f"安全状态：{status.safety_state_name}")
            self.lbl_safety.setStyleSheet(
                "padding:0 8px; color:#8fbf6f;" if status.safety_state == C.SAFETY_NORMAL
                else "padding:0 8px; color:#ff5555; font-weight:bold;")
            if status.last_err:
                self.lbl_err.setText(f"上次错误：{C.err_name(status.last_err)}"
                                     f"（{C.err_cn(status.last_err)}）")
                self.lbl_err.setStyleSheet("padding:0 8px; color:#e6b422;")
            else:
                self.lbl_err.setText("上次错误：无")
            cfg = self.ctx.safety
            self.lbl_hb.setText(
                "心跳：运行中（不可关闭，周期 "
                f"{self.ctx.heartbeat_period_s * 1000:.0f} ms）"
                + (f" · 固件心跳超时 {cfg.heartbeat_timeout_ms} ms" if cfg else ""))
            self.lbl_hb.setStyleSheet("padding:0 8px; color:#8fbf6f;")
            if status.safety_state == C.SAFETY_HEARTBEAT_LOST:
                self._show_alert("固件处于『心跳丢失安全态』：已停止全部通道。"
                                 "请点『清错』退出安全态，或重新连接。")
            elif status.safety_state == C.SAFETY_BUS_LOCKED:
                self._show_alert("固件处于『总线错误锁定』：需要『清错』+『总线恢复』。")

        self.ctx.task(job, on_done=done, name="GET_STATUS")

    def _on_disconnected(self, reason: str) -> None:
        self._after_disconnect(reason)

    # ==================================================================
    # 能力位落地
    # ==================================================================

    def _apply_caps(self) -> None:
        caps = self.ctx.caps
        caps_int = caps.caps if caps is not None else C.CAPS_EXPECTED_BOARD_A
        self.matrix.set_caps(caps_int, caps.channel_count if caps else C.CHANNEL_COUNT)
        self.lbl_bus.setText(self._bus_text())
        if caps is not None:
            missing = [cap_reason(bit, False) for bit, label, _n in C.CAP_LABELS
                       if not (caps_int & bit) and bit in
                       (C.CAP_RTP_48K, C.CAP_MOTOR_POWER_GPIO, C.CAP_RSTN_GPIO)]
            self.ctx.log("能力位：caps=0x%08X；不可用项原因：%s"
                         % (caps_int, "；".join(missing) if missing else "（无关键项）"), "info")
        for page in self.pages.values():
            handler = getattr(page, "on_caps_changed", None)
            if handler is not None:
                handler(caps)

    def _bus_text(self) -> str:
        info = self.ctx.info
        if info is None or info.bus is None:
            return "总线 —"
        bus = info.bus
        return (f"I2C {bus.bus_name}(IO{bus.sda_gpio}/IO{bus.scl_gpio})"
                f"@{bus.speed_hz // 1000}kHz"
                + ("·内部上拉" if bus.internal_pullup else ""))

    def _probe_one(self, chan: int) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.ch_probe(chan)

        def done(res: K.ChProbeResp) -> None:
            self.matrix.set_channel_present(chan, bool(res.present), res.chipid)
            text = (f"CH{chan} 探测：{'在线' if res.present else '离线'}"
                    f" · CHIPID 0x{res.chipid:04X}"
                    + ("" if res.chipid_ok else "（与期望 0x2340 不符）")
                    + f" · VREG {'正常' if res.vreg_ok else '异常'}")
            self.ctx.log(text, "info" if res.present and res.chipid_ok else "warn")
            self.page_channels._set_table(
                chan, 1, "在线 ✔" if res.present else "离线 ✘",
                "good" if res.present else "bad")
            self.page_channels._set_table(
                chan, 2, f"0x{res.chipid:04X}",
                "good" if res.chipid_ok else "warn")

        self.ctx.task(job, on_done=done, name=f"CH_PROBE(CH{chan})")

    # ==================================================================
    # 退出
    # ==================================================================

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.page_tactile.cancel_pending()
        self.page_live.cancel_pending()
        self.page_control.cancel_pending()
        if self.ctx.connected:
            self.page_control.cancel_pending()
            self.emergency_stop(announce=False, synchronous=True)
            self.ctx.log("退出前已尽力发送 STOP_ALL + TRIG_PARK（预算 500 ms）。", "info")
        try:
            if self.ctx.device is not None:
                self.ctx.device.disconnect()
        except Exception:
            pass
        try:
            self.page_measure.stop_recording()
        except Exception:
            pass
        key = self.cmb_port.currentData()
        if key:
            self.settings.setValue(KEY_LAST_PORT, key)
        self.ctx.shutdown()
        super().closeEvent(event)


__all__ = ["MainWindow"]
