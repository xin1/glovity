"""Validated parameters for the firmware live haptic experiment session.

Presets are candidates for hardware evaluation, not validated comfort claims.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

LIVE_KEEPALIVE_MS = 400
LIVE_LEASE_MS = 2000
ENVELOPE_NAMES = {0: "平稳", 1: "呼吸", 2: "脉冲", 3: "双击", 4: "衰减"}


@dataclass(frozen=True)
class LiveParams:
    frequency_hz: float = 168.0
    amplitude: int = 30
    envelope: int = 0
    rate_hz: float = 2.0
    depth: int = 100
    tracking: bool = True

    def validate(self) -> None:
        for name, low, high in (("frequency_hz", 60.0, 300.0),
                                ("rate_hz", 0.1, 30.0)):
            value = getattr(self, name)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not low <= value <= high):
                raise ValueError(f"{name} 必须为 {low}..{high} 的有限数值")
        for name, low, high in (("amplitude", 0, 77), ("envelope", 0, 4),
                                ("depth", 0, 100)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须为 {low}..{high} 的整数")
        if type(self.tracking) is not bool:
            raise ValueError("tracking 必须为布尔值")
        if self.tracking and self.frequency_hz != 168.0:
            raise ValueError("自动谐振跟踪必须使用 168.0 Hz 初始频率")

    def encode_fields(self) -> bytes:
        """Encode 8 bytes, with frequency and envelope rate rounded to 0.1 Hz."""
        self.validate()
        return struct.pack("<HBBHBB", int(self.frequency_hz * 10 + 0.5),
                           self.amplitude, self.envelope,
                           int(self.rate_hz * 10 + 0.5), self.depth,
                           int(self.tracking))


PRESETS = {
    "平稳持续": LiveParams(),
    "轻柔呼吸": LiveParams(amplitude=25, envelope=1, rate_hz=1.0, depth=80),
    "节奏脉冲": LiveParams(amplitude=35, envelope=2, rate_hz=2.0, depth=100),
    "双击节奏": LiveParams(amplitude=35, envelope=3, rate_hz=1.0, depth=100),
    "衰减敲击": LiveParams(amplitude=35, envelope=4, rate_hz=2.0, depth=100),
    "颗粒纹理": LiveParams(amplitude=25, envelope=2, rate_hz=20.0, depth=60),
}
PRESET_DESCRIPTIONS = {
    "平稳持续": "固定幅度的连续振动基线。",
    "轻柔呼吸": "幅度缓慢起伏，作为呼吸节奏的候选。",
    "节奏脉冲": "周期性幅度脉冲，比较节奏与分段感。",
    "双击节奏": "每个节奏周期内的双击候选。",
    "衰减敲击": "周期性衰减包络，比较敲击与余振感。",
    "颗粒纹理": "较快幅度变化，作为颗粒感的候选。",
}
PRESET_DESCRIPTIONS = {
    name: description + " 候选参数，需实际马达与佩戴条件验证，不承诺悦人或更舒适。"
    for name, description in PRESET_DESCRIPTIONS.items()
}
