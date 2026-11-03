"""Shared runtime defaults."""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from telex.tracker.vision.rig import LEFT_MARKER_IDS, RIGHT_MARKER_IDS

DEFAULT_TARGET_IDS: Tuple[int, ...] = tuple(
    sorted(set(LEFT_MARKER_IDS.values()) | set(RIGHT_MARKER_IDS.values()))
)
DEFAULT_MARKER_SIZE_MM = 28.7
# 相机整体朝向前下方；mid 坐标轴绕自身 +X 上抬该角度后，Y 水平朝前、Z 朝上。
DEFAULT_MID_PITCH_UP_DEG = 60.0

_SQRT2_OVER_2 = 0.5 * float(np.sqrt(2.0))
DEFAULT_BASE_TO_IMU_ROTATION = np.array(
    [
        [0.0, -1.0, 0.0],
        [_SQRT2_OVER_2, 0.0, _SQRT2_OVER_2],
        [-_SQRT2_OVER_2, 0.0, _SQRT2_OVER_2],
    ],
    dtype=np.float64,
)
# 左右结构已拆分；当前旋转数值暂同，后续可按手替换。
DEFAULT_BASE_TO_IMU_BY_HAND: Dict[str, np.ndarray] = {
    "left": DEFAULT_BASE_TO_IMU_ROTATION,
    "right": DEFAULT_BASE_TO_IMU_ROTATION,
}
