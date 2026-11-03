# -*- coding: utf-8 -*-
"""通道页（规格书 §4 / §5.2）：8 路状态表 + 通道级操作 + 播放与硬触发。

通道矩阵常驻在主窗口顶部（勾选即"选中通道"），本页负责把选中通道**用起来**：
探测/读信息、设模式、软复位、播放（含播放前满幅安全检查）、PLAY_MULTI、
以及 TRIG 硬触发的脉冲/电平/图案与"TRIG 静默"。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import BasePage, UiContext, mask_to_text
from .widgets import ChannelMatrix


class PageChannels(BasePage):
    """通道状态与操作页。"""

    title = "通道"

    def __init__(self, ctx: UiContext, matrix: Optional[ChannelMatrix] = None,
                 parent: Optional[QWidget] = None) -> None:
        self.matrix = matrix
        super().__init__(ctx, parent)

    # ------------------------------------------------------------------

    def build(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_table_group())
        root.addWidget(self._build_mode_group())
        root.addWidget(self._build_play_group())
        root.addWidget(self._build_trig_group())
        root.addStretch(1)

    def _build_table_group(self) -> QGroupBox:
        box = QGroupBox("通道状态（CH_PROBE_ALL / CH_GET_INFO / SNAPSHOT）")
        layout = QVBoxLayout(box)
        self.table = QTableWidget(C.CHANNEL_COUNT, 7)
        self.table.setHorizontalHeaderLabels(
            ["通道", "在线", "CHIPID", "模式", "播放中", "GLB_STATE", "上次错误"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(210)             # ★ 8 行 + 表头，防止被滚动区压缩
        for row in range(C.CHANNEL_COUNT):
            self.table.setItem(row, 0, QTableWidgetItem(f"CH{row}"))
            for col in range(1, 7):
                self.table.setItem(row, col, QTableWidgetItem("—"))
            self.table.setRowHeight(row, 22)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_probe_all = QPushButton("在线探测（全部）")
        self.btn_probe_all.setToolTip("CH_PROBE_ALL：读取 present_mask 与每路 CHIPID（期望 0x2340）")
        self.btn_probe_all.clicked.connect(self._probe_all)
        self.btn_info_all = QPushButton("读取全部通道信息")
        self.btn_info_all.setToolTip("逐路 CH_GET_INFO：模式 / 播放状态 / GLB_STATE / 上次错误")
        self.btn_info_all.clicked.connect(self._read_all_info)
        self.btn_snapshot_sel = QPushButton("快照选中通道")
        self.btn_snapshot_sel.setToolTip("SNAPSHOT：一次拿到 present/CHIPID/mode/playing/"
                                         "GLB_STATE/SYSST/F0 结果/ADC")
        self.btn_snapshot_sel.clicked.connect(self._snapshot_selected)
        row.addWidget(self.btn_probe_all)
        row.addWidget(self.btn_info_all)
        row.addWidget(self.btn_snapshot_sel)
        row.addStretch(1)
        layout.addLayout(row)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#e6b422;")
        layout.addWidget(self.note)
        return box

    def _build_mode_group(self) -> QGroupBox:
        box = QGroupBox("模式与器件操作（作用于矩阵里勾选的通道）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.cmb_mode = QComboBox()
        for code, name in ((C.MODE_RAM, "RAM（播放 SRAM 波形库，自定义波形走这里）"),
                           (C.MODE_CONT, "CONT（连续驱动 + F0 检测 + 谐振跟踪）"),
                           (C.MODE_TRIG, "TRIG（硬件引脚触发，多路并发首选）"),
                           (C.MODE_RTP, "RTP（主机流式喂数据，仅 12 kHz 可用性验证）")):
            self.cmb_mode.addItem(name, code)
        self.cmb_mode.setToolTip("SET_MODE。TRIG 之前固件会强制确认 0x3A bit2 TRG1_STOP=1，\n"
                                 "写 0 会被拒绝（安全要求）。")
        form.addRow("播放模式", self.cmb_mode)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_set_mode = QPushButton("下发模式")
        self.btn_set_mode.clicked.connect(self._set_mode)
        self.btn_softrst = QPushButton("软复位选中")
        self.btn_softrst.clicked.connect(self._softrst)
        self.btn_default_profile = QPushButton("恢复默认 Profile")
        self.btn_default_profile.setStyleSheet("color:#ff5555;")
        self.btn_default_profile.setToolTip("SET_DEFAULT_PROFILE：把该通道恢复成器件默认 Profile\n"
                                            "（需要二次确认）")
        self.btn_default_profile.clicked.connect(self._default_profile)
        self.btn_sync_modes = QPushButton("按设备回读刷新矩阵")
        self.btn_sync_modes.setToolTip("用 CH_GET_INFO 的结果刷新主窗口通道矩阵显示的模式")
        self.btn_sync_modes.clicked.connect(self._read_all_info)
        for b in (self.btn_set_mode, self.btn_softrst, self.btn_default_profile,
                  self.btn_sync_modes):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        return box

    def _build_play_group(self) -> QGroupBox:
        box = QGroupBox("播放（PLAY_MULTI）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(0, 60000)
        self.sp_timeout.setSuffix(" ms")
        self.sp_timeout.setToolTip("播放安全超时；0 = 用固件默认值（GET_SAFETY.play_timeout_ms）。\n"
                                   "超时固件会强制停止并发 EVT_PLAY_DONE(reason=2)。")
        self.sp_skew = QSpinBox()
        self.sp_skew.setRange(0, 5000)
        self.sp_skew.setSuffix(" µs")
        self.sp_skew.setToolTip("PLAY_MULTI 的通道间错开时间（先不做软件延时，由固件尽量对齐）。")
        form.addRow("安全超时", self.sp_timeout)
        form.addRow("通道错开 skew", self.sp_skew)
        root.addLayout(form)

        self.chk_override = QCheckBox("override 安全限制（危险：允许超过同时满幅路数）")
        self.chk_override.setStyleSheet("color:#ff5555;")
        self.chk_override.setToolTip("满幅路数超过 max_simul_full_amp 时，固件会回 ERR_SAFETY_LIMIT。\n"
                                     "勾选后会用 override=1 再试一次——每次都会二次确认。")
        root.addWidget(self.chk_override)

        row = QHBoxLayout()
        self.btn_play = QPushButton("播放选中通道")
        self.btn_play.setStyleSheet("font-weight:bold;")
        self.btn_play.clicked.connect(self._play)
        self.btn_stop = QPushButton("停止选中通道")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop_all = QPushButton("全部停止 + TRIG 静默")
        self.btn_stop_all.setStyleSheet("color:#ff5555; font-weight:bold;")
        self.btn_stop_all.setToolTip("STOP_ALL(immediate=1) + TRIG_PARK，最优先执行")
        self.btn_stop_all.clicked.connect(self.stop_all_and_park)
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_stop_all)
        row.addStretch(1)
        root.addLayout(row)
        return box

    def _build_trig_group(self) -> QGroupBox:
        box = QGroupBox("硬触发（TRIG）")
        root = QVBoxLayout(box)
        warn = QLabel("★ 每路一条独立 TRIG（新硬件）：硬触发下各通道互不影响，可单独触发；"
                      "若固件上报『CH5/6/7 共用 TRIG』能力位（老硬件），矩阵会自动联动加锁。"
                      "脉冲宽度建议 10~50 µs（≥1 µs），高/低电平需要 ≥0.8 ms，TON2 典型 0.4 ms。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#e6b426;")
        root.addWidget(warn)

        form = QFormLayout()
        self.sp_width = QSpinBox()
        self.sp_width.setRange(1, 60000)
        self.sp_width.setValue(20)
        self.sp_width.setSuffix(" µs")
        self.sp_width.setToolTip("TRIG_PULSE width_us（≥1 µs，建议 10~50 µs）")
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, 32)
        self.sp_count.setValue(1)
        self.sp_count.setToolTip("脉冲个数 1..32")
        self.sp_period = QSpinBox()
        self.sp_period.setRange(1, 60000)
        self.sp_period.setValue(100)
        self.sp_period.setSuffix(" ms")
        self.sp_delay = QSpinBox()
        self.sp_delay.setRange(0, 60000)
        self.sp_delay.setSuffix(" µs")
        self.sp_delay.setToolTip("首个脉冲前的初始延时")
        form.addRow("脉冲宽度", self.sp_width)
        form.addRow("脉冲个数", self.sp_count)
        form.addRow("脉冲周期", self.sp_period)
        form.addRow("初始延时", self.sp_delay)
        self.cmb_level = QComboBox()
        self.cmb_level.addItem("高电平", 1)
        self.cmb_level.addItem("低电平", 0)
        self.sp_hold = QSpinBox()
        self.sp_hold.setRange(1, 60000)
        self.sp_hold.setValue(10)
        self.sp_hold.setSuffix(" ms")
        self.sp_hold.setToolTip("TRIG_LEVEL hold_ms（手册要求 ≥0.8 ms）")
        self.sp_on = QSpinBox()
        self.sp_on.setRange(1, 60000)
        self.sp_on.setValue(20)
        self.sp_on.setSuffix(" µs")
        self.sp_off = QSpinBox()
        self.sp_off.setRange(1, 60000)
        self.sp_off.setValue(20)
        self.sp_off.setSuffix(" µs")
        self.sp_pcount = QSpinBox()
        self.sp_pcount.setRange(1, 60000)
        self.sp_pcount.setValue(10)
        self.sp_ppd = QSpinBox()
        self.sp_ppd.setRange(1, 60000)
        self.sp_ppd.setValue(100)
        self.sp_ppd.setSuffix(" ms")
        form.addRow("电平方向", self.cmb_level)
        form.addRow("电平保持", self.sp_hold)
        form.addRow("图案 on", self.sp_on)
        form.addRow("图案 off", self.sp_off)
        form.addRow("图案次数", self.sp_pcount)
        form.addRow("图案周期", self.sp_ppd)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_pulse = QPushButton("TRIG_PULSE")
        self.btn_pulse.setToolTip("固件本地生成脉冲串（µs 级，不占 I2C）")
        self.btn_pulse.clicked.connect(self._trig_pulse)
        self.btn_level = QPushButton("TRIG_LEVEL")
        self.btn_level.clicked.connect(self._trig_level)
        self.btn_pattern = QPushButton("TRIG_PATTERN")
        self.btn_pattern.clicked.connect(self._trig_pattern)
        self.btn_park = QPushButton("TRIG 静默 TRIG_PARK")
        self.btn_park.setToolTip("把所有 TRIG 输出拉低（安全操作）")
        self.btn_park.clicked.connect(self._trig_park)
        for b in (self.btn_pulse, self.btn_level, self.btn_pattern, self.btn_park):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        return box

    # ==================================================================
    # 辅助
    # ==================================================================

    def _selected(self) -> List[int]:
        return self.ctx.selected_channels()

    def _require_selection(self) -> Optional[List[int]]:
        chans = self._selected()
        if not chans:
            self.ctx.warn(self, "没有选中通道",
                          "请先在顶部通道矩阵里勾选通道（矩阵决定所有操作的目标）。")
            return None
        return chans

    def _set_table(self, row: int, col: int, text: str, color: Optional[str] = None) -> None:
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            self.table.setItem(row, col, item)
        else:
            item.setText(text)
        if color:
            item.setForeground(Qt.GlobalColor.white)
            item.setBackground(Qt.GlobalColor.darkRed if color == "bad"
                               else Qt.GlobalColor.darkGreen if color == "good"
                               else Qt.GlobalColor.darkYellow)

    # ==================================================================
    # 探测 / 信息
    # ==================================================================

    def _probe_all(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return dev.ch_probe_all()

        def done(res: K.ChProbeAllResp) -> None:
            chipids: Dict[int, Optional[int]] = {}
            for chan, hid, lid, st in res.entries:
                chipids[chan] = (hid << 8) | lid
            if self.matrix is not None:
                self.matrix.apply_probe_all(res.present_mask, chipids)
            mismatch = []
            for ch in range(C.CHANNEL_COUNT):
                present = bool(res.present_mask & (1 << ch))
                cid = chipids.get(ch)
                self._set_table(ch, 1, "在线 ✔" if present else "离线 ✘",
                                "good" if present else "bad")
                if cid is None:
                    self._set_table(ch, 2, "—")
                else:
                    self._set_table(ch, 2, f"0x{cid:04X}",
                                    "good" if cid == C.AW_CHIPID_EXPECTED else "warn")
                    if present and cid != C.AW_CHIPID_EXPECTED:
                        mismatch.append(f"CH{ch}=0x{cid:04X}")
            n = bin(res.present_mask).count("1")
            self.ctx.log(f"在线探测：present_mask=0x{res.present_mask:02X}（{n} 路在线）", "info")
            if mismatch:
                self.note.setText("CHIPID 与期望的 0x2340 不符：" + "、".join(mismatch)
                                  + "。可能是 AW86234AFCR 之外的变体，或通道映射接错。")
            else:
                self.note.setText("全部在线通道的 CHIPID 均为 0x2340 ✔")

        self.ctx.task(job, on_done=done, name="CH_PROBE_ALL")

    def _read_all_info(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            out = []
            for ch in range(C.CHANNEL_COUNT):
                try:
                    out.append((ch, dev.ch_get_info(ch)))
                except Exception:
                    out.append((ch, None))
            return out

        def done(rows) -> None:
            modes = {}
            playing_mask = 0
            for ch, info in rows:
                if info is None:
                    continue
                modes[ch] = info.mode
                self._set_table(ch, 3, C.mode_name(info.mode))
                self._set_table(ch, 4, "播放中" if info.playing else "—")
                self._set_table(ch, 5, C.glb_state_name(info.glb_state))
                err = info.last_err
                self._set_table(ch, 6, "无" if not err
                                else f"{C.err_name(err)}（{C.err_cn(err)}）",
                                None if not err else "bad")
                if info.playing:
                    playing_mask |= 1 << ch
            if self.matrix is not None:
                self.matrix.set_modes(modes)
                self.matrix.set_all_playing(playing_mask)
            self.ctx.log("已刷新 8 路通道信息", "info")

        self.ctx.task(job, on_done=done, name="CH_GET_INFO")

    def _snapshot_selected(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            return [(ch, dev.snapshot(ch)) for ch in chans]

        def done(rows) -> None:
            for ch, snap in rows:
                self._set_table(ch, 1, "在线" if snap.present else "离线",
                                "good" if snap.present else "bad")
                self._set_table(ch, 2, f"0x{snap.chipid_h:02X}{snap.chipid_l:02X}")
                self._set_table(ch, 3, C.mode_name(snap.mode))
                self._set_table(ch, 4, "播放中" if snap.playing else "—")
                self._set_table(ch, 5, snap.glb_state_name)
                f0 = snap.f0_hz
                f0_text = f"{f0:.1f} Hz" if f0 else "无效"
                self.ctx.log(
                    f"CH{ch} 快照：{snap.glb_state_name} · SYSST=0x{snap.sysst:02X} · "
                    f"SYSST2=0x{snap.sysst2:02X} · F0={f0_text} · AVG={snap.avg_data} · "
                    f"VBAT={snap.vbat_v():.2f} V · RL={snap.rl_ohm():.2f} Ω", "info")
            if self.matrix is not None:
                playing = sum(1 << ch for ch, s in rows if s.playing)
                self.matrix.set_all_playing(playing)
                self.matrix.set_modes({ch: s.mode for ch, s in rows})

        self.ctx.task(job, on_done=done, name="SNAPSHOT")

    # ==================================================================
    # 模式 / 器件
    # ==================================================================

    def _set_mode(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        mode = int(self.cmb_mode.currentData())
        if mode == C.MODE_TRIG:
            if not self.ctx.confirm(self, "进入 TRIG 模式",
                                    f"确认把 {mask_to_text(self.ctx.selected_mask)} 设为 TRIG？",
                                    "TRIG 走硬件引脚触发；固件会强制确认 0x3A bit2 TRG1_STOP=1，"
                                    "并强制 0x39 bit3 TRG1_BRK=1。\n"
                                    "★ CH5/CH6/CH7 共用 GPIO14，硬触发下三路只能同时动作。"):
                return
        dev = self.ctx.device

        def job(progress):
            ok = []
            for ch in chans:
                dev.set_mode(ch, mode)
                ok.append(ch)
            return ok

        def done(ok) -> None:
            if self.matrix is not None:
                self.matrix.set_modes({ch: mode for ch in ok})
            for ch in ok:
                self._set_table(ch, 3, C.mode_name(mode))
            self.ctx.log(f"已设置模式 {C.mode_name(mode)}：{mask_to_text(self.ctx.selected_mask)}",
                         "user")
            for ch in ok:
                self.ctx.mode_changed.emit(ch, mode)

        self.ctx.task(job, on_done=done, name="SET_MODE")

    def _softrst(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        if not self.ctx.confirm(self, "软复位",
                                f"确认对 {mask_to_text(self.ctx.selected_mask)} 软复位？",
                                "写 0x00 = 0xAA 会清掉全部配置寄存器（SRAM 不清），"
                                "固件会重新下发 Profile。"):
            return
        dev = self.ctx.device

        def job(progress):
            for ch in chans:
                dev.aw_softrst(ch, replay_profile=True)
            return len(chans)

        self.ctx.task(job, on_done=lambda n: self.ctx.log(f"软复位完成：{n} 路", "user"),
                      name="AW_SOFTRST")

    def _default_profile(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        if not self.ctx.confirm(self, "恢复默认 Profile",
                                f"确认对 {mask_to_text(self.ctx.selected_mask)} 执行 "
                                f"SET_DEFAULT_PROFILE？",
                                "该操作会覆盖当前通道的全部参数（含 TRIG 相关位）。"
                                "恢复后建议立刻『下发 → 回读 → 比对』确认。", danger=True):
            return
        dev = self.ctx.device

        def job(progress):
            for ch in chans:
                dev.set_default_profile(ch)
            return len(chans)

        self.ctx.task(job, on_done=lambda n: self.ctx.log(f"已恢复默认 Profile：{n} 路", "user"),
                      name="SET_DEFAULT_PROFILE")

    # ==================================================================
    # 播放
    # ==================================================================

    def _play(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        mask = self.ctx.selected_mask
        override = self.chk_override.isChecked()
        if override and not self.ctx.confirm(self, "override 确认",
                "本次播放将绕过同时满幅路数限制。确认执行？", danger=True):
            return

        def job(progress):
            return dev.safety_check(mask, override=override)

        def done(res) -> None:
            allowed, why = res[0], res[1]
            if allowed:
                self._do_play(mask, override)
                return
            detail = (why + "\n\n是否以 override 试一次？"
                      "（危险：会真的同时驱动多路满幅，注意 USB 口 500 mA 供电限制）")
            if not self.ctx.confirm(self, "超出同时满幅路数限制",
                                    "播放前安全检查未通过。", detail, danger=True):
                self.ctx.log("用户取消播放：安全限制未通过", "user")
                return
            if not self.ctx.confirm(self, "override 二次确认",
                                    "再次确认：以 override 绕过安全限制播放？",
                                    "固件可能仍拒绝；多路满幅同时驱动会拉高电流。", danger=True):
                self.ctx.log("用户取消播放：override 二次确认未通过", "user")
                return
            self.ctx.log("已获用户许可：以 override 重试播放（危险操作）", "warn")
            self._do_play(mask, True)

        self.ctx.task(job, on_done=done, name="安全检查")

    def _do_play(self, mask: int, override: bool) -> None:
        dev = self.ctx.device
        if dev is None:
            return
        timeout = self.sp_timeout.value()
        skew = self.sp_skew.value()
        real_mask = mask  # PLAY_MULTI uses GO; shared TRIG expansion belongs to hardware triggers.

        def job(progress):
            return dev.play_multi(real_mask, timeout, skew, override)

        def done(res: K.PlayMultiResp) -> None:
            if real_mask != mask:
                self.ctx.log("TRIG 联动：CH5/6/7 共用 GPIO14，实际启动掩码已扩展为 "
                             f"0x{real_mask:02X}", "warn")
            if self.matrix is not None:
                self.matrix.set_all_playing(res.started_mask)
            self.ctx.log(f"播放已启动：started_mask=0x{res.started_mask:02X} "
                         f"actual_skew={res.actual_skew_us} µs"
                         + ("（override）" if override else ""), "user")

        self.ctx.task(job, on_done=done, name="PLAY_MULTI")

    def _stop(self) -> None:
        self.ctx.cancel_operations.emit()
        chans = self._selected()
        if chans is None or not self.ctx.require_device(self):
            return
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在通道矩阵里勾选通道。")
            return
        dev = self.ctx.device

        def job(progress):
            for ch in chans:
                try:
                    dev.stop(ch, immediate=True)
                except Exception:
                    pass
            return len(chans)

        def done(n) -> None:
            if self.matrix is not None:
                for ch in chans:
                    self.matrix.set_playing(ch, False)
            self.ctx.log(f"已停止 {n} 路", "user")

        self.ctx.task(job, on_done=done, urgent=True, name="STOP")

    # ==================================================================
    # 安全路径（主窗口也会调用）
    # ==================================================================

    def probe_all(self) -> None:
        """公开入口：CH_PROBE_ALL（主窗口『在线探测』按钮调用）。"""
        self._probe_all()

    def trig_park(self) -> None:
        """公开入口：TRIG_PARK。"""
        self._trig_park()

    def softrst_selected(self) -> None:
        """公开入口：软复位选中通道。"""
        self._softrst()

    def stop_all_and_park(self, announce: bool = True) -> None:
        """STOP_ALL + TRIG_PARK，尽力而为且**插队执行**（安全第一）。"""
        self.ctx.cancel_operations.emit()
        dev = self.ctx.device
        if dev is None or not dev.is_open:
            return

        def job(progress):
            stopped = -1
            try:
                stopped = dev.stop_all(immediate=True)
            except Exception:
                pass
            try:
                dev.trig_park()
            except Exception:
                pass
            return stopped

        def done(stopped) -> None:
            if self.matrix is not None:
                self.matrix.set_all_playing(0)
            if announce:
                self.ctx.log(f"已发送 STOP_ALL + TRIG_PARK"
                             + (f"（停止掩码 0x{stopped:02X}）" if stopped >= 0 else "（部分未确认）"),
                             "user")

        self.ctx.task(job, on_done=done, urgent=True, name="STOP_ALL")

    # ==================================================================
    # TRIG
    # ==================================================================

    def _trig_pulse(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        mask = self.ctx.selected_mask
        width = self.sp_width.value()
        count = self.sp_count.value()
        period = self.sp_period.value()
        delay = self.sp_delay.value()

        def job(progress):
            return dev.trig_pulse(mask, width, count, period, delay)

        def done(started: int) -> None:
            if self.matrix is not None:
                self.matrix.set_all_playing(started)
            self.ctx.log(f"TRIG_PULSE：mask=0x{mask:02X} {width} µs ×{count} "
                         f"周期 {period} ms，实际启动 0x{started:02X}", "user")
            if (mask & 0xE0) and self.ctx.cap(C.CAP_SHARED_TRIG_567):
                self.ctx.log("提示：CH5/6/7 共用 TRIG（GPIO14），掩码含其中一路会同时带动三路。",
                             "warn")

        self.ctx.task(job, on_done=done, name="TRIG_PULSE")

    def _trig_level(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        mask = self.ctx.selected_mask
        level = int(self.cmb_level.currentData())
        hold = self.sp_hold.value()
        if hold < 1:
            self.ctx.warn(self, "参数非法", "TRIG 电平保持时间必须 ≥0.8 ms。")
            return

        def job(progress):
            dev.trig_level(mask, level, hold)
            return level

        self.ctx.task(job, on_done=lambda lv: self.ctx.log(
            f"TRIG_LEVEL：mask=0x{mask:02X} {'高' if lv else '低'}电平保持 {hold} ms", "user"),
            name="TRIG_LEVEL")

    def _trig_pattern(self) -> None:
        chans = self._require_selection()
        if chans is None or not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        mask = self.ctx.selected_mask
        on, off = self.sp_on.value(), self.sp_off.value()
        count, period = self.sp_pcount.value(), self.sp_ppd.value()

        def job(progress):
            dev.trig_pattern(mask, on, off, count, period)
            return mask

        self.ctx.task(job, on_done=lambda m: self.ctx.log(
            f"TRIG_PATTERN：mask=0x{m:02X} on={on} µs off={off} µs ×{count} "
            f"周期 {period} ms", "user"), name="TRIG_PATTERN")

    def _trig_park(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device

        def job(progress):
            dev.trig_park()

        self.ctx.task(job, on_done=lambda _r: self.ctx.log("TRIG_PARK 已发送（所有 TRIG 输出拉低）",
                                                           "user"),
                      urgent=True, name="TRIG_PARK")

    # ==================================================================
    # 连接状态
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        for w in (self.btn_probe_all, self.btn_info_all, self.btn_snapshot_sel,
                  self.btn_set_mode, self.btn_softrst, self.btn_default_profile,
                  self.btn_sync_modes, self.btn_play, self.btn_stop, self.btn_stop_all,
                  self.btn_pulse, self.btn_level, self.btn_pattern, self.btn_park):
            w.setEnabled(ok)
        if not ok:
            for row in range(C.CHANNEL_COUNT):
                for col in range(1, 7):
                    self._set_table(row, col, "—")
            self.note.setText("")


__all__ = ["PageChannels"]
