# -*- coding: utf-8 -*-
"""模式与参数页（规格书 §5.4）：RAM / CONT / TRIG / RTP 四种模式的全部参数控件。

三个硬要求：

1. **每个控件都有单位、范围、默认值、寄存器地址与公式的 tooltip**；
2. TRIG 段的 ``TRG1_BRK`` / ``TRG1_STOP`` **只读且强制为开**（置灰 + 写明理由）；
3. ``[下发到选中通道]`` 之后**立即 ``GET_PROFILE`` 回读并逐字段比对**，
   不一致的字段标红并列出"期望 vs 实际"（``device.set_profile_verify()``）。

页面顶部内嵌 CONT 专用的实时幅度滑条（:class:`~awtest.ui.widgets.amp_slider.AmpSlider`）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from awtest import profiles as P
from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import BasePage, mask_to_text
from .widgets import AmpSlider

# 各字段的中文名（比对表里用）
_FIELD_CN: Dict[str, str] = {name: spec[2] for name, spec in P.FIELD_SPECS.items()}

_MODE_DESC = {
    C.MODE_RAM: "播放 SRAM 波形库中的波形，支持播放队列与循环。**自定义波形走这里**。",
    C.MODE_CONT: "连续驱动 + F0 检测 + 谐振跟踪，支持**实时幅度**调节。",
    C.MODE_TRIG: "硬件引脚触发，延时最低（0.4 ms 量级），**多路并发首选**。"
                 "共享触发线由设备能力位决定。",
    C.MODE_RTP: "主机流式喂数据。**仅 12 kHz 可用性验证**；24 kHz 临界；48 kHz 本板不可用。",
}

_RTP48_REASON = ("48 kHz 需要 ≥1 MHz 的 I2C 速率，而 TCA9548A 与 ESP32-C6 都只到 400 kHz，"
                 "因此本板不可用（写 48K 固件会回 ERR_UNSUPPORTED）。")

_TRIG_BRK_TIP = ("0x39 bit3 TRG1_BRK。TRIG 模式下必须为 1：否则停止后自动刹车不工作，"
                 "触发起振后只能等波形自然播完。界面只读，无法关闭。")
_TRIG_STOP_TIP = ("0x3A bit2 TRG1_STOP。**关闭后 TRIG 播放将无法被停止，安全机制失效**，"
                  "因此界面强制为开且置灰不可编辑（协议 §11.1）。")


class _Collapsible(QWidget):
    """可折叠参数分组（点击标题展开/收起）。"""

    def __init__(self, title: str, expanded: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._btn = QToolButton()
        self._btn.setText(title)
        self._btn.setCheckable(True)
        self._btn.setChecked(expanded)
        self._btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._btn.clicked.connect(self._toggle)
        self.body = QWidget()
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(12, 4, 4, 4)
        self.body.setVisible(expanded)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        root.addWidget(self._btn)
        root.addWidget(self.body)
        self._btn.setStyleSheet("QToolButton { font-weight: bold; border: none; }")

    def _toggle(self) -> None:
        on = self._btn.isChecked()
        self.body.setVisible(on)
        self._btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    def add(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)


class PageModeParams(BasePage):
    """四种模式的参数编辑 + 下发/回读/比对。"""

    title = "模式与参数"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self._loading = False
        self.profile = P.Profile()
        self._ram_wait_checks: List[QCheckBox] = []
        self._ram_seq_spins: List[QSpinBox] = []
        self._seq_loop_spins: List[QSpinBox] = []
        self._trap_labels: List[QLabel] = []

        root = QVBoxLayout(self)
        self.amp_slider = AmpSlider(self.ctx)
        root.addWidget(self.amp_slider)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---- 左：模式 + 参数 ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        mode_box = QGroupBox("播放模式")
        mode_layout = QVBoxLayout(mode_box)
        self.mode_list = QListWidget()
        self.mode_list.setMaximumHeight(96)
        for code, name in ((C.MODE_RAM, "RAM"), (C.MODE_CONT, "CONT"),
                           (C.MODE_TRIG, "TRIG"), (C.MODE_RTP, "RTP")):
            item = QListWidgetItem(f"{name} — {_MODE_DESC[code]}")
            item.setData(Qt.UserRole, code)
            item.setToolTip(_MODE_DESC[code])
            self.mode_list.addItem(item)
        self.mode_list.setCurrentRow(0)
        self.mode_list.currentItemChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_list)
        self.mode_desc = QLabel(_MODE_DESC[C.MODE_RAM])
        self.mode_desc.setWordWrap(True)
        self.mode_desc.setStyleSheet("color:#5fa8d3;")
        mode_layout.addWidget(self.mode_desc)
        left_layout.addWidget(mode_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        self.groups_layout = QVBoxLayout(holder)
        self.groups_layout.setSpacing(6)
        self.group_common = self._build_common_group()
        self.group_ram = self._build_ram_group()
        self.group_cont = self._build_cont_group()
        self.group_trig = self._build_trig_group()
        self.group_rtp = self._build_rtp_group()
        self.group_vbat = self._build_vbat_group()
        for g in (self.group_common, self.group_ram, self.group_cont,
                  self.group_trig, self.group_rtp, self.group_vbat):
            self.groups_layout.addWidget(g)
        self.groups_layout.addStretch(1)
        scroll.setWidget(holder)
        left_layout.addWidget(scroll, 1)

        # ---- 右：操作 + 校验 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        act = QGroupBox("下发与校验")
        act_layout = QVBoxLayout(act)
        tip = QLabel("流程：下发到选中通道 → 立即 GET_PROFILE 回读 → 逐字段比对，"
                     "不一致的字段会标红并列出『期望 vs 实际』。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#888;")
        act_layout.addWidget(tip)
        self.btn_send = QPushButton("下发到选中通道")
        self.btn_send.setStyleSheet("font-weight:bold;")
        self.btn_send.setToolTip("SET_PROFILE → 回读 → 比对（对矩阵里勾选的每一路都做）")
        self.btn_send.clicked.connect(self._send)
        self.btn_read = QPushButton("读回")
        self.btn_read.setToolTip("GET_PROFILE，用设备里的值刷新界面")
        self.btn_read.clicked.connect(self._read_back)
        self.btn_default = QPushButton("恢复默认")
        self.btn_default.setStyleSheet("color:#ff5555;")
        self.btn_default.setToolTip("SET_DEFAULT_PROFILE（需要二次确认），随后自动读回")
        self.btn_default.clicked.connect(self._restore_default)
        act_layout.addWidget(self.btn_send)
        act_layout.addWidget(self.btn_read)
        act_layout.addWidget(self.btn_default)

        self.result_label = QLabel("尚未下发。")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color:#888;")
        act_layout.addWidget(self.result_label)

        self.mismatch = QTableWidget(0, 5)
        self.mismatch.setHorizontalHeaderLabels(["通道", "字段", "期望", "实际", "说明"])
        self.mismatch.verticalHeader().setVisible(False)
        self.mismatch.setEditTriggers(QTableWidget.NoEditTriggers)
        self.mismatch.horizontalHeader().setStretchLastSection(True)
        self.mismatch.setMinimumHeight(180)
        act_layout.addWidget(self.mismatch)
        right_layout.addWidget(act)

        prof_box = QGroupBox("Profile 存取（JSON）")
        prof_layout = QVBoxLayout(prof_box)
        self.preset_list = QListWidget()
        self.preset_list.setMaximumHeight(110)
        for name in P.PRESET_FACTORIES:
            item = QListWidgetItem(name)
            item.setToolTip("预设 Profile：双击加载到界面")
            self.preset_list.addItem(item)
        self.preset_list.itemDoubleClicked.connect(self._load_preset)
        prof_layout.addWidget(QLabel("预设（双击加载）："))
        prof_layout.addWidget(self.preset_list)
        row = QHBoxLayout()
        b_save = QPushButton("保存到文件")
        b_save.clicked.connect(self._save_profile)
        b_load = QPushButton("从文件加载")
        b_load.clicked.connect(self._open_profile)
        b_loaddev = QPushButton("从设备读回另存")
        b_loaddev.setToolTip("GET_PROFILE → 存成 JSON（用于把现场调好的参数固化下来）")
        b_loaddev.clicked.connect(self._save_from_device)
        for b in (b_save, b_load, b_loaddev):
            row.addWidget(b)
        prof_layout.addLayout(row)
        right_layout.addWidget(prof_box)
        right_layout.addStretch(1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._sync_ui(self.profile)
        self._on_mode_changed()
        self.on_connection_changed(self.ctx.connected)

    # ------------------------------------------------------------------
    # 通用 / 播放
    # ------------------------------------------------------------------

    def _build_common_group(self) -> _Collapsible:
        g = _Collapsible("通用 / 播放", True)
        form = QFormLayout()

        self.sl_gain = QSlider(Qt.Horizontal)
        self.sl_gain.setRange(0, 255)
        self.sl_gain.valueChanged.connect(self._on_gain)
        self.sp_gain = QSpinBox()
        self.sp_gain.setRange(0, 255)
        self.sp_gain.valueChanged.connect(self._on_gain)
        gain_row = QWidget()
        gr = QHBoxLayout(gain_row)
        gr.setContentsMargins(0, 0, 0, 0)
        gr.addWidget(self.sl_gain, 1)
        gr.addWidget(self.sp_gain)
        self.lbl_gain = QLabel("1.00×")
        gr.addWidget(self.lbl_gain)
        gain_tip = ("寄存器 0x07 PLAYCFG2 · GAIN，范围 0..255，默认 128。\n"
                    "公式：倍率 = code / 128（0.00×~1.99×）；默认 128 = 1.00×。\n"
                    "满幅判定阈值：GAIN > 192 视为满幅（协议 §11）。")
        self.sl_gain.setToolTip(gain_tip)
        self.sp_gain.setToolTip(gain_tip)
        form.addRow("增益 GAIN", gain_row)

        self.lbl_mode_reg = QLabel("0x08 PLAY_MODE = b00 (RAM)")
        self.lbl_mode_reg.setStyleSheet("font-family:Consolas,monospace; color:#5fa8d3;")
        self.lbl_mode_reg.setToolTip(
            "寄存器 0x08 bit1:0 · PLAY_MODE，默认 0 = RAM。取值 0=RAM 1=RTP 2=CONT；\n"
            "TRIG 不看这一位，而是由 0x45 bit6:4 TRG1_MODE = b011 选择。\n"
            "模式在上面的『播放模式』列表里选，这里只是对照寄存器值。")
        form.addRow("播放模式寄存器", self.lbl_mode_reg)

        self.chk_auto_brk = QCheckBox("自动刹车 BRK_EN（默认开）")
        self.chk_auto_brk.setToolTip("寄存器 0x08 bit2 · BRK_EN，默认 1。\n"
                                "关闭后停止播放时不做自动刹车，LRA 余振会拖长。")
        form.addRow("", self.chk_auto_brk)

        self.cmb_stop = QComboBox()
        self.cmb_stop.addItem("播完停", 0)
        self.cmb_stop.addItem("立即停", 1)
        self.cmb_stop.setToolTip("寄存器 0x08 bit3 · STOP_MODE，默认 0 = 播完停。")
        form.addRow("停止方式", self.cmb_stop)

        self.lbl_trap = QLabel("")
        self.lbl_trap.setWordWrap(True)
        self.lbl_trap.setStyleSheet("color:#e6b422;")
        form.addRow(self.lbl_trap)

        holder = QWidget()
        holder.setLayout(form)
        g.add(holder)
        return g

    # ------------------------------------------------------------------
    # RAM
    # ------------------------------------------------------------------

    def _build_ram_group(self) -> _Collapsible:
        g = _Collapsible("RAM 段（播放队列 / 循环 / 等待）", False)
        box = QWidget()
        root = QVBoxLayout(box)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(QLabel("播放队列（每步可填『波形号 1..127』或勾选『等待』后填等待码）："))

        grid = QGridLayout()
        grid.addWidget(QLabel("步"), 0, 0)
        grid.addWidget(QLabel("类型"), 0, 1)
        grid.addWidget(QLabel("值"), 0, 2)
        grid.addWidget(QLabel("步"), 0, 3)
        grid.addWidget(QLabel("类型"), 0, 4)
        grid.addWidget(QLabel("值"), 0, 5)
        for i in range(8):
            wait = QCheckBox("等待")
            wait.setToolTip("勾选后该步是等待：0x0A~0x11 的 bit7 SEQxWAIT=1，\n"
                            "bit6:0 为等待码，实际时间 = 码值 × WAITSLOT 个单位。")
            wait.stateChanged.connect(self._refresh_derived)
            spin = QSpinBox()
            spin.setRange(0, 127)
            spin.setToolTip("0x0A~0x11 WAVCFG1..8 (WAVSEQ1..8)："
                            "bit6:0 = 波形号（1..127）或等待码（0..127）")
            spin.valueChanged.connect(self._refresh_derived)
            self._ram_wait_checks.append(wait)
            self._ram_seq_spins.append(spin)
            r = 1 + i % 4
            c = (i // 4) * 3
            grid.addWidget(QLabel(f"#{i + 1}"), r, c)
            grid.addWidget(wait, r, c + 1)
            grid.addWidget(spin, r, c + 2)
        root.addLayout(grid)

        form = QFormLayout()
        self.sp_mainloop = self._loop_spin("0x15 bit3:0 · MAINLOOP。界面显示为『次数』= code+1；\n"
                                          "16 表示无限（写 code=15）。默认 1 次。")
        form.addRow("主循环 MAINLOOP", self.sp_mainloop)

        for i in range(8):
            spin = self._loop_spin(f"0x12~0x15 · SEQ{i + 1}LOOP（每寄存器两个 4 位字段）。\n"
                                   "显示为次数 = code+1；16 表示无限（code=15）。")
            self._seq_loop_spins.append(spin)
            form.addRow(f"每步循环 SEQ{i + 1}LOOP", spin)

        self.cmb_waitslot = QComboBox()
        for code, name in ((0, "1 个采样周期"), (1, "8 个采样周期"),
                           (2, "64 个采样周期"), (3, "512 个采样周期")):
            self.cmb_waitslot.addItem(name, code)
        self.cmb_waitslot.setToolTip("0x15 bit5:4 · WAITSLOT，默认 0 = 1 个采样周期。\n"
                                     "等待时间 = 等待码 × WAITSLOT ÷ 采样率。")
        self.cmb_waitslot.currentIndexChanged.connect(self._refresh_derived)
        form.addRow("等待单位 WAITSLOT", self.cmb_waitslot)
        root.addLayout(form)

        self.lbl_ram_calc = QLabel("")
        self.lbl_ram_calc.setStyleSheet("color:#5fa8d3;")
        self.lbl_ram_calc.setWordWrap(True)
        root.addWidget(self.lbl_ram_calc)
        root.addWidget(QLabel("提示：自定义波形必须先在本工具的『波形编辑』页下发到 SRAM，"
                              "再用这里的队列引用它的波形号。"))
        g.add(box)
        return g

    def _loop_spin(self, tip: str) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(1, 16)
        sp.setSuffix(" 次")
        sp.setToolTip(tip)
        sp.valueChanged.connect(self._refresh_derived)
        return sp

    # ------------------------------------------------------------------
    # CONT
    # ------------------------------------------------------------------

    def _build_cont_group(self) -> _Collapsible:
        g = _Collapsible("CONT 段（连续驱动 / F0 检测 / 谐振跟踪）", False)
        box = QWidget()
        form = QFormLayout(box)

        self.sp_f0 = QDoubleSpinBox()
        self.sp_f0.setRange(94.0, 3000.0)
        self.sp_f0.setDecimals(1)
        self.sp_f0.setSingleStep(1.0)
        self.sp_f0.setSuffix(" Hz")
        self.sp_f0.setToolTip("寄存器 0x18 · F_PRE，范围 1..255（默认 141）。\n"
                              "公式：F_PRE = round(24000 / F0)，F0 = 24000 / F_PRE。\n"
                              "界面会显示取整后**实际写入**的 F0。")
        self.sp_f0.valueChanged.connect(self._refresh_derived)
        form.addRow("目标 F0", self.sp_f0)
        self.lbl_f0_actual = QLabel("")
        self.lbl_f0_actual.setStyleSheet("color:#5fa8d3;")
        form.addRow("", self.lbl_f0_actual)

        self.sl_drv1, self.lbl_drv1 = self._level_slider(
            "寄存器 0x1C bit6:0 · DRV1_LVL，范围 0..127（默认 127）。\n"
            "空载输出电压 ≈ VDD × L / 128（软件调节模式）。\n"
            "满幅判定阈值：> 96 视为满幅（协议 §11）。写这一字节必须读-改-写保住 bit7 TRACK_EN。")
        form.addRow("驱动电平 1 DRV1_LVL", self._slider_row(self.sl_drv1, self.lbl_drv1))
        self.sl_drv2, self.lbl_drv2 = self._level_slider(
            "寄存器 0x1D bit6:0 · DRV2_LVL，范围 0..127（默认 80）。\n"
            "空载输出电压 ≈ VDD × L / 128。")
        form.addRow("驱动电平 2 DRV2_LVL", self._slider_row(self.sl_drv2, self.lbl_drv2))

        self.sp_drv1_time = self._spin(0, 255, "寄存器 0x1E · DRV1_TIME，范围 0..255（默认 4）。\n"
                                              "第一次驱动的半周期数。")
        form.addRow("驱动时间 1（半周期）", self.sp_drv1_time)
        self.sp_drv2_time = self._spin(0, 255, "寄存器 0x1F · DRV2_TIME，范围 0..255（默认 6）。\n"
                                              "第二次驱动的半周期数。")
        form.addRow("驱动时间 2（半周期）", self.sp_drv2_time)

        self.sp_drv_width = self._spin(
            0, 255, "寄存器 0x19 · DRV_WIDTH，范围 0..255（默认 106）。\n"
                    "公式：t = code / 48000 秒。必须小于 F0 的半周期，否则波形重叠（越界会红框）。")
        self.sp_drv_width.valueChanged.connect(self._refresh_derived)
        self.lbl_drv_width = QLabel("")
        form.addRow("驱动宽度 DRV_WIDTH", self.sp_drv_width)
        form.addRow("", self.lbl_drv_width)

        self.sp_brk_gain = self._spin(0, 15, "寄存器 0x1B bit3:0 · BRK_GAIN，范围 0..15（默认 8）。")
        form.addRow("刹车增益 BRK_GAIN", self.sp_brk_gain)

        self.sp_brk_time = self._spin(
            0, 255, "寄存器 0x20 · BRK_TIME，范围 0..255（默认 8），单位：刹车半周期数。\n"
                    "★ =0 时自动刹车引擎失效（数据手册明确警告）。")
        self.sp_brk_time.valueChanged.connect(self._refresh_derived)
        form.addRow("刹车时间 BRK_TIME", self.sp_brk_time)

        self.chk_track = QCheckBox("谐振跟踪 TRACK_EN（默认开）")
        self.chk_track.setToolTip("寄存器 0x1C bit7 · TRACK_EN，默认 1。\n"
                                  "实时跟踪 LRA 谐振点；实时幅度滑条用读-改-写保住这一位。")
        form.addRow("", self.chk_track)

        self.sp_track_margin = self._spin(0, 255,
                                          "寄存器 0x21 · TRACK_MARGIN，范围 0..255（默认 0x11=17）。\n"
                                          "越小跟踪越准，但环路越不稳定。")
        form.addRow("跟踪裕量 TRACK_MARGIN", self.sp_track_margin)

        self.chk_f0det = QCheckBox("F0 检测使能 EN_F0_DET")
        self.chk_f0det.setToolTip("寄存器 0x17 bit5 · EN_F0_DET。\n"
                                  "★ =1 时自动刹车引擎不工作（数据手册明确警告）。")
        self.chk_f0det.stateChanged.connect(self._refresh_derived)
        form.addRow("", self.chk_f0det)

        self.cmb_edge = QComboBox()
        for code in sorted(P.EDGE_FRE_TABLE):
            self.cmb_edge.addItem(P.EDGE_FRE_TABLE[code], code)
        self.cmb_edge.setToolTip("寄存器 0x17 bit3:0 · EDGE_FRE。\n"
                                 "b0000~b0111 = 无边缘控制；b1000~b1111 = 200/210/260/280/300/600/700/800 Hz。")
        form.addRow("边缘频率 EDGE_FRE", self.cmb_edge)

        self.cmb_sin = QComboBox()
        self.cmb_sin.addItem("sin", 0)
        self.cmb_sin.addItem("cos", 1)
        self.cmb_sin.setToolTip("寄存器 0x17 bit4 · SIN_MODE，默认 0 = sin。")
        form.addRow("边缘波形类型 SIN_MODE", self.cmb_sin)

        self.sp_wait_num = self._spin(
            0, 255, "寄存器 0x1A · WAIT_NUM，范围 0..255（默认 6）。\n"
                    "公式：t = code / 48000 秒（循环衰减时间）。\n"
                    "★ CONT 下 >0 会在每个驱动段之间插停顿，听感是“一块一块的”；"
                    "要持续振动必须置 0。")
        form.addRow("循环衰减 WAIT_NUM", self.sp_wait_num)

        self.btn_sustain = QPushButton("一键持续振动参数")
        self.btn_sustain.setToolTip(
            "★ 实测配方（2026-09，AW86234FCR ×4）：\n"
            "CONT 模式下要得到**连续平稳**的持续振动，四个参数缺一不可：\n"
            "    DRV1_TIME = 255（驱动段拉满）\n"
            "    DRV2_TIME = 0\n"
            "    BRK_TIME  = 0（不插主动刹车段）\n"
            "    WAIT_NUM  = 0（★ 关键：不置 0 就会一段一段）\n"
            "只改这四个值，并把模式切到 CONT；F0 / 驱动电平 / TRACK_EN 保持你当前的设置。")
        self.btn_sustain.clicked.connect(self._apply_sustain_preset)
        form.addRow("", self.btn_sustain)

        self.lbl_cont_warn = QLabel("")
        self.lbl_cont_warn.setWordWrap(True)
        self.lbl_cont_warn.setStyleSheet("color:#e6b422;")
        form.addRow(self.lbl_cont_warn)
        g.add(box)
        return g

    def _level_slider(self, tip: str) -> Tuple[QSlider, QLabel]:
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 127)
        sl.setToolTip(tip)
        lbl = QLabel("—")
        lbl.setStyleSheet("font-family:Consolas,monospace;")
        lbl.setMinimumWidth(140)
        sl.valueChanged.connect(self._refresh_derived)
        return sl, lbl

    def _slider_row(self, sl: QSlider, lbl: QLabel) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(sl, 1)
        h.addWidget(lbl)
        return w

    def _spin(self, lo: int, hi: int, tip: str, suffix: str = "") -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        if suffix:
            sp.setSuffix(suffix)
        sp.setToolTip(tip)
        return sp

    # ------------------------------------------------------------------
    # TRIG
    # ------------------------------------------------------------------

    def _build_trig_group(self) -> _Collapsible:
        g = _Collapsible("TRIG 段（硬件触发）", False)
        box = QWidget()
        form = QFormLayout(box)

        self.cmb_trigger = QComboBox()
        for name in K.TrigParams.TRIG_MODES:
            self.cmb_trigger.addItem(name)
        self.cmb_trigger.setToolTip(
            "联动设置 4 个位（数据手册 Table 4 真值表）：\n"
            "上升沿：TRG1_LEV=0 TRG1_POS=1 TRG1_NEG=0（0x33 bit7 / 0x36 bit7）\n"
            "下降沿：TRG1_LEV=0 TRG1_POS=0 TRG1_NEG=1\n"
            "双边沿：TRG1_LEV=0 TRG1_POS=1 TRG1_NEG=1\n"
            "高电平：TRG1_LEV=1 TRG1_POLAR=0（0x39 bit4 / bit5）\n"
            "低电平：TRG1_LEV=1 TRG1_POLAR=1")
        self.cmb_trigger.currentIndexChanged.connect(self._refresh_derived)
        form.addRow("触发方式", self.cmb_trigger)

        self.lbl_trig_bits = QLabel("")
        self.lbl_trig_bits.setStyleSheet("color:#5fa8d3; font-family:Consolas,monospace;")
        form.addRow("", self.lbl_trig_bits)

        self.sp_seq_p = self._spin(1, 127, "寄存器 0x33 bit6:0 · TRG1SEQ_P，范围 1..127（默认 1）。\n"
                                           "正沿触发时播放的波形号（SRAM 波形库里的编号）。")
        form.addRow("正沿波形号", self.sp_seq_p)
        self.sp_seq_n = self._spin(1, 127, "寄存器 0x36 bit6:0 · TRG1SEQ_N，范围 1..127（默认 1）。\n"
                                           "负沿触发时播放的波形号。")
        form.addRow("负沿波形号", self.sp_seq_n)

        self.sl_trg_gain = QSlider(Qt.Horizontal)
        self.sl_trg_gain.setRange(0, 255)
        self.sl_trg_gain.setToolTip("寄存器 0x43 · TRG_GAIN，范围 0..255（默认 0x80=128）。\n"
                                    "公式：倍率 = code / 128。★ 0x43 是 TRIG 播放增益，**不是刹车位**。")
        self.sl_trg_gain.valueChanged.connect(self._refresh_derived)
        self.lbl_trg_gain = QLabel("—")
        self.lbl_trg_gain.setStyleSheet("font-family:Consolas,monospace;")
        form.addRow("TRIG 增益", self._slider_row(self.sl_trg_gain, self.lbl_trg_gain))

        self.sp_start_dly = self._spin(
            0, 255, "寄存器 0x3C · START_DLY，范围 0..255（默认 8）。\n"
                    "公式：t = code / 48000 秒（默认 8 ≈ 166.7 µs）。\n"
                    "★ 测 A6（TRIG→起振延时）时设为 0，用右边的『设为 0』按钮。")
        self.sp_start_dly.valueChanged.connect(self._refresh_derived)
        dly_row = QWidget()
        dh = QHBoxLayout(dly_row)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.addWidget(self.sp_start_dly, 1)
        b_zero = QPushButton("设为 0")
        b_zero.setToolTip("测 A6 TRIG→起振延时时用（把 START_DLY 归零，减少固定延时）")
        b_zero.clicked.connect(lambda: self.sp_start_dly.setValue(0))
        dh.addWidget(b_zero)
        self.lbl_dly = QLabel("")
        dh.addWidget(self.lbl_dly)
        form.addRow("启动延时 START_DLY", dly_row)

        self.sp_prio = self._spin(0, 3, "寄存器 0x3E bit1:0 · TRG1_PRIO，范围 0..3，默认 3（最高）。\n"
                                        "用于 GO 播放与 TRIG 播放互相打断时的仲裁。")
        form.addRow("优先级 TRG1_PRIO", self.sp_prio)

        self.chk_brk = QCheckBox("刹车使能 TRG1_BRK（只读 · 强制为开）")
        self.chk_brk.setChecked(True)
        self.chk_brk.setEnabled(False)
        self.chk_brk.setToolTip(_TRIG_BRK_TIP)
        form.addRow("", self.chk_brk)

        self.chk_stop = QCheckBox("允许立即停止 TRG1_STOP（只读 · 强制为开）")
        self.chk_stop.setChecked(True)
        self.chk_stop.setEnabled(False)
        self.chk_stop.setToolTip(_TRIG_STOP_TIP)
        form.addRow("", self.chk_stop)

        note = QLabel("以上两位由协议 §11.1 强制：TRIG 模式下若 TRG1_BRK=0 则停止后无自动刹车，"
                      "若 TRG1_STOP=0 则 TRIG 播放根本无法被停止。因此界面不提供关闭入口，"
                      "SET_TRIG_PARAMS 收到 0 也会被拒绝（ERR_BAD_PARAM）。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#e6b422;")
        form.addRow(note)

        warn = QLabel("★ 新硬件每路一条独立 TRIG：硬触发下各通道互不影响（老硬件才存在 "
                      "CH5/6/7 共用 GPIO14 的联动）。TRIG 下 A1 不再输出中断，"
                      "播放结束只能靠轮询状态寄存器。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#e6b426;")
        form.addRow(warn)
        g.add(box)
        return g

    # ------------------------------------------------------------------
    # RTP
    # ------------------------------------------------------------------

    def _build_rtp_group(self) -> _Collapsible:
        g = _Collapsible("RTP 段（主机流式喂数据）", False)
        box = QWidget()
        form = QFormLayout(box)

        self.cmb_rate = QComboBox()
        self.cmb_rate.addItem("12 kHz", C.RATE_12K)
        self.cmb_rate.addItem("12 kHz（备用编码 b11）", C.RATE_12K_ALT)
        self.cmb_rate.addItem("24 kHz（临界，需实测）", C.RATE_24K)
        self.cmb_rate.addItem("48 kHz（本板不可用）", C.RATE_48K)
        self.cmb_rate.setToolTip("寄存器 0x46 bit1:0 · WAVDAT_MODE。\n"
                                 "b10/b11 = 12 kHz（≥250 kHz I2C）；b00 = 24 kHz（≥400 kHz，临界）；\n"
                                 "b01 = 48 kHz（需 1 MHz，本板不可用）。")
        self.cmb_rate.currentIndexChanged.connect(self._refresh_derived)
        form.addRow("采样率 WAVDAT_MODE", self.cmb_rate)

        self.chk_auto_sin = QCheckBox("Auto Dynamic Sine")
        self.chk_auto_sin.setToolTip("寄存器 0x31 bit2（Profile 里是 rtp_flags bit0）。\n"
                                     "由芯片内部生成半周期正弦，I2C 负载远低于流式 RTP。")
        form.addRow("", self.chk_auto_sin)

        self.chk_sin96 = QCheckBox("96 kHz 正弦")
        self.chk_sin96.setToolTip("寄存器 0x31 bit3（Profile 里是 rtp_flags bit1）。")
        form.addRow("", self.chk_sin96)

        self.ed_base = QLineEdit("0x0800")
        self.ed_base.setToolTip("寄存器 0x2C bit3:0（高）+ 0x2D（低）· BASE_ADDR，范围 0x0000..0x0FFF。\n"
                                "默认 0x0800，把 3 kB SRAM 分成波形库区与 RTP FIFO 区。")
        self.ed_base.editingFinished.connect(self._refresh_derived)
        form.addRow("Base Addr（十六进制）", self.ed_base)

        self.sp_fifo_ae = self._spin(0, 4095,
                                     "寄存器 0x2E bit7:4（高）+ 0x2F（低）· FIFO_AE，范围 0..4095，默认 512。\n"
                                     "公式：FIFO_AE = FIFO_AEH × 256 + FIFO_AEL。")
        form.addRow("FIFO 近空 FIFO_AE", self.sp_fifo_ae)
        self.sp_fifo_af = self._spin(0, 4095,
                                     "寄存器 0x2E bit3:0（高）+ 0x30（低）· FIFO_AF，范围 0..4095，默认 1536。\n"
                                     "公式：FIFO_AF = FIFO_AFH × 256 + FIFO_AFL。")
        form.addRow("FIFO 近满 FIFO_AF", self.sp_fifo_af)

        note = QLabel("本板结论：RTP 只做 12 kHz 可行性验证（C3）。24 kHz 处于临界，"
                      "8 路 2 ms 周期轮询会把 400 kHz 总线跑满，需要加大周期。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        form.addRow(note)
        g.add(box)
        return g

    # ------------------------------------------------------------------
    # 电压补偿
    # ------------------------------------------------------------------

    def _build_vbat_group(self) -> _Collapsible:
        g = _Collapsible("电压补偿（VBAT）", False)
        box = QWidget()
        form = QFormLayout(box)
        self.cmb_vbat_mode = QComboBox()
        self.cmb_vbat_mode.addItem("软件调节（输出 = VDD × L / 128）", 0)
        self.cmb_vbat_mode.addItem("硬件调节（输出 = VBAT_REF × L / 128）", 1)
        self.cmb_vbat_mode.setToolTip("寄存器 0x4E bit6 · VBAT_MODE，默认 0 = 软件调节。\n"
                                      "硬件调节时若 (VBAT_REF × L)/VDD > 128，输出被限到 VDD。")
        form.addRow("VBAT 模式", self.cmb_vbat_mode)
        self.cmb_vbat_ref = QComboBox()
        for code in sorted(P.VBAT_REF_TABLE):
            self.cmb_vbat_ref.addItem(f"{P.VBAT_REF_TABLE[code]:.1f} V", code)
        self.cmb_vbat_ref.setToolTip("寄存器 0x4F bit6:4 · VBAT_REF，默认 b010 = 4.0 V。\n"
                                     "档位：3.3 / 3.6 / 4.0 / 4.2 / 4.5 / 4.8 / 5.0 / 5.5 V。")
        form.addRow("VBAT 参考", self.cmb_vbat_ref)
        g.add(box)
        return g

    # ==================================================================
    # 模式切换显示
    # ==================================================================

    def _current_mode(self) -> int:
        item = self.mode_list.currentItem()
        return int(item.data(Qt.UserRole)) if item else C.MODE_RAM

    def _on_gain(self, value: int) -> None:
        """增益滑块与微调双向同步（避免互相递归触发）。"""
        if self._loading:
            return
        for w in (self.sl_gain, self.sp_gain):
            if w.value() != value:
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)
        self._refresh_derived()

    def _on_mode_changed(self, *_a) -> None:
        mode = self._current_mode()
        self.mode_desc.setText(_MODE_DESC.get(mode, ""))
        self.group_ram.setVisible(mode == C.MODE_RAM)
        self.group_cont.setVisible(mode == C.MODE_CONT)
        self.group_trig.setVisible(mode == C.MODE_TRIG)
        self.group_rtp.setVisible(mode == C.MODE_RTP)
        self._refresh_derived()

    def _apply_sustain_preset(self) -> None:
        """套用"持续振动"配方（`首轮实测记录_CH4-CH7.md` §2.20d）。

        只动决定输出包络的四个字段 + 把模式切到 CONT；
        F0 / 驱动电平 / TRACK_EN 保持用户当前设置，
        避免"点一下把调好的频率也改掉"。
        """
        self._loading = True
        try:
            for row in range(self.mode_list.count()):
                item = self.mode_list.item(row)
                if item is not None and int(item.data(Qt.UserRole)) == C.MODE_CONT:
                    self.mode_list.setCurrentRow(row)
                    break
            self.sp_drv1_time.setValue(255)
            self.sp_drv2_time.setValue(0)
            self.sp_brk_time.setValue(0)
            self.sp_wait_num.setValue(0)
        finally:
            self._loading = False
        self._on_mode_changed()
        self.ctx.log("已套用“持续振动”配方：DRV1_TIME=255 / DRV2_TIME=0 / BRK_TIME=0 / "
                     "WAIT_NUM=0，模式已切到 CONT。下发到通道后按“播放”即可持续振动。",
                     "user")

    # ==================================================================
    # 模型 ↔ 界面
    # ==================================================================

    def _refresh_derived(self, *_a) -> None:
        """刷新所有"换算/警告"提示，并做本地校验。"""
        if self._loading:
            return
        gain = self.sp_gain.value()
        self.lbl_gain.setText(f"{gain / 128.0:.2f}×" + ("（满幅）" if gain > C.FULL_AMP_RAM_GAIN else ""))

        mode = self._current_mode()
        if mode == C.MODE_TRIG:
            self.lbl_mode_reg.setText("0x08 PLAY_MODE 保持不变（TRIG 由 0x45 bit6:4 TRG1_MODE = b011 选择）")
        else:
            self.lbl_mode_reg.setText(f"0x08 PLAY_MODE = b{mode:02b} ({C.mode_name(mode)})")

        f0 = self.sp_f0.value()
        f_pre = max(1, min(255, int(round(24000.0 / f0)))) if f0 > 0 else 255
        actual = 24000.0 / f_pre
        self.lbl_f0_actual.setText(f"F_PRE = {f_pre} → 实际 F0 = {actual:.1f} Hz"
                                   + ("（取整有偏差）" if abs(actual - f0) > 1.0 else ""))

        width_us = self.sp_drv_width.value() / 48000.0 * 1e6
        half_us = (1e6 / actual / 2.0) if actual else 0.0
        ok = half_us == 0.0 or width_us < half_us
        self.lbl_drv_width.setText(f"t = {width_us:.1f} µs（F0 半周期 {half_us:.1f} µs）"
                                   + ("" if ok else "  ★ 越界：驱动宽度必须小于半周期"))
        self.sp_drv_width.setStyleSheet("" if ok else "border:1px solid #ff5555;")

        vdd = self.amp_slider.vdd_spin.value() if hasattr(self, "amp_slider") else 5.0
        for sl, lbl in ((self.sl_drv1, self.lbl_drv1), (self.sl_drv2, self.lbl_drv2)):
            v = sl.value()
            lbl.setText(f"{v} ≈ {v * vdd / 128.0:.2f} V"
                        + ("·满幅" if v > C.FULL_AMP_CONT_DRV1_LVL else ""))

        self.lbl_trg_gain.setText(f"{self.sl_trg_gain.value()} = "
                                  f"{self.sl_trg_gain.value() / 128.0:.2f}×")
        self.lbl_dly.setText(f"{self.sp_start_dly.value() / 48000.0 * 1e6:.1f} µs")

        tp = K.TrigParams()
        tp.apply_trigger_mode(self.cmb_trigger.currentText())
        self.lbl_trig_bits.setText(
            f"TRG1_LEV={tp.trig1_lev} TRG1_POLAR={tp.trig1_polar} "
            f"TRG1_POS={tp.trig1_pos_en} TRG1_NEG={tp.trig1_neg_en} → {tp.trigger_mode}")

        waitslot = int(self.cmb_waitslot.currentData())
        unit = (1, 8, 64, 512)[waitslot]
        fs = C.RATE_HZ.get(int(self.cmb_rate.currentData()), 24000)
        ms = unit / fs * 1000.0
        self.lbl_ram_calc.setText(
            f"等待单位：1 码 = {unit} 个采样周期 @ {fs // 1000} kHz ≈ {ms * 1000:.1f} µs；"
            f"主循环 {self.sp_mainloop.value()} 次"
            + ("（无限）" if self.sp_mainloop.value() >= 16 else ""))

        warns: List[str] = []
        if self.sp_brk_time.value() == 0:
            warns.append("BRK_TIME = 0：自动刹车引擎会失效。")
        if self.chk_f0det.isChecked():
            warns.append("EN_F0_DET = 1：自动刹车引擎不工作（F0 检测期间属正常）。")
        if self.chk_brk.isChecked() is False or self.chk_stop.isChecked() is False:
            warns.append("TRG1_BRK / TRG1_STOP 必须为 1，界面已强制打开。")
        # ★ 持续振动检查（2026-09 实机结论，见首轮实测记录 §2.20d）
        if self._current_mode() == C.MODE_CONT:
            if self.sp_wait_num.value() > 0:
                warns.append(f"WAIT_NUM={self.sp_wait_num.value()} → 每个驱动段之间会插停顿，"
                             "听到的是“一块一块的”；要持续振动请点上面的"
                             "“一键持续振动参数”。")
            elif self.sp_drv1_time.value() < 100:
                warns.append(f"DRV1_TIME={self.sp_drv1_time.value()} 太短，"
                             "持续振动建议 255。")
            elif self.sp_drv2_time.value() or self.sp_brk_time.value():
                warns.append("DRV2_TIME / BRK_TIME 非 0 → 稳态里会插低幅度段和主动刹车，"
                             "持续振动建议都置 0。")
        self.lbl_cont_warn.setText(" ".join(warns))
        self.lbl_trap.setText(
            "默认值陷阱提醒：0x45 默认 0x73（TRG1_MODE=7、INTN_PIN=1）、0x33 默认 0x01"
            "（TRG1_POS=0）、0x3A 默认 0x08（TRG1_STOP=0）—— 下发 Profile 时这三处会被一并写好。")

        # 48 kHz 置灰 + 写明原因
        model = self.cmb_rate.model()
        for row in range(self.cmb_rate.count()):
            if self.cmb_rate.itemData(row) == C.RATE_48K:
                item = model.item(row)
                if item is not None:
                    item.setEnabled(False)
                    item.setToolTip("不可用：" + _RTP48_REASON)

    def _sync_model(self) -> P.Profile:
        """把界面上的值收集成一个 Profile。"""
        p = P.Profile()
        p.mode = self._current_mode()
        p.gain = self.sp_gain.value()
        p.brk_en = 1 if self.chk_auto_brk.isChecked() else 0
        p.stop_mode = int(self.cmb_stop.currentData())
        p.vbat_mode = int(self.cmb_vbat_mode.currentData())
        p.vbat_ref = int(self.cmb_vbat_ref.currentData())

        # RAM
        for i in range(8):
            v = self._ram_seq_spins[i].value() & 0x7F
            if self._ram_wait_checks[i].isChecked():
                v |= 0x80
            p.wavseq[i] = v
        p.mainloop = max(0, min(15, self.sp_mainloop.value() - 1))
        p.waitslot = int(self.cmb_waitslot.currentData())
        for i in range(8):
            code = max(0, min(15, self._seq_loop_spins[i].value() - 1))
            byte_index = i // 2
            if i % 2 == 0:
                p.seq_loop[byte_index] = (p.seq_loop[byte_index] & 0x0F) | (code << 4)
            else:
                p.seq_loop[byte_index] = (p.seq_loop[byte_index] & 0xF0) | code

        # CONT
        p.set_f0_hz(self.sp_f0.value())
        p.drv1_lvl = self.sl_drv1.value()
        p.drv2_lvl = self.sl_drv2.value()
        p.drv1_time = self.sp_drv1_time.value()
        p.drv2_time = self.sp_drv2_time.value()
        p.drv_width = self.sp_drv_width.value()
        p.brk_gain = self.sp_brk_gain.value()
        p.brk_time = self.sp_brk_time.value()
        p.track_en = 1 if self.chk_track.isChecked() else 0
        p.track_margin = self.sp_track_margin.value()
        p.en_f0_det = 1 if self.chk_f0det.isChecked() else 0
        p.edge_fre = int(self.cmb_edge.currentData())
        p.sin_mode = int(self.cmb_sin.currentData())
        p.wait_num = self.sp_wait_num.value()

        # TRIG
        tp = K.TrigParams()
        tp.apply_trigger_mode(self.cmb_trigger.currentText())
        p.trig1_pos_en, p.trig1_neg_en = tp.trig1_pos_en, tp.trig1_neg_en
        p.trig1_lev, p.trig1_polar = tp.trig1_lev, tp.trig1_polar
        p.trig1_seq_p = self.sp_seq_p.value()
        p.trig1_seq_n = self.sp_seq_n.value()
        p.trg_gain = self.sl_trg_gain.value()
        p.start_dly = self.sp_start_dly.value()
        p.trig1_prio = self.sp_prio.value()
        p.trig1_brk = 1
        p.trig1_stop = 1
        p.trig1_mode = 3
        p.intn_pin = 0

        # RTP
        p.wavdat_mode = int(self.cmb_rate.currentData())
        flags = 0
        if self.chk_auto_sin.isChecked():
            flags |= 0x01
        if self.chk_sin96.isChecked():
            flags |= 0x02
        p.rtp_flags = flags
        p.base_addr = self._parse_base_addr()
        p.fifo_ae = self.sp_fifo_ae.value()
        p.fifo_af = self.sp_fifo_af.value()
        return p

    def _parse_base_addr(self) -> int:
        text = self.ed_base.text().strip()
        try:
            value = int(text, 16) if text.lower().startswith("0x") else int(text, 16)
        except ValueError:
            value = 0x0800
        return max(0x0000, min(0x0FFF, value))

    def _sync_ui(self, p: P.Profile) -> None:
        """把 Profile 刷到界面上（不触发模型回写）。"""
        self._loading = True
        try:
            row = {C.MODE_RAM: 0, C.MODE_CONT: 1, C.MODE_TRIG: 2, C.MODE_RTP: 3}.get(p.mode, 0)
            self.mode_list.setCurrentRow(row)
            self.sp_gain.setValue(p.gain)
            self.sl_gain.setValue(p.gain)
            self.chk_auto_brk.setChecked(bool(p.brk_en))
            idx = self.cmb_stop.findData(p.stop_mode)
            if idx >= 0:
                self.cmb_stop.setCurrentIndex(idx)

            for i in range(8):
                v = p.wavseq[i] if i < len(p.wavseq) else 0
                self._ram_wait_checks[i].setChecked(bool(v & 0x80))
                self._ram_seq_spins[i].setValue(v & 0x7F)
            self.sp_mainloop.setValue(min(16, p.mainloop + 1))
            idx = self.cmb_waitslot.findData(p.waitslot)
            if idx >= 0:
                self.cmb_waitslot.setCurrentIndex(idx)
            for i in range(8):
                bi, half = i // 2, i % 2
                byte = p.seq_loop[bi] if bi < len(p.seq_loop) else 0
                code = (byte >> 4) if half == 0 else (byte & 0x0F)
                self._seq_loop_spins[i].setValue(min(16, code + 1))

            self.sp_f0.setValue(max(94.0, min(3000.0, p.f0_hz or 170.0)))
            self.sl_drv1.setValue(p.drv1_lvl)
            self.sl_drv2.setValue(p.drv2_lvl)
            self.sp_drv1_time.setValue(p.drv1_time)
            self.sp_drv2_time.setValue(p.drv2_time)
            self.sp_drv_width.setValue(p.drv_width)
            self.sp_brk_gain.setValue(p.brk_gain)
            self.sp_brk_time.setValue(p.brk_time)
            self.chk_track.setChecked(bool(p.track_en))
            self.sp_track_margin.setValue(p.track_margin)
            self.chk_f0det.setChecked(bool(p.en_f0_det))
            idx = self.cmb_edge.findData(p.edge_fre)
            if idx >= 0:
                self.cmb_edge.setCurrentIndex(idx)
            idx = self.cmb_sin.findData(p.sin_mode)
            if idx >= 0:
                self.cmb_sin.setCurrentIndex(idx)
            self.sp_wait_num.setValue(p.wait_num)

            tp = K.TrigParams(trig1_pos_en=p.trig1_pos_en, trig1_seq_p=p.trig1_seq_p,
                              trig1_neg_en=p.trig1_neg_en, trig1_seq_n=p.trig1_seq_n,
                              trig1_polar=p.trig1_polar, trig1_lev=p.trig1_lev)
            idx = self.cmb_trigger.findText(tp.trigger_mode)
            if idx < 0:
                idx = 0
            self.cmb_trigger.setCurrentIndex(idx)
            self.sp_seq_p.setValue(max(1, min(127, p.trig1_seq_p or 1)))
            self.sp_seq_n.setValue(max(1, min(127, p.trig1_seq_n or 1)))
            self.sl_trg_gain.setValue(p.trg_gain)
            self.sp_start_dly.setValue(p.start_dly)
            self.sp_prio.setValue(p.trig1_prio)
            self.chk_brk.setChecked(True)
            self.chk_stop.setChecked(True)

            idx = self.cmb_rate.findData(p.wavdat_mode)
            if idx >= 0:
                self.cmb_rate.setCurrentIndex(idx)
            self.chk_auto_sin.setChecked(bool(p.rtp_flags & 0x01))
            self.chk_sin96.setChecked(bool(p.rtp_flags & 0x02))
            self.ed_base.setText(f"0x{p.base_addr:04X}")
            self.sp_fifo_ae.setValue(p.fifo_ae)
            self.sp_fifo_af.setValue(p.fifo_af)

            idx = self.cmb_vbat_mode.findData(p.vbat_mode)
            if idx >= 0:
                self.cmb_vbat_mode.setCurrentIndex(idx)
            idx = self.cmb_vbat_ref.findData(p.vbat_ref)
            if idx >= 0:
                self.cmb_vbat_ref.setCurrentIndex(idx)
        finally:
            self._loading = False
        self._refresh_derived()

    # ==================================================================
    # 下发 / 读回 / 比对
    # ==================================================================

    def _send(self) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在顶部通道矩阵里勾选要下发的通道。")
            return
        profile = self._sync_model()
        problems = profile.validate()
        warns = profile.warnings()
        blocks = [f"⚠ {_FIELD_CN.get(n, n)}：{why}" for n, why in problems]
        notes = [f"· {_FIELD_CN.get(n, n)}：{why}" for n, why in warns]
        if problems:
            if not self.ctx.confirm(self, "Profile 校验有告警",
                                    "本地校验发现以下问题，仍要下发吗？",
                                    "\n".join(blocks + notes)):
                self.ctx.log("用户取消下发（本地校验未通过）", "user")
                return
        elif notes:
            # ★ 非阻塞提示（例如"这样配出来不是持续振动"）：不打断下发，但要能看见
            self.ctx.log("Profile 提示：" + "；".join(
                f"{_FIELD_CN.get(n, n)} {why}" for n, why in warns), "warn")
        dev = self.ctx.device
        mask = self.ctx.selected_mask

        def job(progress):
            results = []
            for i, ch in enumerate(chans):
                diffs = dev.set_profile_verify(ch, profile)
                results.append((ch, diffs))
                progress((i + 1, len(chans), ch))
            return results

        def done(results) -> None:
            self.mismatch.setRowCount(0)
            total_bad = 0
            for ch, diffs in results:
                if not diffs:
                    continue
                for field, expect, actual in diffs:
                    total_bad += 1
                    row = self.mismatch.rowCount()
                    self.mismatch.insertRow(row)
                    name = _FIELD_CN.get(field, field)
                    spec = P.FIELD_SPECS.get(field)
                    note_text = spec[3] if spec else ""
                    for col, text in enumerate([f"CH{ch}", f"{name}（{field}）",
                                                str(expect), str(actual), note_text]):
                        item = QTableWidgetItem(text)
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(Qt.GlobalColor.darkRed)
                        self.mismatch.setItem(row, col, item)
            if total_bad:
                self.result_label.setText(
                    f"下发完成，但回读发现 {total_bad} 处不一致（见下表，红色）。"
                    "常见原因：字段被固件限幅、器件处于其他模式、或需要重新 GO 才生效。")
                self.result_label.setStyleSheet("color:#ff5555;")
                self.ctx.log(f"下发回读比对：{total_bad} 处不一致", "warn")
            else:
                self.result_label.setText(
                    f"下发完成：{len(results)} 路全部字段回读一致 ✔（{mask_to_text(mask)}）")
                self.result_label.setStyleSheet("color:#8fbf6f;")
                self.ctx.log(f"下发并回读比对通过：{mask_to_text(mask)}", "user")

        def on_progress(payload):
            i, total, ch = payload
            self.result_label.setText(f"下发中… {i}/{total}（CH{ch}）")

        self.ctx.log(f"下发 Profile（{mask_to_text(mask)}）", "user")
        self.ctx.task(job, on_done=done, on_progress=on_progress, name="SET_PROFILE")

    def _read_back(self) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在顶部通道矩阵里勾选通道。")
            return
        dev = self.ctx.device
        ch = chans[0]

        def job(progress):
            return dev.get_profile(ch)

        def done(p: P.Profile) -> None:
            self.profile = p
            self._sync_ui(p)
            self.result_label.setText(f"已从 CH{ch} 读回 Profile 并刷新界面。")
            self.result_label.setStyleSheet("color:#8fbf6f;")
            self.ctx.log(f"GET_PROFILE(CH{ch})：mode={C.mode_name(p.mode)} gain={p.gain} "
                         f"drv1={p.drv1_lvl} f_pre={p.f_pre}", "info")

        self.ctx.task(job, on_done=done, name="GET_PROFILE")

    def _restore_default(self) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在顶部通道矩阵里勾选通道。")
            return
        if not self.ctx.confirm(self, "恢复默认 Profile",
                                f"确认对 {mask_to_text(self.ctx.selected_mask)} 执行 "
                                f"SET_DEFAULT_PROFILE？",
                                "会覆盖当前全部参数（含 TRIG 的 0x45/0x33/0x3A 相关位）。",
                                danger=True):
            return
        dev = self.ctx.device

        def job(progress):
            out = []
            for ch in chans:
                dev.set_default_profile(ch)
                out.append((ch, dev.get_profile(ch)))
            return out

        def done(rows) -> None:
            if rows:
                self._sync_ui(rows[0][1])
            self.ctx.log(f"已恢复默认 Profile：{mask_to_text(self.ctx.selected_mask)}", "user")
            self.result_label.setText("已恢复默认 Profile 并读回。")
            self.result_label.setStyleSheet("color:#8fbf6f;")

        self.ctx.task(job, on_done=done, name="SET_DEFAULT_PROFILE")

    # ==================================================================
    # Profile 存取
    # ==================================================================

    def _load_preset(self, item: QListWidgetItem) -> None:
        factory = P.PRESET_FACTORIES.get(item.text())
        if factory is None:
            return
        p = factory()
        self._sync_ui(p)
        self.ctx.log(f"已加载预设 Profile：{item.text()}", "user")

    def _save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存 Profile", "profile.json",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            self._sync_model().save(path)
            self.ctx.log(f"Profile 已保存到 {path}", "user")
        except Exception as exc:
            self.ctx.error(self, "保存失败", "写入 Profile 失败。", str(exc))

    def _open_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载 Profile", "",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            p = P.Profile.load(path)
        except Exception as exc:
            self.ctx.error(self, "加载失败", f"无法解析 {os.path.basename(path)}。", str(exc))
            return
        problems = p.validate()
        warns = p.warnings()
        if problems or warns:
            self.ctx.warn(self, "Profile 校验告警",
                          "文件里的字段有越界或危险值，请核对后再下发。",
                          "\n".join([f"⚠ {_FIELD_CN.get(n, n)}：{why}" for n, why in problems] +
                                    [f"· {_FIELD_CN.get(n, n)}：{why}" for n, why in warns]))
        self._sync_ui(p)
        self.ctx.log(f"已加载 Profile：{path}", "user")

    def _save_from_device(self) -> None:
        if not self.ctx.require_device(self):
            return
        chans = self.ctx.selected_channels()
        if not chans:
            self.ctx.warn(self, "没有选中通道", "请先在顶部通道矩阵里勾选通道。")
            return
        dev = self.ctx.device
        ch = chans[0]

        def job(progress):
            return dev.get_profile(ch)

        def done(p: P.Profile) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "另存设备里的 Profile",
                                                  f"CH{ch}_profile.json", "JSON (*.json)")
            if not path:
                return
            try:
                p.save(path)
                self.ctx.log(f"已把 CH{ch} 的 Profile 另存为 {path}", "user")
            except Exception as exc:
                self.ctx.error(self, "保存失败", "写入文件失败。", str(exc))

        self.ctx.task(job, on_done=done, name="GET_PROFILE")

    # ==================================================================
    # 连接 / 选择变化
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        for w in (self.btn_send, self.btn_read, self.btn_default):
            w.setEnabled(ok)

    def on_selection_changed(self, mask: int) -> None:
        self.amp_slider.set_channel(self.default_channel())


__all__ = ["PageModeParams"]
