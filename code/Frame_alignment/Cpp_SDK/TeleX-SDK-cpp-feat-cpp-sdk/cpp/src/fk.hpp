// 骨骼正向运动学：11 路 IMU 四元数 -> 21 个关节的 3D 位置（腕部局部系，米）。
#ifndef TELEX_FK_HPP
#define TELEX_FK_HPP

#include <array>
#include <string>

#include "protocol.hpp"
#include "rotation.hpp"

namespace telex {

constexpr int NUM_SLOTS  = 16;
constexpr int NUM_JOINTS = 21;

enum class Side { Left, Right };

inline Side side_from_hand_id(int hand_id) {
    return hand_id == HAND_RIGHT ? Side::Right : Side::Left;
}
inline const char* side_name(Side s) { return s == Side::Right ? "right" : "left"; }

/// 三步校准的预计算结果。valid=false 表示走未校准路径。
struct Calibration {
    bool  valid = false;
    Side  side  = Side::Left;
    std::array<Rot, NUM_SLOTS> base{};
    std::array<Rot, NUM_SLOTS> err{};
};

/// 11 路帧内 IMU -> 16 槽位四元数（含腕部安装修正与 PIP->DIP 复制）。
std::array<Rot, NUM_SLOTS> build_slot_quats(const std::array<QuatWXYZ, IMU_COUNT>& imu);

/// 由三步校准姿势求各槽位安装误差。quats 为 (3,16) 的 scipy xyzw 四元数。
Calibration installation_error(const std::array<std::array<Rot, NUM_SLOTS>, 3>& poses,
                               Side side);

/// 计算 21 关节位置。calib 为 nullptr 或 invalid 时走未校准路径。
/// imu_valid < IMU_COUNT 时输出全零（与 Python 行为一致）。
void compute_skeleton_joints(const std::array<QuatWXYZ, IMU_COUNT>& imu,
                             int imu_valid,
                             Side side,
                             const Calibration* calib,
                             Vec3 out[NUM_JOINTS]);

/// 计算 16 个逐级父子局部旋转，顺序为：腕，拇指3节，食指3节，
/// 中指3节，无名指3节，小指3节。out[0]（腕）固定为单位旋转；其余满足
/// q_local = inverse(q_parent) * q_child，并已重表达到关节位置使用的输出坐标系。
/// DIP/拇指末节没有独立 IMU，沿用 FK 的耦合模型推算。
/// imu_valid < IMU_COUNT 时输出全单位旋转。
void compute_joint_local_rotations(const std::array<QuatWXYZ, IMU_COUNT>& imu,
                                   int imu_valid,
                                   Side side,
                                   const Calibration* calib,
                                   Rot out[NUM_SLOTS]);

}  // namespace telex
#endif  // TELEX_FK_HPP
