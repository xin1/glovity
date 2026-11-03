# -*- coding: utf-8 -*-
"""随包资源目录：寄存器地址簿（``register_book.json``）、预设 Profile、预设测试脚本。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))

REGISTER_BOOK_PATH = os.path.join(HERE, "register_book.json")
PROFILE_DIR = os.path.join(HERE, "profiles")
SCRIPT_DIR = os.path.join(HERE, "scripts")


def load_register_book(path: Optional[str] = None) -> Dict[str, Any]:
    """加载寄存器地址簿；文件缺失或损坏时返回空簿（界面不会崩）。"""
    p = path or REGISTER_BOOK_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"version": "", "registers": [], "notes": [], "error": f"无法读取 {p}"}
    if not isinstance(data.get("registers"), list):
        data["registers"] = []
    return data


def register_map(path: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
    """返回 {地址: 条目}，重复地址以先出现的为准。"""
    out: Dict[int, Dict[str, Any]] = {}
    for item in load_register_book(path).get("registers", []):
        try:
            addr = int(item.get("addr"))
        except Exception:
            continue
        out.setdefault(addr, item)
    return out


__all__ = ["REGISTER_BOOK_PATH", "PROFILE_DIR", "SCRIPT_DIR",
           "load_register_book", "register_map"]
