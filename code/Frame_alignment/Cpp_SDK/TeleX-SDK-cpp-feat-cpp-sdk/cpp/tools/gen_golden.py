#!/usr/bin/env python3
"""用仓库现有 Python 实现生成 C++ 侧的金标准测试向量。

产物写到 cpp/tests/golden/：
  - protocol.json       168B 帧的构造/解析基准
  - fk_uncalib.json     未校准 FK（11 路单位四元数等若干姿势）
  - fk_calib.json       三步校准 FK（合成贴装误差，与 test_glove_fk.py 同流程）
  - rotation.json       scipy Rotation 各原语的逐点基准（含 pi 附近退化点）
  - imu_calibration_left.npy / _right.npy  合成校准文件（供 npy_loader 测试）

用法（仓库根）::

    PYTHONPATH=src python3 cpp/tools/gen_golden.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as R

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from telex.glove import fk  # noqa: E402
from telex.glove.types import GloveFrame, ImuData  # noqa: E402
from telex.hub.parse import build_test_frame, parse_glove_frame, parse_wrist_frame  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "golden"
NUM_IMUS = 11


def _dump(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def _frame_from_rots(rots: list[R], hand: str = "left") -> GloveFrame:
    imus = []
    for rot in rots:
        x, y, z, w = rot.as_quat()
        imus.append(ImuData(quat_w=w, quat_x=x, quat_y=y, quat_z=z))
    return GloveFrame(timestamp=0, imu_data=imus, frame_id=0, hand=hand)


def _quats_wxyz(rots: list[R]) -> list[list[float]]:
    out = []
    for rot in rots:
        x, y, z, w = rot.as_quat()
        out.append([float(w), float(x), float(y), float(z)])
    return out


# ---------------------------------------------------------------------------
# 1. 协议
# ---------------------------------------------------------------------------

def gen_protocol() -> None:
    cases = []
    for hand, fid, quat in (
        ("left", 7, (1.0, 0.0, 0.0, 0.0)),
        ("right", 1, (1.0, 0.0, 0.0, 0.0)),
        ("left", 65535, (0.5, 0.5, 0.5, 0.5)),
        ("right", 0, (0.0, 1.0, 0.0, 0.0)),
        # 量化边界：+1.0 与 -1.0 分别 clamp 到 32767 / -32768
        ("left", 12345, (-1.0, 0.0, 0.0, 0.0)),
    ):
        raw = build_test_frame(hand, frame_id=fid, quat_wxyz=quat)
        glove = parse_glove_frame(raw, timestamp_ms=1234)
        wrist = parse_wrist_frame(raw, timestamp_s=1.5)
        assert glove is not None and wrist is not None
        cases.append({
            "hand": hand,
            "frame_id_in": fid,
            "quat_wxyz_in": list(quat),
            "frame_hex": raw.hex(),
            "checksum": raw[-1],
            "parsed": {
                "frame_id": glove.frame_id,
                "hand": glove.hand,
                "bat_percent": wrist.bat_percent,
                "imu_quats_wxyz": [
                    [i.quat_w, i.quat_x, i.quat_y, i.quat_z] for i in glove.imu_data
                ],
                "wrist_angle_deg": [float(v) for v in wrist.angle],
                "wrist_gyro_dps": [float(v) for v in wrist.gyro],
                "wrist_quat_wxyz": [float(v) for v in wrist.quat],
            },
        })
    _dump("protocol.json", {"frame_size": 168, "cases": cases})


# ---------------------------------------------------------------------------
# 2. rotation 原语（C++ rotation.hpp 必须逐点复现）
# ---------------------------------------------------------------------------

def gen_rotation() -> None:
    rng = np.random.default_rng(20260905)
    rotvecs = [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, np.pi],                 # 恰好 pi：轴向符号退化点
        [0.0, 0.0, np.pi - 1e-9],
        [0.0, 0.0, np.pi + 1e-9],
        [np.pi, 0.0, 0.0],
        [0.0, np.pi, 0.0],
        [1e-12, 0.0, 0.0],                 # 近零：归一化会除零
        [0.3, -0.4, 1.2],
    ]
    rotvecs += rng.normal(0, 1.0, (12, 3)).tolist()

    prim = []
    for v in rotvecs:
        r = R.from_rotvec(v)
        x, y, z, w = r.as_quat()
        prim.append({
            "rotvec_in": [float(c) for c in v],
            "quat_wxyz": [float(w), float(x), float(y), float(z)],
            "rotvec_out": [float(c) for c in r.as_rotvec()],
            "matrix": [[float(c) for c in row] for row in r.as_matrix()],
            # 矩阵往返：Shepperd 分支一致性
            "matrix_roundtrip_quat_wxyz": (
                lambda q: [float(q[3]), float(q[0]), float(q[1]), float(q[2])]
            )(R.from_matrix(r.as_matrix()).as_quat()),
            "apply_unit_x": [float(c) for c in r.apply([1.0, 0.0, 0.0])],
            "apply_v": [float(c) for c in r.apply([0.3, -0.7, 0.2])],
            "inv_quat_wxyz": (
                lambda q: [float(q[3]), float(q[0]), float(q[1]), float(q[2])]
            )(r.inv().as_quat()),
        })

    # 组合：a * b
    comp = []
    for i in range(0, len(rotvecs) - 1, 2):
        a, b = R.from_rotvec(rotvecs[i]), R.from_rotvec(rotvecs[i + 1])
        qa, qb = a.as_quat(), b.as_quat()
        qc = (a * b).as_quat()
        comp.append({
            "a_wxyz": [float(qa[3]), float(qa[0]), float(qa[1]), float(qa[2])],
            "b_wxyz": [float(qb[3]), float(qb[0]), float(qb[1]), float(qb[2])],
            "ab_wxyz": [float(qc[3]), float(qc[0]), float(qc[1]), float(qc[2])],
            "ab_rotvec": [float(c) for c in (a * b).as_rotvec()],
        })

    _dump("rotation.json", {"primitives": prim, "compose": comp})


# ---------------------------------------------------------------------------
# 3. FK — 未校准路径
# ---------------------------------------------------------------------------

def gen_fk_uncalib() -> None:
    rng = np.random.default_rng(1234)
    cases = []
    poses: list[tuple[str, list[R]]] = [
        ("identity", [R.identity() for _ in range(NUM_IMUS)]),
        ("wrist_y180", [R.from_euler("y", 180, degrees=True)] + [R.identity()] * (NUM_IMUS - 1)),
        ("index_bend", None),  # 占位，下面填
        ("random", [R.from_rotvec(v) for v in rng.normal(0, 0.6, (NUM_IMUS, 3))]),
    ]
    bent = [R.identity() for _ in range(NUM_IMUS)]
    bent[3] = R.from_euler("y", -90, degrees=True)
    bent[4] = R.from_euler("y", -180, degrees=True)
    poses[2] = ("index_bend", bent)

    for name, rots in poses:
        for side in ("left", "right"):
            frame = _frame_from_rots(rots, hand=side)
            joints = fk.compute_skeleton_joints(frame, side=side)
            cases.append({
                "name": f"{name}_{side}",
                "side": side,
                "imu_quats_wxyz": _quats_wxyz(rots),
                "joints": [[float(c) for c in row] for row in joints],
            })
    # 少于 11 路 IMU → 全零
    short = GloveFrame(timestamp=0, imu_data=[ImuData(1, 0, 0, 0)] * 5, frame_id=0, hand="left")
    cases.append({
        "name": "short_frame_left",
        "side": "left",
        "imu_quats_wxyz": [[1.0, 0.0, 0.0, 0.0]] * 5,
        "joints": [[float(c) for c in row]
                   for row in fk.compute_skeleton_joints(short, side="left")],
    })
    _dump("fk_uncalib.json", {"tol": 1e-12, "cases": cases})


# ---------------------------------------------------------------------------
# 4. FK — 三步校准路径（与 tests/test_glove_fk.py 同一合成流程）
# ---------------------------------------------------------------------------

def _synth_calib(side: str, seed: int):
    rng = np.random.default_rng(seed)
    installs = [R.identity()] + [
        R.from_rotvec(rng.normal(0, 0.2, 3)) for _ in range(NUM_IMUS - 1)
    ]
    steps = [
        R.identity(),
        R.from_euler("y", 90, degrees=True),
        R.from_euler("y", -90, degrees=True),
    ]
    quats = np.zeros((3, fk.NUM_SLOTS, 4))
    for s, step in enumerate(steps):
        quats[s] = fk._build_slot_quats(_frame_from_rots([step * inst for inst in installs],
                                                         hand=side))
    return installs, quats


def gen_fk_calib() -> None:
    cases = []
    for side, seed in (("left", 42), ("right", 7)):
        installs, quats = _synth_calib(side, seed)
        npy_path = OUT / f"imu_calibration_{side}.npy"
        OUT.mkdir(parents=True, exist_ok=True)
        np.save(npy_path, {"imu_calib_quats": quats, "side": side}, allow_pickle=True)
        print(f"  wrote {npy_path.relative_to(ROOT)}")

        flat = [R.identity() * inst for inst in installs]
        bent = list(flat)
        bend = R.from_euler("y", -90, degrees=True)
        bent[3] = bend * bent[3]
        bent[4] = bend * bend * bent[4]
        rigid_g = R.from_euler("zyx", [40, -25, 70], degrees=True)
        rigid = [rigid_g * r for r in flat]

        for name, rots in (("flat", flat), ("index_bend", bent), ("rigid_rot", rigid)):
            frame = _frame_from_rots(rots, hand=side)
            joints = fk.compute_skeleton_joints(frame, side=side, calib_path=str(npy_path))
            cases.append({
                "name": f"{name}_{side}",
                "side": side,
                "calib_npy": npy_path.name,
                "imu_quats_wxyz": _quats_wxyz(rots),
                "joints": [[float(c) for c in row] for row in joints],
            })

        # 校准文件本体也导出为 JSON，便于 npy_loader 单测比对
        _dump(f"calib_{side}.json", {
            "side": side,
            "imu_calib_quats_xyzw": [[[float(c) for c in q] for q in pose] for pose in quats],
        })

    _dump("fk_calib.json", {"tol": 1e-9, "cases": cases})


def gen_proto2_fixture() -> None:
    """手工拼一个 pickle protocol 2 + numpy.core.multiarray 的校准文件。

    现场校准工具若跑在 numpy 1.x 上，产出的就是这种形态：模块名不带下划线，
    且原始字节经 ``_codecs.encode(latin1)`` 承载。开发机上的 numpy 2.x 只会
    产 protocol 4，不做这个 fixture 就测不到该路径。
    """
    import pickle

    src = OUT / "imu_calibration_left.npy"
    data = np.load(src, allow_pickle=True).item()
    payload = {"imu_calib_quats": data["imu_calib_quats"], "side": data["side"]}

    body = pickle.dumps(np.array(payload, dtype=object), protocol=2)
    body = body.replace(b"numpy._core.multiarray", b"numpy.core.multiarray")

    head = b"{'descr': '|O', 'fortran_order': False, 'shape': (), }"
    head = head + b" " * (64 - ((10 + len(head) + 1) % 64)) + b"\n"
    blob = b"\x93NUMPY\x01\x00" + len(head).to_bytes(2, "little") + head + body

    path = OUT / "imu_calibration_left_proto2.npy"
    path.write_bytes(blob)
    # 自检：numpy 必须能读回，且与源文件数值一致
    back = np.load(path, allow_pickle=True).item()
    assert back["side"] == data["side"]
    assert np.array_equal(back["imu_calib_quats"], data["imu_calib_quats"])
    print(f"  wrote {path.relative_to(ROOT)} (pickle protocol 2 / numpy.core.multiarray)")


def main() -> int:
    print(f"生成金标准 → {OUT.relative_to(ROOT)}")
    gen_protocol()
    gen_rotation()
    gen_fk_uncalib()
    gen_fk_calib()
    gen_proto2_fixture()
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
