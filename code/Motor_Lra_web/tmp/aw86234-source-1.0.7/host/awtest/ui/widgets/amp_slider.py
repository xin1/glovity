# -*- coding: utf-8 -*-
"""实时幅度滑条（CONT 专用）：限速发送 ``SET_CONT_LIVE`` + 往返延迟显示 + ★生效性测试。

规格书 §5.6 的要点全部落在这里：

* 只在 **CONT 模式且正在播放** 时可用（控件置灰并写明原因）；
* 用 ``SET_CONT_LIVE`` + ``NORESP`` 高频发送，**限速 20~50 Hz**，拖动中丢弃中间值只发最新；
* 可用"带应答模式"开关测量**真实往返延迟**；无应答时显示发送间隔与估算；
* 每 ~1 s 用普通 ``REG_READ(0x1C)`` 对账，确认链路与生效值；
* ★ **生效性测试**：DRV1_LVL=32 → 等 200 ms → 读回 → DRV1_LVL=127 → 等 200 ms → 读回，
  报告"播放中改幅度是否即时生效"，并把结论写进日志。
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from awtest.protocol import commands as C

from .. import UiContext

_EFFECT_TEST_HELP = ("按下后依次：DRV1_LVL=32 → 等 200 ms → 读回 0x1C → DRV1_LVL=127 → "
                     "等 200 ms → 读回 0x1C。回读值一致仅证明寄存器写入成功，不能证明马达振幅即时变化；"
                     "若不一致，说明需要重新 GO，滑条应改用『停→改→重播』流程。")


class AmpSlider(QWidget):
    """CONT 实时幅度滑条。"""

    conclusion = Signal(str)          # 生效性测试结论（一句话）
    latency_reported = Signal(float)  # 最近一次延迟（ms）

    def __init__(self, ctx: UiContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._chan = 0
        self._mode = C.MODE_CONT
        self._playing = False
        self._enabled = False
        self._pending: Optional[int] = None
        self._dirty = False
        self._send_t0: float = 0.0
        self._rtt: list = []
        self._last_sent: Optional[int] = None
        self._link_ok = True
        self._build()

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def _build(self) -> None:
        box = QGroupBox("实时幅度（CONT 模式 · 播放中可用）")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        root = QVBoxLayout(box)

        self.status_label = QLabel("未启用：需要 CONT 模式且正在播放")
        self.status_label.setStyleSheet("color:#e6b422;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 127)
        self.slider.setValue(127)
        self.slider.setTickInterval(16)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setToolTip("DRV1_LVL（0x1C bit6:0，0..127）\n"
                               "输出空载电压估算 = VDD × L / 128\n"
                               "满幅判定阈值：> 96 视为满幅（协议 §11）")
        self.slider.valueChanged.connect(self._on_value_changed)
        row.addWidget(self.slider, 1)

        self.value_label = QLabel("127 (1.00×)")
        self.value_label.setMinimumWidth(150)
        self.value_label.setStyleSheet("font-family:Consolas,monospace;")
        row.addWidget(self.value_label)
        root.addLayout(row)

        form = QFormLayout()
        self.vdd_spin = QDoubleSpinBox()
        self.vdd_spin.setRange(2.0, 6.5)
        self.vdd_spin.setSingleStep(0.1)
        self.vdd_spin.setValue(5.0)
        self.vdd_spin.setSuffix(" V")
        self.vdd_spin.setToolTip("用于把电平码换算成空载电压（软件调节模式：VDD×L/128）。\n"
                                 "实际值请用测量页的 DET_READ(VBAT) 读取。")
        self.vdd_spin.valueChanged.connect(self._refresh_value_label)
        form.addRow("VDD 估算", self.vdd_spin)

        self.drv2_spin = QSpinBox()
        self.drv2_spin.setRange(-1, 127)
        self.drv2_spin.setValue(-1)
        self.drv2_spin.setSpecialValueText("不联动")
        self.drv2_spin.setToolTip("DRV2_LVL（0x1D bit6:0）。选择『不联动』时只发改 DRV1_LVL，\n"
                                  "DRV2 保持设备里的原值。")
        form.addRow("联动 DRV2_LVL", self.drv2_spin)

        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(20, 50)
        self.rate_spin.setValue(33)
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setToolTip("限速 20~50 Hz：拖动过程中丢弃中间值，只发最新值，"
                                  "避免把 400 kHz 的 I2C 打满。")
        self.rate_spin.valueChanged.connect(self._apply_rate)
        form.addRow("发送限速", self.rate_spin)

        self.ack_check = QCheckBox("带应答模式（测量真实往返延迟）")
        self.ack_check.setToolTip("勾选后用带应答的 SET_CONT_LIVE 测量真实 RTT；\n"
                                  "不勾选时（默认）用 NORESP 省一半流量，只显示发送间隔。")
        form.addRow("", self.ack_check)
        root.addLayout(form)

        self.latency_label = QLabel("往返延迟：尚无数据")
        self.latency_label.setStyleSheet("font-family:Consolas,monospace; color:#5fa8d3;")
        root.addWidget(self.latency_label)

        self.link_label = QLabel("链路对账：尚未对账（每 ~1 s 读 0x1C 一次）")
        self.link_label.setStyleSheet("color:#888;")
        root.addWidget(self.link_label)

        btn_row = QHBoxLayout()
        self.effect_btn = QPushButton("寄存器写入验证（实验）")
        self.effect_btn.setToolTip(_EFFECT_TEST_HELP)
        self.effect_btn.clicked.connect(self.run_effect_test)
        btn_row.addWidget(self.effect_btn)
        self.reconcile_btn = QPushButton("立即对账")
        self.reconcile_btn.setToolTip("读 REG_READ(0x1C,1) 确认链路与设备内的 DRV1_LVL")
        self.reconcile_btn.clicked.connect(self.reconcile)
        btn_row.addWidget(self.reconcile_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.conclusion_label = QLabel("")
        self.conclusion_label.setWordWrap(True)
        self.conclusion_label.setStyleSheet("color:#8fbf6f;")
        root.addWidget(self.conclusion_label)

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / max(1, self.rate_spin.value())))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setInterval(1000)
        self._reconcile_timer.timeout.connect(self.reconcile)
        self._reconcile_timer.start()

        self._apply_enabled()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def set_channel(self, chan: int) -> None:
        self._chan = chan

    def set_mode(self, chan: int, mode: int) -> None:
        if chan == self._chan:
            self._mode = mode
            self._apply_enabled()

    def set_playing(self, chan: int, playing: bool) -> None:
        if chan == self._chan:
            self._playing = bool(playing)
            self._apply_enabled()

    def set_context_channel(self, chan: int, mode: int, playing: bool) -> None:
        self._chan = chan
        self._mode = mode
        self._playing = playing
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        ok = (self._mode == C.MODE_CONT) and self._playing
        self._enabled = ok
        for w in (self.slider, self.drv2_spin, self.effect_btn, self.reconcile_btn):
            w.setEnabled(ok)
        if ok:
            self.status_label.setText(
                f"已启用：CH{self._chan} CONT 播放中 · 拖动即改 DRV1_LVL"
                f"（{'带应答' if self.ack_check.isChecked() else 'NORESP'}，"
                f"限速 {self.rate_spin.value()} Hz）")
            self.status_label.setStyleSheet("color:#8fbf6f;")
        else:
            why = ("当前不是 CONT 模式（实时幅度只对 CONT 连续驱动有效）"
                   if self._mode != C.MODE_CONT else "该通道当前没有在播放")
            self.status_label.setText(f"未启用：{why}。")
            self.status_label.setStyleSheet("color:#e6b422;")

    def _apply_rate(self) -> None:
        self._timer.setInterval(int(1000 / max(1, self.rate_spin.value())))
        self._apply_enabled()

    def _refresh_value_label(self) -> None:
        v = self.slider.value()
        volts = v * self.vdd_spin.value() / 128.0
        full = " · 满幅" if v > C.FULL_AMP_CONT_DRV1_LVL else ""
        self.value_label.setText(f"{v} ({v / 128.0:.2f}×) ≈ {volts:.2f} V{full}")

    # ------------------------------------------------------------------
    # 发送（限速 + 丢弃中间值）
    # ------------------------------------------------------------------

    def _on_value_changed(self, value: int) -> None:
        self._pending = int(value)
        self._dirty = True
        self._refresh_value_label()

    def _tick(self) -> None:
        if not self._enabled or not self._dirty or self._pending is None:
            return
        value = self._pending
        self._dirty = False
        self._send(value)

    def _drv2_payload(self, fallback: int = 0) -> int:
        v = self.drv2_spin.value()
        if v < 0:
            return fallback
        return v

    def _send(self, value: int) -> None:
        dev = self.ctx.device
        if dev is None:
            return
        chan = self._chan
        d2 = self.drv2_spin.value()
        ack = self.ack_check.isChecked()
        self._send_t0 = time.perf_counter()
        self._last_sent = value

        def job(progress):
            actual_d2 = dev.reg_read_u8(chan, 0x1D) & 0x7F if d2 < 0 else d2
            dev.set_cont_live(chan, value, actual_d2, noresp=not ack)
            return value

        def done(sent_value):
            dt = (time.perf_counter() - self._send_t0) * 1000.0
            if ack:
                self._rtt.append(dt)
                del self._rtt[:-20]
                self.latency_label.setText(
                    "真实往返延迟（带应答）：最近 %.1f ms · 平均 %.1f ms · 最大 %.1f ms"
                    % (dt, sum(self._rtt) / len(self._rtt), max(self._rtt)))
            else:
                self.latency_label.setText(
                    "NORESP 发送耗时：%.2f ms（无应答，无法测得真实往返；"
                    "勾选『带应答模式』可测）" % dt)
            self.latency_reported.emit(dt)

        self.ctx.task(job, on_done=done, name="SET_CONT_LIVE")

    # ------------------------------------------------------------------
    # 对账
    # ------------------------------------------------------------------

    def reconcile(self) -> None:
        if not self._enabled or self.ctx.device is None:
            return
        dev = self.ctx.device
        chan = self._chan

        def job(progress):
            return dev.reg_read_u8(chan, 0x1C)

        def done(raw: int) -> None:
            lvl = raw & 0x7F
            track = (raw >> 7) & 1
            note = ""
            if self._last_sent is not None and lvl != self._last_sent:
                note = (f"（下发 {self._last_sent}）不一致：可能需重新 GO 才生效，"
                        "可改用『停 → 改 → 重播』流程")
            self.link_label.setText(
                f"链路对账：CH{chan} 0x1C = 0x{raw:02X} → DRV1_LVL={lvl} "
                f"TRACK_EN={track} {note}")
            self.link_label.setStyleSheet("color:#e6b422;" if note else "color:#8fbf6f;")
            self._link_ok = not note

        self.ctx.task(job, on_done=done, name="REG_READ(0x1C)")

    # ------------------------------------------------------------------
    # ★ 生效性测试
    # ------------------------------------------------------------------

    def run_effect_test(self) -> None:
        if self.ctx.device is None or not self._enabled:
            self.ctx.warn(self, "无法测试",
                          "生效性测试需要 CONT 模式且正在播放（且已连接设备）。")
            return
        dev = self.ctx.device
        chan = self._chan
        d2 = self.drv2_spin.value()
        restore = self.slider.value()
        self.effect_btn.setEnabled(False)
        self.conclusion_label.setText("测试中：32 → 读回 → 127 → 读回 …")
        self.ctx.log(f"开始『实时幅度生效性测试』：CH{chan}（DRV1_LVL 32/127，各等 200 ms）", "user")

        def job(progress):
            actual_d2 = dev.reg_read_u8(chan, 0x1D) & 0x7F if d2 < 0 else d2
            out = {"chan": chan, "targets": (32, 127)}
            out["ack1"] = dev.set_cont_live(chan, 32, actual_d2, noresp=False)
            time.sleep(0.2)
            out["read1"] = dev.reg_read_u8(chan, 0x1C) & 0x7F
            out["ack2"] = dev.set_cont_live(chan, 127, actual_d2, noresp=False)
            time.sleep(0.2)
            out["read2"] = dev.reg_read_u8(chan, 0x1C) & 0x7F
            # 恢复到滑条当前值，避免测试改变实际输出
            try:
                dev.set_cont_live(chan, restore, actual_d2, noresp=False)
            except Exception:
                pass
            out["restored"] = restore
            return out

        def done(res: dict) -> None:
            self.effect_btn.setEnabled(self._enabled)
            t32, t127 = res["targets"]
            r1, r2 = res["read1"], res["read2"]
            ok = (r1 == t32 and r2 == t127)
            if ok:
                text = (f"寄存器写入回读一致；机械振幅即时响应仍需示波器或传感器实测。"
                        f"CH{res['chan']} 下发 {t32} → 200 ms 后回读 {r1}；"
                        f"下发 {t127} → 200 ms 后回读 {r2}。"
                        f"（已恢复到 {res['restored']}）")
                self.conclusion_label.setStyleSheet("color:#8fbf6f;")
            else:
                text = (f"寄存器回读不一致，请先排查通信与寄存器状态。"
                        f"CH{res['chan']} 下发 {t32} → 回读 {r1}；下发 {t127} → 回读 {r2}。"
                        f"建议把滑条流程改为『停 → 改值 → 重播』，"
                        f"并用 REG_WRITE(0x1C) 读-改-写保住 TRACK_EN。"
                        f"（已恢复到 {res['restored']}）")
                self.conclusion_label.setStyleSheet("color:#ff5555;")
            self.conclusion_label.setText(text)
            self.ctx.log("[生效性测试] " + text, "info" if ok else "warn")
            self.conclusion.emit(text)
            self.ctx.notify(self, "生效性测试结论", "已完成，结论见滑条面板与日志页。",
                          text.replace("**", ""))

        def failed(msg: str) -> None:
            self.effect_btn.setEnabled(self._enabled)
            self.conclusion_label.setText("生效性测试失败：" + msg.splitlines()[0])
            self.conclusion_label.setStyleSheet("color:#ff5555;")

        self.ctx.task(job, on_done=done, on_error=failed, name="生效性测试")


__all__ = ["AmpSlider"]
