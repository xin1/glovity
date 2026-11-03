# -*- coding: utf-8 -*-
"""日志页（规格书 §5.10）：统一日志流 + 按协议字段着色的 hex 原始帧视图。

统一收口四类信息：**ASCII 行、协议帧摘要、EVT_LOG 文本、错误与用户操作**，
每条都带时间戳与级别。开关：显示/隐藏 hex 原始帧、字段分解、仅显示错误、暂停滚动、
清空、导出；``EVT_ERROR`` 与 ``EVT_SAFETY_TRIP`` 高亮并可弹窗提醒。
"""

from __future__ import annotations

import time
from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QSplitter,
                               QTextEdit, QVBoxLayout, QWidget)

from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import LOG_COLORS, LOG_LEVEL_CN, BasePage
from .widgets import HexView, frame_summary

_LEVEL_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3,
                "rx": 1, "tx": 1, "user": 1, "evt": 2}

_HIGHLIGHT_LEVELS = {"error", "warn"}


class PageConsole(BasePage):
    """统一日志与原始帧视图。"""

    title = "日志"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self._pending: List[Tuple[str, str, bool]] = []
        self._paused = False
        self._counts = {"debug": 0, "info": 0, "warn": 0, "error": 0}

        root = QVBoxLayout(self)
        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Vertical)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(160)          # ★ 防止被滚动区压缩
        self.log_view.document().setMaximumBlockCount(20000)
        self.log_view.setStyleSheet(
            "font-family:Consolas,'Courier New',monospace; font-size:12px;"
            "background:#101010; color:#d0d0d0;")
        splitter.addWidget(self.log_view)

        self.hex_view = HexView()
        splitter.addWidget(self.hex_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#888;")
        root.addWidget(self.status_label)

        # 数据源：界面日志 / 传输层日志 / ASCII 行 / 帧 / 事件
        self.ctx.log_line.connect(lambda level, text: self.append(text, level))
        self.ctx.bridge.log_received.connect(self._on_transport_log)
        self.ctx.bridge.ascii_received.connect(self._on_ascii)
        self.ctx.bridge.frame_received.connect(self._on_frame)
        self.ctx.bridge.event_received.connect(self._on_event)

        self.append("日志页就绪。协议帧摘要、ASCII 行、EVT_LOG、错误与用户操作都会汇总到这里。",
                    "info")
        self._refresh_status()

    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.chk_hex = QCheckBox("显示 hex 原始帧")
        self.chk_hex.setChecked(True)
        self.chk_hex.setToolTip("在下方 hex 视图里记录每一帧（收发都记），按协议字段着色。")
        row.addWidget(self.chk_hex)

        self.chk_fields = QCheckBox("字段分解")
        self.chk_fields.setChecked(True)
        self.chk_fields.setToolTip("在 hex 帧下面附一行字段解释（VER/FLAGS/SEQ/CMD/CHAN/LEN/CRC）。")
        self.chk_fields.toggled.connect(lambda on: self.hex_view.set_show_fields(on))
        row.addWidget(self.chk_fields)

        self.chk_errors = QCheckBox("仅显示错误")
        self.chk_errors.setToolTip("只显示 error/warn 级别的日志（协议违例、错误应答、安全告警）。")
        row.addWidget(self.chk_errors)

        self.chk_pause = QCheckBox("暂停滚动")
        self.chk_pause.setToolTip("暂停期间日志进入缓冲区，取消暂停后一次性补齐。")
        self.chk_pause.toggled.connect(self._on_pause_toggled)
        row.addWidget(self.chk_pause)

        self.chk_popup = QCheckBox("告警弹窗提醒")
        self.chk_popup.setToolTip("收到 EVT_ERROR / EVT_SAFETY_TRIP 时额外弹窗提醒（默认关闭）。")
        row.addWidget(self.chk_popup)

        row.addWidget(QLabel("最低级别："))
        self.cmb_level = QComboBox()
        for name, level in (("全部", "debug"), ("信息", "info"), ("警告", "warn"),
                            ("仅错误", "error")):
            self.cmb_level.addItem(name, level)
        self.cmb_level.setCurrentIndex(0)
        self.cmb_level.setToolTip("过滤主日志流的最低级别。")
        row.addWidget(self.cmb_level)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        self.btn_export = QPushButton("导出日志")
        self.btn_export.clicked.connect(self._export)
        row.addWidget(self.btn_export)
        row.addStretch(1)
        return bar

    # ==================================================================
    # 写入
    # ==================================================================

    def append(self, text: str, level: str = "info", force: bool = False) -> None:
        """统一的日志写入入口（级别决定颜色；错误与告警加粗高亮）。"""
        text = str(text)
        if "\n" in text:
            for line in text.splitlines():
                self.append(line, level, force)
            return
        self._counts[level] = self._counts.get(level, 0) + 1
        self._refresh_status()
        if not self._passes_filter(level, force):
            return
        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        tag = LOG_LEVEL_CN.get(level, level)
        highlight = level in _HIGHLIGHT_LEVELS or force
        line = f"[{stamp}] [{tag:<4}] {text}"
        if self._paused:
            self._pending.append((line, level, highlight))
            del self._pending[:-5000]
            return
        self._write(line, level, highlight)

    def _passes_filter(self, level: str, force: bool) -> bool:
        if force:
            return True
        if self.chk_errors.isChecked():
            return level in _HIGHLIGHT_LEVELS
        minimum = _LEVEL_ORDER.get(self.cmb_level.currentData() or "debug", 0)
        return _LEVEL_ORDER.get(level, 1) >= minimum

    def _write(self, line: str, level: str, highlight: bool) -> None:
        color = LOG_COLORS.get(level, "#d0d0d0")
        weight = "font-weight:bold;" if highlight else ""
        bg = "background-color:#4a0000;" if highlight else ""
        safe = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.log_view.append(
            f"<span style='color:{color};{weight}{bg}'>{safe}</span>")

    def flush_pending(self) -> None:
        pending, self._pending = self._pending, []
        for line, level, highlight in pending:
            self._write(line, level, highlight)

    def _on_pause_toggled(self, on: bool) -> None:
        self._paused = on
        if not on:
            self.flush_pending()
        self._refresh_status()

    def _refresh_status(self) -> None:
        c = self._counts
        self.status_label.setText(
            f"调试 {c.get('debug', 0)} · 信息 {c.get('info', 0)} · 警告 {c.get('warn', 0)} · "
            f"错误 {c.get('error', 0)}"
            + (f" · 暂停中（缓冲 {len(self._pending)} 条）" if self._paused else ""))

    # ==================================================================
    # 数据源
    # ==================================================================

    def _on_transport_log(self, level: str, text: str) -> None:
        self.append(text, level if level in LOG_COLORS else "info")

    def _on_ascii(self, line: str) -> None:
        self.append(f"[ASCII] {line}", "rx")

    def _on_frame(self, frame, direction: str) -> None:
        if self.chk_hex.isChecked():
            self.hex_view.append_frame(frame, direction)
        self.append(frame_summary(frame, direction),
                    "tx" if direction == "tx" else "rx")
        if frame.more:
            self.append("★ 协议违例：收到 MORE=1 的帧（v1.0 设备不得置位）", "error", force=True)

    def _on_event(self, frame) -> None:
        evt = K.decode_event(frame)
        name = C.cmd_name(frame.cmd)
        if frame.cmd == C.EVT_LOG and isinstance(evt, K.EvtLog):
            level = {0: "debug", 1: "error", 2: "warn", 3: "info", 4: "debug"}.get(
                evt.level, "info")
            self.append(f"[EVT_LOG/{C.LOG_LEVEL_NAMES.get(evt.level, evt.level)}] {evt.text}",
                        level)
            return
        if frame.cmd == C.EVT_ERROR and isinstance(evt, K.EvtError):
            text = (f"[EVT_ERROR] {C.err_name(evt.errcode)}"
                    f"（{C.err_cn(evt.errcode)}）cmd={C.cmd_name(evt.cmd)}"
                    + (f" —— {evt.text}" if evt.text else ""))
            self.append(text, "error", force=True)
            self._maybe_popup("设备上报错误", text)
            return
        if frame.cmd == C.EVT_SAFETY_TRIP and isinstance(evt, K.EvtSafetyTrip):
            text = (f"[EVT_SAFETY_TRIP] 原因：{evt.reason_name}"
                    f" · 受影响通道掩码 0x{evt.affected_mask:02X} · detail={evt.detail}")
            self.append(text, "error", force=True)
            self._maybe_popup("安全机制触发", text + "\n固件已强制停止相关通道。")
            return
        if frame.cmd == C.EVT_PLAY_DONE and isinstance(evt, K.EvtPlayDone):
            level = "warn" if evt.reason == C.PLAY_DONE_SAFETY_TIMEOUT else "info"
            self.append(f"[EVT_PLAY_DONE] {evt.reason_name} · 实际播放 {evt.elapsed_ms} ms",
                        level, force=(level == "warn"))
            if evt.reason == C.PLAY_DONE_SAFETY_TIMEOUT:
                self._maybe_popup("播放因安全超时被强制停止",
                                  f"实际超时值 {evt.elapsed_ms} ms。")
            return
        self.append(f"[事件] {name} payload={frame.payload.hex(' ').upper()}", "evt")

    def _maybe_popup(self, title: str, text: str) -> None:
        if not self.chk_popup.isChecked():
            return
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(title)
        box.setInformativeText(text)
        box.setModal(False)
        box.show()

    # ==================================================================
    # 操作
    # ==================================================================

    def _clear(self) -> None:
        self.log_view.clear()
        self.hex_view.clear()
        self._pending.clear()
        self._counts = {k: 0 for k in self._counts}
        self._refresh_status()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "awtest_log.txt",
                                              "文本 (*.txt);;全部 (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("=== 统一日志 ===\n")
                fh.write(self.log_view.toPlainText())
                if self.chk_hex.isChecked():
                    fh.write("\n\n=== hex 原始帧 ===\n")
                    fh.write(self.hex_view.toPlainText())
        except Exception as exc:
            self.ctx.error(self, "导出失败", "写入日志文件失败。", str(exc))
            return
        self.ctx.log(f"日志已导出：{path}", "user")


__all__ = ["PageConsole"]
