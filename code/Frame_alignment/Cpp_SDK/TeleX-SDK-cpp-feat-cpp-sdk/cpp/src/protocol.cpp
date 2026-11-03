#include "protocol.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>

namespace telex {

bool parse_frame(const uint8_t* data, size_t len, RawFrame& out) {
    if (data == nullptr) return false;
    if (len != static_cast<size_t>(V2_FRAME_SIZE) &&
        len != static_cast<size_t>(LEGACY_FRAME_SIZE) &&
        len != static_cast<size_t>(LEGACY_CAPTURE_FRAME_SIZE)) return false;
    if (data[0] != MAGIC0 || data[1] != MAGIC1) return false;

    RawFrame f;
    int header_size = HEADER_SIZE;
    int extra_offset = EXTRA_OFFSET;

    if (len == static_cast<size_t>(V2_FRAME_SIZE)) {
        if (data[2] != 2 || (data[3] != HAND_LEFT && data[3] != HAND_RIGHT)) return false;
        const uint8_t flags = data[4];
        const uint16_t fail_mask = read_u16_le(data + 16);
        if ((flags & ~0x03u) != 0 || data[5] > 100 || (fail_mask & ~0x07FFu) != 0)
            return false;
        if (crc16_ccitt_false(data, V2_CRC_OFFSET) != read_u16_le(data + V2_CRC_OFFSET))
            return false;
        f.hand_id = static_cast<int>(data[3]);
        f.bat_percent = static_cast<int>(data[5]);
        f.frame_id = read_u16_le(data + 6);
        f.device_timestamp_us = read_u64_le(data + 8);
        f.imu_fail_mask = fail_mask;
        f.recording = (flags & 0x01u) != 0;
        f.wrist_gyro_valid = (flags & 0x02u) != 0;
        f.protocol = PROTOCOL_V2_180;
        f.protocol_version = 2;
        f.has_device_timestamp = true;
        f.has_recording_flag = true;
        f.has_fail_mask = true;
        header_size = V2_HEADER_SIZE;
        extra_offset = V2_EXTRA_OFFSET;
    } else if (len == static_cast<size_t>(LEGACY_FRAME_SIZE) ||
               len == static_cast<size_t>(LEGACY_CAPTURE_FRAME_SIZE)) {
        if ((data[2] != HAND_LEFT && data[2] != HAND_RIGHT) || data[3] != IMU_COUNT ||
            data[4] > 100 || xor_check(data, len - 1) != data[len - 1]) return false;
        f.hand_id = static_cast<int>(data[2]);
        f.bat_percent = static_cast<int>(data[4]);
        f.frame_id = read_u16_le(data + 5);
        f.wrist_gyro_valid = true;
        f.protocol = len == static_cast<size_t>(LEGACY_FRAME_SIZE)
                         ? PROTOCOL_LEGACY_168 : PROTOCOL_LEGACY_170;
        f.protocol_version = 1;
    } else {
        return false;
    }

    f.raw_size = len;
    std::copy(data, data + len, f.raw.begin());

    for (int i = 0; i < IMU_COUNT; ++i) {
        const uint8_t* p = data + header_size + i * IMU_RAW_SIZE;
        // raw[0..2] = euler, raw[3..6] = quat(w,x,y,z)
        f.imu[i].w = read_i16_le(p + 6)  * QUAT_SCALE;
        f.imu[i].x = read_i16_le(p + 8)  * QUAT_SCALE;
        f.imu[i].y = read_i16_le(p + 10) * QUAT_SCALE;
        f.imu[i].z = read_i16_le(p + 12) * QUAT_SCALE;
    }

    const uint8_t* wp = data + header_size + WRIST_IMU_INDEX * IMU_RAW_SIZE;
    for (int i = 0; i < 3; ++i)
        f.wrist_euler_deg[i] = read_i16_le(wp + 2 * i) * ANG_SCALE;

    const uint8_t* gp = data + extra_offset;
    for (int i = 0; i < 3; ++i)
        f.wrist_gyro_dps[i] = read_i16_le(gp + 2 * i) * GYRO_SCALE;

    out = f;
    return true;
}

uint16_t crc16_ccitt_false(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]) << 8;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000u) ? static_cast<uint16_t>((crc << 1) ^ 0x1021u)
                                  : static_cast<uint16_t>(crc << 1);
    }
    return crc;
}

namespace {

void write_i16_le(uint8_t* p, int v) {
    const uint16_t u = static_cast<uint16_t>(static_cast<int16_t>(v));
    p[0] = static_cast<uint8_t>(u & 0xFF);
    p[1] = static_cast<uint8_t>((u >> 8) & 0xFF);
}

int quantize(double v) {
    // 对应 Python: int(max(-32768, min(32767, round(v * 32768.0))))
    double r = std::round(v * 32768.0);
    if (r < -32768.0) r = -32768.0;
    if (r > 32767.0) r = 32767.0;
    return static_cast<int>(r);
}

}  // namespace

std::vector<uint8_t> build_test_frame(int hand_id, uint16_t frame_id,
                                      const QuatWXYZ& quat, uint8_t bat_percent) {
    std::vector<uint8_t> buf(FRAME_SIZE, 0);
    buf[0] = MAGIC0;
    buf[1] = MAGIC1;
    buf[2] = static_cast<uint8_t>(hand_id);
    buf[3] = IMU_COUNT;
    buf[4] = bat_percent;
    buf[5] = static_cast<uint8_t>(frame_id & 0xFF);
    buf[6] = static_cast<uint8_t>((frame_id >> 8) & 0xFF);

    const int qw = quantize(quat.w), qx = quantize(quat.x);
    const int qy = quantize(quat.y), qz = quantize(quat.z);
    for (int i = 0; i < IMU_COUNT; ++i) {
        uint8_t* p = buf.data() + HEADER_SIZE + i * IMU_RAW_SIZE;
        write_i16_le(p + 0, 0);
        write_i16_le(p + 2, 0);
        write_i16_le(p + 4, 0);
        write_i16_le(p + 6, qw);
        write_i16_le(p + 8, qx);
        write_i16_le(p + 10, qy);
        write_i16_le(p + 12, qz);
    }
    for (int i = 0; i < 3; ++i) write_i16_le(buf.data() + EXTRA_OFFSET + 2 * i, 0);
    buf[FRAME_SIZE - 1] = xor_check(buf.data(), FRAME_SIZE - 1);
    return buf;
}

std::vector<uint8_t> build_test_frame_v2(int hand_id, uint16_t frame_id,
                                         const QuatWXYZ& quat, uint64_t timestamp_us,
                                         bool recording, uint8_t bat_percent,
                                         uint16_t fail_mask, bool wrist_gyro_valid) {
    std::vector<uint8_t> buf(V2_FRAME_SIZE, 0);
    buf[0] = MAGIC0; buf[1] = MAGIC1; buf[2] = 2;
    buf[3] = static_cast<uint8_t>(hand_id);
    buf[4] = static_cast<uint8_t>((recording ? 1 : 0) | (wrist_gyro_valid ? 2 : 0));
    buf[5] = bat_percent;
    buf[6] = static_cast<uint8_t>(frame_id & 0xFF);
    buf[7] = static_cast<uint8_t>((frame_id >> 8) & 0xFF);
    for (unsigned i = 0; i < 8; ++i) buf[8 + i] = static_cast<uint8_t>(timestamp_us >> (8U * i));
    buf[16] = static_cast<uint8_t>(fail_mask & 0xFF);
    buf[17] = static_cast<uint8_t>((fail_mask >> 8) & 0xFF);
    const int q[4] = {quantize(quat.w), quantize(quat.x), quantize(quat.y), quantize(quat.z)};
    for (int i = 0; i < IMU_COUNT; ++i) {
        uint8_t* p = buf.data() + V2_HEADER_SIZE + i * IMU_RAW_SIZE;
        for (int k = 0; k < 3; ++k) write_i16_le(p + 2 * k, 0);
        for (int k = 0; k < 4; ++k) write_i16_le(p + 6 + 2 * k, q[k]);
    }
    const uint16_t crc = crc16_ccitt_false(buf.data(), V2_CRC_OFFSET);
    buf[V2_CRC_OFFSET] = static_cast<uint8_t>(crc & 0xFF);
    buf[V2_CRC_OFFSET + 1] = static_cast<uint8_t>((crc >> 8) & 0xFF);
    return buf;
}

void FrameSync::feed(const uint8_t* data, size_t len) {
    if (len == 0) return;
    buf_.insert(buf_.end(), data, data + len);
}

bool FrameSync::next(RawFrame& out) {
    for (;;) {
        // 找 magic
        size_t idx = std::string::npos;
        if (buf_.size() >= 2) {
            for (size_t i = 0; i + 1 < buf_.size(); ++i) {
                if (buf_[i] == MAGIC0 && buf_[i + 1] == MAGIC1) { idx = i; break; }
            }
        }
        if (idx == std::string::npos) {
            // 无 magic：保留末尾 FRAME_SIZE 字节（可能含半个 magic 与部分帧）
            if (buf_.size() > static_cast<size_t>(FRAME_SIZE))
                buf_.erase(buf_.begin(),
                           buf_.end() - static_cast<std::ptrdiff_t>(FRAME_SIZE));
            return false;
        }
        if (idx > 0) buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(idx));

        if (buf_.size() < 4) return false;

        const bool v2 = buf_[2] == 2 && (buf_[3] == HAND_LEFT || buf_[3] == HAND_RIGHT);
        const bool legacy = (buf_[2] == HAND_LEFT || buf_[2] == HAND_RIGHT) && buf_[3] == IMU_COUNT;
        if (!v2 && !legacy) {
            buf_.erase(buf_.begin());  // 设备号非法，丢 1 字节重扫
            continue;
        }
        if (v2) {
            if (buf_.size() < static_cast<size_t>(V2_FRAME_SIZE)) return false;
            if (parse_frame(buf_.data(), V2_FRAME_SIZE, out)) {
                buf_.erase(buf_.begin(), buf_.begin() + V2_FRAME_SIZE);
                return true;
            }
            buf_.erase(buf_.begin());
            continue;
        }
        if (buf_.size() < static_cast<size_t>(LEGACY_FRAME_SIZE)) return false;
        size_t legacy_size = 0;
        if (buf_.size() >= static_cast<size_t>(LEGACY_CAPTURE_FRAME_SIZE) &&
            parse_frame(buf_.data(), LEGACY_CAPTURE_FRAME_SIZE, out))
            legacy_size = LEGACY_CAPTURE_FRAME_SIZE;
        else if (parse_frame(buf_.data(), LEGACY_FRAME_SIZE, out))
            legacy_size = LEGACY_FRAME_SIZE;
        else if (buf_.size() < static_cast<size_t>(LEGACY_CAPTURE_FRAME_SIZE))
            return false;
        if (legacy_size != 0) {
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(legacy_size));
            return true;
        }
        buf_.erase(buf_.begin());  // 校验失败只前进 1 字节
    }
}

}  // namespace telex
