#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace glovity {

constexpr std::size_t kV2FrameSize = 180;
constexpr std::uint8_t kV2Version = 2;
constexpr std::uint8_t kFlagRecording = 0x01;
constexpr std::uint8_t kFlagWristGyroValid = 0x02;

inline std::uint16_t read_u16_le(const std::uint8_t* p) {
    return static_cast<std::uint16_t>(p[0]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(p[1]) << 8);
}

inline std::int16_t read_i16_le(const std::uint8_t* p) {
    return static_cast<std::int16_t>(read_u16_le(p));
}

inline std::uint64_t read_u64_le(const std::uint8_t* p) {
    std::uint64_t value = 0;
    for (unsigned i = 0; i < 8; ++i)
        value |= static_cast<std::uint64_t>(p[i]) << (8U * i);
    return value;
}

inline std::uint16_t crc16_ccitt_false(const std::uint8_t* data, std::size_t size) {
    std::uint16_t crc = 0xFFFF;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= static_cast<std::uint16_t>(data[i]) << 8;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000) ? static_cast<std::uint16_t>((crc << 1) ^ 0x1021)
                                 : static_cast<std::uint16_t>(crc << 1);
    }
    return crc;
}

struct V2Frame {
    std::uint8_t hand_id = 0;
    std::uint8_t flags = 0;
    std::uint8_t battery_percent = 0;
    std::uint16_t frame_seq = 0;
    std::uint64_t timestamp_us = 0;
    std::uint16_t imu_fail_mask = 0;
    std::array<std::array<std::int16_t, 7>, 11> imu{};
    std::array<std::int16_t, 3> wrist_gyro{};

    bool recording() const { return (flags & kFlagRecording) != 0; }
    bool wrist_gyro_valid() const { return (flags & kFlagWristGyroValid) != 0; }
};

inline bool parse_v2_frame(const std::uint8_t* data, std::size_t size, V2Frame& out) {
    if (!data || size != kV2FrameSize || data[0] != 0xAA || data[1] != 0x55 ||
        data[2] != kV2Version || data[3] > 1 || (data[4] & 0xFC) != 0 ||
        data[5] > 100 || (read_u16_le(data + 16) & 0xF800) != 0)
        return false;
    if (crc16_ccitt_false(data, 178) != read_u16_le(data + 178)) return false;

    V2Frame decoded;
    decoded.hand_id = data[3];
    decoded.flags = data[4];
    decoded.battery_percent = data[5];
    decoded.frame_seq = read_u16_le(data + 6);
    decoded.timestamp_us = read_u64_le(data + 8);
    decoded.imu_fail_mask = read_u16_le(data + 16);
    for (std::size_t i = 0; i < 11; ++i)
        for (std::size_t j = 0; j < 7; ++j)
            decoded.imu[i][j] = read_i16_le(data + 18 + (i * 7 + j) * 2);
    for (std::size_t i = 0; i < 3; ++i)
        decoded.wrist_gyro[i] = read_i16_le(data + 172 + i * 2);
    out = decoded;
    return true;
}

}  // namespace glovity
