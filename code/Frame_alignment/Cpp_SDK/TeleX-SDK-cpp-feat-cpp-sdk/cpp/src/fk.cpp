#include "fk.hpp"

#include <cmath>
#include <cstring>

namespace telex {
namespace {

// 帧内 IMU 下标（地址序 0x50..0x59,0x60）-> 16 槽位
constexpr int FRAME_INDEX_TO_SLOT[IMU_COUNT] = {0, 13, 14, 1, 2, 4, 5, 10, 11, 7, 8};

// PIP 槽位 -> DIP 槽位（DIP 无物理 IMU，复制 PIP）
constexpr int PIP_SLOTS[5] = {2, 5, 8, 11, 14};
constexpr int DIP_SLOTS[5] = {3, 6, 9, 12, 15};

constexpr int NUM_FINGERS = 5;

// 每指: MCP 槽位, PIP 槽位
constexpr int FINGER_MCP_SLOT[NUM_FINGERS] = {1, 4, 7, 10, 13};
constexpr int FINGER_PIP_SLOT[NUM_FINGERS] = {2, 5, 8, 11, 14};

// 每指关节链（0 腕）。注意小指占 9-12、无名指占 13-16。
constexpr int CHAIN[NUM_FINGERS][5] = {
    {0, 1,  2,  3,  4},    // index  食指
    {0, 5,  6,  7,  8},    // middle 中指
    {0, 13, 14, 15, 16},   // ring   无名指
    {0, 9,  10, 11, 12},   // pinky  小指
    {0, 17, 18, 19, 20},   // thumb  拇指
};

// 平展姿势下 MCP（拇指为 CMC）相对腕部: (前向, 掌宽)，米
constexpr double PALM_FWD[NUM_FINGERS]   = {0.085, 0.090, 0.086, 0.078, 0.025};
constexpr double PALM_WIDTH[NUM_FINGERS] = {0.030, 0.010, -0.010, -0.030, 0.035};

// 指骨长度（米）
constexpr double BONE[NUM_FINGERS][3] = {
    {0.040, 0.025, 0.020},
    {0.043, 0.027, 0.022},
    {0.040, 0.025, 0.020},
    {0.033, 0.020, 0.016},
    {0.045, 0.030, 0.025},
};

constexpr double DIP_RATIO[NUM_FINGERS] = {2.0/3.0, 2.0/3.0, 2.0/3.0, 2.0/3.0, 0.6};

constexpr int THUMB = 4;
constexpr double THUMB_DIR_FW    = 0.835;
constexpr double THUMB_DIR_WIDTH = 0.550;

// 虚拟手系 -> 输出系（指尖 +Y、手背 +Z）
constexpr double OUT_LEFT[3][3]  = {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}};
constexpr double OUT_RIGHT[3][3] = {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}};

inline Rot q_y180() { return Rot::from_quat_xyzw(0.0, 1.0, 0.0, 0.0); }

// NOKOV/公开 API 顺序 -> 内部槽位链。每行依次为近、中、远端。
constexpr int OUTPUT_CHAINS[NUM_FINGERS][3] = {
    {13, 14, 15}, // thumb
    {1,   2,  3}, // index
    {4,   5,  6}, // middle
    {10, 11, 12}, // ring
    {7,   8,  9}, // pinky
};

std::array<Rot, NUM_SLOTS> wrist_relative_slot_rotations(
    const std::array<QuatWXYZ, IMU_COUNT>& imu, const Calibration* calib) {
    const std::array<Rot, NUM_SLOTS> current = build_slot_quats(imu);
    std::array<Rot, NUM_SLOTS> hand{};
    if (calib != nullptr && calib->valid) {
        for (int s = 0; s < NUM_SLOTS; ++s) {
            const Rot delta = calib->base[s].inv() * current[s];
            hand[s] = calib->err[s] * delta * calib->err[s].inv();
        }
    } else {
        hand = current;
    }

    std::array<Rot, NUM_SLOTS> local{};
    const Rot wrist_inv = hand[0].inv();
    for (int s = 0; s < NUM_SLOTS; ++s) local[s] = wrist_inv * hand[s];

    // 末节无独立 IMU：与位置 FK 使用完全相同的耦合模型。
    for (int f = 0; f < NUM_FINGERS; ++f) {
        const int mcp = FINGER_MCP_SLOT[f];
        const int pip = FINGER_PIP_SLOT[f];
        const int dip = pip + 1;
        const Vec3 rel = (local[mcp].inv() * local[pip]).as_rotvec();
        local[dip] = local[pip] * Rot::from_rotvec(vscale(rel, DIP_RATIO[f]));
    }
    return local;
}

}  // namespace

std::array<Rot, NUM_SLOTS> build_slot_quats(const std::array<QuatWXYZ, IMU_COUNT>& imu) {
    std::array<Rot, NUM_SLOTS> slots{};  // 默认单位元
    for (int i = 0; i < IMU_COUNT; ++i) {
        const int slot = FRAME_INDEX_TO_SLOT[i];
        const QuatWXYZ& q = imu[i];
        const double n2 = q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z;
        Rot r = (std::sqrt(n2) < 1e-6) ? Rot::identity()
                                       : Rot::from_quat_wxyz(q.w, q.x, q.y, q.z);
        if (slot == 0) r = r * q_y180();  // 腕部安装方向修正（右乘）
        slots[slot] = r;
    }
    for (int i = 0; i < 5; ++i) slots[DIP_SLOTS[i]] = slots[PIP_SLOTS[i]];
    return slots;
}

Calibration installation_error(const std::array<std::array<Rot, NUM_SLOTS>, 3>& poses,
                               Side side) {
    const auto& base = poses[0];
    const auto& z1   = poses[1];
    const auto& z2   = poses[2];

    Calibration c;
    c.valid = true;
    c.side  = side;
    c.base  = base;

    const Vec3 y_axis = {0.0, 0.0, 1.0};

    for (int s = 0; s < NUM_SLOTS; ++s) {
        Vec3 z_rot = (z2[s] * z1[s].inv()).as_rotvec();
        const double n = vnorm(z_rot);
        bool ok = n > 1e-8;
        if (ok) z_rot = vscale(z_rot, 1.0 / n);

        // 半球对齐：步骤 2->3 接近 180 度时轴向符号退化，用步骤 1->2 的反方向作参考。
        const Vec3 ref = vscale((z1[s] * base[s].inv()).as_rotvec(), -1.0);
        if (vdot(z_rot, ref) < 0.0) z_rot = vscale(z_rot, -1.0);

        Vec3 z_axis = vsub(z_rot, vscale(y_axis, vdot(z_rot, y_axis)));
        const double zn = vnorm(z_axis);
        ok = ok && (zn > 1e-8);
        if (ok) z_axis = vscale(z_axis, 1.0 / zn);
        if (side == Side::Left) z_axis = vscale(z_axis, -1.0);
        const Vec3 x_axis = vcross(y_axis, z_axis);

        if (!ok) {
            // 退化槽位不做修正
            c.err[s] = Rot::identity();
            continue;
        }
        // 列向量为 x,y,z 的旋转矩阵
        Mat3 m{};
        for (int r = 0; r < 3; ++r) {
            m[r][0] = x_axis[r];
            m[r][1] = y_axis[r];
            m[r][2] = z_axis[r];
        }
        c.err[s] = Rot::from_rotvec((Rot::from_matrix(m).inv() * base[s]).as_rotvec());
    }
    return c;
}

void compute_skeleton_joints(const std::array<QuatWXYZ, IMU_COUNT>& imu,
                             int imu_valid,
                             Side side,
                             const Calibration* calib,
                             Vec3 out[NUM_JOINTS]) {
    for (int i = 0; i < NUM_JOINTS; ++i) out[i] = {0.0, 0.0, 0.0};
    if (imu_valid < IMU_COUNT) return;

    Side geo_side;
    if (calib != nullptr && calib->valid) {
        geo_side = calib->side;
    } else {
        geo_side = side;
    }
    const std::array<Rot, NUM_SLOTS> local = wrist_relative_slot_rotations(imu, calib);

    const double sx = (geo_side == Side::Right) ? -1.0 : 1.0;

    Vec3 virt[NUM_JOINTS];
    for (int i = 0; i < NUM_JOINTS; ++i) virt[i] = {0.0, 0.0, 0.0};

    for (int f = 0; f < NUM_FINGERS; ++f) {
        const Vec3 offset = {sx * PALM_FWD[f], 0.0, PALM_WIDTH[f]};
        Vec3 flat_dir;
        if (f == THUMB) {
            flat_dir = {sx * THUMB_DIR_FW, 0.0, THUMB_DIR_WIDTH};
            flat_dir = vscale(flat_dir, 1.0 / vnorm(flat_dir));
        } else {
            flat_dir = {sx, 0.0, 0.0};
        }

        const Rot& rot_mcp = local[FINGER_MCP_SLOT[f]];
        const Rot& rot_pip = local[FINGER_PIP_SLOT[f]];
        // DIP 耦合：在 PIP 基础上按比例追加 PIP 相对 MCP 的弯曲
        const Vec3 rel = (rot_mcp.inv() * rot_pip).as_rotvec();
        const Rot rot_dip = rot_pip * Rot::from_rotvec(vscale(rel, DIP_RATIO[f]));

        Vec3 pos = offset;
        virt[CHAIN[f][1]] = pos;
        pos = vadd(pos, vscale(rot_mcp.apply(flat_dir), BONE[f][0]));
        virt[CHAIN[f][2]] = pos;
        pos = vadd(pos, vscale(rot_pip.apply(flat_dir), BONE[f][1]));
        virt[CHAIN[f][3]] = pos;
        pos = vadd(pos, vscale(rot_dip.apply(flat_dir), BONE[f][2]));
        virt[CHAIN[f][4]] = pos;
    }

    // 虚拟手系 -> 输出系：out[i] = M @ virt[i]
    const double (*M)[3] = (geo_side == Side::Right) ? OUT_RIGHT : OUT_LEFT;
    for (int i = 0; i < NUM_JOINTS; ++i) {
        for (int r = 0; r < 3; ++r) {
            out[i][r] = M[r][0] * virt[i][0] + M[r][1] * virt[i][1] + M[r][2] * virt[i][2];
        }
    }
}

void compute_joint_local_rotations(const std::array<QuatWXYZ, IMU_COUNT>& imu,
                                   int imu_valid,
                                   Side side,
                                   const Calibration* calib,
                                   Rot out[NUM_SLOTS]) {
    for (int i = 0; i < NUM_SLOTS; ++i) out[i] = Rot::identity();
    if (imu_valid < IMU_COUNT) return;

    const Side geo_side = (calib != nullptr && calib->valid) ? calib->side : side;
    const std::array<Rot, NUM_SLOTS> wrist = wrist_relative_slot_rotations(imu, calib);
    const double (*matrix)[3] = (geo_side == Side::Right) ? OUT_RIGHT : OUT_LEFT;
    Mat3 basis{};
    for (int r = 0; r < 3; ++r)
        for (int c = 0; c < 3; ++c) basis[r][c] = matrix[r][c];
    const Rot change_basis = Rot::from_matrix(basis);
    const auto express_in_output_basis = [&change_basis](const Rot& r) {
        return change_basis * r * change_basis.inv();
    };

    out[0] = Rot::identity();
    int output_index = 1;
    for (int f = 0; f < NUM_FINGERS; ++f) {
        const int proximal = OUTPUT_CHAINS[f][0];
        const int middle   = OUTPUT_CHAINS[f][1];
        const int distal   = OUTPUT_CHAINS[f][2];
        out[output_index++] = express_in_output_basis(wrist[proximal]);
        out[output_index++] = express_in_output_basis(wrist[proximal].inv() * wrist[middle]);
        out[output_index++] = express_in_output_basis(wrist[middle].inv() * wrist[distal]);
    }
}

}  // namespace telex
