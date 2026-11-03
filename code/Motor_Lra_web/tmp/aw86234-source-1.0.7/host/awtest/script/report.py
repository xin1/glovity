# -*- coding: utf-8 -*-
"""测试报告导出（CSV / Markdown）。

* :func:`write_csv` 的列固定为
  ``step_index,matrix_id,op,chan,params,expected,actual,status,elapsed_ms,note``
  （`05` 文档 §5.9 与接口约定 §3）。
* :func:`write_markdown` 的结构刻意与 ``06_验收测试与调试清单.md`` §5/§6 的记录表
  口径对齐，保证硬件测试记录之间可以互相引用：

  1. 标题（脚本名 + 矩阵编号）
  2. **关键实测值**表 —— ``run.manual`` 的全部人工读数 + ``meta`` 里的
     测试日期 / 板号 / 固件版本 / 电源等
  3. **汇总**表 —— 总数 / 通过 / 失败 / 跳过 + 总耗时
  4. **详细步骤**表 —— 步骤号 / 操作 / 通道 / 参数 / 期望 / 实际 / 判定 / 耗时 / 备注
  5. 结论段

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .engine import RunResult

#: CSV 表头（顺序不可改，界面与外部工具按此解析）
CSV_HEADER = ["step_index", "matrix_id", "op", "chan", "params", "expected",
              "actual", "status", "elapsed_ms", "note"]

#: 关键实测值表里优先展示的元数据字段（其余 meta 项按原顺序追加）
META_ORDER = ["测试日期", "板号", "固件版本", "电源", "上位机版本", "端口",
              "环境温度", "操作者", "备注"]

STATUS_CN = {"pass": "通过", "fail": "失败", "skip": "跳过", "running": "进行中",
             "abort": "已中止"}


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


def _cell(value: Any) -> str:
    """把任意值转成单行 Markdown 表格单元格。"""
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text.strip()


def _json_text(value: Any) -> str:
    import json
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _flat(value: Any) -> str:
    """CSV 单元格：换行折成空格，保证一条记录只占一行。"""
    text = "" if value is None else str(value)
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def status_cn(status: str) -> str:
    """状态英文 -> 中文（未知状态原样返回）。"""
    return STATUS_CN.get(status, status)


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------


def write_csv(run: RunResult, path: str) -> str:
    """导出逐步骤 CSV，返回写出的路径。"""
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for step in run.steps:
            writer.writerow([
                step.index,
                step.matrix_id,
                step.op,
                "" if step.chan is None else step.chan,
                _flat(_json_text(step.params)),
                _flat(step.expected),
                _flat(step.actual),
                step.status,
                f"{step.elapsed_ms:.1f}",
                _flat(step.note),
            ])
    return path


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def write_markdown(run: RunResult, path: str, meta: Optional[Dict[str, Any]] = None) -> str:
    """导出 Markdown 报告，返回写出的路径。"""
    _ensure_dir(path)
    text = build_markdown(run, meta)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def build_markdown(run: RunResult, meta: Optional[Dict[str, Any]] = None) -> str:
    """生成 Markdown 报告文本（界面可直接预览）。"""
    meta = dict(meta or {})
    script = run.script
    lines: List[str] = []

    # ---- 1. 标题 ----
    lines.append(f"# 测试报告 · {script.name or os.path.basename(script.path or '未命名脚本')}")
    lines.append("")
    matrix = "、".join(script.matrix_ids) if script.matrix_ids else "未标注"
    lines.append(f"- **测试矩阵编号**：{matrix}")
    if script.path:
        lines.append(f"- **脚本文件**：`{script.path}`")
    lines.append(f"- **开始时间**：{run.started_at}")
    lines.append(f"- **结束时间**：{run.finished_at or '（未结束）'}")
    lines.append(f"- **总耗时**：{run.elapsed_ms:.0f} ms")
    if run.port:
        lines.append(f"- **串口**：{run.port}")
    if run.version:
        lines.append(f"- **上位机版本**：{run.version}")
    lines.append(f"- **整体结论**：**{status_cn(run.status)}**")
    if script.description:
        lines.append("")
        lines.append(f"> {script.description}")
    lines.append("")

    # ---- 2. 关键实测值 ----
    lines.append("## 关键实测值")
    lines.append("")
    lines.append("| 项目 | 数值 | 来源 |")
    lines.append("|---|---|---|")
    manual_fields = set()
    for name, value in run.manual.items():
        manual_fields.add(name)
        lines.append(f"| {_cell(name)} | {_cell(value)} | 人工录入（prompt） |")
    ordered_meta: List[str] = [k for k in META_ORDER if k in meta]
    ordered_meta += [k for k in meta if k not in ordered_meta]
    for key in ordered_meta:
        if key in manual_fields:
            continue
        lines.append(f"| {_cell(key)} | {_cell(meta[key])} | 报告元数据 |")
    if not run.manual and not ordered_meta:
        lines.append("| （无） | — | 本次运行没有人工读数与元数据 |")
    lines.append("")
    lines.append("> 人工读数由脚本的 `prompt` 步骤收集；"
                 "测试日期 / 板号 / 固件版本 / 电源等由报告元数据带入，"
                 "与 `06_验收测试与调试清单.md` §5 的实测值表口径一致。")
    lines.append("")

    # ---- 3. 汇总 ----
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 统计项 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 步骤总数 | {run.total} |")
    lines.append(f"| 通过 | {run.passed} |")
    lines.append(f"| 失败 | {run.failed} |")
    lines.append(f"| 跳过 | {run.skipped} |")
    lines.append(f"| 总耗时 | {run.elapsed_ms:.0f} ms |")
    lines.append(f"| 执行状态 | {status_cn(run.status)} |")
    lines.append("")

    # ---- 4. 详细步骤 ----
    lines.append("## 详细步骤")
    lines.append("")
    lines.append("| 步骤号 | 操作 | 通道 | 参数 | 期望 | 实际 | 判定 | 耗时(ms) | 备注 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for step in run.steps:
        note = step.note
        if len(script.matrix_ids) > 1 and step.matrix_id:
            note = f"[{step.matrix_id}] {note}".strip()
        lines.append("| " + " | ".join([
            str(step.index),
            _cell(step.op),
            "" if step.chan is None else str(step.chan),
            _cell(_json_text(step.params)),
            _cell(step.expected),
            _cell(step.actual),
            status_cn(step.status),
            f"{step.elapsed_ms:.1f}",
            _cell(note),
        ]) + " |")
    if not run.steps:
        lines.append("| — | — | — | — | — | — | — | — | 没有执行任何步骤 |")
    lines.append("")

    # ---- 5. 结论 ----
    lines.append("## 结论")
    lines.append("")
    if run.status == "pass":
        lines.append(f"脚本「{script.name}」全部 {run.total} 步执行完毕，"
                     f"**{run.passed} 步通过、{run.skipped} 步跳过、无失败项**，"
                     f"对应矩阵项 {matrix} 判为通过。")
    elif run.status == "abort":
        lines.append(f"脚本「{script.name}」被用户中止，已执行 {run.total} 步"
                     f"（通过 {run.passed} / 失败 {run.failed} / 跳过 {run.skipped}）。"
                     f"中止前的结果可供参考，但**本次运行不构成验收结论**。")
    else:
        failed = [s for s in run.steps if s.status == "fail"]
        lines.append(f"脚本「{script.name}」有 **{len(failed)} 步判定失败**，"
                     f"对应矩阵项 {matrix} **未通过**。失败步骤：")
        lines.append("")
        for step in failed:
            reason = step.note or step.actual or "（无说明）"
            lines.append(f"- 第 {step.index} 步 `{step.op}`：{reason}")
    skipped = [s for s in run.steps if s.status == "skip"]
    if skipped:
        lines.append("")
        lines.append("跳过项说明（需要人工补测或补齐条件）：")
        lines.append("")
        for step in skipped:
            lines.append(f"- 第 {step.index} 步 `{step.op}`：{step.note or '（无说明）'}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 默认路径
# --------------------------------------------------------------------------


def default_report_paths(prefix: str = "report",
                         directory: str = "reports") -> Tuple[str, str]:
    """生成一对带时间戳的报告路径 ``(csv, markdown)``。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{prefix}_{stamp}"
    return (os.path.join(directory, base + ".csv"),
            os.path.join(directory, base + ".md"))
