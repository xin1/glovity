"""Glove FK 单元测试：三步校准消费 + 21 关节骨架几何正确性（无硬件）。

合成流程与 Remote_USB_ThreeStepCalibration/selftest_synthetic.py 相同：
给每颗 IMU 一个随机贴装误差，生成三步校准姿势 + 任意手势帧，
验证 FK 能通过校准文件把贴装误差消掉、骨架几何符合物理预期。
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R

from telex.glove import fk
from telex.glove.types import GloveFrame, ImuData

NUM_IMUS = 11

# 帧内 IMU 下标 → 16 槽位（与 fk.FRAME_INDEX_TO_SLOT 一致，用于构造合成数据）
SLOT_OF = fk.FRAME_INDEX_TO_SLOT


def _make_frame(world_rots: list[R], hand: str = "left") -> GloveFrame:
    """由 11 个世界旋转构造 GloveFrame（quat wxyz）。"""
    imus = []
    for rot in world_rots:
        x, y, z, w = rot.as_quat()
        imus.append(ImuData(quat_w=w, quat_x=x, quat_y=y, quat_z=z))
    return GloveFrame(timestamp=0, imu_data=imus, frame_id=0, hand=hand)


def _synth_world_rots(step_rot: R, installs: list[R]) -> list[R]:
    """某一姿势下 11 颗 IMU 的世界旋转 = 姿势旋转 × 各自贴装误差。"""
    return [step_rot * inst for inst in installs]


@pytest.fixture()
def calib_env(tmp_path):
    """生成带贴装误差的合成三步校准 npy + 平展帧（左手）。"""
    rng = np.random.default_rng(42)
    installs = [R.identity()] + [
        R.from_rotvec(rng.normal(0, 0.2, 3)) for _ in range(NUM_IMUS - 1)
    ]
    # 步骤1 平放；步骤2/3 绕世界 y 轴 ±90 度（同真实校准动作）
    steps = [
        R.identity(),
        R.from_euler("y", 90, degrees=True),
        R.from_euler("y", -90, degrees=True),
    ]

    calib_quats = np.zeros((3, fk.NUM_SLOTS, 4))
    for s, step_rot in enumerate(steps):
        frame = _make_frame(_synth_world_rots(step_rot, installs))
        calib_quats[s] = fk._build_slot_quats(frame)

    path = tmp_path / "imu_calibration_left.npy"
    np.save(path, {"imu_calib_quats": calib_quats, "side": "left"},
            allow_pickle=True)
    return {
        "path": str(path),
        "installs": installs,
        "flat_frame": _make_frame(_synth_world_rots(R.identity(), installs)),
    }


def test_shape_and_wrist_origin(calib_env):
    joints = fk.compute_skeleton_joints(
        calib_env["flat_frame"], side="left", calib_path=calib_env["path"]
    )
    assert joints.shape == (21, 3)
    assert np.allclose(joints[0], 0.0)
    assert np.isfinite(joints).all()


def test_flat_pose_gives_flat_hand(calib_env):
    """平展姿势 + 校准 → 手指伸直沿 +Y、整手近似共面（z≈0）。"""
    joints = fk.compute_skeleton_joints(
        calib_env["flat_frame"], side="left", calib_path=calib_env["path"]
    )
    # 共面: 全部关节 z 分量近 0（贴装误差已被校准消除）
    assert np.abs(joints[:, 2]).max() < 0.005
    # 四指沿 +Y 递增伸直
    for chain in ([1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]):
        ys = joints[chain, 1]
        assert (np.diff(ys) > 0).all(), f"chain {chain} 未沿 +Y 伸直: {ys}"
    # 指尖距腕 ≈ 掌长 + 三节指骨（食指）
    expect = 0.085 + sum(fk.DEFAULT_BONE_LENGTHS["index"])
    assert abs(np.linalg.norm(joints[4]) - expect) < 0.01


def test_finger_bend_moves_tip_toward_palm(calib_env):
    """食指 MCP/PIP 弯曲 90 度 → 指尖朝掌心方向（-Z）卷曲，其余手指不动。"""
    installs = calib_env["installs"]
    world = _synth_world_rots(R.identity(), installs)
    # 弯曲 = 绕该 IMU 校准后局部弯曲轴转动；世界系中对应绕 y 轴（左手 z 取反后
    # 弯向掌心）。食指 MCP=帧下标3, PIP=帧下标4
    bend = R.from_euler("y", -90, degrees=True)
    world[3] = bend * world[3]
    world[4] = bend * bend * world[4]  # PIP 相对 MCP 再弯 90 度

    joints = fk.compute_skeleton_joints(
        _make_frame(world), side="left", calib_path=calib_env["path"]
    )
    flat = fk.compute_skeleton_joints(
        calib_env["flat_frame"], side="left", calib_path=calib_env["path"]
    )
    # 食指指尖显著离开平展位置且向掌心（-Z）方向卷曲
    assert np.linalg.norm(joints[4] - flat[4]) > 0.03
    assert joints[4][2] < -0.01
    # 中指等未动手指保持平展位置
    assert np.allclose(joints[5:9], flat[5:9], atol=1e-6)
    # 骨长守恒
    for a, b, name, seg in ((1, 2, "index", 0), (2, 3, "index", 1), (3, 4, "index", 2)):
        got = np.linalg.norm(joints[b] - joints[a])
        assert abs(got - fk.DEFAULT_BONE_LENGTHS[name][seg]) < 1e-9


def test_rigid_hand_rotation_keeps_local_flat(calib_env):
    """整手刚性转动（腕部相对化后）骨架保持平展不变。"""
    installs = calib_env["installs"]
    g = R.from_euler("zyx", [40, -25, 70], degrees=True)
    world = [g * rot for rot in _synth_world_rots(R.identity(), installs)]
    joints = fk.compute_skeleton_joints(
        _make_frame(world), side="left", calib_path=calib_env["path"]
    )
    flat = fk.compute_skeleton_joints(
        calib_env["flat_frame"], side="left", calib_path=calib_env["path"]
    )
    assert np.allclose(joints, flat, atol=1e-6)


def test_uncalibrated_fallback_and_missing_file(calib_env):
    """未校准 / 校准文件缺失时不抛异常，输出有限值。"""
    frame = calib_env["flat_frame"]
    j1 = fk.compute_skeleton_joints(frame, side="left")
    j2 = fk.compute_skeleton_joints(frame, side="left", calib_path="/no/such.npy")
    assert j1.shape == j2.shape == (21, 3)
    assert np.isfinite(j1).all() and np.isfinite(j2).all()
    assert np.allclose(j1, j2)  # 加载失败 → 与未校准一致


def test_right_hand_mirrored_thumb(calib_env, tmp_path):
    """右手校准文件 → 拇指在 -X 侧；左手在 +X 侧。"""
    joints_l = fk.compute_skeleton_joints(
        calib_env["flat_frame"], side="left", calib_path=calib_env["path"]
    )
    assert joints_l[17][0] > 0  # 左手拇指 CMC 在 +X

    rng = np.random.default_rng(7)
    installs = [R.identity()] + [
        R.from_rotvec(rng.normal(0, 0.2, 3)) for _ in range(NUM_IMUS - 1)
    ]
    steps = [
        R.identity(),
        R.from_euler("y", 90, degrees=True),
        R.from_euler("y", -90, degrees=True),
    ]
    quats = np.zeros((3, fk.NUM_SLOTS, 4))
    for s, sr in enumerate(steps):
        quats[s] = fk._build_slot_quats(
            _make_frame(_synth_world_rots(sr, installs), hand="right")
        )
    path = tmp_path / "imu_calibration_right.npy"
    np.save(path, {"imu_calib_quats": quats, "side": "right"}, allow_pickle=True)

    joints_r = fk.compute_skeleton_joints(
        _make_frame(_synth_world_rots(R.identity(), installs), hand="right"),
        side="right",
        calib_path=str(path),
    )
    assert joints_r[17][0] < 0  # 右手拇指 CMC 在 -X
    assert np.abs(joints_r[:, 2]).max() < 0.005  # 平展共面
