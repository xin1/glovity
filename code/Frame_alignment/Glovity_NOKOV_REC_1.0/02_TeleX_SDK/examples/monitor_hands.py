#!/usr/bin/env python3
"""硬件示例：一次 TeleXSession.read() 打印左右 Tracker + 手套数据。

用法::

    python examples/monitor_hands.py --camera 2
    python examples/monitor_hands.py --camera 2 --side left --no-imu-fusion
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许未安装时从仓库根直接跑
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from telex.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
