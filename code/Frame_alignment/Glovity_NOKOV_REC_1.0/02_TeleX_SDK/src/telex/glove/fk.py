"""
骨骼正向运动学 (FK) — 11 IMU 全量 + 三步校准 → 21 个骨骼关节的 3D 位置（腕部局部系）。

数据链路（与 mano_minimal 旧管线 / Remote_USB_ThreeStepCalibration 对齐）：

1. 180B V2 帧中的 11 路 IMU 按地址顺序 [0x50..0x59, 0x60] 映射到 16 关节槽位
   （腕部 0x50 右乘 Y180 安装修正，PIP 槽位复制到 DIP 槽位）；
2. 可选加载三步校准 .npy（``Remote_USB_ThreeStepCalibration`` 产物，
   ``{"imu_calib_quats": (3,16,4), "side": ...}``），求全局安装误差并逐帧修正；
3. 每指 MCP / PIP 两段独立旋转 + DIP 按比例耦合，沿骨长逐节累加出 21 关节坐标。

输出坐标约定（腕部局部系，米）：指尖 +Y，手背 +Z（掌心 -Z），
拇指侧：左手 +X / 右手 -X。joints[0]（腕部）恒为原点。

未提供 / 加载失败校准文件时退回未校准模式（仅腕部相对旋转，姿态会有贴装偏差）。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np
from loguru import logger
from scipy.spatial.transform import Rotation as R

from telex.glove.types import GloveFrame

# ---------------------------------------------------------------------------
# 11 IMU → 16 关节槽位映射（与三步校准工具逐位一致）
# ---------------------------------------------------------------------------

# 16 槽位: 0 腕; 1/2/3 食指 MCP/PIP/DIP; 4/5/6 中指; 7/8/9 无名指;
#          10/11/12 小指; 13/14/15 拇指
NUM_SLOTS = 16

# 帧内 IMU 下标（= 地址顺序 0x50..0x59,0x60）→ 16 槽位
FRAME_INDEX_TO_SLOT = {
    0: 0,    # 0x50 手背（腕）
    1: 13,   # 0x51 拇指 MCP
    2: 14,   # 0x52 拇指 PIP
    3: 1,    # 0x53 食指 MCP
    4: 2,    # 0x54 食指 PIP
    5: 4,    # 0x55 中指 MCP
    6: 5,    # 0x56 中指 PIP
    7: 10,   # 0x57 小指 MCP
    8: 11,   # 0x58 小指 PIP
    9: 7,    # 0x59 无名指 MCP
    10: 8,   # 0x60 无名指 PIP
}

# PIP → DIP 槽位复制（DIP 无物理 IMU）
PIP_TO_DIP = {2: 3, 5: 6, 8: 9, 11: 12, 14: 15}

# 腕部 IMU 安装方向修正：绕 Y 轴 180 度（scipy x,y,z,w）
_Q_Y180 = np.array([0.0, 1.0, 0.0, 0.0])

# ---------------------------------------------------------------------------
# 21 关节骨架（0 腕; 1-4 食; 5-8 中; 9-12 小; 13-16 无名; 17-20 拇）
# ---------------------------------------------------------------------------

SKELETON_CHAINS = {
    "index": [0, 1, 2, 3, 4],
    "middle": [0, 5, 6, 7, 8],
    "pinky": [0, 9, 10, 11, 12],
    "ring": [0, 13, 14, 15, 16],
    "thumb": [0, 17, 18, 19, 20],
}

# 每指驱动 IMU 的 16 槽位: (MCP 槽位, PIP 槽位)
FINGER_SLOTS = {
    "index": (1, 2),
    "middle": (4, 5),
    "ring": (7, 8),
    "pinky": (10, 11),
    "thumb": (13, 14),
}

# 平展姿势下 MCP（拇指为 CMC）相对腕部的位置: (前向分量, 掌宽分量)，米。
# 掌宽方向以拇指侧为正（虚拟手系 +z，两手相同）。
PALM_MCP_OFFSETS = {
    "index": (0.085, 0.030),
    "middle": (0.090, 0.010),
    "ring": (0.086, -0.010),
    "pinky": (0.078, -0.030),
    "thumb": (0.025, 0.035),
}

# 拇指平展方向的 (前向, 掌宽) 分量（其余四指纯前向）
_THUMB_DIR_FW = (0.835, 0.550)

# 指骨长度（米）: [近节, 中节, 远节]；拇指为 [掌骨, 近节, 远节]
DEFAULT_BONE_LENGTHS = {
    "index": [0.040, 0.025, 0.020],
    "middle": [0.043, 0.027, 0.022],
    "ring": [0.040, 0.025, 0.020],
    "pinky": [0.033, 0.020, 0.016],
    "thumb": [0.045, 0.030, 0.025],
}

# DIP 关节耦合比（DIP 弯曲角 ≈ 比例 × PIP 相对 MCP 的弯曲角）
DIP_COUPLING_RATIO = {
    "index": 2.0 / 3.0,
    "middle": 2.0 / 3.0,
    "ring": 2.0 / 3.0,
    "pinky": 2.0 / 3.0,
    "thumb": 0.6,
}

# 虚拟手系（校准数学定义: 手指前向 = 左手 +x / 右手 -x，手背 +y，拇指侧 +z）
# → 输出系（指尖 +Y、手背 +Z、拇指侧: 左手 +X / 右手 -X）
_VIRTUAL_TO_OUT = {
    "left": np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    "right": np.array([[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
}


# ---------------------------------------------------------------------------
# 帧内 IMU → 16 槽位四元数
# ---------------------------------------------------------------------------

def _build_slot_quats(frame: GloveFrame) -> np.ndarray:
    """把一帧 11 路 IMU 映射为 (16, 4) 槽位四元数（scipy x,y,z,w，已归一化）。"""
    identity = np.array([0.0, 0.0, 0.0, 1.0])
    quats = np.tile(identity, (NUM_SLOTS, 1))

    for index, slot in FRAME_INDEX_TO_SLOT.items():
        imu = frame.imu_data[index]
        q = np.array([imu.quat_x, imu.quat_y, imu.quat_z, imu.quat_w])
        if np.linalg.norm(q) < 1e-6:
            q = identity.copy()
        if slot == 0:
            q = (R.from_quat(q) * R.from_quat(_Q_Y180)).as_quat()
        quats[slot] = q

    for pip_slot, dip_slot in PIP_TO_DIP.items():
        quats[dip_slot] = quats[pip_slot].copy()

    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    return quats


# ---------------------------------------------------------------------------
# 三步校准：全局安装误差（数学与 Remote_USB_ThreeStepCalibration / fsglove 一致）
# ---------------------------------------------------------------------------

def _installation_error(
    base_q: np.ndarray,
    z_rot_pose_1_q: np.ndarray,
    z_rot_pose_2_q: np.ndarray,
    side: str,
) -> R:
    """由三步姿势四元数求每槽位安装误差旋转 E（虚拟手系 ← IMU 体系）。"""
    base = R.from_quat(base_q)
    z1 = R.from_quat(z_rot_pose_1_q)
    z2 = R.from_quat(z_rot_pose_2_q)

    z_rot = (z2 * z1.inv()).as_rotvec()
    norms = np.linalg.norm(z_rot, axis=1)
    ok = norms > 1e-8
    z_rot[ok] = z_rot[ok] / norms[ok][:, np.newaxis]

    # 半球对齐：步骤 2→3 接近 180° 时轴向符号退化，用步骤 1→2（约 90°，
    # 方向良定义）的反方向作参考，逐槽位统一弯曲轴符号。
    # 非退化数据下 dot>0，此步为无操作，与 fsglove 原始行为一致。
    ref = -(z1 * base.inv()).as_rotvec()
    flip = np.sum(z_rot * ref, axis=1) < 0
    z_rot[flip] = -z_rot[flip]

    y_axis = np.asarray([0.0, 0.0, 1.0])[np.newaxis, :].repeat(NUM_SLOTS, 0)
    z_axis = z_rot - y_axis * np.sum(z_rot * y_axis, axis=1)[:, np.newaxis]
    z_norms = np.linalg.norm(z_axis, axis=1)
    ok = ok & (z_norms > 1e-8)
    z_axis[ok] = z_axis[ok] / z_norms[ok][:, np.newaxis]
    if side == "left":
        z_axis = z_axis * -1
    x_axis = np.cross(y_axis, z_axis)

    virtual_rot = np.stack([x_axis, y_axis, z_axis], axis=2)
    virtual_rot[~ok] = np.eye(3)
    err_rotvec = (R.from_matrix(virtual_rot).inv() * base).as_rotvec()
    # 退化槽位（步骤 2/3 间几乎无旋转）不做修正
    err_rotvec[~ok] = 0.0
    if not ok.all():
        bad = np.flatnonzero(~ok).tolist()
        logger.warning(f"[glove.fk] 校准数据槽位 {bad} 旋转量退化，已跳过其安装修正")
    return R.from_rotvec(err_rotvec)


# 校准缓存: abspath -> (mtime, payload | None)
_CALIB_CACHE: Dict[str, Tuple[float, Optional[dict]]] = {}


def _load_calibration(path: str) -> Optional[dict]:
    """加载三步校准 .npy 并预计算安装误差；带 mtime 缓存，失败仅告警一次。"""
    key = os.path.abspath(path)
    try:
        mtime = os.path.getmtime(key)
    except OSError:
        mtime = -1.0
    hit = _CALIB_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]

    payload: Optional[dict] = None
    try:
        data = np.load(key, allow_pickle=True).item()
        quats = np.asarray(data["imu_calib_quats"], dtype=np.float64)
        if quats.shape != (3, NUM_SLOTS, 4):
            raise ValueError(f"期望 imu_calib_quats (3,16,4)，实际 {quats.shape}")
        calib_side = str(data.get("side", "left"))
        payload = {
            "base": R.from_quat(quats[0]),
            "err": _installation_error(quats[0], quats[1], quats[2], calib_side),
            "side": calib_side,
        }
        logger.info(f"[glove.fk] 已加载三步校准: {key} (side={calib_side})")
    except Exception as exc:
        logger.warning(f"[glove.fk] 校准文件加载失败，退回未校准 FK: {key}: {exc}")
    _CALIB_CACHE[key] = (mtime, payload)
    return payload


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def compute_skeleton_joints(
    frame: GloveFrame,
    side: str = "left",
    calib_path: Optional[str] = None,
) -> np.ndarray:
    """从一帧 IMU 数据计算 21 个骨骼关节的 3D 位置（米，腕部原点 / 腕部局部系）。

    Args:
        frame: 一帧手套数据（11 路 IMU）
        side: "left" 或 "right"
        calib_path: 三步校准 .npy 路径（可选；缺省为未校准模式）

    Returns:
        shape (21, 3) 的 numpy 数组，joints[0] 恒为原点；
        指尖 +Y，手背 +Z，拇指侧：左手 +X / 右手 -X
    """
    joints = np.zeros((21, 3), dtype=np.float64)
    if len(frame.imu_data) < 11:
        return joints

    current = R.from_quat(_build_slot_quats(frame))

    calib = _load_calibration(calib_path) if calib_path else None
    if calib is not None:
        # E · (B⁻¹ · C) · E⁻¹: 相对平展姿势的旋转，重表达到虚拟手系
        delta = calib["base"].inv() * current
        hand = calib["err"] * delta * calib["err"].inv()
        geo_side = calib["side"]
    else:
        hand = current
        geo_side = side if side in ("left", "right") else "left"

    # 腕部相对化：整手刚性转动时手指保持平展
    local = hand[0].inv() * hand

    # 虚拟手系内的平展几何：手指前向 = +x（左手）/ -x（右手）
    sx = -1.0 if geo_side == "right" else 1.0
    out_map = _VIRTUAL_TO_OUT[geo_side]

    for finger, (mcp_slot, pip_slot) in FINGER_SLOTS.items():
        chain = SKELETON_CHAINS[finger]
        lengths = DEFAULT_BONE_LENGTHS[finger]
        fwd, width = PALM_MCP_OFFSETS[finger]
        offset = np.array([sx * fwd, 0.0, width])
        if finger == "thumb":
            flat_dir = np.array([sx * _THUMB_DIR_FW[0], 0.0, _THUMB_DIR_FW[1]])
            flat_dir /= np.linalg.norm(flat_dir)
        else:
            flat_dir = np.array([sx, 0.0, 0.0])

        rot_mcp = local[mcp_slot]
        rot_pip = local[pip_slot]
        # DIP 耦合：在 PIP 基础上按比例追加 PIP 相对 MCP 的弯曲
        rel_rotvec = (rot_mcp.inv() * rot_pip).as_rotvec()
        rot_dip = rot_pip * R.from_rotvec(DIP_COUPLING_RATIO[finger] * rel_rotvec)

        pos = offset.copy()
        joints[chain[1]] = pos
        pos = pos + rot_mcp.apply(flat_dir) * lengths[0]
        joints[chain[2]] = pos
        pos = pos + rot_pip.apply(flat_dir) * lengths[1]
        joints[chain[3]] = pos
        pos = pos + rot_dip.apply(flat_dir) * lengths[2]
        joints[chain[4]] = pos

    # 虚拟手系 → 输出系（指尖 +Y、手背 +Z）
    return joints @ out_map.T
