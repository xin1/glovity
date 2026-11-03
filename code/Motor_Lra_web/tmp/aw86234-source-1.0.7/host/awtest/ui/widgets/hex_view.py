# -*- coding: utf-8 -*-
"""原始帧 hex 视图与帧摘要工具。

按协议字段给 hex 着色分解：``SOF / VER / FLAGS / SEQ / CMD / CHAN / LEN / PAYLOAD / CRC``
（规格书 §5.10）。同一份渲染函数也被日志页与寄存器页复用，保证"日志里的 hex"
和"hex 视图"完全一致。
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from PySide6.QtWidgets import QTextEdit, QWidget

from awtest.protocol import commands as C
from awtest.protocol.framing import (FLAG_ERROR, FLAG_EVENT, FLAG_MORE,
                                     FLAG_NORESP, FLAG_RESPONSE, Frame)

# 字段颜色（深色底）
COLOR_SOF = "#888888"
COLOR_VER = "#5fa8d3"
COLOR_FLAGS = "#e6b422"
COLOR_SEQ = "#8fbf6f"
COLOR_CMD = "#ff9f43"
COLOR_CHAN = "#c9a0dc"
COLOR_LEN = "#aaaaaa"
COLOR_PAYLOAD = "#d0d0d0"
COLOR_CRC = "#ff5555"
COLOR_ERROR = "#ff5555"
COLOR_EVENT = "#d7a3ff"


def _span(text: str, color: str, bold: bool = False) -> str:
    weight = "font-weight:bold;" if bold else ""
    return f"<span style='color:{color};{weight}'>{text}</span>"


def flags_text(flags: int) -> str:
    parts = []
    if flags & FLAG_RESPONSE:
        parts.append("RESPONSE")
    if flags & FLAG_EVENT:
        parts.append("EVENT")
    if flags & FLAG_ERROR:
        parts.append("ERROR")
    if flags & FLAG_MORE:
        parts.append("MORE(违例)")
    if flags & FLAG_NORESP:
        parts.append("NORESP")
    return "|".join(parts) if parts else "0"


def chan_text(chan: int) -> str:
    if chan == 0xFF:
        return "0xFF(ALL)"
    if chan == 0xFE:
        return "0xFE(SYS)"
    return f"CH{chan}"


def payload_text(payload: bytes) -> str:
    return " ".join(f"{b:02X}" for b in payload) if payload else "—"


def frame_summary(frame: Frame, direction: str = "rx") -> str:
    """一行摘要（ASCII 行 / 日志 / 表格都用它）。"""
    head = f"{'TX' if direction == 'tx' else 'RX'} {C.cmd_name(frame.cmd)}"
    if frame.is_event:
        head = f"EVT {C.cmd_name(frame.cmd)}"
    parts = [head, f"seq={frame.seq}", f"chan={chan_text(frame.chan)}",
             f"len={len(frame.payload)}"]
    if frame.is_error:
        code = frame.err_code_safe
        parts.append(f"{C.err_name(code)}({C.err_cn(code)})")
        if frame.error_text:
            parts.append(f"“{frame.error_text}”")
    elif frame.payload:
        parts.append(f"payload={payload_text(frame.payload)}")
    if frame.more:
        parts.append("★MORE=1 协议违例")
    return " ".join(parts)


def frame_html(frame: Frame, direction: str = "rx",
               stamp: Optional[str] = None,
               show_fields: bool = True) -> str:
    """把一帧渲染成带字段着色的 HTML（供 hex 视图使用）。"""
    raw = frame.to_bytes()
    body = raw[2:-2]
    crc = raw[-2:]
    if frame.is_error:
        head_color = COLOR_ERROR
    elif frame.is_event:
        head_color = COLOR_EVENT
    elif direction == "tx":
        head_color = "#8fbf6f"
    else:
        head_color = "#5fa8d3"
    tag = "TX→" if direction == "tx" else "RX←"
    stamp = stamp or time.strftime("%H:%M:%S")

    cols: List[Tuple[str, bytes, str]] = [
        ("SOF", raw[0:2], COLOR_SOF),
        ("VER", raw[2:3], COLOR_VER),
        ("FLAGS", raw[3:4], COLOR_FLAGS),
        ("SEQ", raw[4:5], COLOR_SEQ),
        ("CMD", raw[5:6], COLOR_CMD),
        ("CHAN", raw[6:7], COLOR_CHAN),
        ("LEN", raw[7:8], COLOR_LEN),
        ("PAYLOAD", bytes(body), COLOR_PAYLOAD),
        ("CRC", bytes(crc), COLOR_CRC),
    ]
    hex_parts = []
    for _name, data, color in cols:
        if not data:
            continue
        hex_parts.append(_span(" ".join(f"{b:02X}" for b in data), color))
    line = (f"{_span(stamp, '#7f8c8d')} {_span(tag, head_color, True)} "
            + " ".join(hex_parts))
    if not show_fields:
        return line

    legend = [
        _span(f"VER={raw[2]:02X}", COLOR_VER),
        _span(f"FLAGS={raw[3]:02X}({flags_text(frame.flags)})", COLOR_FLAGS),
        _span(f"SEQ={frame.seq:02X}", COLOR_SEQ),
        _span(f"CMD={C.cmd_name(frame.cmd)}(0x{frame.cmd:02X})", COLOR_CMD),
        _span(f"CHAN={chan_text(frame.chan)}", COLOR_CHAN),
        _span(f"LEN={len(frame.payload)}", COLOR_LEN),
        _span(f"CRC={crc[1]:02X}{crc[0]:02X}", COLOR_CRC),
    ]
    detail = f"payload={payload_text(frame.payload)}"
    if frame.is_error:
        code = frame.err_code_safe
        detail += f" · <b>{C.err_name(code)}（{C.err_cn(code)}）</b>"
        if frame.error_text:
            detail += f" “{frame.error_text}”"
    if frame.more:
        detail += " · <b>★ MORE=1 协议违例（v1.0 设备不得置位）</b>"

    return (line + "<br>&nbsp;&nbsp;&nbsp;&nbsp;"
            + " ".join(legend)
            + f"<br>&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:#999'>{detail}</span>")


class HexView(QTextEdit):
    """hex 原始帧视图（只读，等宽字体）。"""

    MAX_BLOCKS = 4000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.NoWrap)
        self.setStyleSheet("font-family:Consolas,'Courier New',monospace; font-size:12px;")
        self.show_fields = True

    def set_show_fields(self, on: bool) -> None:
        self.show_fields = bool(on)

    def append_frame(self, frame: Frame, direction: str = "rx",
                     stamp: Optional[str] = None) -> None:
        self.append(frame_html(frame, direction, stamp, self.show_fields))
        self._trim()

    def append_line(self, text: str, color: str = "#d0d0d0", bold: bool = False) -> None:
        self.append(_span(text, color, bold) if color or bold else text)
        self._trim()

    def _trim(self) -> None:
        doc = self.document()
        if doc.blockCount() > self.MAX_BLOCKS:
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            for _ in range(doc.blockCount() - self.MAX_BLOCKS):
                cur.select(cur.SelectionType.BlockUnderCursor)
                cur.removeSelectedText()
                cur.deleteChar()


__all__ = ["HexView", "frame_html", "frame_summary", "flags_text", "chan_text"]
