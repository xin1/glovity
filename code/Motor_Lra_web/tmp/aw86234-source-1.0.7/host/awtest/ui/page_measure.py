# -*- coding: utf-8 -*-
"""测量页（规格书 §5.7）：F0 检测 / DET_READ / SNAPSHOT / MON 监视 / 记录 / 人工录入。

要点：

* F0 失败时**必须**显示 ``codec.F0Result.diagnosis()`` 给出的诊断与排查建议，
  **绝不显示假数据**（valid=0 时 F0 一栏显示"无效"，不画进趋势图）；
* ``GLB_STATE`` 一律显示成状态名（STANDBY/CONT/RAM/RTP/TRIG/BRAKE）；
* ``MON_START`` 时给出总线负载警告：8 路 2 ms 周期会把 400 kHz I2C 跑满；
* 手动录入区收集示波器才能读到的量（Vpp / 峰值电流 / TRIG 延时 / 温升 / VREG）。
"""

from __future__ import annotations

import csv
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem,
                               QTextEdit, QVBoxLayout, QWidget)

from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import BasePage

try:
    import pyqtgraph as pg
    HAVE_PYQTGRAPH = True
except Exception as exc:  # pragma: no cover
    pg = None
    HAVE_PYQTGRAPH = False
    PYQTGRAPH_ERROR = f"{type(exc).__name__}: {exc}"
else:
    PYQTGRAPH_ERROR = ""

_MAX_POINTS = 600


class PageMeasure(BasePage):
    """测量与记录页。"""

    title = "测量"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self.f0_history: List[Tuple[str, int, Optional[float], bool]] = []
        self.records: List[Dict[str, object]] = []
        self.mon_series: Dict[str, List[Tuple[float, float]]] = {}
        self._mon_running = False
        self._t0 = 0.0
        import time
        self._time = time

        splitter = QSplitter(Qt.Vertical)
        root = QVBoxLayout(self)
        root.addWidget(splitter)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.addWidget(self._build_f0_group(), 1)
        top_layout.addWidget(self._build_det_group(), 1)
        splitter.addWidget(top)

        mid = QWidget()
        mid_layout = QHBoxLayout(mid)
        mid_layout.addWidget(self._build_snapshot_group(), 2)
        mid_layout.addWidget(self._build_monitor_group(), 3)
        splitter.addWidget(mid)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.addWidget(self._build_manual_group(), 1)
        bottom_layout.addWidget(self._build_record_group(), 1)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)

        # 事件订阅：MON 的实时数据来自 EVT_MEASURE_RESULT（kind&0x0F == 2）
        self.ctx.bridge.event_received.connect(self._on_event)

        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._record_tick)

    # ------------------------------------------------------------------
    # F0
    # ------------------------------------------------------------------

    def _build_f0_group(self) -> QGroupBox:
        box = QGroupBox("F0 谐振频率检测")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.cmb_f0_chan = QComboBox()
        for ch in range(C.CHANNEL_COUNT):
            self.cmb_f0_chan.addItem(f"CH{ch}", ch)
        form.addRow("通道", self.cmb_f0_chan)

        self.cmb_f0_mode = QComboBox()
        self.cmb_f0_mode.addItem("F0 检测模式（结果在 0x24/0x25）", 0)
        self.cmb_f0_mode.addItem("CONT 连续检测（结果在 0x26/0x27）", 1)
        self.cmb_f0_mode.setToolTip("F0_START 的 mode：0 = 单次 F0 检测；1 = CONT 连续检测。\n"
                                    "两者结果寄存器不同，界面按选择读取。")
        form.addRow("检测模式", self.cmb_f0_mode)

        self.sp_f0_timeout = QSpinBox()
        self.sp_f0_timeout.setRange(50, 10000)
        self.sp_f0_timeout.setValue(500)
        self.sp_f0_timeout.setSuffix(" ms")
        self.sp_f0_timeout.setToolTip("F0_START 的超时。检测期间 GLB_STATE != 0，\n"
                                      "此时读到的 F0 值是无效的。")
        form.addRow("超时", self.sp_f0_timeout)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_f0 = QPushButton("开始 F0 检测")
        self.btn_f0.setStyleSheet("font-weight:bold;")
        self.btn_f0.clicked.connect(self._f0_measure)
        self.btn_f0_read = QPushButton("读结果")
        self.btn_f0_read.clicked.connect(self._f0_result)
        self.btn_f0_raw = QPushButton("读原始寄存器 0x24~0x27")
        self.btn_f0_raw.clicked.connect(self._f0_raw)
        for b in (self.btn_f0, self.btn_f0_read, self.btn_f0_raw):
            row.addWidget(b)
        root.addLayout(row)

        self.f0_label = QLabel("尚未检测。")
        self.f0_label.setStyleSheet("font-family:Consolas,monospace; color:#5fa8d3;")
        self.f0_label.setWordWrap(True)
        root.addWidget(self.f0_label)

        self.f0_diag = QLabel("")
        self.f0_diag.setWordWrap(True)
        self.f0_diag.setStyleSheet("color:#e6b422;")
        root.addWidget(self.f0_diag)

        self.f0_table = QTableWidget(0, 4)
        self.f0_table.setHorizontalHeaderLabels(["时间", "通道", "F0 (Hz)", "有效"])
        self.f0_table.verticalHeader().setVisible(False)
        self.f0_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.f0_table.horizontalHeader().setStretchLastSection(True)
        self.f0_table.setMaximumHeight(140)
        # ★ 必须有最小高度：外面的 QScrollArea(widgetResizable=True) 会把页面缩到
        #   视口大小，而 QTableWidget / PlotWidget 的最小高度接近 0 —— 不给下限的话
        #   布局会把它们压成几十像素的细条，看起来就是"图表加载不正确"。
        self.f0_table.setMinimumHeight(96)
        root.addWidget(self.f0_table)

        if HAVE_PYQTGRAPH:
            self.f0_plot = pg.PlotWidget()
            self.f0_plot.setBackground("k")
            self.f0_plot.setMinimumHeight(170)
            self.f0_plot.setLabel("left", "F0", units="Hz")
            self.f0_plot.setLabel("bottom", "样本序号")
            self.f0_curve = self.f0_plot.plot([], [], pen=pg.mkPen("#8fbf6f", width=2),
                                              symbol="o", symbolSize=6)
            root.addWidget(self.f0_plot)
        else:
            self.f0_plot = None
            self.f0_curve = None
            lbl = QLabel("未安装 pyqtgraph，F0 趋势图不可用（数据仍会记录）")
            lbl.setStyleSheet("color:#ff5555;")
            root.addWidget(lbl)
        return box

    # ------------------------------------------------------------------
    # DET_READ
    # ------------------------------------------------------------------

    def _build_det_group(self) -> QGroupBox:
        box = QGroupBox("LRA 阻抗 / 电池电压（DET_READ）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.cmb_det_chan = QComboBox()
        for ch in range(C.CHANNEL_COUNT):
            self.cmb_det_chan.addItem(f"CH{ch}", ch)
        form.addRow("通道", self.cmb_det_chan)
        self.cmb_det_seq = QComboBox()
        for seq, name in C.DET_SEQ_NAMES.items():
            self.cmb_det_seq.addItem(name, seq)
        self.cmb_det_seq.setToolTip("DET_READ 的 seq：0 = VBAT（VDD = 6.1×AVG/1023）；\n"
                                    "3 = RL（RL = 610×AVG/(1023×D2S_GAIN)）；4 = OS（原始值）。\n"
                                    "换马达（0815 ↔ 1040）后 RL 应明显变化，可用来核对接线与马达。")
        form.addRow("测量项", self.cmb_det_seq)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_det = QPushButton("读取 DET_READ")
        self.btn_det.clicked.connect(self._det_read)
        self.btn_det_vbat = QPushButton("读 VBAT")
        self.btn_det_vbat.clicked.connect(lambda: self._det_read(C.DET_SEQ_VBAT))
        self.btn_det_rl = QPushButton("读 RL")
        self.btn_det_rl.clicked.connect(lambda: self._det_read(C.DET_SEQ_RL))
        for b in (self.btn_det, self.btn_det_vbat, self.btn_det_rl):
            row.addWidget(b)
        root.addLayout(row)

        self.det_label = QLabel("尚未读取。\n耗时较长的检测（如 RL）会占用该通道。")
        self.det_label.setStyleSheet("font-family:Consolas,monospace;")
        self.det_label.setWordWrap(True)
        root.addWidget(self.det_label)

        note = QLabel("注意：0x51~0x53 是 ADC / 电池电压 / 阻抗，**不是 F0 结果**；"
                      "F0 结果在 0x24~0x27。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        root.addWidget(note)
        return box

    # ------------------------------------------------------------------
    # SNAPSHOT
    # ------------------------------------------------------------------

    def _build_snapshot_group(self) -> QGroupBox:
        box = QGroupBox("快照 SNAPSHOT")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        self.btn_snap_sel = QPushButton("抓取选中通道")
        self.btn_snap_sel.clicked.connect(lambda: self._snapshot(False))
        self.btn_snap_all = QPushButton("抓取全部 8 路")
        self.btn_snap_all.clicked.connect(lambda: self._snapshot(True))
        self.btn_snap_record = QPushButton("抓取到记录")
        self.btn_snap_record.setToolTip("把本次快照写入记录表（可导出 CSV）")
        self.btn_snap_record.clicked.connect(lambda: self._snapshot(False, also_record=True))
        for b in (self.btn_snap_sel, self.btn_snap_all, self.btn_snap_record):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.snap_table = QTableWidget(0, 11)
        self.snap_table.setHorizontalHeaderLabels(
            ["时间", "通道", "GLB_STATE", "SYSST", "SYSST2", "F0(Hz)", "CONT_F0(Hz)",
             "DETRD1", "DETRD2", "DETRD3", "VBAT/RL"])
        self.snap_table.verticalHeader().setVisible(False)
        self.snap_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.snap_table.horizontalHeader().setStretchLastSection(True)
        self.snap_table.setMinimumHeight(140)        # ★ 见 f0_plot 的说明
        root.addWidget(self.snap_table, 1)
        return box

    # ------------------------------------------------------------------
    # MON
    # ------------------------------------------------------------------

    def _build_monitor_group(self) -> QGroupBox:
        box = QGroupBox("实时监视 MON_START / MON_STOP")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.sp_mon_period = QSpinBox()
        self.sp_mon_period.setRange(2, 5000)
        self.sp_mon_period.setValue(20)
        self.sp_mon_period.setSuffix(" ms")
        self.sp_mon_period.setToolTip("MON_START 的 period_ms（下限 2 ms）。\n"
                                      "★ 8 路 2 ms 周期会把 400 kHz 的 I2C 跑满，建议 ≥10 ms。")
        self.sp_mon_period.valueChanged.connect(self._refresh_mon_warning)
        form.addRow("周期", self.sp_mon_period)

        fields_row = QWidget()
        fh = QHBoxLayout(fields_row)
        fh.setContentsMargins(0, 0, 0, 0)
        self.mon_field_checks: List[Tuple[int, QCheckBox]] = []
        for bit, label in C.MON_FIELD_LABELS:
            chk = QCheckBox(label)
            chk.setChecked(bit in (C.MON_FIELD_GLB_STATE, C.MON_FIELD_SYSST))
            fh.addWidget(chk)
            self.mon_field_checks.append((bit, chk))
        form.addRow("字段", fields_row)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_mon_start = QPushButton("开始监视")
        self.btn_mon_start.clicked.connect(self._mon_start)
        self.btn_mon_stop = QPushButton("停止监视")
        self.btn_mon_stop.clicked.connect(self._mon_stop)
        self.btn_mon_clear = QPushButton("清空曲线")
        self.btn_mon_clear.clicked.connect(self._mon_clear)
        for b in (self.btn_mon_start, self.btn_mon_stop, self.btn_mon_clear):
            row.addWidget(b)
        row.addStretch(1)
        self.mon_label = QLabel("未在监视")
        self.mon_label.setStyleSheet("font-family:Consolas,monospace;")
        row.addWidget(self.mon_label)
        root.addLayout(row)

        self.mon_warning = QLabel("")
        self.mon_warning.setWordWrap(True)
        self.mon_warning.setStyleSheet("color:#e6b422;")
        root.addWidget(self.mon_warning)

        if HAVE_PYQTGRAPH:
            self.mon_plot = pg.PlotWidget()
            self.mon_plot.setBackground("k")
            self.mon_plot.setMinimumHeight(200)      # ★ 见 f0_plot 的说明
            self.mon_plot.addLegend()
            self.mon_plot.setLabel("bottom", "时间", units="s")
            self.mon_curves: Dict[str, object] = {}
            root.addWidget(self.mon_plot, 1)
        else:
            self.mon_plot = None
            self.mon_curves = {}
            lbl = QLabel("未安装 pyqtgraph，实时折线图不可用（数据仍会记录）")
            lbl.setStyleSheet("color:#ff5555;")
            root.addWidget(lbl)
        return box

    def _refresh_mon_warning(self) -> None:
        period = self.sp_mon_period.value()
        n = max(1, bin(self.ctx.selected_mask or 0x01).count("1"))
        if period < 10:
            msg = (f"★ 总线负载警告：8 路 × {period} ms 周期会跑满 400 kHz I2C"
                   "（TCA9548A 与 ESP32-C6 都只到 400 kHz）。建议把周期加大到 ≥10 ms，"
                   "或减少监视通道数与字段数。")
        else:
            msg = (f"当前周期 {period} ms，监视 {n} 路；若出现 ERR_BUS_BUSY / "
                   "ERR_I2C_TIMEOUT，请加大周期或减少字段。")
        self.mon_warning.setText(msg)

    # ------------------------------------------------------------------
    # 人工录入
    # ------------------------------------------------------------------

    def _build_manual_group(self) -> QGroupBox:
        box = QGroupBox("人工测量录入（示波器读数，软件无法获得）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.man: Dict[str, QDoubleSpinBox] = {}
        specs = [
            ("vpp", "Vpp", 0.0, 30.0, 0.1, " V", "示波器测得的驱动波形峰峰值"),
            ("peak_current", "单路峰值电流", 0.0, 5.0, 0.01, " A", "单通道满幅时的峰值电流"),
            ("current_8ch", "8 路同时电流", 0.0, 20.0, 0.01, " A", "8 路同时满幅的总电流（注意 USB 500 mA 限制）"),
            ("trig_delay", "TRIG→起振延时", 0.0, 50000.0, 1.0, " µs", "A6 项：把 START_DLY 设为 0 后测"),
            ("temp_rise", "温升", 0.0, 200.0, 0.1, " °C", "连续驱动一段时间后的壳温升"),
            ("vreg", "VREG 实测", 0.0, 5.0, 0.01, " V", "A2 点实测的内部 LDO 输出（手册标称 1.5 V）"),
        ]
        for key, label, lo, hi, step, unit, tip in specs:
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(3 if step < 0.1 else 2)
            sp.setSuffix(unit)
            sp.setSpecialValueText("未测")
            sp.setToolTip(tip)
            self.man[key] = sp
            form.addRow(label, sp)
        root.addLayout(form)
        self.man_note = QLineEdit()
        self.man_note.setPlaceholderText("备注（马达型号 0815/1040、供电方式、现象…）")
        root.addWidget(self.man_note)
        row = QHBoxLayout()
        self.btn_man_record = QPushButton("记入记录")
        self.btn_man_record.clicked.connect(self._record_manual)
        self.btn_man_export = QPushButton("导出记录 CSV")
        self.btn_man_export.clicked.connect(self._export_records)
        row.addWidget(self.btn_man_record)
        row.addWidget(self.btn_man_export)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        return box

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def _build_record_group(self) -> QGroupBox:
        box = QGroupBox("数据记录")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.sp_rec_period = QSpinBox()
        self.sp_rec_period.setRange(20, 60000)
        self.sp_rec_period.setValue(200)
        self.sp_rec_period.setSuffix(" ms")
        self.sp_rec_period.setToolTip("记录周期：按该周期调 SNAPSHOT（或吃 MON 事件）写记录表。")
        form.addRow("记录周期", self.sp_rec_period)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_rec_start = QPushButton("开始记录")
        self.btn_rec_start.clicked.connect(self._rec_start)
        self.btn_rec_stop = QPushButton("停止记录")
        self.btn_rec_stop.clicked.connect(self._rec_stop)
        self.btn_rec_clear = QPushButton("清空")
        self.btn_rec_clear.clicked.connect(self._rec_clear)
        for b in (self.btn_rec_start, self.btn_rec_stop, self.btn_rec_clear):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.rec_label = QLabel("记录条数：0")
        root.addWidget(self.rec_label)

        self.rec_view = QTextEdit()
        self.rec_view.setReadOnly(True)
        self.rec_view.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        self.rec_view.setMinimumHeight(120)          # ★ 见 f0_plot 的说明
        root.addWidget(self.rec_view, 1)
        return box

    # ==================================================================
    # F0 实现
    # ==================================================================

    def _current_f0_chan(self) -> int:
        return int(self.cmb_f0_chan.currentData())

    def _f0_measure(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = self._current_f0_chan()
        timeout = self.sp_f0_timeout.value()
        mode = int(self.cmb_f0_mode.currentData())
        self.f0_label.setText("检测中…")
        self.f0_diag.setText("")

        def job(progress):
            return dev.f0_measure(ch, timeout, mode, poll_s=0.1)

        def done(res: K.F0Result) -> None:
            self._show_f0(ch, res)

        self.ctx.task(job, on_done=done, name="F0_START",
                      on_error=lambda msg: self.f0_label.setText("检测失败：" + msg.splitlines()[0]))

    def _f0_result(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = self._current_f0_chan()

        def job(progress):
            return dev.f0_result(ch)

        self.ctx.task(job, on_done=lambda res: self._show_f0(ch, res), name="F0_RESULT")

    def _f0_raw(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = self._current_f0_chan()

        def job(progress):
            return dev.reg_read(ch, 0x24, 4)

        def done(data: bytes) -> None:
            if len(data) < 4:
                return
            raw_det = (data[0] << 8) | data[1]
            raw_cont = (data[2] << 8) | data[3]
            f_det = (C.F0_RAW_BASE_ONESHOT / raw_det) if raw_det else None
            f_cont = (C.F0_RAW_BASE_CONT / raw_cont) if raw_cont else None
            self.ctx.log(
                f"CH{ch} 原始 0x24~0x27 = {data.hex(' ').upper()}："
                f"F0检测 raw={raw_det} → {('%.1f Hz' % f_det) if f_det else '无效'}；"
                f"连续检测 raw={raw_cont} → {('%.1f Hz' % f_cont) if f_cont else '无效'}", "info")
            self.f0_label.setText(
                f"原始寄存器：0x24/0x25 = {data[0]:02X} {data[1]:02X} → "
                f"{('%.2f Hz' % f_det) if f_det else '无效（raw=0）'}\n"
                f"　　　　　　0x26/0x27 = {data[2]:02X} {data[3]:02X} → "
                f"{('%.2f Hz' % f_cont) if f_cont else '无效（raw=0）'}")

        self.ctx.task(job, on_done=done, name="REG_READ(0x24)")

    def _show_f0(self, ch: int, res: K.F0Result) -> None:
        f0 = res.f0_hz
        stamp = self._time.strftime("%H:%M:%S")
        if res.valid and f0 is not None:
            self.f0_label.setText(
                f"CH{ch}：valid=1 · raw H/L = 0x{res.raw_h:02X}/0x{res.raw_l:02X}"
                f"（{res.raw}）· F0 = {f0:.2f} Hz")
            self.f0_label.setStyleSheet("font-family:Consolas,monospace; color:#8fbf6f;")
        else:
            self.f0_label.setText(
                f"CH{ch}：valid=0 · raw H/L = 0x{res.raw_h:02X}/0x{res.raw_l:02X}"
                f"（{res.raw}）· F0 = 无效（不显示推测值）")
            self.f0_label.setStyleSheet("font-family:Consolas,monospace; color:#ff5555;")
        diag = res.diagnosis()
        self.f0_diag.setText(diag or "检测有效，无需诊断。")
        self.f0_history.append((stamp, ch, f0 if res.valid else None, bool(res.valid)))
        row = self.f0_table.rowCount()
        self.f0_table.insertRow(row)
        for col, text in enumerate([stamp, f"CH{ch}",
                                    f"{f0:.2f}" if (res.valid and f0) else "无效",
                                    "是" if res.valid else "否"]):
            item = QTableWidgetItem(text)
            if not res.valid:
                item.setForeground(Qt.GlobalColor.white)
                item.setBackground(Qt.GlobalColor.darkRed)
            self.f0_table.setItem(row, col, item)
        self.f0_table.scrollToBottom()
        if self.f0_curve is not None:
            pts = [(i, f) for i, (_s, _c, f, ok) in enumerate(self.f0_history) if ok and f]
            self.f0_curve.setData([p[0] for p in pts], [p[1] for p in pts])
        level = "info" if res.valid else "warn"
        self.ctx.log(f"F0 检测 CH{ch}：" + ("F0=%.2f Hz" % f0 if res.valid else "无效（已给出诊断）"),
                     level)
        if diag:
            self.ctx.log("[F0 诊断] " + diag, "warn")

    # ==================================================================
    # DET_READ 实现
    # ==================================================================

    def _det_read(self, seq: Optional[int] = None) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_det_chan.currentData())
        if seq is None:
            seq = int(self.cmb_det_seq.currentData())

        def job(progress):
            return dev.det_read(ch, seq)

        def done(res: K.DetReadResp) -> None:
            d2s = self.ctx.device.d2s_gain if self.ctx.device else 8
            value = res.value(d2s)
            unit = res.value_unit
            text = (f"CH{ch} seq={C.DET_SEQ_NAMES.get(seq, seq)}："
                    f"ADC_DATA={res.adc_data} AVG_DATA={res.avg_data} "
                    f"valid={res.valid}\n"
                    f"换算：VDD = 6.1×AVG/1023 = {K.DetReadResp.vbat_v(res.avg_data):.3f} V；"
                    f"RL = 610×AVG/(1023×D2S_GAIN({d2s})) = "
                    f"{K.DetReadResp.rl_ohm(res.avg_data, d2s):.3f} Ω\n"
                    f"器件返回的换算值 = "
                    + (f"{value:.3f} {unit}" if value is not None else "无效（valid=0）"))
            self.det_label.setText(text)
            self.ctx.log(text.replace("\n", " · "), "info" if res.valid else "warn")

        self.ctx.task(job, on_done=done, name="DET_READ")

    # ==================================================================
    # SNAPSHOT 实现
    # ==================================================================

    def _snapshot(self, all_channels: bool, also_record: bool = False) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        chans = list(range(C.CHANNEL_COUNT)) if all_channels else self.ctx.selected_channels()
        if not chans:
            chans = [self._current_f0_chan()]
        stamp = self._time.strftime("%H:%M:%S")

        def job(progress):
            out = []
            for ch in chans:
                try:
                    out.append((ch, dev.snapshot(ch)))
                except Exception as exc:
                    out.append((ch, exc))
            return out

        def done(rows) -> None:
            for ch, snap in rows:
                if not isinstance(snap, K.Snapshot):
                    continue
                f0 = snap.f0_hz
                cf0 = snap.cont_f0_hz
                detail = f"VBAT={snap.vbat_v():.3f} V · RL={snap.rl_ohm():.3f} Ω"
                values = [stamp, f"CH{ch}", snap.glb_state_name,
                          f"0x{snap.sysst:02X}", f"0x{snap.sysst2:02X}",
                          f"{f0:.2f}" if f0 else "无效",
                          f"{cf0:.2f}" if cf0 else "无效",
                          f"0x{snap.reg51:02X}", f"0x{snap.reg52:02X}",
                          f"0x{snap.reg53:02X}", detail]
                row = self.snap_table.rowCount()
                self.snap_table.insertRow(row)
                for col, text in enumerate(values):
                    self.snap_table.setItem(row, col, QTableWidgetItem(text))
                if also_record:
                    self._append_record({
                        "timestamp": stamp, "chan": ch, "glb_state": snap.glb_state,
                        "glb_state_name": snap.glb_state_name,
                        "mode": C.mode_name(snap.mode), "playing": int(snap.is_playing),
                        "present": snap.present, "chipid": f"0x{snap.chipid_h:02X}{snap.chipid_l:02X}",
                        "sysst": snap.sysst, "sysst2": snap.sysst2,
                        "f0_hz": f"{f0:.2f}" if f0 else "",
                        "cont_f0_hz": f"{cf0:.2f}" if cf0 else "",
                        "vbat_v": f"{snap.vbat_v():.3f}", "rl_ohm": f"{snap.rl_ohm():.3f}",
                        "avg_data": snap.avg_data, "adc_data": snap.adc_data,
                    })
            self.snap_table.scrollToBottom()
            self.ctx.log(f"SNAPSHOT 完成：{len(rows)} 路" + ("（已记入记录表）" if also_record else ""),
                         "info")

        self.ctx.task(job, on_done=done, name="SNAPSHOT")

    # ==================================================================
    # MON 实现
    # ==================================================================

    def _mon_fields(self) -> int:
        fields = 0
        for bit, chk in self.mon_field_checks:
            if chk.isChecked():
                fields |= bit
        return fields

    def _mon_start(self) -> None:
        if not self.ctx.require_device(self):
            return
        mask = self.ctx.selected_mask or 0x01
        period = self.sp_mon_period.value()
        fields = self._mon_fields()
        if not fields:
            self.ctx.warn(self, "未选择字段", "请至少勾选一个监视字段。")
            return
        if period < 10 and bin(mask).count("1") > 2:
            if not self.ctx.confirm(self, "总线负载警告",
                                    f"{bin(mask).count('1')} 路 × {period} ms 周期可能跑满 I2C 总线。",
                                    "TCA9548A 与 ESP32-C6 都只到 400 kHz。建议周期 ≥10 ms，"
                                    "或减少通道/字段。仍要继续吗？", danger=True):
                return
        dev = self.ctx.device

        def job(progress):
            dev.mon_start(mask, period, fields)
            return mask

        def done(_m) -> None:
            self._mon_running = True
            self._t0 = self._time.time()
            self.mon_label.setText(f"监视中：mask=0x{mask:02X} 周期 {period} ms 字段 0x{fields:02X}")
            self.ctx.log(f"MON_START：mask=0x{mask:02X} 周期 {period} ms 字段 0x{fields:02X}", "user")

        self.ctx.task(job, on_done=done, name="MON_START")

    def _mon_stop(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        mask = self.ctx.selected_mask or 0x01

        def job(progress):
            dev.mon_stop(mask)

        def done(_r) -> None:
            self._mon_running = False
            self.mon_label.setText("已停止监视")
            self.ctx.log("MON_STOP 已发送", "user")

        self.ctx.task(job, on_done=done, name="MON_STOP")

    def _mon_clear(self) -> None:
        self.mon_series.clear()
        if self.mon_plot is not None:
            self.mon_plot.clear()
            self.mon_curves.clear()
        self.mon_label.setText("曲线已清空")

    def _on_event(self, frame) -> None:
        if frame.cmd != C.EVT_MEASURE_RESULT:
            return
        evt = K.decode_event(frame)
        if evt is None or evt.mon_fields == 0:
            return
        t = self._time.time() - (self._t0 or self._time.time())
        label = None
        value = float(evt.value)
        if evt.mon_fields & C.MON_FIELD_GLB_STATE:
            label = "GLB_STATE"
        elif evt.mon_fields & C.MON_FIELD_SYSST:
            label = "SYSST"
        elif evt.mon_fields & C.MON_FIELD_SYSST2:
            label = "SYSST2"
        elif evt.mon_fields & C.MON_FIELD_DETRD:
            label = "DETRD"
        elif evt.mon_fields & C.MON_FIELD_LDO:
            label = "LDO/VREG"
        if label is None:
            return
        series = self.mon_series.setdefault(label, [])
        series.append((t, value if label != "GLB_STATE" else evt.value))
        del series[:-_MAX_POINTS]
        if self.mon_plot is not None:
            curve = self.mon_curves.get(label)
            if curve is None:
                curve = self.mon_plot.plot([], [], pen=None, symbol="o", symbolSize=4,
                                           name=label)
                self.mon_curves[label] = curve
            curve.setData([p[0] for p in series], [p[1] for p in series])

    # ==================================================================
    # 记录
    # ==================================================================

    def _append_record(self, row: Dict[str, object]) -> None:
        self.records.append(row)
        self.rec_label.setText(f"记录条数：{len(self.records)}")
        keys = ["timestamp", "chan", "glb_state_name", "mode", "playing",
                "f0_hz", "vbat_v", "rl_ohm"]
        self.rec_view.append(" · ".join(f"{k}={row.get(k, '')}" for k in keys))

    def _record_tick(self) -> None:
        if not self.ctx.connected:
            return
        dev = self.ctx.device
        chans = self.ctx.selected_channels() or [self._current_f0_chan()]
        stamp = self._time.strftime("%H:%M:%S")

        def job(progress):
            out = []
            for ch in chans:
                try:
                    out.append((ch, dev.snapshot(ch)))
                except Exception:
                    pass
            return out

        def done(rows) -> None:
            for ch, snap in rows:
                f0 = snap.f0_hz
                self._append_record({
                    "timestamp": stamp, "chan": ch, "glb_state": snap.glb_state,
                    "glb_state_name": snap.glb_state_name, "mode": C.mode_name(snap.mode),
                    "playing": int(snap.is_playing), "present": snap.present,
                    "sysst": snap.sysst, "sysst2": snap.sysst2,
                    "f0_hz": f"{f0:.2f}" if f0 else "", "vbat_v": f"{snap.vbat_v():.3f}",
                    "rl_ohm": f"{snap.rl_ohm():.3f}", "avg_data": snap.avg_data,
                    "adc_data": snap.adc_data,
                })

        self.ctx.task(job, on_done=done, name="记录 SNAPSHOT")

    def _rec_start(self) -> None:
        if not self.ctx.require_device(self):
            return
        self._rec_timer.setInterval(self.sp_rec_period.value())
        self._rec_timer.start()
        self.ctx.log(f"开始记录（周期 {self.sp_rec_period.value()} ms）", "user")

    def _rec_stop(self) -> None:
        self._rec_timer.stop()
        self.ctx.log(f"停止记录（共 {len(self.records)} 条）", "user")

    def _rec_clear(self) -> None:
        self.records.clear()
        self.rec_view.clear()
        self.rec_label.setText("记录条数：0")

    def _record_manual(self) -> None:
        row = {k: (sp.value() if sp.value() > 0 else "") for k, sp in self.man.items()}
        row["timestamp"] = self._time.strftime("%H:%M:%S")
        row["chan"] = "人工"
        row["note"] = self.man_note.text()
        for key, label in (("vpp", "Vpp(V)"), ("peak_current", "单路峰值电流(A)"),
                           ("current_8ch", "8路同时电流(A)"), ("trig_delay", "TRIG延时(µs)"),
                           ("temp_rise", "温升(°C)"), ("vreg", "VREG(V)")):
            value = row.get(key, "")
            if value != "":
                row[label] = value
        self._append_record(row)
        self.ctx.log("人工测量值已记入记录表：" + "、".join(
            f"{k}={v}" for k, v in row.items() if k in
            ("vpp", "peak_current", "current_8ch", "trig_delay", "temp_rise", "vreg")), "user")

    def _export_records(self) -> None:
        if not self.records:
            self.ctx.warn(self, "没有记录", "记录表为空，请先抓取快照或记录数据。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出记录 CSV", "measure_records.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        keys: List[str] = []
        for row in self.records:
            for k in row:
                if k not in keys:
                    keys.append(k)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=keys)
                writer.writeheader()
                for row in self.records:
                    writer.writerow(row)
        except Exception as exc:
            self.ctx.error(self, "导出失败", "写入 CSV 失败。", str(exc))
            return
        self.ctx.log(f"记录已导出：{path}（{len(self.records)} 条）", "user")

    # ==================================================================
    # 状态
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        for w in (self.btn_f0, self.btn_f0_read, self.btn_f0_raw, self.btn_det,
                  self.btn_det_vbat, self.btn_det_rl, self.btn_snap_sel,
                  self.btn_snap_all, self.btn_snap_record, self.btn_mon_start,
                  self.btn_mon_stop, self.btn_rec_start):
            w.setEnabled(ok)
        if not ok:
            self._rec_timer.stop()
            self._mon_running = False
            self.mon_label.setText("未连接")
        self._refresh_mon_warning()

    def on_selection_changed(self, mask: int) -> None:
        chans = self.ctx.selected_channels()
        if chans:
            for combo in (self.cmb_f0_chan, self.cmb_det_chan):
                idx = combo.findData(chans[0])
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self._refresh_mon_warning()

    def stop_recording(self) -> None:
        """公开入口：停止周期记录（主窗口退出时调用）。"""
        self._rec_timer.stop()


__all__ = ["PageMeasure"]
