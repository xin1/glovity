# -*- coding: utf-8 -*-
"""波形 CSV 导入导出（`05` 文档 §5.5 "CSV 导入导出"）。

* :func:`load_csv` 支持 **1 列（幅度）** 与 **2 列（时间, 幅度）**，两种都允许带表头；
  也容忍 ``#`` 注释行、空行、分号/制表符分隔以及 UTF-8 BOM。
  越界样本会被**裁剪到 -127..127 并记录**（通过 ``notes`` 参数或
  :data:`LAST_LOAD_NOTES` 取回说明文案）。
* :func:`save_csv` 固定写出 ``time_ms,amplitude`` 两列 + 表头，
  给定时基后时间列按 ``i * 1000 / sample_rate`` 计算（毫秒）。

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import csv
import os
import re
from typing import List, Optional, Sequence, Set

from .generator import SAMPLE_MAX, SAMPLE_MIN, clip

#: 最近一次 :func:`load_csv` 的说明文案（表头跳过、越界裁剪条数等）
LAST_LOAD_NOTES: List[str] = []

_SPLIT_RE = re.compile(r"[,;\t]+")


def _format_time(value: float) -> str:
    """时间列格式化：去掉多余的尾随 0，但保留足够精度。"""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _split_fields(line: str) -> List[str]:
    """按逗号/分号/制表符切分；没有分隔符时按空白切分。"""
    if _SPLIT_RE.search(line):
        parts = _SPLIT_RE.split(line)
    else:
        parts = line.split()
    return [p.strip() for p in parts if p.strip() != ""]


def load_csv(path: str, notes: Optional[List[str]] = None) -> List[int]:
    """读入 CSV 波形，返回有符号样本列表。

    :param path: CSV 文件路径
    :param notes: 可选，收集说明文案（表头跳过、无法解析的行、越界裁剪条数）
    :raises ValueError: 文件里没有任何可解析的样本
    """
    note_list: List[str] = notes if notes is not None else []
    note_list.clear()

    with open(path, "r", encoding="utf-8-sig") as fh:
        lines = fh.read().splitlines()

    out: List[int] = []
    clipped_lines: List[int] = []
    bad_lines = 0
    header_done = False

    for lineno, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or text.startswith("#") or text.startswith("//"):
            continue
        fields = _split_fields(text)
        if not fields:
            continue
        numbers: List[float] = []
        parse_failed: Optional[Set[str]] = None
        for field in fields:
            try:
                numbers.append(float(field))
            except ValueError:
                parse_failed = {field}
                break
        if parse_failed is not None:
            if not header_done and not out:
                # 第一行解析不出来 -> 当成表头
                header_done = True
                note_list.append(f"第 {lineno} 行按表头跳过：{text}")
            else:
                bad_lines += 1
                note_list.append(f"第 {lineno} 行无法解析，已跳过：{text}")
            continue
        header_done = True
        # 1 列 = 幅度；2 列及以上 = 时间,幅度（多余的列忽略，取第二列）
        value = numbers[0] if len(numbers) == 1 else numbers[1]
        ivalue = int(round(value))
        if ivalue < SAMPLE_MIN or ivalue > SAMPLE_MAX:
            clipped_lines.append(lineno)
            ivalue = clip(ivalue)
        out.append(ivalue)

    if bad_lines:
        note_list.append(f"共跳过 {bad_lines} 行无法解析的内容")
    if clipped_lines:
        shown = "、".join(str(n) for n in clipped_lines[:10])
        more = "…" if len(clipped_lines) > 10 else ""
        note_list.append(f"{len(clipped_lines)} 个越界样本已裁剪到 "
                         f"{SAMPLE_MIN}..{SAMPLE_MAX}（行号：{shown}{more}）")
    if not out:
        raise ValueError(f"CSV 中没有解析到任何样本：{path}")

    LAST_LOAD_NOTES[:] = list(note_list)
    return out


def save_csv(path: str, samples: Sequence[int], sample_rate: Optional[int] = None) -> None:
    """把样本写成 ``time_ms,amplitude`` 两列 + 表头。

    ``sample_rate`` 为 ``None`` 时时间列直接写样本序号。
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["time_ms", "amplitude"])
        for index, sample in enumerate(samples):
            if sample_rate:
                time_ms = index * 1000.0 / float(sample_rate)
                writer.writerow([_format_time(time_ms), int(sample)])
            else:
                writer.writerow([str(index), int(sample)])
