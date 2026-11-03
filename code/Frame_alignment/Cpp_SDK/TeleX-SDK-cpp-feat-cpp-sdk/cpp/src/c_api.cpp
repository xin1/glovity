// C ABI 实现。这是唯一导出符号的翻译单元。
//
// 硬性约束：
//   1) 异常绝不能穿越 C 边界 —— 每个入口都用 TELEX_ENTRY_* 包裹。
//   2) 不跨 ABI 分配/释放堆内存 —— 输出一律写入调用方提供的缓冲区。
//   3) 诊断信息为中性英文，不泄露算法语义。
#include "../include/telex_glove.h"

#include <cstring>
#include <algorithm>
#include <cstddef>
#include <exception>
#include <memory>
#include <new>
#include <string>

#include "calibration.hpp"
#include "fk.hpp"
#include "hub.hpp"
#include "labels.hpp"
#include "protocol.hpp"

namespace {

thread_local std::string g_last_error;

telex_glove_status set_err(telex_glove_status s, const std::string& msg) {
    g_last_error = msg;
    return s;
}

#define TELEX_ENTRY_BEGIN try {
#define TELEX_ENTRY_END(dflt)                                                       \
    } catch (const std::bad_alloc&) {                                               \
        return set_err(TELEX_GLOVE_E_OUT_OF_MEMORY, "out of memory");               \
    } catch (const std::exception& e) {                                             \
        return set_err(TELEX_GLOVE_E_INTERNAL, e.what());                           \
    } catch (...) {                                                                 \
        return set_err(TELEX_GLOVE_E_INTERNAL, "unknown error");                    \
    }                                                                               \
    return (dflt);

bool valid_hand(telex_glove_hand h) {
    return h == TELEX_GLOVE_HAND_LEFT || h == TELEX_GLOVE_HAND_RIGHT;
}

int hand_id(telex_glove_hand h) {
    return h == TELEX_GLOVE_HAND_RIGHT ? telex::HAND_RIGHT : telex::HAND_LEFT;
}

void fill_snapshot(const telex::HandSnapshot& in, telex_glove_snapshot* out) {
    const uint32_t caller_size = out->struct_size;
    telex_glove_snapshot temp{};
    temp.struct_size     = caller_size;
    temp.timestamp_ns    = in.timestamp_ns;
    temp.seq             = in.seq;
    temp.frame_id        = in.frame_id;
    temp.hand            = in.hand_id;
    temp.battery_percent = in.battery_percent;
    temp.online          = in.online ? 1 : 0;
    temp.calibrated      = in.calibrated ? 1 : 0;
    for (int i = 0; i < TELEX_GLOVE_IMU_COUNT; ++i) {
        temp.imu[i].w = in.imu[i].w; temp.imu[i].x = in.imu[i].x;
        temp.imu[i].y = in.imu[i].y; temp.imu[i].z = in.imu[i].z;
    }
    for (int i = 0; i < TELEX_GLOVE_JOINT_COUNT; ++i) {
        temp.joints[i].x = in.joints[i][0];
        temp.joints[i].y = in.joints[i][1];
        temp.joints[i].z = in.joints[i][2];
    }
    temp.wrist_euler_deg.x = in.wrist_euler_deg[0];
    temp.wrist_euler_deg.y = in.wrist_euler_deg[1];
    temp.wrist_euler_deg.z = in.wrist_euler_deg[2];
    temp.wrist_gyro_dps.x  = in.wrist_gyro_dps[0];
    temp.wrist_gyro_dps.y  = in.wrist_gyro_dps[1];
    temp.wrist_gyro_dps.z  = in.wrist_gyro_dps[2];
    temp.device_timestamp_us = in.device_timestamp_us;
    temp.imu_fail_mask = in.imu_fail_mask;
    temp.raw_frame_size = static_cast<uint16_t>(in.raw_size);
    temp.protocol = in.protocol;
    temp.protocol_version = in.protocol_version;
    temp.recording = in.recording ? 1 : 0;
    temp.wrist_gyro_valid = in.wrist_gyro_valid ? 1 : 0;
    temp.has_device_timestamp = in.has_device_timestamp ? 1 : 0;
    temp.has_recording_flag = in.has_recording_flag ? 1 : 0;
    temp.has_fail_mask = in.has_fail_mask ? 1 : 0;
    temp.joints_valid = in.joints_valid ? 1 : 0;
    std::copy(in.raw.begin(), in.raw.end(), temp.raw_frame);
    for (int i = 0; i < TELEX_GLOVE_JOINT_ROTATION_COUNT; ++i) {
        temp.joint_local_rotations[i].w = in.joint_local_rotations[i].w();
        temp.joint_local_rotations[i].x = in.joint_local_rotations[i].x();
        temp.joint_local_rotations[i].y = in.joint_local_rotations[i].y();
        temp.joint_local_rotations[i].z = in.joint_local_rotations[i].z();
    }
    temp.joint_local_rotations_valid = in.joint_local_rotations_valid ? 1 : 0;
    std::memcpy(out, &temp, std::min<size_t>(caller_size, sizeof(temp)));
}

bool check_snapshot_arg(telex_glove_snapshot* out) {
    return out != nullptr && out->struct_size >= TELEX_GLOVE_SNAPSHOT_ABI1_SIZE;
}

}  // namespace

struct telex_glove_hub {
    std::unique_ptr<telex::Hub> impl;
    bool started = false;
};

extern "C" {

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_create(const char* port, int32_t baud, telex_glove_hub** out_hub) {
    TELEX_ENTRY_BEGIN
    if (out_hub == nullptr) return set_err(TELEX_GLOVE_E_INVALID_ARG, "out_hub is null");
    *out_hub = nullptr;
    if (port == nullptr || port[0] == '\0')
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "port is null or empty");
    const int b = (baud > 0) ? static_cast<int>(baud) : telex::DEFAULT_BAUD;

    auto h = std::unique_ptr<telex_glove_hub>(new telex_glove_hub());
    h->impl.reset(new telex::Hub(port, b));
    *out_hub = h.release();
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_start(telex_glove_hub* hub, double startup_timeout_s) {
    TELEX_ENTRY_BEGIN
    if (hub == nullptr || !hub->impl) return set_err(TELEX_GLOVE_E_INVALID_ARG, "hub is null");
    if (hub->started) return TELEX_GLOVE_OK;
    std::string err;
    if (!hub->impl->start(startup_timeout_s, err))
        return set_err(TELEX_GLOVE_E_PORT_OPEN, err);
    hub->started = true;
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API void telex_glove_hub_stop(telex_glove_hub* hub) {
    try {
        if (hub != nullptr && hub->impl) { hub->impl->stop(); hub->started = false; }
    } catch (...) { /* 析构路径不抛 */ }
}

TELEX_GLOVE_API void telex_glove_hub_destroy(telex_glove_hub* hub) {
    try { delete hub; } catch (...) { /* 析构路径不抛 */ }
}

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_set_calibration(telex_glove_hub* hub, telex_glove_hand hand,
                                const char* npy_path) {
    TELEX_ENTRY_BEGIN
    if (hub == nullptr || !hub->impl) return set_err(TELEX_GLOVE_E_INVALID_ARG, "hub is null");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");
    const std::string path = (npy_path == nullptr) ? std::string() : std::string(npy_path);
    std::string err;
    if (!hub->impl->set_calibration(hand_id(hand), path, err)) {
        const bool io = err.find("open") != std::string::npos ||
                        err.find("read") != std::string::npos;
        return set_err(io ? TELEX_GLOVE_E_CALIB_IO : TELEX_GLOVE_E_CALIB_FORMAT, err);
    }
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_latest(telex_glove_hub* hub, telex_glove_hand hand,
                       telex_glove_snapshot* out) {
    TELEX_ENTRY_BEGIN
    if (hub == nullptr || !hub->impl) return set_err(TELEX_GLOVE_E_INVALID_ARG, "hub is null");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");
    if (!check_snapshot_arg(out))
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "snapshot struct_size mismatch");
    if (!hub->started) return set_err(TELEX_GLOVE_E_NOT_STARTED, "hub not started");

    telex::HandSnapshot snap;
    if (!hub->impl->latest(hand_id(hand), snap))
        return set_err(TELEX_GLOVE_E_NO_DATA, "hand offline");
    fill_snapshot(snap, out);
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_wait_next(telex_glove_hub* hub, telex_glove_hand hand,
                          double timeout_s, telex_glove_snapshot* out) {
    TELEX_ENTRY_BEGIN
    if (hub == nullptr || !hub->impl) return set_err(TELEX_GLOVE_E_INVALID_ARG, "hub is null");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");
    if (!check_snapshot_arg(out))
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "snapshot struct_size mismatch");
    if (!hub->started) return set_err(TELEX_GLOVE_E_NOT_STARTED, "hub not started");

    telex::HandSnapshot snap;
    if (!hub->impl->wait_next(hand_id(hand), timeout_s, snap))
        return set_err(TELEX_GLOVE_E_TIMEOUT, "no frame within timeout");
    fill_snapshot(snap, out);
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_stats(telex_glove_hub* hub, telex_glove_hand hand,
                      telex_glove_stats* out) {
    TELEX_ENTRY_BEGIN
    if (hub == nullptr || !hub->impl) return set_err(TELEX_GLOVE_E_INVALID_ARG, "hub is null");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");
    if (out == nullptr || out->struct_size != sizeof(telex_glove_stats))
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "stats struct_size mismatch");

    const telex::HandStats st = hub->impl->stats(hand_id(hand));
    const uint32_t keep = out->struct_size;
    std::memset(out, 0, sizeof(*out));
    out->struct_size   = keep;
    out->connected     = st.connected ? 1 : 0;
    out->online        = st.online ? 1 : 0;
    out->rx_total      = st.rx_total;
    out->rx_rate_hz    = st.rx_rate_hz;
    out->last_rx_age_s = st.last_rx_age_s;
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_parse_frame(const uint8_t* data, size_t len, telex_glove_snapshot* out) {
    TELEX_ENTRY_BEGIN
    if (data == nullptr) return set_err(TELEX_GLOVE_E_INVALID_ARG, "data is null");
    if (!check_snapshot_arg(out))
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "snapshot struct_size mismatch");

    telex::RawFrame f;
    if (!telex::parse_frame(data, len, f))
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "frame validation failed");

    telex::HandSnapshot snap;
    snap.frame_id        = f.frame_id;
    snap.device_timestamp_us = f.device_timestamp_us;
    snap.imu_fail_mask   = f.imu_fail_mask;
    snap.recording       = f.recording;
    snap.wrist_gyro_valid = f.wrist_gyro_valid;
    snap.protocol        = f.protocol;
    snap.protocol_version = f.protocol_version;
    snap.has_device_timestamp = f.has_device_timestamp;
    snap.has_recording_flag = f.has_recording_flag;
    snap.has_fail_mask   = f.has_fail_mask;
    snap.raw             = f.raw;
    snap.raw_size        = f.raw_size;
    snap.hand_id         = f.hand_id;
    snap.battery_percent = f.bat_percent;
    snap.online          = true;
    snap.calibrated      = false;
    snap.imu             = f.imu;
    for (int k = 0; k < 3; ++k) {
        snap.wrist_euler_deg[k] = f.wrist_euler_deg[k];
        snap.wrist_gyro_dps[k]  = f.wrist_gyro_dps[k];
    }
    snap.joints_valid = !f.has_fail_mask || f.imu_fail_mask == 0;
    snap.joint_local_rotations_valid = snap.joints_valid;
    if (snap.joints_valid) {
        telex::compute_skeleton_joints(f.imu, telex::IMU_COUNT,
                                       telex::side_from_hand_id(f.hand_id), nullptr,
                                       snap.joints);
        telex::compute_joint_local_rotations(
            f.imu, telex::IMU_COUNT, telex::side_from_hand_id(f.hand_id), nullptr,
            snap.joint_local_rotations);
    }
    fill_snapshot(snap, out);
    g_last_error.clear();
    return TELEX_GLOVE_OK;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_compute_joints(const telex_glove_quat* imu11, telex_glove_hand hand,
                           const char* npy_path, telex_glove_vec3* out_joints21) {
    TELEX_ENTRY_BEGIN
    if (imu11 == nullptr || out_joints21 == nullptr)
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "null buffer");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");

    std::array<telex::QuatWXYZ, telex::IMU_COUNT> imu{};
    for (int i = 0; i < telex::IMU_COUNT; ++i)
        imu[i] = telex::QuatWXYZ{imu11[i].w, imu11[i].x, imu11[i].y, imu11[i].z};

    const telex::Side side = (hand == TELEX_GLOVE_HAND_RIGHT) ? telex::Side::Right
                                                              : telex::Side::Left;
    telex::CalibrationStore store;
    const telex::Calibration* calib = nullptr;
    telex_glove_status rc = TELEX_GLOVE_OK;
    if (npy_path != nullptr && npy_path[0] != '\0') {
        calib = store.get(npy_path, side);
        if (calib == nullptr) {
            // 与 Python 一致：加载失败仍产出未校准结果，但如实回报
            rc = TELEX_GLOVE_E_CALIB_FORMAT;
            g_last_error = store.last_error();
        }
    }

    telex::Vec3 joints[telex::NUM_JOINTS];
    telex::compute_skeleton_joints(imu, telex::IMU_COUNT, side, calib, joints);
    for (int i = 0; i < telex::NUM_JOINTS; ++i) {
        out_joints21[i].x = joints[i][0];
        out_joints21[i].y = joints[i][1];
        out_joints21[i].z = joints[i][2];
    }
    if (rc == TELEX_GLOVE_OK) g_last_error.clear();
    return rc;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API telex_glove_status
telex_glove_compute_joint_local_rotations(
    const telex_glove_quat* imu11, telex_glove_hand hand,
    const char* npy_path, telex_glove_quat* out_rotations16) {
    TELEX_ENTRY_BEGIN
    if (imu11 == nullptr || out_rotations16 == nullptr)
        return set_err(TELEX_GLOVE_E_INVALID_ARG, "null buffer");
    if (!valid_hand(hand)) return set_err(TELEX_GLOVE_E_INVALID_ARG, "invalid hand");

    std::array<telex::QuatWXYZ, telex::IMU_COUNT> imu{};
    for (int i = 0; i < telex::IMU_COUNT; ++i)
        imu[i] = telex::QuatWXYZ{imu11[i].w, imu11[i].x, imu11[i].y, imu11[i].z};

    const telex::Side side = (hand == TELEX_GLOVE_HAND_RIGHT) ? telex::Side::Right
                                                              : telex::Side::Left;
    telex::CalibrationStore store;
    const telex::Calibration* calib = nullptr;
    telex_glove_status rc = TELEX_GLOVE_OK;
    if (npy_path != nullptr && npy_path[0] != '\0') {
        calib = store.get(npy_path, side);
        if (calib == nullptr) {
            rc = TELEX_GLOVE_E_CALIB_FORMAT;
            g_last_error = store.last_error();
        }
    }

    telex::Rot rotations[telex::NUM_SLOTS];
    telex::compute_joint_local_rotations(
        imu, telex::IMU_COUNT, side, calib, rotations);
    for (int i = 0; i < telex::NUM_SLOTS; ++i) {
        out_rotations16[i].w = rotations[i].w();
        out_rotations16[i].x = rotations[i].x();
        out_rotations16[i].y = rotations[i].y();
        out_rotations16[i].z = rotations[i].z();
    }
    if (rc == TELEX_GLOVE_OK) g_last_error.clear();
    return rc;
    TELEX_ENTRY_END(TELEX_GLOVE_E_INTERNAL)
}

TELEX_GLOVE_API const char* telex_glove_version(void) { return TELEX_GLOVE_VERSION_STR; }
TELEX_GLOVE_API uint32_t    telex_glove_abi_version(void) { return TELEX_GLOVE_ABI_VERSION; }

TELEX_GLOVE_API const char* telex_glove_last_error(void) { return g_last_error.c_str(); }

TELEX_GLOVE_API const char* telex_glove_status_string(telex_glove_status s) {
    switch (s) {
        case TELEX_GLOVE_OK:              return "ok";
        case TELEX_GLOVE_E_INVALID_ARG:   return "invalid argument";
        case TELEX_GLOVE_E_PORT_OPEN:     return "cannot open port";
        case TELEX_GLOVE_E_PORT_CONFIG:   return "cannot configure port";
        case TELEX_GLOVE_E_NOT_STARTED:   return "hub not started";
        case TELEX_GLOVE_E_TIMEOUT:       return "timeout";
        case TELEX_GLOVE_E_NO_DATA:       return "no data";
        case TELEX_GLOVE_E_CALIB_IO:      return "calibration io error";
        case TELEX_GLOVE_E_CALIB_FORMAT:  return "calibration format error";
        case TELEX_GLOVE_E_DISCONNECTED:  return "disconnected";
        case TELEX_GLOVE_E_OUT_OF_MEMORY: return "out of memory";
        case TELEX_GLOVE_E_INTERNAL:      return "internal error";
    }
    return "unknown status";
}

TELEX_GLOVE_API const char* telex_glove_joint_name(int32_t index) {
    if (index < 0 || index >= TELEX_GLOVE_JOINT_COUNT) return nullptr;
    return telex::JOINT_LABELS[index];
}

TELEX_GLOVE_API const char* telex_glove_imu_name(int32_t index) {
    if (index < 0 || index >= TELEX_GLOVE_IMU_COUNT) return nullptr;
    return telex::IMU_LABELS[index];
}

}  // extern "C"
