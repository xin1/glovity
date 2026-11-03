# -*- coding: utf-8 -*-
"""寄存器页（规格书 §5.8）：地址簿 / 读 / 写 / 位域写 / 整片 dump / 监视列表 / 陷阱提醒。

* 地址簿来自 ``resources/register_book.json``（按 02 文档 §7 整理，覆盖手册已定义地址）；
* 写操作**一律二次确认**，并把"原值 → 新值"列出来（先读后写）；
* 命中 ``commands.UNDEFINED_REGISTERS`` 或地址簿里没有的地址 → **黄色警告**；
* 读到 ``0x45`` / ``0x33`` / ``0x3A`` 时旁注"默认值陷阱"提示与期望值（02 文档 §2）；
* 整片 dump 分两次请求（``0x00,64`` + ``0x40,42``），与数据手册默认值对比并高亮差异。
"""

from __future__ import annotations

import csv
import json
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from awtest import resources as RES
from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import BasePage
from .widgets import BitfieldEditor, BitfieldView

_TRAP_KEYS = {0x45: "T-1/T-2", 0x33: "T-3", 0x3A: "T-4"}


class PageRegister(BasePage):
    """寄存器直读直写页。"""

    title = "寄存器"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self.book = RES.register_map()
        self.addrs: List[int] = sorted(self.book)
        self.watch: List[int] = []
        self.last_values: Dict[int, int] = {}
        self.dump: Dict[int, int] = {}

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self._build_read_group())
        lv.addWidget(self._build_write_group())
        lv.addStretch(1)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(self._build_dump_group(), 3)
        rv.addWidget(self._build_watch_group(), 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_poll)
        self._on_addr_changed()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def _build_read_group(self) -> QGroupBox:
        box = QGroupBox("读寄存器（REG_READ）")
        root = QVBoxLayout(box)
        form = QFormLayout()

        self.cmb_chan = QComboBox()
        for ch in range(C.CHANNEL_COUNT):
            self.cmb_chan.addItem(f"CH{ch}", ch)
        form.addRow("通道", self.cmb_chan)

        self.cmb_addr = QComboBox()
        self.cmb_addr.setEditable(False)
        for addr in self.addrs:
            item = self.book[addr]
            self.cmb_addr.addItem(f"0x{addr:02X}  {item.get('name', '')}", addr)
        self.cmb_addr.setToolTip("地址簿来自 resources/register_book.json（依据 02 文档 §7）。")
        self.cmb_addr.currentIndexChanged.connect(self._on_addr_changed)
        form.addRow("地址簿", self.cmb_addr)

        self.ed_addr = QLineEdit("0x45")
        self.ed_addr.setToolTip("也可以直接填任意地址（十六进制），例如 0x45 或 45。")
        self.ed_addr.editingFinished.connect(self._on_addr_edit)
        form.addRow("地址（hex）", self.ed_addr)

        self.sp_len = QSpinBox()
        self.sp_len.setRange(1, C.REG_MAX_LEN)
        self.sp_len.setValue(1)
        self.sp_len.setToolTip(f"REG_READ 长度 1..{C.REG_MAX_LEN} 字节。")
        form.addRow("长度", self.sp_len)
        root.addLayout(form)

        self.addr_info = QLabel("")
        self.addr_info.setWordWrap(True)
        self.addr_info.setStyleSheet("color:#5fa8d3;")
        root.addWidget(self.addr_info)

        self.addr_warn = QLabel("")
        self.addr_warn.setWordWrap(True)
        self.addr_warn.setStyleSheet("color:#e6b422;")
        root.addWidget(self.addr_warn)

        row = QHBoxLayout()
        self.btn_read = QPushButton("读取")
        self.btn_read.setStyleSheet("font-weight:bold;")
        self.btn_read.clicked.connect(self._read)
        self.btn_watch_add = QPushButton("加入监视")
        self.btn_watch_add.clicked.connect(self._watch_add)
        row.addWidget(self.btn_read)
        row.addWidget(self.btn_watch_add)
        row.addStretch(1)
        root.addLayout(row)

        self.read_label = QLabel("—")
        self.read_label.setStyleSheet("font-family:Consolas,monospace;")
        self.read_label.setWordWrap(True)
        root.addWidget(self.read_label)

        self.bitview = BitfieldView()
        root.addWidget(self.bitview)
        return box

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def _build_write_group(self) -> QGroupBox:
        box = QGroupBox("写寄存器（REG_WRITE / REG_UPDATE）")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.sp_value = QSpinBox()
        self.sp_value.setRange(0, 255)
        self.sp_value.setToolTip("要写入的单字节值（0..255）。")
        form.addRow("单字节值", self.sp_value)
        root.addLayout(form)

        self.btn_write = QPushButton("单字节写（写前二次确认，显示『原值 → 新值』）")
        self.btn_write.clicked.connect(self._write_byte)
        root.addWidget(self.btn_write)

        self.bit_editor = BitfieldEditor()
        root.addWidget(self.bit_editor)

        self.btn_update = QPushButton("位域写 REG_UPDATE（只改勾选的位域）")
        self.btn_update.clicked.connect(self._write_bits)
        root.addWidget(self.btn_update)

        self.write_label = QLabel("")
        self.write_label.setWordWrap(True)
        root.addWidget(self.write_label)
        return box

    # ------------------------------------------------------------------
    # Dump
    # ------------------------------------------------------------------

    def _build_dump_group(self) -> QGroupBox:
        box = QGroupBox("整片 Dump（0x00,64 + 0x40,42 → 覆盖 0x00~0x69）")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        self.btn_dump = QPushButton("开始整片 Dump")
        self.btn_dump.setStyleSheet("font-weight:bold;")
        self.btn_dump.clicked.connect(self._dump_full)
        self.btn_dump_csv = QPushButton("导出 CSV")
        self.btn_dump_csv.clicked.connect(lambda: self._dump_export("csv"))
        self.btn_dump_json = QPushButton("导出 JSON")
        self.btn_dump_json.clicked.connect(lambda: self._dump_export("json"))
        self.chk_diff_only = QPushButton("只看与默认值不同")
        self.chk_diff_only.setCheckable(True)
        self.chk_diff_only.toggled.connect(lambda _b: self._refresh_dump_table())
        for b in (self.btn_dump, self.btn_dump_csv, self.btn_dump_json, self.chk_diff_only):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.dump_table = QTableWidget(0, 5)
        self.dump_table.setHorizontalHeaderLabels(["地址", "名称", "当前值", "默认值", "差异"])
        self.dump_table.verticalHeader().setVisible(False)
        self.dump_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dump_table.horizontalHeader().setStretchLastSection(True)
        self.dump_table.setMinimumHeight(160)        # ★ 防止被滚动区压缩（见 page_measure f0_plot）
        self.dump_table.itemSelectionChanged.connect(self._on_dump_selected)
        root.addWidget(self.dump_table, 1)

        self.dump_label = QLabel("尚未 Dump。整片读取较慢（两次 REG_DUMP，各 2 s 超时）。")
        self.dump_label.setWordWrap(True)
        self.dump_label.setStyleSheet("color:#888;")
        root.addWidget(self.dump_label)
        return box

    # ------------------------------------------------------------------
    # 监视
    # ------------------------------------------------------------------

    def _build_watch_group(self) -> QGroupBox:
        box = QGroupBox("监视列表（周期轮询，变化高亮）")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        self.sp_watch_period = QSpinBox()
        self.sp_watch_period.setRange(100, 10000)
        self.sp_watch_period.setValue(500)
        self.sp_watch_period.setSuffix(" ms")
        self.sp_watch_period.setToolTip("轮询周期。轮询同样占用 I2C 带宽，多路同时监视时请加大周期。")
        self.btn_watch_start = QPushButton("开始监视")
        self.btn_watch_start.clicked.connect(self._watch_start)
        self.btn_watch_stop = QPushButton("停止")
        self.btn_watch_stop.clicked.connect(self._watch_stop)
        self.btn_watch_clear = QPushButton("清空列表")
        self.btn_watch_clear.clicked.connect(self._watch_clear)
        for b in (self.sp_watch_period, self.btn_watch_start, self.btn_watch_stop,
                  self.btn_watch_clear):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.watch_table = QTableWidget(0, 5)
        self.watch_table.setHorizontalHeaderLabels(["地址", "名称", "当前值", "上次值", "变化"])
        self.watch_table.verticalHeader().setVisible(False)
        self.watch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.watch_table.horizontalHeader().setStretchLastSection(True)
        self.watch_table.setMinimumHeight(120)       # ★ 同上
        root.addWidget(self.watch_table, 1)
        return box

    # ==================================================================
    # 地址簿交互
    # ==================================================================

    def current_addr(self) -> int:
        text = self.ed_addr.text().strip()
        try:
            return int(text, 16) & 0xFF
        except ValueError:
            return 0

    def _on_addr_changed(self) -> None:
        addr = int(self.cmb_addr.currentData() or 0)
        self.ed_addr.setText(f"0x{addr:02X}")
        self._refresh_addr_info()

    def _on_addr_edit(self) -> None:
        addr = self.current_addr()
        idx = self.cmb_addr.findData(addr)
        if idx >= 0:
            self.cmb_addr.blockSignals(True)
            self.cmb_addr.setCurrentIndex(idx)
            self.cmb_addr.blockSignals(False)
        self._refresh_addr_info()

    def _refresh_addr_info(self) -> None:
        addr = self.current_addr()
        item = self.book.get(addr)
        if item is None:
            self.addr_info.setText(f"0x{addr:02X}：（地址簿中没有这一项）")
        else:
            default = item.get("default")
            self.addr_info.setText(
                f"0x{addr:02X} · {item.get('name', '')} · 访问属性 {item.get('rw', '?')} · "
                f"默认值 {('—' if default is None else hex(default))}\n{item.get('desc', '')}")
        self._refresh_addr_flag()
        fields = (item or {}).get("bits") or []
        current = self.last_values.get(addr, 0)
        self.bitview.show_value(current, fields, title=f"0x{addr:02X} 位域解释（上次读到的值）")
        self.bit_editor.set_byte(current, fields)

    def _refresh_addr_flag(self) -> None:
        """未定义 / 陷阱 / 地址簿未列出的黄色警告。"""
        addr = self.current_addr()
        item = self.book.get(addr)
        msgs: List[str] = []
        if addr in C.UNDEFINED_REGISTERS:
            msgs.append(f"★ 数据手册未定义该地址（0x{addr:02X}，协议附录 D 第 11 条）："
                        "写入可能有未知后果，请确认后再写。")
        elif item is None:
            msgs.append(f"★ 地址簿中没有 0x{addr:02X}：可能是未定义或被手册遗漏的地址，"
                        "写入前请对照《AW8623X 寄存器全表》。")
        elif item.get("uncertain"):
            msgs.append(f"★ 0x{addr:02X} 在手册中未明确列出（{item.get('desc', '')}）")
        if item and item.get("trap"):
            trap = item.get("trap_note") or ("默认值陷阱：" + str(item.get("trap")))
            msgs.append(f"★ 默认值陷阱 {item.get('trap')}：{trap}")
        self.addr_warn.setText("\n".join(msgs))
        self.addr_warn.setStyleSheet("color:#e6b422;" if msgs else "")
        if addr in _TRAP_KEYS and not (item and item.get("trap")):
            self.addr_warn.setText(f"★ 0x{addr:02X} 属于默认值陷阱寄存器（{_TRAP_KEYS[addr]}）。")

    # ==================================================================
    # 读
    # ==================================================================

    def _read(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        addr = self.current_addr()
        length = self.sp_len.value()
        if addr in C.UNDEFINED_REGISTERS:
            self.ctx.log(f"读取未定义地址 0x{addr:02X}（只读，风险较低）", "warn")

        def job(progress):
            return dev.reg_read(ch, addr, length)

        def done(data: bytes) -> None:
            if not data:
                self.read_label.setText("读取失败：载荷为空")
                return
            hexs = " ".join(f"{b:02X}" for b in data)
            decs = " ".join(str(b) for b in data)
            bins = " ".join(f"{b:08b}" for b in data)
            self.read_label.setText(
                f"CH{ch} 0x{addr:02X}（{length} 字节）\n"
                f"HEX : {hexs}\nDEC : {decs}\nBIN : {bins}")
            self.last_values[addr] = data[0]
            item = self.book.get(addr)
            self.bitview.show_value(data[0], (item or {}).get("bits") or [],
                                    title=f"0x{addr:02X} = 0x{data[0]:02X}"
                                          f"（{item.get('name', '') if item else '地址簿无此项'}）")
            self.bit_editor.set_byte(data[0], (item or {}).get("bits") or [])
            self.sp_value.setValue(data[0])
            self.ctx.log(f"REG_READ CH{ch} 0x{addr:02X}×{length} → {hexs}", "info")

        self.ctx.task(job, on_done=done, name="REG_READ")

    # ==================================================================
    # 写
    # ==================================================================

    def _confirm_write(self, ch: int, addr: int, old: Optional[int], new: int,
                       kind: str) -> bool:
        item = self.book.get(addr)
        name = item.get("name", "") if item else "（地址簿无此项）"
        old_text = "（未能读取原值）" if old is None else f"0x{old:02X}"
        detail = (f"CH{ch} 地址 0x{addr:02X} {name}\n"
                  f"原值 → 新值：{old_text} → 0x{new:02X}\n"
                  f"方式：{kind}")
        if addr in C.UNDEFINED_REGISTERS:
            detail += ("\n★ 该地址在数据手册中未定义，写入可能有未知后果！")
        elif item is None:
            detail += "\n★ 地址簿中没有该地址，请确认它是安全的。"
        if item and item.get("trap"):
            detail += f"\n★ 默认值陷阱 {item.get('trap')}：{item.get('trap_note', '')}"
        return self.ctx.confirm(self, "写寄存器确认",
                                f"确认写入 0x{addr:02X}？", detail, danger=True)

    def _write_byte(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        addr = self.current_addr()
        new = self.sp_value.value()

        def job(progress):
            try:
                old = dev.reg_read_u8(ch, addr)
            except Exception:
                old = None
            return old

        def done(old) -> None:
            if not self._confirm_write(ch, addr, old, new, "REG_WRITE 单字节写"):
                self.ctx.log(f"用户取消了 0x{addr:02X} 的写入", "user")
                return

            def job2(progress):
                dev.reg_write(ch, addr, new)
                back = None
                try:
                    back = dev.reg_read_u8(ch, addr)
                except Exception:
                    pass
                return back

            def done2(back) -> None:
                if back == new:
                    self.write_label.setText(f"已写入 0x{addr:02X} = 0x{new:02X}，回读一致 ✔")
                    self.write_label.setStyleSheet("color:#8fbf6f;")
                else:
                    self.write_label.setText(
                        f"已写入 0x{addr:02X} = 0x{new:02X}，但回读为 "
                        f"{('（读取失败）' if back is None else hex(back))} —— 可能被器件限幅或该位只读")
                    self.write_label.setStyleSheet("color:#ff5555;")
                self.last_values[addr] = back if back is not None else new
                self.ctx.log(f"REG_WRITE CH{ch} 0x{addr:02X} = 0x{new:02X}"
                             f"（回读 {('—' if back is None else hex(back))}）", "user")

            self.ctx.task(job2, on_done=done2, name="REG_WRITE")

        self.ctx.task(job, on_done=done, name="REG_READ(写前)")

    def _write_bits(self) -> None:
        if not self.ctx.require_device(self):
            return
        mask, value, notes = self.bit_editor.changes()
        if mask == 0:
            self.ctx.warn(self, "未选择位域", "请勾选要修改的位域再执行位域写。")
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        addr = self.current_addr()
        predicted = self.bit_editor.predicted_byte()

        def job(progress):
            try:
                old = dev.reg_read_u8(ch, addr)
            except Exception:
                old = None
            return old

        def done(old) -> None:
            detail = (f"位域：{'；'.join(notes)}\n"
                      f"mask = 0x{mask:02X}, value = 0x{value:02X}")
            if not self._confirm_write(ch, addr, old, predicted if old is None
                                       else (old & ~mask) | (value & mask),
                                       "REG_UPDATE 位域写（读-改-写）"):
                self.ctx.log(f"用户取消了 0x{addr:02X} 的位域写", "user")
                return

            def job2(progress):
                resp = dev.reg_update(ch, addr, mask, value)
                return resp

            def done2(resp: K.RegUpdateResp) -> None:
                self.write_label.setText(
                    f"REG_UPDATE 完成：old=0x{resp.old:02X} → new=0x{resp.new:02X}\n{detail}")
                self.write_label.setStyleSheet("color:#8fbf6f;")
                self.last_values[addr] = resp.new
                self.bitview.show_value(resp.new, (self.book.get(addr) or {}).get("bits") or [],
                                        title=f"0x{addr:02X} 写后 = 0x{resp.new:02X}")
                self.ctx.log(f"REG_UPDATE CH{ch} 0x{addr:02X} mask=0x{mask:02X} "
                             f"value=0x{value:02X} → 0x{resp.old:02X}→0x{resp.new:02X}", "user")

            self.ctx.task(job2, on_done=done2, name="REG_UPDATE")

        self.ctx.task(job, on_done=done, name="REG_READ(写前)")

    # ==================================================================
    # Dump
    # ==================================================================

    def _dump_full(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())

        def job(progress):
            progress("读取 0x00~0x3F")
            out = dev.reg_dump_full(ch)
            progress("读取 0x40~0x69")
            return out

        def done(data: Dict[int, int]) -> None:
            self.dump = dict(data)
            self._refresh_dump_table()
            n_diff = sum(1 for a, v in data.items()
                         if (self.book.get(a, {}).get("default") is not None
                             and self.book[a]["default"] != v))
            self.dump_label.setText(
                f"CH{ch} Dump 完成：{len(data)} 个地址，其中 {n_diff} 个与手册默认值不同"
                "（红色高亮）。")
            self.ctx.log(f"REG_DUMP CH{ch} 完成：{len(data)} 字节，{n_diff} 处与默认值不同", "info")

        self.ctx.task(job, on_done=done, name="REG_DUMP", on_progress=self.dump_label.setText)

    def _refresh_dump_table(self) -> None:
        only_diff = self.chk_diff_only.isChecked()
        self.dump_table.setRowCount(0)
        for addr in sorted(self.dump):
            item = self.book.get(addr, {})
            default = item.get("default")
            value = self.dump[addr]
            diff = default is not None and default != value
            if only_diff and not diff:
                continue
            row = self.dump_table.rowCount()
            self.dump_table.insertRow(row)
            vals = [f"0x{addr:02X}", item.get("name", "（未定义/未列出）"),
                    f"0x{value:02X}", "—" if default is None else f"0x{default:02X}",
                    "★ 不同" if diff else ""]
            for col, text in enumerate(vals):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.UserRole, addr)
                if addr in C.UNDEFINED_REGISTERS:
                    cell.setForeground(Qt.GlobalColor.black)
                    cell.setBackground(Qt.GlobalColor.yellow)
                elif diff:
                    cell.setForeground(Qt.GlobalColor.white)
                    cell.setBackground(Qt.GlobalColor.darkRed)
                self.dump_table.setItem(row, col, cell)

    def _on_dump_selected(self) -> None:
        rows = self.dump_table.selectionModel().selectedRows() if self.dump_table.selectionModel() else []
        if not rows:
            return
        item = self.dump_table.item(rows[0].row(), 0)
        if item is None:
            return
        addr = item.data(Qt.UserRole)
        self.ed_addr.setText(f"0x{addr:02X}")
        self._on_addr_edit()
        value = self.dump.get(addr)
        if value is not None:
            self.last_values[addr] = value
            self.sp_value.setValue(value)
            fields = (self.book.get(addr) or {}).get("bits") or []
            self.bitview.show_value(value, fields, title=f"0x{addr:02X} = 0x{value:02X}")
            self.bit_editor.set_byte(value, fields)

    def _dump_export(self, kind: str) -> None:
        if not self.dump:
            self.ctx.warn(self, "没有数据", "请先执行整片 Dump。")
            return
        if kind == "csv":
            path, _ = QFileDialog.getSaveFileName(self, "导出 Dump CSV", "reg_dump.csv",
                                                  "CSV (*.csv)")
            if not path:
                return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["addr", "name", "value", "default", "diff"])
                    for addr in sorted(self.dump):
                        item = self.book.get(addr, {})
                        default = item.get("default")
                        writer.writerow([f"0x{addr:02X}", item.get("name", ""),
                                         f"0x{self.dump[addr]:02X}",
                                         "" if default is None else f"0x{default:02X}",
                                         "1" if (default is not None
                                                 and default != self.dump[addr]) else "0"])
            except Exception as exc:
                self.ctx.error(self, "导出失败", "写入 CSV 失败。", str(exc))
                return
        else:
            path, _ = QFileDialog.getSaveFileName(self, "导出 Dump JSON", "reg_dump.json",
                                                  "JSON (*.json)")
            if not path:
                return
            payload = {f"0x{addr:02X}": {"name": self.book.get(addr, {}).get("name", ""),
                                         "value": self.dump[addr],
                                         "default": self.book.get(addr, {}).get("default")}
                       for addr in sorted(self.dump)}
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            except Exception as exc:
                self.ctx.error(self, "导出失败", "写入 JSON 失败。", exc.__str__())
                return
        self.ctx.log(f"Dump 已导出：{path}", "user")

    # ==================================================================
    # 监视列表
    # ==================================================================

    def _watch_add(self) -> None:
        addr = self.current_addr()
        if addr in self.watch:
            return
        if len(self.watch) >= 16:
            self.ctx.warn(self, "监视列表已满", "REG_READ_MULTI 一次最多 16 项。")
            return
        self.watch.append(addr)
        row = self.watch_table.rowCount()
        self.watch_table.insertRow(row)
        item = self.book.get(addr, {})
        self.watch_table.setItem(row, 0, QTableWidgetItem(f"0x{addr:02X}"))
        self.watch_table.setItem(row, 1, QTableWidgetItem(item.get("name", "")))
        for col in (2, 3, 4):
            self.watch_table.setItem(row, col, QTableWidgetItem("—"))
        self.ctx.log(f"已加入监视列表：0x{addr:02X} {item.get('name', '')}", "user")

    def _watch_start(self) -> None:
        if not self.ctx.require_device(self):
            return
        if not self.watch:
            self.ctx.warn(self, "监视列表为空", "请先『读取』某个地址并『加入监视』。")
            return
        self._watch_timer.setInterval(self.sp_watch_period.value())
        self._watch_timer.start()
        self.ctx.log(f"开始监视 {len(self.watch)} 个寄存器"
                     f"（周期 {self.sp_watch_period.value()} ms）", "user")

    def _watch_stop(self) -> None:
        self._watch_timer.stop()
        self.ctx.log("已停止寄存器监视", "user")

    def _watch_clear(self) -> None:
        self._watch_timer.stop()
        self.watch.clear()
        self.watch_table.setRowCount(0)

    def _watch_poll(self) -> None:
        if not self.ctx.connected or not self.watch:
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        items = [(addr, 1) for addr in self.watch[:16]]

        def job(progress):
            return dev.reg_read_multi(ch, items)

        def done(data: bytes) -> None:
            if len(data) < len(items):
                return
            for row, addr in enumerate(self.watch[:16]):
                value = data[row]
                old = self.last_values.get(addr)
                self.last_values[addr] = value
                self.watch_table.setItem(row, 2, QTableWidgetItem(f"0x{value:02X}"))
                self.watch_table.setItem(row, 3, QTableWidgetItem(
                    "—" if old is None else f"0x{old:02X}"))
                changed = old is not None and old != value
                cell = QTableWidgetItem("★ 变化" if changed else "")
                if changed:
                    cell.setForeground(Qt.GlobalColor.white)
                    cell.setBackground(Qt.GlobalColor.darkYellow)
                self.watch_table.setItem(row, 4, cell)

        self.ctx.task(job, on_done=done, name="监视轮询")

    # ==================================================================
    # 状态
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        for w in (self.btn_read, self.btn_write, self.btn_update, self.btn_dump,
                  self.btn_watch_add, self.btn_watch_start):
            w.setEnabled(ok)
        if not ok:
            self._watch_timer.stop()

    def on_selection_changed(self, mask: int) -> None:
        chans = self.ctx.selected_channels()
        if chans:
            idx = self.cmb_chan.findData(chans[0])
            if idx >= 0:
                self.cmb_chan.setCurrentIndex(idx)


__all__ = ["PageRegister"]
