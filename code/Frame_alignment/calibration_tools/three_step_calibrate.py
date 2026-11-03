"""Capture three glove poses and write an SDK-compatible calibration .npy."""

from __future__ import annotations

import argparse
from pathlib import Path
import time

import numpy as np
from scipy.spatial.transform import Rotation

from frame_alignment.protocol import AutoStreamDecoder, GloveFrame


FRAME_TO_SLOT = (0, 13, 14, 1, 2, 4, 5, 10, 11, 7, 8)
PIP_SLOTS = (2, 5, 8, 11, 14)
DIP_SLOTS = (3, 6, 9, 12, 15)


def mean_quaternion_wxyz(values: list[tuple[float, float, float, float]]) -> np.ndarray:
    if not values:
        raise ValueError("没有四元数样本")
    array = np.asarray(values, dtype=np.float64)
    norms = np.linalg.norm(array, axis=1)
    array = array[norms > 1e-6]
    if len(array) == 0:
        raise ValueError("全部四元数均无效")
    array /= np.linalg.norm(array, axis=1, keepdims=True)
    reference = array[0]
    array[np.sum(array * reference, axis=1) < 0] *= -1
    result = np.mean(array, axis=0)
    return result / np.linalg.norm(result)


def frame_quaternions_to_slots(imu_wxyz: np.ndarray) -> np.ndarray:
    """Map 11 averaged IMUs to the exact 16-slot xyzw format consumed by the SDK."""
    if imu_wxyz.shape != (11, 4):
        raise ValueError(f"期望 (11,4)，实际 {imu_wxyz.shape}")
    slots = Rotation.identity(16)
    rotations = list(slots)
    for frame_index, slot in enumerate(FRAME_TO_SLOT):
        w, x, y, z = imu_wxyz[frame_index]
        rotations[slot] = Rotation.from_quat((x, y, z, w))
    rotations[0] = rotations[0] * Rotation.from_quat((0.0, 1.0, 0.0, 0.0))
    for pip, dip in zip(PIP_SLOTS, DIP_SLOTS):
        rotations[dip] = rotations[pip]
    return np.asarray([rotation.as_quat() for rotation in rotations], dtype=np.float64)


def capture_pose(port, hand_id: int, samples: int, timeout: float) -> np.ndarray:
    decoder = AutoStreamDecoder()
    collected: list[list[tuple[float, float, float, float]]] = [[] for _ in range(11)]
    accepted = 0
    deadline = time.monotonic() + timeout
    while accepted < samples and time.monotonic() < deadline:
        chunk = port.read(max(1, port.in_waiting))
        for frame in decoder.feed(chunk):
            if not isinstance(frame, GloveFrame) or frame.hand_id != hand_id:
                continue
            if frame.imu_fail_mask:
                continue
            for index, imu in enumerate(frame.imus):
                collected[index].append(imu.quaternion_wxyz)
            accepted += 1
            print(f"\r已采集 {accepted}/{samples} 帧", end="", flush=True)
            if accepted >= samples:
                break
    print()
    if accepted < samples:
        raise TimeoutError(f"有效帧不足：{accepted}/{samples}；请检查手别、输出使能和串口")
    averaged = np.asarray([mean_quaternion_wxyz(items) for items in collected])
    return frame_quaternions_to_slots(averaged)


def run(port_name: str, baud: int, side: str, output: Path,
        samples: int, timeout: float, yes: bool) -> None:
    import serial

    hand_id = 0 if side == "left" else 1
    prompts = (
        "步骤1/3：手掌和所有手指保持完全平直，按基准方向平放",
        "步骤2/3：保持整只手平直，将整只手绕纵向参考轴旋转 +90°",
        "步骤3/3：保持整只手平直，将整只手绕纵向参考轴旋转 -90°",
    )
    poses: list[np.ndarray] = []
    with serial.Serial(port_name, baud, timeout=0.05) as port:
        for prompt in prompts:
            print("\n" + prompt)
            if not yes:
                input("姿态稳定后按 Enter 开始采集……")
            port.reset_input_buffer()
            poses.append(capture_pose(port, hand_id, samples, timeout))

    payload = {
        "imu_calib_quats": np.stack(poses, axis=0),
        "side": side,
    }
    if payload["imu_calib_quats"].shape != (3, 16, 4):
        raise AssertionError("内部校准数据形状错误")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(output, payload, allow_pickle=True)
    print(f"校准文件已保存：{output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Glovity 手套三步姿态校准")
    parser.add_argument("--port", required=True, help="手套或接收器正常数据串口，如 COM5")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--side", required=True, choices=("left", "right"))
    parser.add_argument("--output", type=Path, help="输出 .npy 文件")
    parser.add_argument("--samples", type=int, default=60, help="每个姿态平均的有效帧数")
    parser.add_argument("--timeout", type=float, default=10.0, help="每个姿态采集超时秒数")
    parser.add_argument("--yes", action="store_true", help="不等待 Enter，仅供自动测试")
    args = parser.parse_args()
    output = args.output or Path(f"imu_calibration_{args.side}.npy")
    run(args.port, args.baud, args.side, output, args.samples, args.timeout, args.yes)


if __name__ == "__main__":
    main()
