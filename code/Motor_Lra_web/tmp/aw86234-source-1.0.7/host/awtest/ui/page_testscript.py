# -*- coding: utf-8 -*-
"""测试脚本页（规格书 §5.9）：执行 / 暂停 / 中止、单步高亮、三态判定、报告导出。

调用 ``awtest.script``（接口约定 §3）的解析与执行引擎。该模块由脚本引擎负责实现，
若 ``engine`` / ``report`` 尚未就绪（``ImportError``），本页只提示并禁用运行按钮。

``prompt`` 类步骤需要弹窗让人填人工测量值：引擎在工作线程里调用 ``prompt_handler``，
这里通过 Qt 信号把请求投递到 UI 线程弹 ``QInputDialog``，再用 ``threading.Event``
把答案送回工作线程（同步等待，界面不阻塞）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextEdit,
                               QVBoxLayout, QWidget)

from awtest import resources as RES


from . import (HAVE_SCRIPT, BasePage,
               missing_module_hint)
from .widgets import frame_summary

_STATUS_STYLE = {
    "pass": ("通过", "#8fbf6f"),
    "fail": ("失败", "#ff5555"),
    "skip": ("跳过", "#e6b422"),
    "running": ("运行中", "#5fa8d3"),
}


class _ScriptBridge(QObject):
    """把引擎在工作线程里的回调转成 Qt 信号，并同步地要一个人工输入。"""

    step_done = Signal(object)          # StepResult
    log_line = Signal(str)
    ask = Signal(str, str, str)         # message, field, unit

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._answer: Optional[str] = None
        self._event = threading.Event()

    # 工作线程侧 ----
    def on_step(self, step) -> None:
        self.step_done.emit(step)

    def on_log(self, text: str) -> None:
        self.log_line.emit(text)

    def on_prompt(self, message: str, field: str, unit: str) -> Optional[str]:
        self._answer = None
        self._event.clear()
        self.ask.emit(message, field, unit)
        self._event.wait(600)
        return self._answer

    # UI 线程侧 ----
    def provide_answer(self, value: Optional[str]) -> None:
        self._answer = value
        self._event.set()


class PageTestScript(BasePage):
    """测试脚本执行与报告页。"""

    title = "测试脚本"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self.script = None
        self.engine = None
        self.run_result = None
        self._abort = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._frames: List[str] = []
        self._bridge = _ScriptBridge(self)
        self._bridge.step_done.connect(self._on_step)
        self._bridge.log_line.connect(lambda text: self.log_view.append(text))
        self._bridge.ask.connect(self._on_ask)

        root = QVBoxLayout(self)
        if not HAVE_SCRIPT:
            banner = QLabel(missing_module_hint("script"))
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background:#4a3b00; color:#ffd75f; padding:6px; border:1px solid #e6b422;")
            root.addWidget(banner)

        root.addWidget(self._build_manual_group())
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_list_group())
        splitter.addWidget(self._build_run_group())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        self.ctx.bridge.frame_received.connect(self._on_frame)
        self._reload_scripts()
        if not HAVE_SCRIPT:
            self._set_running(False, module_ready=False)

    # ------------------------------------------------------------------
    # 顶部：报告元数据 + 关键实测值
    # ------------------------------------------------------------------

    def _build_manual_group(self) -> QGroupBox:
        box = QGroupBox("报告抬头（写入 Markdown 报告顶部的记录表）与关键实测值")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        form = QFormLayout()
        self.ed_date = QLineEdit(time.strftime("%Y-%m-%d"))
        self.ed_board = QLineEdit("")
        self.ed_board.setPlaceholderText("板号 / SN")
        self.ed_fw = QLineEdit("")
        self.ed_fw.setPlaceholderText("固件版本（可用 PING 读回）")
        self.ed_power = QLineEdit("")
        self.ed_power.setPlaceholderText("供电方式（USB / J10 外部电源 + 电压）")
        self.ed_motor = QLineEdit("")
        self.ed_motor.setPlaceholderText("马达型号（0815 / 1040）")
        form.addRow("测试日期", self.ed_date)
        form.addRow("板号", self.ed_board)
        form.addRow("固件版本", self.ed_fw)
        form.addRow("电源", self.ed_power)
        form.addRow("马达型号", self.ed_motor)
        row.addLayout(form, 1)
        self.manual_view = QTextEdit()
        self.manual_view.setReadOnly(True)
        self.manual_view.setPlaceholderText("脚本执行过程中弹窗收集到的人工测量值会显示在这里"
                                            "（并写入报告顶部的『关键实测值』区）")
        self.manual_view.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        row.addWidget(self.manual_view, 2)
        root.addLayout(row)
        return box

    # ------------------------------------------------------------------
    # 脚本列表
    # ------------------------------------------------------------------

    def _build_list_group(self) -> QGroupBox:
        box = QGroupBox("脚本列表")
        root = QVBoxLayout(box)
        self.script_list = QListWidget()
        self.script_list.itemDoubleClicked.connect(lambda _i: self._run())
        root.addWidget(self.script_list, 1)

        row = QHBoxLayout()
        self.btn_reload = QPushButton("刷新列表")
        self.btn_reload.clicked.connect(self._reload_scripts)
        self.btn_open = QPushButton("打开脚本…")
        self.btn_open.clicked.connect(self._open_script)
        row.addWidget(self.btn_reload)
        row.addWidget(self.btn_open)
        root.addLayout(row)

        self.script_info = QLabel("未选择脚本。")
        self.script_info.setWordWrap(True)
        self.script_info.setStyleSheet("color:#5fa8d3;")
        root.addWidget(self.script_info)
        self.script_list.currentItemChanged.connect(lambda *_a: self._show_info())
        return box

    # ------------------------------------------------------------------
    # 执行 / 报告
    # ------------------------------------------------------------------

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("执行")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        self.btn_run = QPushButton("运行")
        self.btn_run.setStyleSheet("font-weight:bold;")
        self.btn_run.clicked.connect(self._run)
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.clicked.connect(self._pause)
        self.btn_resume = QPushButton("继续")
        self.btn_resume.clicked.connect(self._resume)
        self.btn_abort = QPushButton("中止")
        self.btn_abort.setStyleSheet("color:#ff5555;")
        self.btn_abort.clicked.connect(self._abort_run)
        for b in (self.btn_run, self.btn_pause, self.btn_resume, self.btn_abort):
            row.addWidget(b)
        row.addStretch(1)
        self.status_label = QLabel("就绪")
        row.addWidget(self.status_label)
        root.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_report = QPushButton("导出 Markdown 报告")
        self.btn_report.clicked.connect(lambda: self._export_report("md"))
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.clicked.connect(lambda: self._export_report("csv"))
        row2.addWidget(self.btn_report)
        row2.addWidget(self.btn_csv)
        row2.addStretch(1)
        root.addLayout(row2)

        split = QSplitter(Qt.Vertical)
        self.step_table = QTableWidget(0, 9)
        self.step_table.setHorizontalHeaderLabels(
            ["#", "op", "通道", "参数", "期望", "实际", "状态", "耗时(ms)", "备注"])
        self.step_table.verticalHeader().setVisible(False)
        self.step_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.step_table.horizontalHeader().setStretchLastSection(True)
        self.step_table.setMinimumHeight(160)        # ★ 防止被滚动区压缩
        split.addWidget(self.step_table)

        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.addWidget(QLabel("引擎日志与原始帧摘要（最新的在最后）："))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        self.log_view.setMinimumHeight(120)          # ★ 防止被滚动区压缩
        bv.addWidget(self.log_view, 1)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        return box

    # ==================================================================
    # 脚本加载
    # ==================================================================

    def _reload_scripts(self) -> None:
        self.script_list.clear()
        if not HAVE_SCRIPT:
            return
        from awtest.script import schema as sc
        pairs: List = []
        try:
            pairs = sc.list_preset_scripts(RES.SCRIPT_DIR)
        except Exception as exc:
            self.script_info.setText(f"读取预设脚本目录失败：{exc}")
        if not pairs:
            try:
                pairs = sc.list_preset_scripts()
            except Exception:
                pairs = []
        for name, path in pairs:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.script_list.addItem(item)
        if not pairs:
            hint = QListWidgetItem("（没有找到预设脚本）")
            hint.setFlags(Qt.NoItemFlags)
            self.script_list.addItem(hint)
        self.script_info.setText(
            f"预设脚本目录：{RES.SCRIPT_DIR}\n双击列表项或点『运行』执行。")

    def _open_script(self) -> None:
        if not HAVE_SCRIPT:
            self.ctx.warn(self, "模块未就绪", missing_module_hint("script"))
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开测试脚本", "", "JSON (*.json)")
        if not path:
            return
        self._load_path(path)

    def _load_path(self, path: str) -> bool:
        from awtest.script import schema as sc
        try:
            self.script = sc.load_script(path)
        except Exception as exc:
            self.ctx.error(self, "脚本加载失败", f"无法解析 {os.path.basename(path)}。", str(exc))
            return False
        self._show_info()
        return True

    def _current_path(self) -> Optional[str]:
        item = self.script_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _show_info(self) -> None:
        path = self._current_path()
        if path and (self.script is None or getattr(self.script, "path", None) != path):
            if not self._load_path(path):
                return
        s = self.script
        if s is None:
            self.script_info.setText("未选择脚本。")
            return
        ops = [step.op for step in s.steps]
        self.script_info.setText(
            f"名称：{s.name}\n矩阵项：{', '.join(s.matrix_ids) or '—'}\n"
            f"通道：{s.channels}\n步骤数：{len(s.steps)}\n"
            f"op 序列：{', '.join(ops[:12])}{' …' if len(ops) > 12 else ''}\n"
            f"说明：{s.description}")

    # ==================================================================
    # 执行
    # ==================================================================

    def _set_running(self, running: bool, module_ready: bool = True) -> None:
        ok = module_ready and HAVE_SCRIPT
        self.btn_run.setEnabled(ok and not running)
        self.btn_pause.setEnabled(ok and running)
        self.btn_resume.setEnabled(ok and running)
        self.btn_abort.setEnabled(ok and running)
        self.btn_report.setEnabled(ok and not running and self.run_result is not None)
        self.btn_csv.setEnabled(self.btn_report.isEnabled())
        self.btn_open.setEnabled(ok)
        self.btn_reload.setEnabled(ok)

    def _run(self) -> None:
        if not HAVE_SCRIPT:
            self.ctx.warn(self, "模块未就绪", missing_module_hint("script"))
            return
        if not self.ctx.require_device(self):
            return
        path = self._current_path()
        if path and (self.script is None or getattr(self.script, "path", None) != path):
            if not self._load_path(path):
                return
        if self.script is None:
            self.ctx.warn(self, "没有脚本", "请先在左侧选择一个脚本。")
            return
        from awtest.script import engine as eng
        self.step_table.setRowCount(0)
        self.log_view.clear()
        self.manual_view.clear()
        self._frames.clear()
        self._abort = False
        self._pause_event.set()
        self.run_result = None
        self.status_label.setText("运行中…")
        self._set_running(True)

        script = self.script
        dev = self.ctx.device
        bridge = self._bridge

        def job(progress):
            engine = eng.ScriptEngine(
                dev, script,
                on_step=bridge.on_step,
                on_log=bridge.on_log,
                prompt_handler=bridge.on_prompt,
                should_abort=lambda: self._abort,
                pause_gate=lambda: self._pause_event.wait(),
            )
            self.engine = engine
            return engine.run()

        def done(run) -> None:
            self.run_result = run
            self._set_running(False)
            status = getattr(run, "status", "?")
            self.status_label.setText(f"完成：{status}")
            self.ctx.log(f"脚本『{script.name}』执行结束：{status}"
                         f"（{len(getattr(run, 'steps', []))} 步）", "user")
            if getattr(run, "manual", None):
                self.manual_view.setPlainText("\n".join(
                    f"{k} = {v}" for k, v in run.manual.items()))

        def failed(msg: str) -> None:
            self._set_running(False)
            self.status_label.setText("执行异常")
            self.ctx.error(self, "脚本执行异常", "引擎抛出异常，已中止。", msg.splitlines()[0])

        self.ctx.log(f"开始执行脚本：{script.name}", "user")
        self.ctx.task(job, on_done=done, on_error=failed, name="测试脚本")

    def _pause(self) -> None:
        self._pause_event.clear()
        if self.engine is not None:
            try:
                self.engine.pause()
            except Exception:
                pass
        self.status_label.setText("已暂停")

    def _resume(self) -> None:
        self._pause_event.set()
        if self.engine is not None:
            try:
                self.engine.resume()
            except Exception:
                pass
        self.status_label.setText("运行中…")

    def _abort_run(self) -> None:
        self._abort = True
        self._pause_event.set()
        if self.engine is not None:
            try:
                self.engine.abort()
            except Exception:
                pass
        self.status_label.setText("已请求中止")

    # ==================================================================
    # 回调
    # ==================================================================

    def _on_step(self, step) -> None:
        row = self.step_table.rowCount()
        self.step_table.insertRow(row)
        status = getattr(step, "status", "skip")
        text, color = _STATUS_STYLE.get(status, (status, "#d0d0d0"))
        params = getattr(step, "params", {}) or {}
        vals = [str(getattr(step, "index", row)),
                str(getattr(step, "op", "")),
                "—" if getattr(step, "chan", None) is None else f"CH{step.chan}",
                ", ".join(f"{k}={v}" for k, v in params.items()),
                str(getattr(step, "expected", "")),
                str(getattr(step, "actual", "")),
                text,
                f"{getattr(step, 'elapsed_ms', 0):.1f}",
                str(getattr(step, "note", ""))]
        for col, value in enumerate(vals):
            item = QTableWidgetItem(value)
            if col == 6:
                item.setForeground(Qt.GlobalColor.white)
                item.setBackground(Qt.GlobalColor.darkGreen if status == "pass" else
                                   Qt.GlobalColor.darkRed if status == "fail" else
                                   Qt.GlobalColor.darkYellow)
            self.step_table.setItem(row, col, item)
        self.step_table.selectRow(row)
        self.step_table.scrollToBottom()
        self.log_view.append(
            f"[{getattr(step, 'index', row)}] {getattr(step, 'op', '')} → {text} "
            f"({getattr(step, 'elapsed_ms', 0):.1f} ms) {getattr(step, 'note', '')}")

    def _on_frame(self, frame, direction: str) -> None:
        line = frame_summary(frame, direction)
        self._frames.append(line)
        del self._frames[:-200]
        if self.engine is not None:
            self.log_view.append("  " + line)

    def _on_ask(self, message: str, field: str, unit: str) -> None:
        value, ok = QInputDialog.getText(
            self, "人工测量输入",
            f"{message}\n字段：{field}（单位 {unit or '—'}）")
        self._bridge.provide_answer(value if ok else None)

    # ==================================================================
    # 报告
    # ==================================================================

    def _meta(self) -> Dict[str, str]:
        meta = {
            "测试日期": self.ed_date.text(),
            "板号": self.ed_board.text(),
            "固件版本": self.ed_fw.text(),
            "电源": self.ed_power.text(),
            "马达型号": self.ed_motor.text(),
        }
        if self.ctx.caps is not None and self.ctx.info and self.ctx.info.ping:
            meta["固件版本"] = meta["固件版本"] or self.ctx.info.ping.fw_version
        if self.ctx.info:
            meta["串口"] = self.ctx.info.port
        return meta

    def _export_report(self, kind: str) -> None:
        if self.run_result is None:
            self.ctx.warn(self, "没有结果", "请先运行一次脚本。")
            return
        if not HAVE_SCRIPT:
            self.ctx.warn(self, "模块未就绪", missing_module_hint("script"))
            return
        from awtest.script import report as rep
        if kind == "csv":
            path, _ = QFileDialog.getSaveFileName(self, "导出 CSV 报告", "report.csv",
                                                  "CSV (*.csv)")
            if not path:
                return
            try:
                rep.write_csv(self.run_result, path)
            except Exception as exc:
                self.ctx.error(self, "导出失败", "写 CSV 报告失败。", str(exc))
                return
        else:
            path, _ = QFileDialog.getSaveFileName(self, "导出 Markdown 报告", "report.md",
                                                  "Markdown (*.md)")
            if not path:
                return
            try:
                rep.write_markdown(self.run_result, path, self._meta())
            except Exception as exc:
                self.ctx.error(self, "导出失败", "写 Markdown 报告失败。", str(exc))
                return
        self.ctx.log(f"报告已导出：{path}", "user")

    # ==================================================================
    # 状态
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        if not HAVE_SCRIPT:
            self._set_running(False, module_ready=False)
            return
        self._set_running(ok and self.run_result is None)


__all__ = ["PageTestScript"]
