from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class ImuFrame:
    device: str
    seq: int
    frame_id: int
    timestamp_s: float
    received_monotonic_s: float
    euler_deg: np.ndarray
    gyro_dps: np.ndarray
    quat_wxyz: np.ndarray


def _array3(value: Any, field_name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must contain exactly 3 finite numbers") from exc
    if array.shape != (3,):
        raise ValueError(f"{field_name} must contain exactly 3 numbers")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field_name} must contain only finite numbers")
    return array


def _array4(value: Any, field_name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must contain exactly 4 finite numbers") from exc
    if array.shape != (4,):
        raise ValueError(f"{field_name} must contain exactly 4 numbers")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field_name} must contain only finite numbers")
    return array


def _finite_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not np.isfinite(result):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _finite_int(value: Any, field_name: str) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if not np.isfinite(numeric):
            raise ValueError(f"{field_name} must be a finite integer")
        if not numeric.is_integer():
            raise ValueError(f"{field_name} must be an integer")
        return int(numeric)
    return int(value)


def parse_imu_payload(payload: dict[str, Any], *, topic: str, received_monotonic_s: float) -> ImuFrame:
    if payload.get("schema") != "telex.imu.hand.v1":
        raise ValueError(f"Unsupported IMU schema: {payload.get('schema')}")

    device = str(payload.get("device", ""))
    expected_device = topic.rsplit(".", 1)[-1] if "." in topic else topic
    if device != expected_device:
        raise ValueError(f"IMU topic/device mismatch: topic={topic!r} device={device!r}")

    wrist = payload.get("wrist")
    if not isinstance(wrist, dict):
        raise ValueError("IMU payload missing wrist object")

    timestamp_s = payload.get("timestamp_s")
    if timestamp_s is None and payload.get("timestamp_ns") is not None:
        timestamp_s = _finite_float(
            payload["timestamp_ns"], "timestamp_ns"
        ) / 1_000_000_000.0

    return ImuFrame(
        device=device,
        seq=_finite_int(payload.get("seq", 0), "seq"),
        frame_id=_finite_int(payload.get("frame_id", 0), "frame_id"),
        timestamp_s=_finite_float(
            timestamp_s if timestamp_s is not None else 0.0,
            "timestamp_s",
        ),
        received_monotonic_s=_finite_float(
            received_monotonic_s, "received_monotonic_s"
        ),
        euler_deg=_array3(wrist.get("euler_deg"), "wrist.euler_deg"),
        gyro_dps=_array3(wrist.get("gyro_dps"), "wrist.gyro_dps"),
        quat_wxyz=_array4(wrist.get("quat_wxyz"), "wrist.quat_wxyz"),
    )


def fresh_imu_frame(
    frame: Optional[ImuFrame],
    *,
    now_monotonic_s: float,
    max_age_s: float,
) -> Optional[ImuFrame]:
    if frame is None:
        return None
    now_s = _finite_float(now_monotonic_s, "now_monotonic_s")
    received_s = _finite_float(
        frame.received_monotonic_s, "received_monotonic_s"
    )
    max_age = _finite_float(max_age_s, "max_age_s")
    age_s = now_s - received_s
    if age_s < 0.0 or age_s > max_age:
        return None
    return frame
