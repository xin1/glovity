# -*- coding: utf-8 -*-
"""寄存器位域控件。

* :class:`BitfieldView` —— 只读解释：把 1~N 字节的值拆成 hex/dec/bin，并把每个位域
  **用文字**说明（枚举型如 ``TRG1_MODE = 0b011 (TRIG)``，见规格书 §5.8）。
* :class:`BitfieldEditor` —— 位域写：勾选要修改的位域并给新值，产出 ``REG_UPDATE``
  需要的 ``(mask, value)`` 以及给二次确认框用的"原值 → 新值"文字。

位域描述结构沿用 ``resources/register_book.json`` 的 ``bits`` 字段：
``{"name": str, "msb": int, "lsb": int, "desc": str, "enum": {code: text}}``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                               QLabel, QSpinBox, QTextBrowser, QVBoxLayout,
                               QWidget)


def _field_mask(f: Dict[str, Any]) -> int:
    msb = int(f.get("msb", 0))
    lsb = int(f.get("lsb", 0))
    return ((1 << (msb - lsb + 1)) - 1) << lsb


def _field_value(value: int, f: Dict[str, Any]) -> int:
    lsb = int(f.get("lsb", 0))
    return (value & _field_mask(f)) >> lsb


def bits_to_text(value: int, width: int = 8) -> str:
    return format(value & ((1 << width) - 1), f"0{width}b")


def explain_byte(value: int, fields: Optional[List[Dict[str, Any]]] = None) -> str:
    """给一个字节值 + 位域定义，生成富文本解释（日志、旁注也用它）。"""
    lines: List[str] = []
    lines.append(f"<b>0x{value:02X}</b> = {value} = <code>{bits_to_text(value, 8)}b</code>")
    if not fields:
        return "<br>".join(lines)
    for f in fields:
        mask = _field_mask(f)
        raw = _field_value(value, f)
        msb, lsb = int(f.get("msb", 0)), int(f.get("lsb", 0))
        rng = f"bit{msb}" if msb == lsb else f"bit{msb}:{lsb}"
        enum = f.get("enum") or {}
        text = enum.get(str(raw), enum.get(raw))
        shown = f"0b{bits_to_text(raw, msb - lsb + 1)}"
        if text:
            shown += f" ({text})"
        lines.append(
            f"&nbsp;&nbsp;<b>{f.get('name', '?')}</b> [{rng}] = {shown}"
            f" <span style='color:#888'>mask=0x{mask:02X}</span>"
            + (f" <span style='color:#aaa'>— {f.get('desc')}</span>" if f.get("desc") else ""))
    return "<br>".join(lines)


class BitfieldView(QTextBrowser):
    """只读位域解释视图。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setMinimumHeight(70)
        self.setStyleSheet("font-family:Consolas,monospace; font-size:12px;")

    def show_value(self, value: int, fields: Optional[List[Dict[str, Any]]] = None,
                   title: str = "") -> None:
        body = explain_byte(value, fields)
        if title:
            body = f"<b>{title}</b><br>" + body
        self.setHtml(body)

    def show_text(self, text: str) -> None:
        self.setHtml(text)


class BitfieldEditor(QGroupBox):
    """位域写：勾选要改的位域 → 返回 ``(mask, value)``。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("位域写（REG_UPDATE：只改勾选的位域，其余位保持）", parent)
        self._rows: List[Tuple[Dict[str, Any], QCheckBox, QWidget, QLabel]] = []
        self._body = QWidget(self)
        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self._body)
        self._form = QVBoxLayout(self._body)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._value = 0
        self._fields: List[Dict[str, Any]] = []
        self._empty = QLabel("（该地址没有位域描述，可直接用『单字节写』）")
        self._empty.setStyleSheet("color:#888;")
        self._form.addWidget(self._empty)

    # ---- 构建 ----

    def set_byte(self, value: int, fields: Optional[List[Dict[str, Any]]]) -> None:
        self._value = int(value) & 0xFF
        self._fields = list(fields or [])
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._rows.clear()
        if not self._fields:
            self._empty = QLabel("（该地址没有位域描述，可直接用『单字节写』）")
            self._empty.setStyleSheet("color:#888;")
            self._form.addWidget(self._empty)
            return
        for f in self._fields:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            msb, lsb = int(f.get("msb", 0)), int(f.get("lsb", 0))
            rng = f"bit{msb}" if msb == lsb else f"bit{msb}:{lsb}"
            cur = _field_value(self._value, f)
            chk = QCheckBox(f"{f.get('name', '?')} [{rng}]")
            chk.setToolTip((f.get("desc") or "") + f"\n当前值：{cur}")
            h.addWidget(chk, 2)
            enum = f.get("enum") or {}
            if enum:
                editor: QWidget = QComboBox()
                for code_key, label in enum.items():
                    try:
                        code = int(code_key)
                    except Exception:
                        continue
                    editor.addItem(f"{label}（{code}）", code)
                idx = editor.findData(cur)
                if idx >= 0:
                    editor.setCurrentIndex(idx)
            else:
                editor = QSpinBox()
                width = msb - lsb + 1
                editor.setRange(0, (1 << width) - 1)
                editor.setValue(cur)
                editor.setToolTip(f"位宽 {width}，范围 0..{(1 << width) - 1}")
            h.addWidget(editor, 1)
            cur_label = QLabel(f"当前 {cur}")
            cur_label.setStyleSheet("color:#888;")
            h.addWidget(cur_label, 1)
            self._form.addWidget(row)
            self._rows.append((f, chk, editor, cur_label))

    def clear_selection(self) -> None:
        for _f, chk, _e, _l in self._rows:
            chk.setChecked(False)

    # ---- 产出 ----

    def changes(self) -> Tuple[int, int, List[str]]:
        """返回 ``(mask, value, 说明列表)``；没有任何勾选时 mask=0。"""
        mask = 0
        value = 0
        notes: List[str] = []
        for f, chk, editor, _lbl in self._rows:
            if not chk.isChecked():
                continue
            m = _field_mask(f)
            if isinstance(editor, QComboBox):
                raw = int(editor.currentData())
            else:
                raw = int(editor.value())
            lsb = int(f.get("lsb", 0))
            value |= (raw << lsb) & m
            mask |= m
            old = _field_value(self._value, f)
            notes.append(f"{f.get('name')}: {old} → {raw}")
        return mask, value, notes

    def predicted_byte(self) -> int:
        mask, value, _ = self.changes()
        return ((self._value & ~mask) | (value & mask)) & 0xFF

    def describe(self) -> str:
        _m, _v, notes = self.changes()
        return "；".join(notes) if notes else "（未选择任何位域）"


__all__ = ["BitfieldView", "BitfieldEditor", "explain_byte", "bits_to_text"]
