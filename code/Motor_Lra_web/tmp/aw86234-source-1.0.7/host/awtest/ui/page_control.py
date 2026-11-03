"""Everyday controls: edit locally, then deploy one explicit snapshot."""
from __future__ import annotations

from threading import Event
import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget)

from awtest import profiles as P
from awtest.protocol import codec as K, commands as C
from awtest.quick_control import continuous_profile, deploy, preflight
from . import BasePage, mask_to_text
from .page_live import PageLive
from awtest.wave_presets import PRESET_NAMES, prepare_preset


MODE_TITLES = {C.MODE_CONT: "持续振动 · CONT", C.MODE_RAM: "波形播放 · RAM",
               C.MODE_TRIG: "硬件触发 · TRIG", C.MODE_RTP: "流式调试 · RTP"}
MODE_HELP = {
    C.MODE_CONT: "选择持续实时控制或定时播放。持续实时控制启动后不限时，可随时调整强度、频率和预设。",
    C.MODE_RAM: "选择触感预设后直接点击播放，自动写入并播放一次。也可选“使用已有波形”播放自己上传的波形。",
    C.MODE_TRIG: "选择预设，点击播放即可立即试听；“写入并启用 BOOT”仅部署，随后按板上 BOOT 播放。",
    C.MODE_RTP: "需要持续送入波形数据。这里可部署参数；数据流请通过高级测试脚本发送。",
}


class PageControl(BasePage):
    title = "快捷控制"
    navigate = Signal(str)
    stop_requested = Signal()

    def build(self):
        self._mode = C.MODE_CONT
        self._drafts = {C.MODE_CONT: continuous_profile(), C.MODE_RAM: P.preset_ram_175hz(),
                        C.MODE_TRIG: P.preset_trig_rising(), C.MODE_RTP: P.preset_rtp_12k()}
        self._cancel = Event()
        self._busy = False
        self._loading = False
        self._revision = 0
        self._live_mask = 0
        self._live_token = Event()
        self._live_pending = None
        self._live_inflight = False
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(80)
        self._live_timer.timeout.connect(self._flush_live)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(12)
        heading = QLabel("振动控制")
        heading.setStyleSheet("font-size:24px;font-weight:700;")
        root.addWidget(heading)
        self.target_label = QLabel()
        self.target_label.setWordWrap(True)
        root.addWidget(self.target_label)

        self.editor = QWidget()
        edit = QVBoxLayout(self.editor)
        edit.setContentsMargins(0, 0, 0, 0)
        edit.setSpacing(12)
        row = QHBoxLayout()
        self.mode_buttons = {}
        self.mode_group = QButtonGroup(self)
        for mode, name in MODE_TITLES.items():
            button = QPushButton(name)
            button.setCheckable(True)
            button.setMinimumHeight(42)
            button.setStyleSheet("QPushButton:checked {background:#176b60;color:white;font-weight:700;}")
            button.clicked.connect(lambda checked=False, m=mode: self.set_mode(m))
            self.mode_buttons[mode] = button
            self.mode_group.addButton(button)
            row.addWidget(button)
        edit.addLayout(row)
        self.description = QLabel()
        self.description.setWordWrap(True)
        edit.addWidget(self.description)

        self.cont_kind = QComboBox()
        self.cont_kind.addItems(["持续实时控制 · 直到停止", "定时播放 · 到时停止"])
        self.cont_kind.currentIndexChanged.connect(self._cont_kind_changed)
        edit.addWidget(self.cont_kind)
        self.wave_preset = QComboBox()
        self.wave_preset.addItem("使用已有波形（自定义）", None)
        for name in PRESET_NAMES:
            self.wave_preset.addItem(name, name)
        self.wave_preset.currentIndexChanged.connect(self._preset_edited)
        self.preset_row = QWidget()
        preset_layout = QFormLayout(self.preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.addRow("触感预设", self.wave_preset)
        self.allow_replace = QCheckBox("允许预设替换设备波形库（原波形自动备份到本机）")
        self.allow_replace.setToolTip("首次写入不同预设需要勾选。已有波形不会被静默覆盖；备份保存在本机 AppData/Local/AW86234/waveform-backups。")
        preset_layout.addRow(self.allow_replace)
        edit.addWidget(self.preset_row)

        self.parameters = QGroupBox("播放参数")
        form = QFormLayout(self.parameters)
        form.setVerticalSpacing(12)
        self.level = QSpinBox()
        self.level.setRange(0, 77)
        self.level_slider = QSlider(Qt.Horizontal)
        self.level_slider.setRange(0, 77)
        self.level.valueChanged.connect(self.level_slider.setValue)
        self.level_slider.valueChanged.connect(self.level.setValue)
        self.level_row = QWidget()
        level_layout = QHBoxLayout(self.level_row)
        level_layout.setContentsMargins(0, 0, 0, 0)
        level_layout.addWidget(self.level_slider, 1)
        level_layout.addWidget(self.level)
        self.level_label = QLabel("振动强度")
        form.addRow(self.level_label, self.level_row)

        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(94, 300)
        self.frequency.setDecimals(1)
        self.frequency.setSuffix(" Hz")
        self.frequency_label = QLabel("谐振频率")
        form.addRow(self.frequency_label, self.frequency)
        self.wave_id = QSpinBox()
        self.wave_id.setRange(1, 127)
        self.wave_label = QLabel("波形编号")
        form.addRow(self.wave_label, self.wave_id)
        self.rate = QComboBox()
        self.rate.addItem("12 kHz", C.RATE_12K)
        self.rate.addItem("24 kHz", C.RATE_24K)
        self.rate.addItem("12 kHz（编码 3）", C.RATE_12K_ALT)
        self.rate_label = QLabel("波形采样率")
        form.addRow(self.rate_label, self.rate)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.1, 60)
        self.duration.setDecimals(3)
        self.duration.setValue(2)
        self.duration.setSuffix(" 秒")
        self.duration_label = QLabel("播放时长")
        form.addRow(self.duration_label, self.duration)
        edit.addWidget(self.parameters)

        row = QHBoxLayout()
        self.btn_recipe = QPushButton("恢复平稳持续配方")
        self.btn_recipe.clicked.connect(self.reset_cont)
        row.addWidget(self.btn_recipe)
        self.btn_wave = QPushButton("打开波形编辑")
        self.btn_wave.clicked.connect(lambda: self.navigate.emit("waveform"))
        row.addWidget(self.btn_wave)
        row.addStretch(1)
        for title, slot in (("导入参数", self.load_profile), ("保存参数", self.save_profile)):
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.tools_row = QWidget()
        self.tools_row.setLayout(row)
        edit.addWidget(self.tools_row)
        root.addWidget(self.editor)
        self.live_control = PageLive(self.ctx)
        self.live_control.layout().setContentsMargins(0, 0, 0, 0)
        self.live_control.stop_requested.connect(self.stop_requested.emit)
        root.addWidget(self.live_control)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("background:#eaf3f0;color:#193f38;padding:12px;border-radius:6px;")
        root.addWidget(self.summary)
        actions = QHBoxLayout()
        self.btn_start = QPushButton("下发并启动")
        self.btn_start.setMinimumHeight(44)
        self.btn_start.setStyleSheet("QPushButton {background:#176b60;color:white;font-weight:700;padding:8px 22px;} QPushButton:disabled {background:#ccd6d3;color:#64716e;}")
        self.btn_start.clicked.connect(lambda: self.submit(True))
        self.btn_apply = QPushButton("只下发参数")
        self.btn_apply.setMinimumHeight(44)
        self.btn_apply.clicked.connect(lambda: self.submit(False))
        self.btn_stop = QPushButton("■ 全部停止  Esc")
        self.btn_stop.setMinimumHeight(44)
        self.btn_stop.setStyleSheet("color:#b42318;font-weight:700;")
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        for widget in (self.btn_start, self.btn_apply, self.btn_stop):
            actions.addWidget(widget)
        self.actions_row = QWidget()
        self.actions_row.setLayout(actions)
        root.addWidget(self.actions_row)
        self.result = QLabel("尚未下发。参数修改和模式切换只在本地生效。")
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.PlainText)
        self.result.setMinimumHeight(42)
        root.addWidget(self.result)
        detail = QLabel("下发顺序：核对通道 → 停止目标 → 写入并回读当前模式参数 → 启动。\n"
                        "参数部署到运行中的设备，断电后需重新下发；可保存为 JSON 重用。")
        detail.setWordWrap(True)
        detail.setStyleSheet("color:#777;")
        self.detail = detail
        root.addWidget(detail)
        advanced = QPushButton("查看完整参数 / 高级调试 →")
        advanced.clicked.connect(lambda: self.navigate.emit("mode_params"))
        self.advanced = advanced
        root.addWidget(advanced, 0, Qt.AlignLeft)
        root.addStretch(1)
        self.level.valueChanged.connect(self._level_edited)
        for widget in (self.frequency, self.wave_id, self.duration):
            widget.valueChanged.connect(self._edited)
        self.rate.currentIndexChanged.connect(self._edited)
        self.ctx.bridge.event_received.connect(self._event)
        self.ctx.cancel_operations.connect(self.cancel_pending)
        self._load_controls()
        self._refresh()

    def profile(self):
        p = self._drafts[self._mode].copy()
        if self._mode == C.MODE_CONT:
            p.drv1_lvl = self.level.value()
            p.set_f0_hz(self.frequency.value())
        elif self._mode == C.MODE_TRIG:
            p.trg_gain = self.level.value()
            p.trig1_seq_p = self.wave_id.value()
        else:
            p.gain = self.level.value()
        if self._mode == C.MODE_RAM:
            p.wavseq[0] = self.wave_id.value()
        if self._mode != C.MODE_CONT:
            p.wavdat_mode = self.rate.currentData()
        return p

    def set_mode(self, mode):
        if self._busy or mode == self._mode:
            return
        self._stop_embedded_live()
        self._drafts[self._mode] = self.profile()
        self._mode = mode
        self._load_controls()
        self._edited()

    def _stop_embedded_live(self):
        if self.live_control._mask or self.live_control._starting:
            self.live_control.cancel_pending()
            self.stop_requested.emit()

    def _cont_kind_changed(self, *_):
        self._stop_embedded_live()
        self._refresh()

    def _preset_edited(self, *_):
        self._edited()

    def _load_controls(self):
        self._invalidate_live()
        self._loading = True
        p = self._drafts[self._mode]
        self.mode_buttons[self._mode].setChecked(True)
        cont = self._mode == C.MODE_CONT
        trig = self._mode == C.MODE_TRIG
        # Imported high levels must be displayed truthfully, never silently clipped.
        limit = max(77, p.drv1_lvl) if cont else 255
        self.level.setMaximum(limit)
        self.level_slider.setMaximum(limit)
        self.level.setValue(p.drv1_lvl if cont else p.trg_gain if trig else p.gain)
        # Hidden DRV_WIDTH still constrains valid frequencies; offer only values
        # that keep width below the half-period instead of a late deployment error.
        maximum_hz = math.floor(24000 / (p.drv_width + 1) * 10) / 10
        self.frequency.setRange(94, maximum_hz)
        self.frequency.setToolTip(f"本配方频率上限 {maximum_hz:g} Hz，由驱动宽度决定；完整参数在高级调试中调整。")
        self.frequency.setValue(p.f0_hz)
        self.wave_id.setValue(p.trig1_seq_p if trig else max(1, p.wavseq[0] & 127))
        self.rate.setCurrentIndex(max(0, self.rate.findData(p.wavdat_mode)))
        for widget in (self.frequency, self.frequency_label, self.btn_recipe):
            widget.setVisible(cont)
        for widget in (self.wave_id, self.wave_label, self.btn_wave):
            widget.setVisible(self._mode in (C.MODE_RAM, C.MODE_TRIG))
        for widget in (self.rate, self.rate_label):
            widget.setVisible(not cont)
        for widget in (self.duration, self.duration_label):
            widget.setVisible(self._mode in (C.MODE_RAM, C.MODE_CONT))
        self.duration_label.setText("播放时长" if cont else "最长播放")
        self.duration.setToolTip("到时停止；RAM 波形可能按自身长度和循环设置提前结束。")
        self.level_label.setText("驱动强度（电平）" if cont else "触发增益" if trig else "播放增益")
        self._loading = False

    def reset_cont(self):
        self._drafts[C.MODE_CONT] = continuous_profile()
        self._mode = C.MODE_CONT
        self._load_controls()
        self._edited()

    def _invalidate_live(self):
        self._live_token.set()
        self._live_token = Event()
        self._live_mask = 0
        self._live_pending = None
        self._live_timer.stop()

    def _level_edited(self, value):
        if self._loading:
            return
        self._edited()
        mask = self._live_mask & self.ctx.selected_mask
        if self._mode != C.MODE_CONT or not mask or not self.ctx.connected or self._busy:
            return
        if value > 77:
            self._invalidate_live()
            self.result.setText("实时调节范围为 0–77；更高电平须重新下发并通过安全检查。")
            return
        self._live_pending = (mask, value)
        self.result.setText(f"正在实时调节强度至 {value}…（频率和时长的修改仍需下发）")
        if not self._live_timer.isActive():
            self._live_timer.start()

    def _flush_live(self):
        if self._live_inflight or self._live_pending is None:
            return
        mask, level = self._live_pending
        self._live_pending = None
        token = self._live_token
        self._live_inflight = True
        def job(progress):
            applied = 0
            for ch in range(8):
                if token.is_set():
                    break
                if not mask & (1 << ch):
                    continue
                # Check actual device configuration and playback before each write.
                profile = self.ctx.device.get_profile(ch)
                state = self.ctx.device.get_play_state(ch)
                if profile.mode != C.MODE_CONT or not state.playing:
                    continue
                d2 = self.ctx.device.reg_read_u8(ch, 0x1D) & 127
                if token.is_set():
                    break
                self.ctx.device.set_cont_live(ch, level, d2, noresp=False)
                actual = self.ctx.device.reg_read_u8(ch, 0x1C) & 127
                if actual != level:
                    raise ValueError(f"CH{ch} 强度回读不一致：{actual} / {level}")
                profile.drv1_lvl, profile.drv2_lvl = level, d2
                applied |= 1 << ch
            return applied
        def finish(applied=None, error=None):
            self._live_inflight = False
            if not token.is_set():
                if error:
                    self._invalidate_live()
                    self.result.setText("实时调节失败；请停止并检查设备：" + error.splitlines()[0])
                elif self._live_pending is None:
                    self.result.setText(
                        f"强度 {level} 已回读确认 · {mask_to_text(applied)}；播放计时不变。频率和时长修改仍需下发。"
                        if applied else "目标通道已停止或不在 CONT 模式，未调节强度。")
            if self._live_pending is not None:
                self._live_timer.start()
        self.ctx.task(job, on_done=lambda mask: finish(applied=mask),
                      on_error=lambda message: finish(error=message), name="CONT 实时调节")

    def hideEvent(self, event):
        self._invalidate_live()
        super().hideEvent(event)

    def _edited(self, *_):
        if self._loading:
            return
        self._revision += 1
        self.result.setText("参数已修改，尚未下发；设备当前输出不会随编辑改变。")
        self._refresh()

    def _refresh(self):
        self.target_label.setText("目标通道：" + mask_to_text(self.ctx.selected_mask)
                                  + ("" if self.ctx.connected else " · 请先连接设备"))
        self.description.setText(MODE_HELP[self._mode])
        p = self.profile()
        text = f"{MODE_TITLES[self._mode]} · {mask_to_text(self.ctx.selected_mask)}"
        if self._mode == C.MODE_CONT:
            text += f"\n强度 {p.drv1_lvl} · 实际频率 {p.f0_hz:.1f} Hz · {self.duration.value():g} 秒后停止"
            if p.wait_num or p.drv2_time or p.brk_time or p.drv1_time != 255 or p.en_f0_det:
                text += "\n当前为自定义包络；恢复平稳持续配方可消除驱动段间停顿。"
            if p.drv1_lvl > 77:
                text += "\n已导入超过快捷默认上限 77 的电平，请核对马达与供电。"
        elif self._mode in (C.MODE_RAM, C.MODE_TRIG):
            text += f"\n波形 #{self.wave_id.value()} · {self.rate.currentText()}（须与上传波形一致）"
        else:
            text += "\n只部署流式参数，不会自动启动无数据的 RTP 播放。"
        self.summary.setText(text)
        ready = self.ctx.connected and bool(self.ctx.selected_mask) and not self._busy
        self.btn_apply.setEnabled(ready)
        self.btn_start.setEnabled(ready and self._mode != C.MODE_RTP)
        self.btn_apply.setText("下发并启用 BOOT" if self._mode == C.MODE_TRIG else "只下发参数")
        self.btn_start.setText("▶ 播放一次" if self._mode in (C.MODE_RAM, C.MODE_TRIG) else "▶ 定时播放")
        self.btn_start.setToolTip("自动写入当前所选预设与参数，然后播放；无需先点击写入。")
        if self._mode == C.MODE_TRIG:
            self.btn_apply.setText("写入并启用 BOOT")
        preset = self.wave_preset.currentData() if self._mode in (C.MODE_RAM, C.MODE_TRIG) else None
        if preset:
            self.summary.setText(f"{MODE_TITLES[self._mode]} · {mask_to_text(self.ctx.selected_mask)}\n预设：{preset} · 点击播放自动写入。设备已有不同波形时，须允许备份并替换。")
        self.allow_replace.setVisible(bool(preset))
        self.wave_id.setEnabled(not preset)
        self.rate.setEnabled(not preset)
        cont = self._mode == C.MODE_CONT
        live = cont and self.cont_kind.currentIndex() == 0
        self.cont_kind.setVisible(cont)
        self.preset_row.setVisible(self._mode in (C.MODE_RAM, C.MODE_TRIG))
        self.live_control.setVisible(live)
        for widget in (self.parameters, self.tools_row, self.summary, self.actions_row,
                       self.result, self.detail, self.advanced):
            widget.setVisible(not live)
        self.editor.setEnabled(not self._busy)

    def submit(self, start):
        if self._busy or not self.ctx.connected or not self.ctx.selected_mask:
            return
        self.ctx.cancel_operations.emit()
        self._invalidate_live()
        preset = self.wave_preset.currentData() if self._mode in (C.MODE_RAM, C.MODE_TRIG) else None
        p, mask = self.profile(), self.ctx.selected_mask
        allow_replace = self.allow_replace.isChecked()
        duration = round(self.duration.value() * 1000)
        self._cancel = token = Event()
        revision = self._revision
        self._busy = True
        self._refresh()
        self.result.setText("正在部署…")
        def job(progress):
            if preset:
                preflight(self.ctx.device, p, mask, duration, start, token, progress, check_waves=False)
                wave_id, rate = prepare_preset(self.ctx.device, mask, preset, token, progress, allow_replace=allow_replace)
                p.wavdat_mode = rate
                if p.mode == C.MODE_RAM:
                    p.wavseq = [wave_id] + [0] * 7
                    p.seq_loop = [0] * 4
                    p.mainloop = 0
                else:
                    p.trig1_seq_p = wave_id
            return deploy(self.ctx.device, p, mask, duration, start, token, progress)
        def done(result):
            self._busy = False
            if token.is_set():
                self.result.setText("已取消部署，停止指令已请求。")
            else:
                if p.mode == C.MODE_CONT and revision == self._revision:
                    self._live_mask = result.started_mask
                if preset and revision == self._revision:
                    self._drafts[self._mode] = p.copy()
                    self._loading = True
                    self.wave_id.setValue(p.trig1_seq_p if p.mode == C.MODE_TRIG else p.wavseq[0])
                    self.rate.setCurrentIndex(self.rate.findData(p.wavdat_mode))
                    self._loading = False
                action = "已启动" if result.started_mask else "已下发，未启动"
                if p.mode == C.MODE_TRIG:
                    action += "；BOOT 已启用，按键播放已上传波形"
                self.result.setText(f"{action} · {mask_to_text(mask)} · 当前模式参数回读一致。"
                                    + (" 当前编辑尚未下发。" if revision != self._revision else ""))
                for ch in range(8):
                    if mask & (1 << ch):
                        self.ctx.mode_changed.emit(ch, p.mode)
                        self.ctx.playing_changed.emit(ch)
                self.ctx.log(self.result.text(), "user")
            self._refresh()
        def failed(message):
            self._busy = False
            self.result.setText(message.splitlines()[0] + "\n未完成部署；请检查原因后重试。")
            for ch in range(8):
                if mask & (1 << ch):
                    mode = self.ctx.device.modes.get(ch)
                    if mode is not None:
                        self.ctx.mode_changed.emit(ch, mode)
                    self.ctx.playing_changed.emit(ch)
            self._refresh()
        self.ctx.task(job, on_done=done, on_error=failed,
                      on_progress=lambda text: self.result.setText(text) if not token.is_set() else None,
                      name="快捷部署")

    def cancel_pending(self):
        self._invalidate_live()
        self._cancel.set()
        self.result.setText("停止已请求；待执行的快捷部署已取消。")

    def on_connection_changed(self, ok):
        if not ok:
            self.cancel_pending()
            self.result.setText("设备已断开。请重新连接后再下发。")
        self._refresh()

    def on_selection_changed(self, mask):
        self._invalidate_live()
        self._revision += 1
        self._refresh()

    def _event(self, frame):
        if frame.cmd == C.EVT_PLAY_DONE and not self._busy:
            event = K.decode_event(frame)
            if isinstance(event, K.EvtPlayDone):
                if self._live_mask & (1 << frame.chan):
                    self._invalidate_live()
                self.result.setText(f"CH{frame.chan} 已停止 · {event.reason_name} · {event.elapsed_ms} ms")
        elif frame.cmd == C.EVT_SAFETY_TRIP:
            self.cancel_pending()
            self.result.setText("设备安全机制触发，已取消待执行部署。请查看顶部告警。")

    def save_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存参数", "vibration-profile.json", "JSON (*.json)")
        if path:
            try:
                data = self.profile().to_dict()
                data['_quick_control'] = {'duration_ms': round(self.duration.value() * 1000), 'preset': self.wave_preset.currentData() if self._mode in (C.MODE_RAM, C.MODE_TRIG) else None}
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                self.result.setText("参数已保存：" + path)
            except Exception as exc:
                self.result.setText("保存失败：" + str(exc))

    def load_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入参数", "", "JSON (*.json)")
        if not path:
            return
        try:
            p = P.Profile.load(path)
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            duration_ms = data.get('_quick_control', {}).get('duration_ms', 2000)
            if not isinstance(duration_ms, (int, float)) or not 100 <= duration_ms <= 60000:
                raise ValueError("保存的播放时长须为 100..60000 ms。")
            preset = data.get('_quick_control', {}).get('preset')
            if preset is not None and (preset not in PRESET_NAMES or p.mode not in (C.MODE_RAM, C.MODE_TRIG)):
                raise ValueError('保存的预设名称或模式无效。')
            problems = p.validate()
            if problems:
                raise ValueError("；".join(f"{key}: {why}" for key, why in problems))
            # Complex queues require the complete editor; never rewrite them silently.
            if p.mode == C.MODE_RAM and (not 0 < p.wavseq[0] < 128 or any(p.wavseq[1:])):
                raise ValueError("此文件含多步队列或等待，请在高级参数页导入，保留完整编排。")
            if p.mode == C.MODE_TRIG and not 1 <= p.trig1_seq_p <= 127:
                raise ValueError("此文件的触发波形编号不适用于快捷页，请在高级参数页导入。")
            self._stop_embedded_live()
            self.wave_preset.setCurrentIndex(0)
            self.cont_kind.setCurrentIndex(1)
            self._drafts[self._mode] = self.profile()
            self._drafts[p.mode] = p
            self._mode = p.mode
            self._load_controls()
            self.wave_preset.setCurrentIndex(max(0, self.wave_preset.findData(preset)))
            self.allow_replace.setChecked(False)
            self.duration.setValue(duration_ms / 1000)
            self._edited()
            self.result.setText("已导入，尚未下发：" + path)
        except Exception as exc:
            self.result.setText("导入失败：" + str(exc))
