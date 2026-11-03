#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线跑通全部预设测试脚本并生成示例报告（**不需要真实硬件**）。

它把 `tests/fake_device.FakeDeviceTransport`（行为级假设备）接到 `ScriptEngine` 上，
按脚本里的 `prompt` 自动填一个示例读数，然后导出 CSV + Markdown 到 `reports/`。

用途：
  1. 交付物要求的"5 个预设脚本 + 一份跑出来的示例报告"；
  2. 在硬件到位之前验证脚本 schema、引擎与报告格式；
  3. 回归：脚本改动后能立刻看出哪一步会失败。

用法：
    cd host
    python tools/offline_report.py            # 输出到 ../reports/
    python tools/offline_report.py --out DIR
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
ROOT = os.path.dirname(HOST)
sys.path.insert(0, HOST)
sys.path.insert(0, os.path.join(HOST, "tests"))

from awtest.device import AWTestDevice                   # noqa: E402
from awtest.script import engine as S                    # noqa: E402
from awtest.script import report as R                    # noqa: E402
from awtest.script import schema as SC                   # noqa: E402
from fake_device import FakeDeviceTransport              # noqa: E402


def fake_prompt(message: str, field: str, unit: str):
    """无人值守时的示例读数：按字段名给一个像样的值。"""
    canned = {
        "vpp": ("1.85", "V"),
        "current": ("0.32", "A"),
        "trig_delay": ("412", "µs"),
        "temp_rise": ("6.5", "°C"),
        "vreg": ("1.51", "V"),
        "confirm": ("y", ""),
    }
    for key, (val, _u) in canned.items():
        if key in (field or "").lower() or key in (message or "").lower():
            return val
    return "0"


def build_device() -> AWTestDevice:
    dev = AWTestDevice(FakeDeviceTransport())
    dev.connect("FAKE-OFFLINE")
    return dev


def main() -> int:
    ap = argparse.ArgumentParser(description="离线跑通预设测试脚本并生成示例报告")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"),
                    help="报告输出目录（默认 <仓库>/reports）")
    ap.add_argument("--scripts", default=None, help="脚本目录（默认包内 resources/scripts）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scripts = SC.list_preset_scripts(args.scripts)
    if not scripts:
        print("未找到任何预设脚本", file=sys.stderr)
        return 2

    print(f"离线运行 {len(scripts)} 个预设脚本 → {args.out}\n")
    failures = 0
    for name, path in scripts:
        dev = build_device()
        script = SC.load_script(path)
        eng = S.ScriptEngine(dev, script, prompt_handler=fake_prompt)
        run = eng.run()
        run.port = "FAKE-OFFLINE"
        run.version = "offline"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(args.out, f"{os.path.splitext(os.path.basename(path))[0]}")
        csv_path = R.write_csv(run, base + ".csv")
        md_path = R.write_markdown(run, base + ".md", meta={
            "测试日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "板卡": "Vibration_Test_Board_A_V1.0（离线模拟，非真实硬件）",
            "运行模式": "offline（假设备）",
        })
        n_pass = sum(1 for s in run.steps if s.status == "pass")
        n_fail = sum(1 for s in run.steps if s.status == "fail")
        n_skip = sum(1 for s in run.steps if s.status == "skip")
        if run.status == "fail":
            failures += 1
        print(f"  {name}\n    status={run.status}  通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}")
        print(f"    → {os.path.relpath(csv_path, ROOT)}")
        print(f"    → {os.path.relpath(md_path, ROOT)}")
        _ = stamp

    print()
    if failures:
        print(f"*** {failures} 个脚本存在失败步骤（离线模拟下属预期，请核对脚本本身）***")
        return 1
    print("全部预设脚本离线跑通。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
