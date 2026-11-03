# -*- coding: utf-8 -*-
"""通道矩阵控件：8 张通道卡片（勾选 / 在线灯 / CHIPID / 模式 / 播放中 / 单路探测）。

★ 关键行为（规格书 §5.2）：当 ``GET_CAPS`` 的 ``CAP_SHARED_TRIG_567``(bit12) = 1
且 CH5/CH6/CH7 处于 TRIG 模式时，三路**强制联动**——勾选任意一个，另外两个自动勾选
且加锁不可取消（共用 GPIO14）；切回 RAM/CONT/RTP 模式立即解锁。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from awtest.protocol import commands as C

_LOCK_REASON = ("CH5/6/7 共用 TRIG（GPIO14），硬触发下三路只能同时动作，"
                "因此不能单独取消勾选。切到 RAM / CONT / RTP 模式即可解除联动。")

_CHIPID_HINT = ("CHIPID 与预期的 0x2340 不符：可能是 AW86234AFCR 之外的变体，"
                "或该路读到了错误的器件地址。")

_MODE_COLORS = {
    C.MODE_RAM: "#5fa8d3",
    C.MODE_RTP: "#e6b422",
    C.MODE_CONT: "#8fbf6f",
    C.MODE_TRIG: "#ff9f43",
}

# 与 main_window 的 QSettings(ORG_NAME, APP_NAME) 保持一致（不能 import：
# main_window 会 import 本模块，反向 import 会成环）。
_ORG_NAME = "Glovity"
_APP_NAME = "AW86234-Testbench"


class ChannelCard(QFrame):
    """单个通道卡片。"""

    toggled = Signal(int, bool)
    probe_clicked = Signal(int)

    def __init__(self, chan: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.chan = chan
        self._compact = False
        self._mode = C.MODE_RAM
        self._present: Optional[bool] = None
        self._chipid: Optional[int] = None
        self._playing = False
        self._blink = False
        self._locked = False
        self._build()

    # ---- 构造 ----

    def _build(self) -> None:
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(2)

        top = QHBoxLayout()
        self.checkbox = QCheckBox(f"CH{self.chan}")
        self.checkbox.toggled.connect(self._on_toggle)
        top.addWidget(self.checkbox)
        top.addStretch(1)
        self.lock_label = QLabel("")
        self.lock_label.setToolTip(_LOCK_REASON)
        self.lock_label.setStyleSheet("color:#e6b422; font-size:14px;")
        top.addWidget(self.lock_label)
        root.addLayout(top)

        self.state_label = QLabel("在线：未探测")
        self.state_label.setStyleSheet("color:#888;")
        root.addWidget(self.state_label)

        self.chipid_label = QLabel("CHIPID —")
        self.chipid_label.setStyleSheet("color:#aaa; font-family:Consolas,monospace;")
        root.addWidget(self.chipid_label)

        bottom = QHBoxLayout()
        self.mode_label = QLabel("模式 RAM")
        self.mode_label.setStyleSheet(f"color:{_MODE_COLORS[C.MODE_RAM]};")
        bottom.addWidget(self.mode_label)
        bottom.addStretch(1)
        self.play_label = QLabel("")
        self.play_label.setStyleSheet("color:#ff5555; font-weight:bold;")
        bottom.addWidget(self.play_label)
        root.addLayout(bottom)

        self.probe_btn = QPushButton("探测")
        self.probe_btn.setToolTip(f"对 CH{self.chan} 发 CH_PROBE，读取在线状态与 CHIPID"
                                  "（期望 0x2340）")
        self.probe_btn.clicked.connect(lambda: self.probe_clicked.emit(self.chan))
        root.addWidget(self.probe_btn)

        self.setToolTip(f"CH{self.chan}：TRIG 输入 GPIO{C.TRIG_GPIO_MAP.get(self.chan, -1)}")

    # ---- 状态 ----

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, on: bool) -> None:
        """程序化设置勾选（不触发联动递归）。"""
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(bool(on))
        self.checkbox.blockSignals(False)

    def is_locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool, reason: str = _LOCK_REASON) -> None:
        self._locked = bool(locked)
        self.checkbox.setEnabled(not self._locked)
        self.lock_label.setText("🔒" if self._locked else "")
        self.checkbox.setToolTip(reason if self._locked else "")
        self.probe_btn.setEnabled(True if not self._locked else True)

    def set_present(self, present: Optional[bool], chipid: Optional[int] = None) -> None:
        self._present = present
        self._chipid = chipid
        if present is None:
            self.state_label.setText("未探测" if self._compact else "在线：未探测")
            self.state_label.setStyleSheet("color:#888;")
            self.chipid_label.setText("CHIPID —")
            self.chipid_label.setStyleSheet("color:#aaa; font-family:Consolas,monospace;")
            return
        if present:
            self.state_label.setText("● 在线" if self._compact else "在线：✔")
            self.state_label.setStyleSheet("color:#8fbf6f;")
        else:
            self.state_label.setText("○ 离线" if self._compact else "在线：✘")
            self.state_label.setStyleSheet("color:#ff5555;")
        if chipid is None:
            self.chipid_label.setText("CHIPID —")
            self.chipid_label.setStyleSheet("color:#aaa; font-family:Consolas,monospace;")
            self.chipid_label.setToolTip("")
        else:
            ok = chipid == C.AW_CHIPID_EXPECTED
            self.chipid_label.setText(f"CHIPID 0x{chipid:04X}")
            if ok:
                self.chipid_label.setStyleSheet("color:#8fbf6f; font-family:Consolas,monospace;")
                self.chipid_label.setToolTip("CHIPID 正确（期望 0x2340）")
            else:
                self.chipid_label.setStyleSheet("color:#e6b422; font-family:Consolas,monospace;")
                self.chipid_label.setToolTip(_CHIPID_HINT)

    def set_mode(self, mode: int) -> None:
        self._mode = mode
        self.mode_label.setText(f"模式 {C.mode_name(mode)}")
        color = _MODE_COLORS.get(mode, "#aaa")
        self.mode_label.setStyleSheet(f"color:{color};")

    def mode(self) -> int:
        return self._mode

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        if not playing:
            self.play_label.setText("")

    def set_blink(self, on: bool) -> None:
        if self._playing:
            self.play_label.setText("● 播放中" if on else "○ 播放中")

    # ---- 交互 ----

    def _on_toggle(self, checked: bool) -> None:
        self.toggled.emit(self.chan, checked)


class ChannelMatrix(QWidget):
    """8 通道矩阵（常驻主窗口顶部）。"""

    selection_changed = Signal(int)      # 勾选掩码
    probe_requested = Signal(int)        # 单路探测
    probe_all_requested = Signal()
    get_info_requested = Signal(int)     # 读取通道信息（模式/播放状态）
    info_message = Signal(str)           # 需要显示给用户的一句话

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._caps: int = C.CAPS_EXPECTED_BOARD_A
        self._count = C.CHANNEL_COUNT
        self._cards: Dict[int, ChannelCard] = {}
        self._suppress = False
        self._blink = False

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._on_blink)
        self._blink_timer.start()

        self._build()

    # ---- 构造 ----

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        bar = QHBoxLayout()
        title = QLabel("通道矩阵")
        title.setStyleSheet("font-weight:bold;")
        bar.addWidget(title)
        # ★ 这句提示原来很长（40+ 汉字），QLabel 的最小宽度就是整句文字宽度，
        #   会把窗口的最小宽度顶到 850+。缩短标签、完整说明放进 tooltip。
        self.hint_label = QLabel("勾选决定下发/播放/探测的目标")
        self.hint_label.setStyleSheet("color:#888;")
        self.hint_label.setToolTip(
            "勾选决定『下发到选中通道』『播放』『探测』的目标；\n"
            "TRIG 模式下 CH5/6/7 强制联动（共用 GPIO14），切回 RAM/CONT/RTP 即解除。\n"
            "点「收起」可折叠通道卡片，给页面内容腾出垂直空间。")
        bar.addWidget(self.hint_label)
        bar.addStretch(1)

        # ★ 收起按钮：矩阵常驻顶部约占 180~200 px，在 768/900 高的屏幕上很吃紧。
        #   收起后只留这一行工具条，通道勾选仍可用（全选/反选/在线探测/选中掩码都在这一行）。
        self.collapse_btn = QPushButton("收起 ▴")
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.setToolTip("收起/展开通道卡片，给页面内容腾出垂直空间（选择会被记住）")
        self.collapse_btn.toggled.connect(self.set_collapsed)
        bar.addWidget(self.collapse_btn)

        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self._select_all)
        bar.addWidget(self.select_all_btn)
        self.invert_btn = QPushButton("反选")
        self.invert_btn.clicked.connect(self._invert)
        bar.addWidget(self.invert_btn)
        self.probe_all_btn = QPushButton("在线探测")
        self.probe_all_btn.setToolTip("CH_PROBE_ALL：读取 8 路在线状态与 CHIPID（期望 0x2340）")
        self.probe_all_btn.clicked.connect(self.probe_all_requested.emit)
        bar.addWidget(self.probe_all_btn)
        self.mask_label = QLabel("选中：CH0")
        self.mask_label.setStyleSheet("color:#5fa8d3;")
        bar.addWidget(self.mask_label)
        root.addLayout(bar)

        self.grid_holder = QWidget()
        grid = QGridLayout(self.grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for ch in range(self._count):
            card = ChannelCard(ch)
            card.toggled.connect(self._on_card_toggled)
            card.probe_clicked.connect(self.probe_requested.emit)
            self._cards[ch] = card
            grid.addWidget(card, ch // 4, ch % 4)
        root.addWidget(self.grid_holder)

        # 默认勾选 CH0，方便无设备时也能操作界面
        self._cards[0].set_checked(True)
        self._update_mask_label()

        # 恢复上次的收起状态（键与 main_window 的 QSettings 同一个 org/app）
        settings = QSettings(_ORG_NAME, _APP_NAME)
        if settings.value("ui/matrix_collapsed", False, type=bool):
            self.collapse_btn.setChecked(True)

    def set_compact(self, compact: bool) -> None:
        """One row of channel selectors for everyday control; details in advanced mode."""
        grid = self.grid_holder.layout()
        for ch, card in self._cards.items():
            grid.removeWidget(card)
            card.setMinimumWidth(72 if compact else 150)
            card._compact = compact
            card.set_present(card._present, card._chipid)
            for widget in (card.chipid_label, card.probe_btn):
                widget.setVisible(not compact)
            columns = 8 if compact else 4
            grid.addWidget(card, ch // columns, ch % columns)

    def set_collapsed(self, collapsed: bool) -> None:
        """收起/展开通道卡片（收起后顶部只占一行）。"""
        collapsed = bool(collapsed)
        self.grid_holder.setVisible(not collapsed)
        self.collapse_btn.setText("展开 ▾" if collapsed else "收起 ▴")
        if self.collapse_btn.isChecked() != collapsed:
            self.collapse_btn.blockSignals(True)
            self.collapse_btn.setChecked(collapsed)
            self.collapse_btn.blockSignals(False)
        QSettings(_ORG_NAME, _APP_NAME).setValue("ui/matrix_collapsed", collapsed)

    def is_collapsed(self) -> bool:
        return not self.grid_holder.isVisible()

    # ---- 能力 / 模式 ----

    def set_caps(self, caps: int, channel_count: int = C.CHANNEL_COUNT) -> None:
        self._caps = int(caps)
        self._count = max(1, min(8, int(channel_count)))
        self._refresh_locks()

    @property
    def caps(self) -> int:
        return self._caps

    def card(self, chan: int) -> ChannelCard:
        return self._cards[chan]

    def set_channel_mode(self, chan: int, mode: int) -> None:
        card = self._cards.get(chan)
        if card is None:
            return
        card.set_mode(mode)
        self._refresh_locks()

    def set_modes(self, modes: Dict[int, int]) -> None:
        for chan, mode in modes.items():
            card = self._cards.get(chan)
            if card is not None:
                card.set_mode(mode)
        self._refresh_locks()

    def set_trigger_group_mode(self, mode: int) -> None:
        """把 CH5/6/7 一起设为某模式（TRIG 模式验证与快捷操作使用）。"""
        for chan in C.TRIG_SHARED_CHANNELS:
            card = self._cards.get(chan)
            if card is not None:
                card.set_mode(mode)
        self._refresh_locks()

    def modes(self) -> Dict[int, int]:
        return {ch: card.mode() for ch, card in self._cards.items()}

    def trig_group_locked(self) -> bool:
        return any(self._cards[ch].is_locked() for ch in C.TRIG_SHARED_CHANNELS)

    # ---- 联动逻辑 ----

    def _shared_trig_active(self) -> bool:
        if not (self._caps & C.CAP_SHARED_TRIG_567):
            return False
        return any(self._cards[ch].mode() == C.MODE_TRIG
                   for ch in C.TRIG_SHARED_CHANNELS)

    def _refresh_locks(self) -> None:
        """TRIG 模式下三路联动；其他模式完全独立。"""
        active = self._shared_trig_active()
        cards = [self._cards[ch] for ch in C.TRIG_SHARED_CHANNELS]
        if not active:
            if any(c.is_locked() for c in cards):
                self.info_message.emit("CH5/6/7 已解除 TRIG 联动（当前模式走 I2C GO，三路独立）。")
            for c in cards:
                c.set_locked(False)
            self._update_mask_label()
            return
        any_checked = any(c.is_checked() for c in cards)
        if any_checked:
            for c in cards:
                c.set_checked(True)
                c.set_locked(True, _LOCK_REASON)
        else:
            for c in cards:
                c.set_locked(False)
                c.checkbox.setToolTip("TRIG 模式下勾选本路会同时勾选 CH5/6/7（共用 GPIO14）")
        self._update_mask_label()

    def _on_card_toggled(self, chan: int, checked: bool) -> None:
        if self._suppress:
            return
        if (self._shared_trig_active() and chan in C.TRIG_SHARED_CHANNELS):
            self._suppress = True
            try:
                if checked:
                    for ch in C.TRIG_SHARED_CHANNELS:
                        self._cards[ch].set_checked(True)
                        self._cards[ch].set_locked(True, _LOCK_REASON)
                    self.info_message.emit(
                        "TRIG 模式下 CH5/6/7 共用 GPIO14：已自动勾选另外两路并加锁，"
                        "取消勾选请先切到 RAM/CONT/RTP 模式。")
                else:
                    self._cards[chan].set_checked(True)
                    self.info_message.emit("TRIG 模式下 CH5/6/7 已联动，无法单独取消勾选。")
            finally:
                self._suppress = False
        self._update_mask_label()

    # ---- 勾选 ----

    def selected_mask(self) -> int:
        mask = 0
        for ch, card in self._cards.items():
            if card.is_checked():
                mask |= 1 << ch
        return mask

    def set_selected_mask(self, mask: int) -> None:
        self._suppress = True
        try:
            for ch, card in self._cards.items():
                card.set_checked(bool(mask & (1 << ch)))
        finally:
            self._suppress = False
        self._refresh_locks()
        self._update_mask_label()

    def set_checked(self, chan: int, on: bool) -> None:
        """程序化勾选（走联动逻辑）。"""
        self._cards[chan].set_checked(on)
        self._on_card_toggled(chan, on)

    def is_checked(self, chan: int) -> bool:
        return self._cards[chan].is_checked()

    def is_locked(self, chan: int) -> bool:
        return self._cards[chan].is_locked()

    def _select_all(self) -> None:
        self.set_selected_mask(0xFF)

    def _invert(self) -> None:
        self.set_selected_mask((~self.selected_mask()) & 0xFF)

    def _update_mask_label(self) -> None:
        mask = self.selected_mask()
        chans = [f"CH{ch}" for ch in range(self._count) if mask & (1 << ch)]
        self.mask_label.setText("选中：" + ("、".join(chans) if chans else "（无）"))
        self.selection_changed.emit(mask)

    # ---- 探测结果 ----

    def set_all_unknown(self) -> None:
        for card in self._cards.values():
            card.set_present(None)

    def apply_probe_all(self, present_mask: int, chipids: Dict[int, Optional[int]]) -> None:
        for ch, card in self._cards.items():
            card.set_present(bool(present_mask & (1 << ch)), chipids.get(ch))
        if present_mask and not (self.selected_mask() & present_mask):
            self.set_selected_mask(present_mask & -present_mask)

    def set_channel_present(self, chan: int, present: bool,
                            chipid: Optional[int] = None) -> None:
        self._cards[chan].set_present(present, chipid)

    def set_playing(self, chan: int, playing: bool) -> None:
        self._cards[chan].set_playing(playing)
        if not playing:
            self._cards[chan].set_blink(False)

    def set_all_playing(self, mask: int) -> None:
        for ch, card in self._cards.items():
            card.set_playing(bool(mask & (1 << ch)))

    def _on_blink(self) -> None:
        self._blink = not self._blink
        for card in self._cards.values():
            card.set_blink(self._blink)


__all__ = ["ChannelMatrix", "ChannelCard"]
