"""Timestamp-based hand-frame resampling with quaternion SLERP."""

from __future__ import annotations

from dataclasses import dataclass
import bisect
import math
from typing import Sequence

from .clock import ClockModel
from .protocol import GloveFrame


def _normalise(q: Sequence[float]) -> tuple[float, float, float, float] | None:
    norm = math.sqrt(sum(value * value for value in q))
    if norm < 1e-9:
        return None
    return tuple(value / norm for value in q)  # type: ignore[return-value]


def slerp(a: Sequence[float], b: Sequence[float], fraction: float) -> tuple[float, float, float, float] | None:
    qa = _normalise(a)
    qb = _normalise(b)
    if qa is None or qb is None:
        return None
    dot = sum(x * y for x, y in zip(qa, qb))
    if dot < 0.0:
        qb = tuple(-value for value in qb)
        dot = -dot
    dot = max(-1.0, min(1.0, dot))
    if dot > 0.9995:
        return _normalise(tuple(x + fraction * (y - x) for x, y in zip(qa, qb)))
    angle = math.acos(dot)
    sin_angle = math.sin(angle)
    left = math.sin((1.0 - fraction) * angle) / sin_angle
    right = math.sin(fraction * angle) / sin_angle
    return tuple(left * x + right * y for x, y in zip(qa, qb))  # type: ignore[return-value]


@dataclass(frozen=True)
class AlignedHand:
    nokov_timestamp_us: int
    hand_id: int
    source_before_seq: int
    source_after_seq: int
    interpolation_fraction: float
    recording: bool
    imu_quaternions_wxyz: tuple[tuple[float, float, float, float] | None, ...]
    wrist_gyro_dps: tuple[float, float, float] | None

    def to_dict(self) -> dict:
        return {
            "nokov_timestamp_us": self.nokov_timestamp_us,
            "hand_id": self.hand_id,
            "source_before_seq": self.source_before_seq,
            "source_after_seq": self.source_after_seq,
            "interpolation_fraction": self.interpolation_fraction,
            "recording": self.recording,
            "imu_quaternions_wxyz": [list(q) if q is not None else None for q in self.imu_quaternions_wxyz],
            "wrist_gyro_dps": list(self.wrist_gyro_dps) if self.wrist_gyro_dps is not None else None,
        }


def align_hand_at(
    frames: Sequence[GloveFrame], model: ClockModel, nokov_timestamp_us: int, *, max_gap_us: int = 100_000
) -> AlignedHand | None:
    if len(frames) < 2:
        return None
    ordered = sorted(frames, key=lambda frame: frame.timestamp_us)
    glove_target = model.to_glove(nokov_timestamp_us)
    timestamps = [frame.timestamp_us for frame in ordered]
    right = bisect.bisect_left(timestamps, glove_target)
    if right == 0 or right == len(ordered):
        return None
    before, after = ordered[right - 1], ordered[right]
    gap = after.timestamp_us - before.timestamp_us
    if gap <= 0 or gap > max_gap_us:
        return None
    fraction = (glove_target - before.timestamp_us) / gap
    quaternions = []
    for first, second in zip(before.imus, after.imus):
        if not first.valid or not second.valid:
            quaternions.append(None)
        else:
            quaternions.append(slerp(first.quaternion_wxyz, second.quaternion_wxyz, fraction))
    gyro = None
    if before.wrist_gyro_valid and after.wrist_gyro_valid:
        gyro = tuple(a + fraction * (b - a) for a, b in zip(before.wrist_gyro_dps, after.wrist_gyro_dps))
    return AlignedHand(
        nokov_timestamp_us=nokov_timestamp_us,
        hand_id=before.hand_id,
        source_before_seq=before.frame_seq,
        source_after_seq=after.frame_seq,
        interpolation_fraction=fraction,
        recording=before.recording if fraction < 1.0 else after.recording,
        imu_quaternions_wxyz=tuple(quaternions),
        wrist_gyro_dps=gyro,
    )
