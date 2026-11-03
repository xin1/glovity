"""Live PX-6AX input mapped to one explicitly selected fingertip motor."""
from threading import Event, Thread, Lock
from collections import deque
import csv
import time
import pyqtgraph as pg
from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QHBoxLayout, QComboBox, QFileDialog)
from . import BasePage
from ..tactile import Mapper, MappingConfig, Route
from ..tactile.paxini import PaxiniSource
from ..tactile.session import Session, run_source


class PageTactile(BasePage):
    title = "触觉反馈"
    finished = Signal(str)
    running_changed = Signal(bool)

    def build(self):
        self._stop = Event()
        self._thread = None
        self._latest = None
        self._history = deque(maxlen=15000)  # five minutes at 50 Hz
        self._history_lock = Lock()
        self._origin = None
        root = QVBoxLayout(self)
        heading = QLabel("PX-6AX 触觉 → 指尖振动")
        heading.setStyleSheet("font-size:18px;font-weight:700;")
        root.addWidget(heading)
        intro = QLabel("将一个传感器的接触和压力反馈到对应指尖。\n"
                       "先在顶部连接振动板，再填写触觉传感器串口。停止 / Esc 可随时结束。")
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.port = QLineEdit("COM7")
        self.address = QSpinBox(); self.address.setRange(1,255); self.address.setValue(3)
        self.channel = QSpinBox(); self.channel.setRange(0,7); self.channel.setValue(4)
        self.full_force = QDoubleSpinBox(); self.full_force.setRange(.5,25.5); self.full_force.setValue(10); self.full_force.setSuffix(" N")
        self.max_level = QSpinBox(); self.max_level.setRange(18,60); self.max_level.setValue(40)
        self.compat = QCheckBox("允许已验证转接板的状态 1 兼容返回")
        self.compat.setChecked(True)
        self.compat.setToolTip("本机 COM7 地址3已校验；地址、长度、LRC仍全部检查。其他板应先关闭此兼容项。")
        def pair(first, label, second):
            row = QHBoxLayout(); row.addWidget(first); row.addWidget(QLabel(label)); row.addWidget(second)
            return row
        form.addRow("触觉串口",pair(self.port,"传感器地址",self.address))
        form.addRow("反馈通道 CH",pair(self.channel,"满强度压力",self.full_force))
        self.pattern = QComboBox()
        for label, key in (("连续压力", "steady"), ("力度顿挫 · 轻压慢 / 重压快", "pulse"), ("柔和呼吸", "breath"), ("双击节奏", "double")):
            self.pattern.addItem(label, key)
        self.pattern.setToolTip("顿挫 1.5–8 次/秒；每击约20ms，双击间隔约80ms；呼吸 0.5–1.5 次/秒；双击 1–3 组/秒。")
        form.addRow("触感形式", pair(self.pattern,"最高强度",self.max_level)); form.addRow(self.compat)
        root.addLayout(form)
        self.start_button = QPushButton("开始触觉反馈")
        self.stop_button = QPushButton("停止触觉反馈")
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.cancel_pending)
        buttons = QHBoxLayout(); buttons.addWidget(self.start_button); buttons.addWidget(self.stop_button); root.addLayout(buttons)
        self.status = QLabel("尚未启动。默认最高电平40；正式使用前需空载检查与佩戴标定。")
        self.status.setWordWrap(True); root.addWidget(self.status)
        plot_bar = QHBoxLayout()
        plot_bar.addWidget(QLabel("滚动时间窗"))
        self.window = QComboBox()
        for seconds in (10, 20, 30, 60): self.window.addItem(f"{seconds} 秒", seconds)
        self.window.setCurrentIndex(1); plot_bar.addWidget(self.window)
        self.export_button = QPushButton("导出曲线 CSV（最近 5 分钟）")
        self.export_button.clicked.connect(self.export_history); plot_bar.addWidget(self.export_button)
        plot_bar.addStretch(); root.addLayout(plot_bar)
        self.force_plot = pg.PlotWidget(background="w")
        self.force_plot.setFixedHeight(280)
        self.force_plot.showGrid(x=True,y=True,alpha=.15)
        self.force_plot.setLabel("bottom", "运行时间", units="s")
        self.force_plot.setLabel("left", "压力", units="N", color="#1769aa")
        plot = self.force_plot.getPlotItem()
        plot.setMenuEnabled(False); plot.hideButtons()
        plot.setMouseEnabled(x=False,y=False)
        plot.vb.setMouseMode(pg.ViewBox.PanMode)
        plot.vb.setMenuEnabled(False)
        for axis in ("left","bottom","right"):
            plot.getAxis(axis).setStyle(showValues=True)
        plot.showAxis("right")
        plot.getAxis("right").setLabel("输出电平（非机械振幅）", color="#e87924")
        self.output_view = pg.ViewBox(enableMenu=False)
        plot.scene().addItem(self.output_view)
        plot.getAxis("right").linkToView(self.output_view)
        self.output_view.setXLink(plot.vb)
        self.output_view.setMouseEnabled(x=False,y=False)
        self.output_view.setMouseMode(pg.ViewBox.PanMode)
        self.output_view.setYRange(0,65,padding=0)
        self.force_plot.setYRange(0,12,padding=0)
        self.output_view.enableAutoRange(x=False,y=False)
        plot.vb.enableAutoRange(x=False,y=False)
        def align_views():
            self.output_view.setGeometry(plot.vb.sceneBoundingRect())
            self.output_view.linkedViewChanged(plot.vb,self.output_view.XAxis)
        plot.vb.sigResized.connect(align_views)
        align_views()
        legend = plot.addLegend(offset=(10,5))
        self.raw_curve = self.force_plot.plot(pen=pg.mkPen("#94a3b8",width=1),name="原始压力")
        self.force_curve = self.force_plot.plot(pen=pg.mkPen("#1769aa",width=2),name="滤波压力")
        self.output_curve = pg.PlotCurveItem(pen=pg.mkPen("#e87924",width=2),stepMode="left")
        self.output_view.addItem(self.output_curve)
        legend.addItem(self.output_curve,"已应答的输出指令")
        root.addWidget(self.force_plot,1)
        note = QLabel("固定驱动参数避免低幅度跟踪漂移；短脉冲结束主动制动。\n"
                      "触觉丢失或数据错误会停止；恢复连接后需手动重新开始。\n"
                      "这里的通道配置独立于顶部多选，单个指尖不会自动复制到其他手指。")
        note.setWordWrap(True); root.addWidget(note)
        self.ctx.cancel_operations.connect(self.cancel_pending)
        self.finished.connect(self._done)
        self._timer = QTimer(self); self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh); self._timer.start()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not self.ctx.connected:
            self.status.setText("请先连接顶部振动板串口。")
            return
        port = self.port.text().strip()
        if not port or port.upper() == self.ctx.device.info.port.upper():
            self.status.setText("触觉串口须与振动板串口不同。")
            return
        self.ctx.cancel_operations.emit()
        self.force_plot.setYRange(0,max(12,self.full_force.value()*1.2),padding=0)
        self._stop = Event(); self._latest = None
        with self._history_lock:
            self._history.clear(); self._origin = None
        for curve in (self.raw_curve,self.force_curve,self.output_curve): curve.setData([],[])
        mapper = Mapper(MappingConfig(routes=(Route("finger", self.channel.value(),
                        force_full_n=self.full_force.value(), max_level=self.max_level.value()),), pattern=self.pattern.currentData()))
        source = PaxiniSource(port, self.address.value(), allow_err_len=self.compat.isChecked())
        session = Session(self.ctx.device, mapper, telemetry=True)
        stop = self._stop
        def worker():
            message = "触觉反馈已停止。"
            try:
                run_source(source, session, stop, self._record)
            except Exception as exc:
                message = f"触觉反馈停止：{exc}。排除原因后手动重启。"
            self.finished.emit(message)
        self.start_button.setEnabled(False)
        for w in (self.port,self.address,self.channel,self.full_force,self.max_level,self.compat,self.pattern):
            w.setEnabled(False)
        self.status.setText("读取触觉数据并启动…")
        self.running_changed.emit(True)
        self._thread = Thread(target=worker, daemon=True)
        self._thread.start()

    def _refresh(self):
        with self._history_lock:
            rows = list(self._history)
            row = self._latest
        if rows:
            # Keep the axis advancing through stalled input; draw no invented samples.
            end = max(rows[-1][0], time.monotonic()-self._origin) if self._thread and self._thread.is_alive() else rows[-1][0]
            span = self.window.currentData()
            visible = [r for r in rows if r[0] >= end-span-.1]
            self.raw_curve.setData([r[0] for r in visible], [r[1] for r in visible])
            self.force_curve.setData([r[0] for r in visible], [r[2] for r in visible])
            self.output_curve.setData([r[0] for r in visible], [r[3] for r in visible])
            ceiling=max(12,self.full_force.value()*1.2,max((r[1] for r in rows if r[1]==r[1]),default=0)*1.1)
            self.force_plot.setYRange(0,ceiling,padding=0)
            self.force_plot.setXRange(max(0,end-span),max(span,end),padding=0)
        if row is not None and self._thread and self._thread.is_alive() and not self._stop.is_set():
            ch = self.channel.value(); details = row["channels"][str(ch)]
            self.status.setText(f"CH{ch} · 压力 {details['normal_n']:.2f} N · 下发电平 {row['levels'][ch]} · {details['cue']}\n"
                                f"节奏 {details['tempo_hz']:.1f} Hz · 输入年龄 {details['age_ms']:.1f} ms（不是机械延迟测量）")
            diagnostic=row.get("driver",{}).get(str(ch))
            if diagnostic:
                hz=diagnostic['counter_hz']
                hz_text=f"{hz:.1f} Hz" if hz is not None else "静默"
                self.status.setText(self.status.text()+f"\n芯片回读电平 {diagnostic['level']} · 频率计数估计 {hz_text}（非机械测量；每250ms更新）")

    def _record(self, row):
        ch = next(iter(row["channels"]))
        d = row["channels"][ch]
        with self._history_lock:
            if self._origin is None: self._origin = row["time"]
            self._latest = row
            while self._history and self._history[0][0] < row["time"]-self._origin-300:
                self._history.popleft()
            self._history.append((row["time"]-self._origin, d["raw_normal_n"], d["normal_n"], row["levels"][int(ch)], d["tempo_hz"], d["age_ms"], ch, d["pattern"]))

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self,"导出触觉曲线","tactile_trace.csv","CSV (*.csv)")
        if not path: return
        try:
            with self._history_lock: rows = list(self._history)
            with open(path,"w",newline="",encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(("time_s","raw_normal_N","filtered_normal_N","acknowledged_command_level","tempo_Hz","input_age_ms","channel","pattern"))
                writer.writerows(rows)
        except OSError as exc:
            self.status.setText(f"曲线导出失败：{exc}")

    def _done(self, message):
        with self._history_lock:
            if self._history:
                last = self._history[-1]
                output = 0. if message == "触觉反馈已停止。" else float("nan")
                self._history.append((time.monotonic()-self._origin,float("nan"),float("nan"),output,0.,float("nan"),last[6],last[7]))
            self._latest = None
        self.status.setText(message)
        self.start_button.setEnabled(True)
        for w in (self.port,self.address,self.channel,self.full_force,self.max_level,self.compat,self.pattern):
            w.setEnabled(True)
        self.running_changed.emit(False)

    def cancel_pending(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self.status.setText("正在停止触觉反馈…")

    def on_connection_changed(self, ok):
        if not ok:
            self.cancel_pending()
