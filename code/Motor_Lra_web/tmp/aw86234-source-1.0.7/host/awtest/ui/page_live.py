"""Persistent live haptic session, with bounded/coalesced control traffic."""
from threading import Event

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout,
    QWidget)

from awtest.live_lab import LiveParams, PRESETS, PRESET_DESCRIPTIONS, ENVELOPE_NAMES
from awtest.protocol import commands as C
from . import BasePage, mask_to_text


class PageLive(BasePage):
    title = "实时调试"
    stop_requested = Signal()

    def build(self):
        self._token = Event()
        self._mask = 0
        self._starting = False
        self._updating = False
        self._renewing = False
        self._pending = None
        self._loading = False
        self._revision = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        heading = QLabel("持续振动 · 实时控制")
        heading.setStyleSheet("font-size:18px;font-weight:700;")
        root.addWidget(heading)
        intro = QLabel("启动一次，播放中直接调整参数或切换触感预设。没有播放倒计时。\n"
                       "停止 / Esc 可随时结束；断连或控制失联会自动停机。")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.targets = QLabel()
        self.targets.setWordWrap(True)
        root.addWidget(self.targets)

        self.editor = QGroupBox("播放中可实时修改")
        form = QFormLayout(self.editor)
        form.setVerticalSpacing(14)
        self.presets = QComboBox()
        self.presets.addItem("自定义")
        self.presets.addItems(list(PRESETS))
        form.addRow("触感预设", self.presets)
        self.preset_note = QLabel("预设是试听起点，可继续调整；实际触感取决于马达及佩戴方式。")
        self.preset_note.setWordWrap(True)
        form.addRow(self.preset_note)
        self.tracking = QCheckBox("自动谐振跟踪（推荐）")
        self.tracking.setChecked(True)
        form.addRow(self.tracking)
        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(60, 300)
        self.frequency.setDecimals(1)
        self.frequency.setSingleStep(1)
        self.frequency.setSuffix(" Hz")
        self.frequency.setValue(168)
        form.addRow("手动振动频率", self.frequency)
        self.frequency_note = QLabel()
        self.frequency_note.setWordWrap(True)
        form.addRow(self.frequency_note)
        self.amplitude = QSpinBox()
        self.amplitude.setRange(0, 77)
        self.amplitude.setValue(30)
        self.amplitude_slider = QSlider(Qt.Horizontal)
        self.amplitude_slider.setRange(0, 77)
        self.amplitude_slider.setValue(30)
        self.amplitude.valueChanged.connect(self.amplitude_slider.setValue)
        self.amplitude_slider.valueChanged.connect(self.amplitude.setValue)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.amplitude_slider, 1)
        h.addWidget(self.amplitude)
        form.addRow("峰值强度", row)
        self.envelope = QComboBox()
        for code, name in ENVELOPE_NAMES.items():
            self.envelope.addItem(name, code)
        form.addRow("强弱变化", self.envelope)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(.1, 30)
        self.rate.setDecimals(1)
        self.rate.setValue(2)
        self.rate.setSuffix(" 次/秒")
        form.addRow("变化速度", self.rate)
        self.depth = QSpinBox()
        self.depth.setRange(0, 100)
        self.depth.setValue(100)
        self.depth.setSuffix(" %")
        self.depth.setToolTip("0% 始终保持峰值；100% 允许强度降至零。")
        form.addRow("变化深度", self.depth)
        root.addWidget(self.editor)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("background:#eaf3f0;color:#193f38;padding:12px;border-radius:6px;")
        root.addWidget(self.summary)
        actions = QHBoxLayout()
        self.start_button = QPushButton("开始持续振动")
        self.start_button.setMinimumHeight(46)
        self.start_button.setStyleSheet("background:#176b60;color:white;font-weight:700;")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("■ 全部停止  Esc")
        self.stop_button.setMinimumHeight(46)
        self.stop_button.setStyleSheet("color:#b42318;font-weight:700;")
        self.stop_button.clicked.connect(self._request_stop)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        root.addLayout(actions)
        self.status = QLabel("尚未启动。先选通道和预设，再点击开始。")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(55)
        root.addWidget(self.status)
        detail = QLabel("运行中切换自动谐振 / 手动频率，会短暂停振后切换驱动方式。\n"
                        "强度数字是驱动电平，不是实际机械振幅；不同驱动方式的同一电平手感可能不同。")
        detail.setWordWrap(True)
        detail.setStyleSheet("color:#777;")
        root.addWidget(detail)
        root.addStretch(1)
        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(80)
        self._send_timer.timeout.connect(self._flush)
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(400)
        self._lease_timer.timeout.connect(self._renew)
        self.presets.currentTextChanged.connect(self._preset)
        for widget in (self.frequency, self.amplitude, self.rate, self.depth):
            widget.valueChanged.connect(self._edited)
        self.tracking.toggled.connect(self._edited)
        self.envelope.currentIndexChanged.connect(self._edited)
        self.ctx.cancel_operations.connect(self.cancel_pending)
        self.ctx.bridge.event_received.connect(self._event)
        self._refresh()

    def params(self):
        return LiveParams(frequency_hz=168.0 if self.tracking.isChecked() else self.frequency.value(), amplitude=self.amplitude.value(),
                          envelope=self.envelope.currentData(), rate_hz=self.rate.value(),
                          depth=self.depth.value(), tracking=self.tracking.isChecked())

    def _refresh(self):
        tracking = self.tracking.isChecked()
        self.frequency.setEnabled(not tracking)
        self.frequency_note.setText("频率由马达谐振跟踪决定；手动频率已锁定。"
                                    if tracking else "按设定频率生成正弦；远离谐振点时振感可能变弱。")
        modulated = self.envelope.currentData() != 0
        self.rate.setEnabled(modulated)
        self.depth.setEnabled(modulated)
        self.editor.setEnabled(not self._starting)
        mask = self._mask or self.ctx.selected_mask
        self.targets.setText(("正在控制：" if self._mask else "目标通道：") + mask_to_text(mask)
                             + ("（新勾选的通道将在下次启动时使用）" if self._mask and mask != self.ctx.selected_mask else ""))
        p = self.params()
        self.summary.setText(f"{'自动谐振' if tracking else f'手动 {p.frequency_hz:g} Hz'} · 峰值强度 {p.amplitude} · "
                             f"{self.envelope.currentText()} · 持续至停止"
                             + (f"\n变化 {p.rate_hz:g} 次/秒 · 深度 {p.depth}%" if modulated else ""))
        self.start_button.setEnabled(self.ctx.connected and bool(self.ctx.selected_mask)
                                     and not self._starting and not self._mask)
        self.start_button.setText("正在启动…" if self._starting else "持续运行中" if self._mask else "开始持续振动")

    def _preset(self, name):
        if self._loading or name not in PRESETS:
            return
        p = PRESETS[name]
        self._loading = True
        # Presets change the feel, not the user's tracking choice or frequency.
        self.amplitude.setValue(p.amplitude)
        self.envelope.setCurrentIndex(self.envelope.findData(p.envelope))
        self.rate.setValue(p.rate_hz)
        self.depth.setValue(p.depth)
        self._loading = False
        self._edited(mark_custom=False)
        self.preset_note.setText(PRESET_DESCRIPTIONS[name])

    def _edited(self, *_, mark_custom=True):
        if self._loading:
            return
        self._revision += 1
        if mark_custom:
            self._loading = True
            self.presets.setCurrentIndex(0)
            self._loading = False
            self.preset_note.setText("自定义参数。运行中修改会自动发送。")
        self._refresh()
        if self._mask and not self._starting:
            self._pending = self.params()
            self.status.setText("正在应用新参数…")
            if not self._send_timer.isActive():
                self._send_timer.start()
        else:
            self.status.setText("参数已准备，点击开始持续振动。")

    def start(self):
        if self._starting or self._mask or not self.ctx.connected or not self.ctx.selected_mask:
            return
        self.ctx.cancel_operations.emit()
        token = self._token = Event()
        mask, params = self.ctx.selected_mask, self.params()
        self._starting = True
        self._refresh()
        self.status.setText("正在检查设备并启动…")
        def job(progress):
            touched = False
            try:
                if token.is_set(): return
                if not self.ctx.device.get_caps().has(C.CAP_LIVE_LAB):
                    raise ValueError("请先更新至支持实时调试的固件 1.0.3。")
                if token.is_set(): return
                if mask & ~self.ctx.device.ch_probe_all().present_mask:
                    raise ValueError("目标包含离线通道，请重新选择。")
                if token.is_set(): return
                touched = True
                self.ctx.device.live_control(mask, params, start=True)
                if token.is_set():
                    self.ctx.device.stop_all()
            except Exception:
                if touched:
                    try: self.ctx.device.stop_all()
                    except Exception: pass
                raise
        def done(_):
            self._starting = False
            if not token.is_set():
                self._mask = mask
                self._lease_timer.start()
                self.status.setText("已启动，持续至停止。现在可直接调节参数或选择预设。")
                self._notify_modes(mask, params)
            self._refresh()
        def error(message):
            self._starting = False
            if not token.is_set():
                self.cancel_pending()
                self.status.setText(message.splitlines()[0] + "\n未启动实时调试。")
            self._refresh()
        self.ctx.task(job, on_done=done, on_error=error, name="实时调试启动")

    def _notify_modes(self, mask, params):
        for ch in range(8):
            if mask & (1 << ch):
                self.ctx.mode_changed.emit(ch, C.MODE_CONT if params.tracking else C.MODE_RTP)
                self.ctx.playing_changed.emit(ch)

    def _flush(self):
        if self._updating or self._pending is None or not self._mask:
            return
        params, self._pending = self._pending, None
        mask, token = self._mask, self._token
        self._updating = True
        def job(progress):
            if token.is_set(): return
            try:
                self.ctx.device.live_control(mask, params, start=False)
            except Exception:
                try: self.ctx.device.stop_all()
                except Exception: pass
                raise
        def done(_):
            self._updating = False
            if not token.is_set():
                self._notify_modes(mask, params)
                if self._pending is None:
                    self.status.setText("设备已接收参数，继续运行至停止。")
            if self._pending is not None:
                self._send_timer.start()
        def error(message):
            self._updating = False
            if not token.is_set():
                self.cancel_pending()
                self.status.setText("参数更新失败，已请求停止；请检查设备后重新启动。\n" + message.splitlines()[0])
        self.ctx.task(job, on_done=done, on_error=error, name="实时参数更新")

    def _renew(self):
        if self._renewing or not self._mask:
            return
        mask, token = self._mask, self._token
        self._renewing = True
        def job(progress):
            if token.is_set(): return
            try:
                self.ctx.device.live_keepalive(mask)
            except Exception:
                try: self.ctx.device.stop_all()
                except Exception: pass
                raise
        def done(_):
            self._renewing = False
        def error(message):
            self._renewing = False
            if not token.is_set():
                self.cancel_pending()
                self.status.setText("实时控制连接中断，已请求停止。请重新启动。\n" + message.splitlines()[0])
        self.ctx.task(job, on_done=done, on_error=error, urgent=True, name="实时控制续约")

    def cancel_pending(self):
        self._token.set()
        self._mask = 0
        self._pending = None
        self._send_timer.stop()
        self._lease_timer.stop()
        self.status.setText("停止已请求；实时调节已取消。")
        self._refresh()

    def _request_stop(self):
        self.cancel_pending()
        self.stop_requested.emit()

    def on_connection_changed(self, ok):
        if not ok:
            self.cancel_pending()
            self.status.setText("设备已断开。重新连接后需手动启动。")
        self._refresh()

    def on_selection_changed(self, mask):
        self._refresh()

    def _event(self, frame):
        if frame.cmd == C.EVT_SAFETY_TRIP:
            self.cancel_pending()
            self.status.setText("设备保护已触发，实时调试已取消。请查看顶部提示。")
        elif frame.cmd == C.EVT_PLAY_DONE and self._mask & (1 << frame.chan):
            # Firmware engine changes keep the session alive and emit no DONE.
            # Every external DONE is terminal, even during an in-flight update.
            self.cancel_pending()
            self.status.setText("设备播放已结束，实时调试已停止。")
