# -*- coding: utf-8 -*-
"""``awtest.script`` —— 测试脚本引擎与报告导出（纯逻辑，无 GUI 依赖）。

子模块：

* :mod:`awtest.script.schema` —— 脚本 JSON 格式定义、校验与预设脚本枚举
* :mod:`awtest.script.engine` —— 执行引擎（断言 / 人工读数 / 循环 / 中止）
* :mod:`awtest.script.report` —— CSV 与 Markdown 报告导出

**本包及其子模块一律不得 import PySide6 / pyqtgraph**（`05` 文档 §2 硬性要求）。
"""

from __future__ import annotations

from .engine import RunResult, ScriptEngine, StepResult
from .report import (CSV_HEADER, build_markdown, default_report_paths,
                     status_cn, write_csv, write_markdown)
from .schema import (ASSERT_OPS, OPS, Step, TestScript, list_preset_scripts,
                     load_script, parse_script, preset_scripts_dir,
                     save_script, validate_script)

__all__ = [
    "OPS", "ASSERT_OPS", "Step", "TestScript",
    "parse_script", "load_script", "validate_script", "save_script",
    "list_preset_scripts", "preset_scripts_dir",
    "ScriptEngine", "StepResult", "RunResult",
    "write_csv", "write_markdown", "build_markdown", "default_report_paths",
    "CSV_HEADER", "status_cn",
]
