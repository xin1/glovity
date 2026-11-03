# -*- coding: utf-8 -*-
"""自定义控件集合。

* :class:`~awtest.ui.widgets.channel_matrix.ChannelMatrix` —— 常驻通道矩阵（含 CH5/6/7 联动）
* :class:`~awtest.ui.widgets.amp_slider.AmpSlider` —— CONT 实时幅度滑条 + 生效性测试
* :class:`~awtest.ui.widgets.bitfield_view.BitfieldView` / ``BitfieldEditor`` —— 寄存器位域
* :class:`~awtest.ui.widgets.hex_view.HexView` —— 按协议字段着色的原始帧 hex 视图
"""

from __future__ import annotations

from .amp_slider import AmpSlider
from .bitfield_view import BitfieldEditor, BitfieldView, explain_byte
from .channel_matrix import ChannelCard, ChannelMatrix
from .hex_view import HexView, frame_html, frame_summary

__all__ = [
    "ChannelMatrix", "ChannelCard", "AmpSlider",
    "BitfieldView", "BitfieldEditor", "explain_byte",
    "HexView", "frame_html", "frame_summary",
]
