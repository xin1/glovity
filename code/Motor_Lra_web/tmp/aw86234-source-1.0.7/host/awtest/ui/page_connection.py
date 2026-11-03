# -*- coding: utf-8 -*-
"""连接页（规格书 §5.1）：设备信息 / 能力清单 / 总线信息 / 安全参数 / 复位与总线控制。

页面本身不负责打开串口（那是工具栏的事），它负责把 ``ConnectionInfo`` 展示出来，
并提供"按能力位动态禁用 + 写明原因"的复位与总线操作区（规格书 §5.3）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from awtest.device import ConnectionInfo
from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import BasePage, cap_reason


class PageConnection(BasePage):
    """连接与设备信息页。"""

    title = "连接"

    def build(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setSpacing(8)

        root.addWidget(self._build_device_group())
        root.addWidget(self._build_caps_group())
        root.addWidget(self._build_bus_group())
        root.addWidget(self._build_safety_group())
        root.addWidget(self._build_reset_group())
        root.addStretch(1)

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------

    def _build_device_group(self) -> QGroupBox:
        box = QGroupBox("设备信息")
        form = QFormLayout(box)
        self.lbl_port = QLabel("—")
        self.lbl_proto = QLabel("—")
        self.lbl_fw = QLabel("—")
        self.lbl_board = QLabel("—")
        self.lbl_uptime = QLabel("—")
        self.lbl_channels = QLabel("—")
        self.lbl_safety_state = QLabel("—")
        self.lbl_last_err = QLabel("—")
        self.lbl_active = QLabel("—")
        form.addRow("串口", self.lbl_port)
        form.addRow("协议版本", self.lbl_proto)
        form.addRow("固件版本", self.lbl_fw)
        form.addRow("板号 board_id", self.lbl_board)
        form.addRow("运行时间 uptime", self.lbl_uptime)
        form.addRow("通道数", self.lbl_channels)
        form.addRow("安全状态", self.lbl_safety_state)
        form.addRow("上次错误 last_err", self.lbl_last_err)
        form.addRow("长操作通道掩码 active_mask", self.lbl_active)
        tip = QLabel("连接流程：MODE BIN → PING → GET_CAPS → GET_STATUS → BUS_PROBE → "
                     "CH_PROBE_ALL → 启动心跳（心跳不可关闭，关掉会让固件 5 秒后停机）。")
        tip.setStyleSheet("color:#888;")
        tip.setWordWrap(True)
        form.addRow(tip)
        return box

    # ------------------------------------------------------------------
    # 能力清单
    # ------------------------------------------------------------------

    def _build_caps_group(self) -> QGroupBox:
        box = QGroupBox("能力清单（GET_CAPS：不可用的项已写明原因）")
        layout = QVBoxLayout(box)
        self.caps_table = QTableWidget(0, 4)
        self.caps_table.setHorizontalHeaderLabels(["能力位", "状态", "名称", "说明 / 为什么不可用"])
        self.caps_table.verticalHeader().setVisible(False)
        self.caps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.caps_table.horizontalHeader().setStretchLastSection(True)
        self.caps_table.setMinimumHeight(280)
        layout.addWidget(self.caps_table)
        self.caps_raw = QLabel("caps = —")
        self.caps_raw.setStyleSheet("font-family:Consolas,monospace; color:#5fa8d3;")
        layout.addWidget(self.caps_raw)
        self.degrade_label = QLabel("")
        self.degrade_label.setStyleSheet("color:#e6b422; font-weight:bold;")
        self.degrade_label.setWordWrap(True)
        layout.addWidget(self.degrade_label)
        return box

    def refresh_caps_table(self) -> None:
        caps = self.ctx.caps
        rows = caps.cap_report() if caps is not None else [
            (bit, label, note, bool(C.CAPS_EXPECTED_BOARD_A & bit))
            for bit, label, note in C.CAP_LABELS]
        self.caps_table.setRowCount(len(rows))
        for row, (bit, label, note, ok) in enumerate(rows):
            reason = cap_reason(bit, ok)
            if ok:
                status = "降级" if reason else "可用"
                color = "#e6b422" if reason else "#8fbf6f"
                detail = reason or note
            else:
                status = "不可用"
                color = "#ff5555"
                detail = reason or note
            items = [QTableWidgetItem(f"bit{bit}"),
                     QTableWidgetItem(status),
                     QTableWidgetItem(label),
                     QTableWidgetItem(detail)]
            items[1].setForeground(Qt.GlobalColor.white)
            items[1].setBackground(Qt.GlobalColor.darkRed if not ok else Qt.GlobalColor.darkGreen)
            for col, item in enumerate(items):
                item.setToolTip(detail)
                self.caps_table.setItem(row, col, item)
        self.caps_table.resizeColumnsToContents()
        if caps is not None:
            self.caps_raw.setText(
                f"caps = 0x{caps.caps:08X}   max_payload = {caps.max_payload}   "
                f"channel_count = {caps.channel_count}")
            degrade = [cap_reason(bit, True) for bit, _l, _n, ok in rows
                       if ok and cap_reason(bit, True)]
            self.degrade_label.setText("降级运行：" + "；".join(degrade) if degrade else "")
        else:
            self.caps_raw.setText("caps = —（未连接：按本板期望值展示）")
            self.degrade_label.setText("")

    # ------------------------------------------------------------------
    # 总线信息
    # ------------------------------------------------------------------

    def _build_bus_group(self) -> QGroupBox:
        box = QGroupBox("总线信息与降速")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.lbl_bus = QLabel("—")
        self.lbl_gpio = QLabel("—")
        self.lbl_speed = QLabel("—")
        self.lbl_pullup = QLabel("—")
        self.lbl_tca = QLabel("—")
        self.lbl_ctrl = QLabel("—")
        form.addRow("当前总线", self.lbl_bus)
        form.addRow("I2C 引脚 (SDA/SCL)", self.lbl_gpio)
        form.addRow("速率", self.lbl_speed)
        form.addRow("内部上拉", self.lbl_pullup)
        form.addRow("TCA9548A", self.lbl_tca)
        form.addRow("TCA 控制字节", self.lbl_ctrl)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_speed100 = QPushButton("降到 100 kHz")
        self.btn_speed100.setToolTip("BUS_SET_SPEED(100000)：上拉不足时更稳")
        self.btn_speed100.clicked.connect(lambda: self._set_speed(C.I2C_SPEED_SAFE))
        self.btn_speed400 = QPushButton("升到 400 kHz")
        self.btn_speed400.setToolTip("BUS_SET_SPEED(400000)：TCA9548A 的上限就是 400 kHz")
        self.btn_speed400.clicked.connect(lambda: self._set_speed(C.I2C_SPEED_FAST))
        self.chk_pullup = QCheckBox("启用内部上拉（BUS_SET_PULLUP）")
        self.chk_pullup.setToolTip("上游 I2C 上拉不足时的补救手段；启用后建议配合 100 kHz 降速。")
        self.btn_pullup = QPushButton("应用上拉设置")
        self.btn_pullup.clicked.connect(self._set_pullup)
        row.addWidget(self.btn_speed100)
        row.addWidget(self.btn_speed400)
        row.addWidget(self.chk_pullup)
        row.addWidget(self.btn_pullup)
        row.addStretch(1)
        root.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_bus_probe = QPushButton("重新探测总线")
        self.btn_bus_probe.clicked.connect(self._bus_probe)
        self.btn_bus_recover = QPushButton("总线恢复 BUS_RECOVER")
        self.btn_bus_recover.setToolTip("9 clock + STOP / TCA RESET 脉冲，I2C 卡死时用")
        self.btn_bus_recover.clicked.connect(self._bus_recover)
        self.btn_tca_read = QPushButton("读 TCA 控制字节")
        self.btn_tca_read.clicked.connect(self._tca_read)
        self.btn_tca_reset = QPushButton("TCA_RESET")
        self.btn_tca_reset.clicked.connect(self._tca_reset)
        row2.addWidget(self.btn_bus_probe)
        row2.addWidget(self.btn_bus_recover)
        row2.addWidget(self.btn_tca_read)
        row2.addWidget(self.btn_tca_reset)
        row2.addStretch(1)
        root.addLayout(row2)
        return box

    # ------------------------------------------------------------------
    # 安全参数
    # ------------------------------------------------------------------

    def _build_safety_group(self) -> QGroupBox:
        box = QGroupBox("安全参数（GET/ SET_SAFETY）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(0, 60000)
        self.sp_timeout.setSuffix(" ms")
        self.sp_timeout.setToolTip("play_timeout_ms：每次 PLAY / TRIG_* 的安全超时，超时固件会\n"
                                   "强制 STOP + TRIG_PARK 并发 EVT_PLAY_DONE(reason=2)。")
        self.sp_maxfull = QSpinBox()
        self.sp_maxfull.setRange(0, 8)
        self.sp_maxfull.setToolTip("max_simul_full_amp：允许同时满幅的路数。\n"
                                   "超限时播放类命令返回 ERR_SAFETY_LIMIT。")
        self.sp_hb = QSpinBox()
        self.sp_hb.setRange(100, 60000)
        self.sp_hb.setSuffix(" ms")
        self.sp_hb.setToolTip("heartbeat_timeout_ms：固件超过这个时间没收到任何合法帧，\n"
                              "就会停止全部通道。上位机按 /3 周期发 PING，且不提供关闭选项。")
        form.addRow("播放超时", self.sp_timeout)
        form.addRow("同时满幅上限", self.sp_maxfull)
        form.addRow("心跳超时", self.sp_hb)
        root.addLayout(form)
        row = QHBoxLayout()
        self.btn_safety_read = QPushButton("读取安全参数")
        self.btn_safety_read.clicked.connect(self._read_safety)
        self.btn_safety_write = QPushButton("写入安全参数")
        self.btn_safety_write.setToolTip("写入后立即回读确认")
        self.btn_safety_write.clicked.connect(self._write_safety)
        row.addWidget(self.btn_safety_read)
        row.addWidget(self.btn_safety_write)
        row.addStretch(1)
        root.addLayout(row)
        self.lbl_safety_tip = QLabel(
            "心跳周期 = 心跳超时/3；心跳定时器连接后自动启动，界面上**不提供关闭选项**"
            "（关掉会让固件 5 秒后自动停机，属于安全设计）。")
        self.lbl_safety_tip.setStyleSheet("color:#888;")
        self.lbl_safety_tip.setWordWrap(True)
        root.addWidget(self.lbl_safety_tip)
        return box

    # ------------------------------------------------------------------
    # 复位区
    # ------------------------------------------------------------------

    def _build_reset_group(self) -> QGroupBox:
        box = QGroupBox("复位与器件操作")
        root = QVBoxLayout(box)

        grid = QGridLayout()
        self.sp_reset_delay = QSpinBox()
        self.sp_reset_delay.setRange(0, 2000)
        self.sp_reset_delay.setValue(100)
        self.sp_reset_delay.setSuffix(" ms")
        self.sp_reset_delay.setToolTip("MCU_RESET 的延迟：收到命令后延迟这么久再复位 ESP32-C6")
        self.btn_mcu_reset = QPushButton("MCU 复位")
        self.btn_mcu_reset.clicked.connect(self._mcu_reset)
        grid.addWidget(QLabel("MCU_RESET 延迟"), 0, 0)
        grid.addWidget(self.sp_reset_delay, 0, 1)
        grid.addWidget(self.btn_mcu_reset, 0, 2)

        self.btn_softrst_sel = QPushButton("软复位选中通道")
        self.btn_softrst_sel.setToolTip("AW_SOFTRST（写 0x00=0xAA）。★ 软复位会清掉全部配置寄存器，\n"
                                        "固件会重新应用一遍 Profile；本板没有硬件复位。")
        self.btn_softrst_sel.clicked.connect(lambda: self._softrst(False))
        self.btn_softrst_all = QPushButton("全部软复位")
        self.btn_softrst_all.setStyleSheet("color:#ff5555;")
        self.btn_softrst_all.setToolTip("对全部 8 路执行 AW_SOFTRST（需要二次确认）")
        self.btn_softrst_all.clicked.connect(lambda: self._softrst(True))
        grid.addWidget(self.btn_softrst_sel, 1, 0, 1, 2)
        grid.addWidget(self.btn_softrst_all, 1, 2)

        self.btn_power_cycle = QPushButton("断电重启")
        self.btn_power_cycle.clicked.connect(self._power_cycle)
        grid.addWidget(self.btn_power_cycle, 2, 0)
        self.btn_hw_reset = QPushButton("硬件复位（RSTN）")
        self.btn_hw_reset.clicked.connect(self._hw_reset)
        grid.addWidget(self.btn_hw_reset, 2, 1)

        self.btn_init_default = QPushButton("下发器件默认初始化")
        self.btn_init_default.setToolTip("AW_INIT_DEFAULT：把 0x00~0x69 恢复成数据手册默认值\n"
                                         "（注意会踩到 0x45/0x33/0x3A 的默认值陷阱）")
        self.btn_init_default.clicked.connect(self._init_default)
        grid.addWidget(self.btn_init_default, 2, 2)
        root.addLayout(grid)

        row = QHBoxLayout()
        self.btn_wake = QPushButton("唤醒 AW_WAKE")
        self.btn_wake.clicked.connect(lambda: self._dev_cmd("aw_wake", "唤醒"))
        self.btn_standby = QPushButton("待机 AW_STANDBY")
        self.btn_standby.clicked.connect(lambda: self._dev_cmd("aw_standby", "待机"))
        self.btn_clear_err = QPushButton("清除错误 CLEAR_ERRORS")
        self.btn_clear_err.setToolTip("退出 safety_state=1 安全态；总线锁定（=2）还需要 BUS_RECOVER")
        self.btn_clear_err.clicked.connect(self._clear_errors)
        self.btn_selftest = QPushButton("固件自检 SELFTEST")
        self.btn_selftest.clicked.connect(self._selftest)
        for b in (self.btn_wake, self.btn_standby, self.btn_clear_err, self.btn_selftest):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.reset_note = QLabel("")
        self.reset_note.setStyleSheet("color:#e6b422;")
        self.reset_note.setWordWrap(True)
        root.addWidget(self.reset_note)
        return box

    # ==================================================================
    # 数据刷新（由主窗口调用）
    # ==================================================================

    def update_info(self, info: Optional[ConnectionInfo]) -> None:
        if info is None:
            for lbl in (self.lbl_port, self.lbl_proto, self.lbl_fw, self.lbl_board,
                        self.lbl_uptime, self.lbl_channels):
                lbl.setText("—")
            self.refresh_caps_table()
            return
        self.lbl_port.setText(info.port or "—")
        if info.ping:
            self.lbl_proto.setText(f"0x{info.ping.proto_ver:02X} (v1.0)")
            self.lbl_fw.setText(info.ping.fw_version)
            self.lbl_board.setText(f"{info.ping.board_id}"
                                   + ("（AW86234 八路振动测试板 A）"
                                      if info.ping.board_id == C.BOARD_ID_VIBRATION_TEST_BOARD_A else ""))
            self.lbl_uptime.setText(self._fmt_uptime(info.ping.uptime_ms))
        if info.caps:
            self.lbl_channels.setText(f"{info.caps.channel_count} 路")
        if info.bus:
            self.update_bus(info.bus)
        self.refresh_caps_table()

    def update_status(self, status: Optional[K.Status]) -> None:
        if status is None:
            self.lbl_safety_state.setText("—")
            self.lbl_last_err.setText("—")
            self.lbl_active.setText("—")
            return
        self.lbl_safety_state.setText(f"{status.safety_state} · {status.safety_state_name}")
        err = status.last_err
        self.lbl_last_err.setText("无" if not err
                                  else f"{C.err_name(err)}（{C.err_cn(err)}）")
        self.lbl_active.setText(f"0x{status.active_mask:02X}")
        self.lbl_uptime.setText(self._fmt_uptime(status.uptime_ms))

    def update_bus(self, bus: Optional[K.BusProbeResp]) -> None:
        if bus is None:
            for lbl in (self.lbl_bus, self.lbl_gpio, self.lbl_speed,
                        self.lbl_pullup, self.lbl_tca):
                lbl.setText("—")
            return
        self.lbl_bus.setText(f"{bus.bus_name}（sel_used={bus.sel_used}）")
        self.lbl_gpio.setText(f"SDA=IO{bus.sda_gpio} · SCL=IO{bus.scl_gpio}")
        self.lbl_speed.setText(f"{bus.speed_hz} Hz（{bus.speed_hz // 1000} kHz）")
        self.lbl_pullup.setText("已启用（降级运行）" if bus.internal_pullup else "未启用")
        self.lbl_tca.setText(f"{'在线' if bus.tca_present else '未响应'}"
                             f"（地址 0x{bus.tca_addr:02X}）")
        self.lbl_ctrl.setText(f"0x{bus.ctrl_readback:02X}")
        if bus.internal_pullup:
            self.lbl_pullup.setStyleSheet("color:#e6b422;")
            self.reset_note.setText(
                "降级运行：内部弱上拉已启用（上游上拉不足）。建议补焊 4.7 kΩ 上拉到 3.3 V，"
                "并把速率降到 100 kHz。")
        else:
            self.lbl_pullup.setStyleSheet("")
            self.reset_note.setText("")

    def update_safety(self, cfg: Optional[K.SafetyCfg]) -> None:
        if cfg is None:
            return
        self.sp_timeout.setValue(cfg.play_timeout_ms)
        self.sp_maxfull.setValue(cfg.max_simul_full_amp)
        self.sp_hb.setValue(cfg.heartbeat_timeout_ms)

    @staticmethod
    def _fmt_uptime(ms: int) -> str:
        s = ms / 1000.0
        if s < 60:
            return f"{s:.1f} s"
        return f"{int(s // 60)} min {s % 60:.1f} s"

    # ==================================================================
    # 能力位驱动（规格书 §5.3）
    # ==================================================================

    def _apply_caps(self) -> None:
        motor = self.ctx.cap(C.CAP_MOTOR_POWER_GPIO)
        self.btn_power_cycle.setEnabled(motor)
        if motor:
            self.btn_power_cycle.setToolTip("通过 GPIO 切断/恢复马达电源后重启（需二次确认）")
        else:
            self.btn_power_cycle.setToolTip(
                "不可用（CAP_MOTOR_POWER_GPIO=0）：" + cap_reason(C.CAP_MOTOR_POWER_GPIO, False))
        rstn = self.ctx.cap(C.CAP_RSTN_GPIO)
        self.btn_hw_reset.setVisible(True)
        self.btn_hw_reset.setEnabled(rstn)
        self.btn_hw_reset.setToolTip(
            "通过 RSTN 引脚复位 AW86234" if rstn else
            "不可用（CAP_RSTN_GPIO=0）：" + cap_reason(C.CAP_RSTN_GPIO, False))
        self.refresh_caps_table()

    def on_caps_changed(self, caps) -> None:
        self._apply_caps()

    def on_connection_changed(self, ok: bool) -> None:
        for w in (self.btn_speed100, self.btn_speed400, self.btn_pullup, self.chk_pullup,
                  self.btn_bus_probe, self.btn_bus_recover, self.btn_tca_read,
                  self.btn_tca_reset, self.btn_safety_read, self.btn_safety_write,
                  self.btn_mcu_reset, self.btn_softrst_sel, self.btn_softrst_all,
                  self.btn_init_default, self.btn_wake, self.btn_standby,
                  self.btn_clear_err, self.btn_selftest):
            w.setEnabled(ok)
        self._apply_caps()
        if not ok:
            self.update_info(None)
            self.update_status(None)
            self.update_bus(None)

    # ==================================================================
    # 操作实现
    # ==================================================================

    def _bus_probe(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.bus_probe(K.BusProbeResp.BUS_AUTO)

        def done(bus):
            self.update_bus(bus)
            self.ctx.log(f"总线探测：{bus.bus_name} IO{bus.sda_gpio}/IO{bus.scl_gpio} "
                         f"@{bus.speed_hz // 1000}kHz "
                         f"内部上拉={'开' if bus.internal_pullup else '关'}", "info")

        self.ctx.task(job, on_done=done, name="BUS_PROBE")

    def _set_speed(self, hz: int) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.bus_set_speed(hz)

        def done(actual: int) -> None:
            self.lbl_speed.setText(f"{actual} Hz（{actual // 1000} kHz）")
            self.ctx.log(f"总线速率已设为 {actual // 1000} kHz", "user")

        self.ctx.task(job, on_done=done, name="BUS_SET_SPEED")

    def _set_pullup(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        enable = self.chk_pullup.isChecked()

        def job(progress):
            dev.bus_set_pullup(enable)
            return dev.bus_probe(K.BusProbeResp.BUS_AUTO)

        self.ctx.task(job, on_done=lambda bus: (self.update_bus(bus),
                                                self.ctx.log(
                                                    f"内部上拉已{'启用' if enable else '关闭'}", "user")),
                      name="BUS_SET_PULLUP")

    def _bus_recover(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.bus_recover()

        def done(method: int) -> None:
            self.ctx.log(f"总线恢复结果：{C.BUS_RECOVER_METHODS.get(method, method)}",
                         "info" if method else "warn")

        self.ctx.task(job, on_done=done, name="BUS_RECOVER")

    def _tca_read(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.tca_read_ctrl(), dev.tca_get_mask()

        def done(res) -> None:
            ctrl, mask = res
            self.lbl_ctrl.setText(f"0x{ctrl:02X}")
            self.lbl_tca.setText(self.lbl_tca.text() + f" · 选通掩码 0x{mask:02X}")
            self.ctx.log(f"TCA 控制字节 0x{ctrl:02X}，选通掩码 0x{mask:02X}", "info")

        self.ctx.task(job, on_done=done, name="TCA_READ_CTRL")

    def _tca_reset(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            dev.tca_reset(10)

        self.ctx.task(job, on_done=lambda _r: self.ctx.log("TCA_RESET 已发送（保持 10 µs）", "user"),
                      name="TCA_RESET")

    def _read_safety(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.get_safety()

        def done(cfg):
            self.ctx.safety = cfg
            self.update_safety(cfg)
            self.ctx.log(f"安全参数：播放超时 {cfg.play_timeout_ms} ms · 同时满幅上限 "
                         f"{cfg.max_simul_full_amp} · 心跳超时 {cfg.heartbeat_timeout_ms} ms",
                         "info")

        self.ctx.task(job, on_done=done, name="GET_SAFETY")

    def _write_safety(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        cfg = K.SafetyCfg(play_timeout_ms=self.sp_timeout.value(),
                          max_simul_full_amp=self.sp_maxfull.value(),
                          heartbeat_timeout_ms=self.sp_hb.value(),
                          flags=self.ctx.safety.flags if self.ctx.safety else 0)
        if not self.ctx.confirm(self, "写入安全参数",
                                "确认写入以下安全参数？",
                                f"播放超时 {cfg.play_timeout_ms} ms\n"
                                f"同时满幅上限 {cfg.max_simul_full_amp}\n"
                                f"心跳超时 {cfg.heartbeat_timeout_ms} ms"):
            return

        def job(progress):
            return dev.set_safety(cfg)

        def done(back):
            self.ctx.safety = back
            self.update_safety(back)
            self.ctx.log(f"安全参数已写入并回读：{back.play_timeout_ms}/"
                         f"{back.max_simul_full_amp}/{back.heartbeat_timeout_ms}", "user")

        self.ctx.task(job, on_done=done, name="SET_SAFETY")

    def _mcu_reset(self) -> None:
        if not self.ctx.require_device(self):
            return
        delay = self.sp_reset_delay.value()
        if not self.ctx.confirm(self, "MCU 复位",
                                f"确认复位 ESP32-C6（延迟 {delay} ms）？",
                                "复位后串口会重新枚举，需要重新连接。", danger=True):
            return
        dev = self.ctx.device

        def job(progress):
            dev.mcu_reset(delay)

        self.ctx.task(job, on_done=lambda _r: self.ctx.log(f"MCU_RESET 已发送（延迟 {delay} ms）", "user"),
                      name="MCU_RESET")

    # ---- 公开入口（主窗口工具栏调用） ----

    def softrst(self, all_channels: bool) -> None:
        """软复位（全部或选中通道）。"""
        self._softrst(all_channels)

    def clear_errors(self) -> None:
        """CLEAR_ERRORS。"""
        self._clear_errors()

    def read_safety(self) -> None:
        """GET_SAFETY。"""
        self._read_safety()

    def _softrst(self, all_channels: bool) -> None:
        if not self.ctx.require_device(self):
            return
        if all_channels:
            chans = list(range(self.ctx.channels))
            if not self.ctx.confirm(self, "全部软复位",
                                    "确认对全部通道执行软复位（写 0x00 = 0xAA）？",
                                    "软复位会清掉全部配置寄存器（只保留 SRAM），固件会重新下发 "
                                    "Profile；本板没有硬件复位，这是唯一的软件复位手段。",
                                    danger=True):
                return
        else:
            chans = self.ctx.selected_channels()
            if not chans:
                self.ctx.warn(self, "没有选中通道", "请先在通道矩阵里勾选要复位的通道。")
                return
        dev = self.ctx.device

        def job(progress):
            done = 0
            for ch in chans:
                dev.aw_softrst(ch, replay_profile=True)
                done += 1
                progress((done, len(chans), ch))
            return len(chans)

        def on_progress(payload):
            done, total, ch = payload
            self.ctx.log(f"软复位进度 {done}/{total}（CH{ch}）", "debug")

        self.ctx.task(job, on_done=lambda n: self.ctx.log(f"软复位完成：{n} 路", "user"),
                      on_progress=on_progress, name="AW_SOFTRST")

    def _power_cycle(self) -> None:
        reason = cap_reason(C.CAP_MOTOR_POWER_GPIO, False)
        self.ctx.warn(self, "断电重启不可用",
                      "固件报告 CAP_MOTOR_POWER_GPIO = 0，无法通过软件断电重启马达。",
                      reason)

    def _hw_reset(self) -> None:
        self.ctx.warn(self, "硬件复位不可用",
                      "固件报告 CAP_RSTN_GPIO = 0，没有可用的硬件复位引脚。",
                      cap_reason(C.CAP_RSTN_GPIO, False))

    def _init_default(self) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在通道矩阵里勾选通道。")
            return
        if not self.ctx.confirm(self, "下发器件默认初始化",
                                f"确认对 {len(chans)} 路执行 AW_INIT_DEFAULT？",
                                "会把器件寄存器恢复成数据手册默认值，其中 0x45/0x33/0x3A "
                                "的默认值会让 TRIG 完全失效（默认值陷阱 T-1/T-2/T-3/T-4），"
                                "恢复后必须重新下发 Profile。", danger=True):
            return
        dev = self.ctx.device

        def job(progress):
            out = []
            for ch in chans:
                r = dev.aw_init_default(ch)
                out.append((ch, r.steps_ok, r.steps_total))
            return out

        def done(rows) -> None:
            for ch, ok, total in rows:
                self.ctx.log(f"CH{ch} 默认初始化：{ok}/{total} 步成功", "info")

        self.ctx.task(job, on_done=done, name="AW_INIT_DEFAULT")

    def _dev_cmd(self, method: str, label: str) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在通道矩阵里勾选通道。")
            return
        dev = self.ctx.device

        def job(progress):
            fn = getattr(dev, method)
            for ch in chans:
                fn(ch)
            return len(chans)

        self.ctx.task(job, on_done=lambda n: self.ctx.log(f"{label} 完成：{n} 路", "user"),
                      name=label)

    def _clear_errors(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            dev.clear_errors()
            return dev.get_status()

        def done(status):
            self.update_status(status)
            self.ctx.log("已清除错误并回读状态", "user")

        self.ctx.task(job, on_done=done, name="CLEAR_ERRORS")

    def _selftest(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.selftest()

        self.ctx.task(job, on_done=lambda ok: self.ctx.log(
            f"SELFTEST 返回：{'通过' if ok else '失败/有告警'}", "info" if ok else "warn"),
            name="SELFTEST")


__all__ = ["PageConnection"]
