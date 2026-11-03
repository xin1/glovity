// Receiver_RX 180 字节 V2 与 168/170 字节 legacy 帧的解析与重同步。
//
// 帧格式（小端）:
//   [0:2]     magic 0xAA 0x55
//   [2]       hand_id 0=LEFT 1=RIGHT
//   [3]       imu_count = 11
//   [4]       bat_percent
//   [5:7]     frame_id uint16
//   [7:161]   imu[11] x (int16 euler[3] + int16 quat[4])   quat 序为 w,x,y,z
//   [161:167] wrist_gyro int16[3]
//   [167]     checksum = XOR of bytes[0:167)
#ifndef TELEX_PROTOCOL_HPP
#define TELEX_PROTOCOL_HPP

#include <cstddef>
#include <cstdint>
#include <array>
#include <vector>

namespace telex {

constexpr uint8_t MAGIC0 = 0xAA;
constexpr uint8_t MAGIC1 = 0x55;
constexpr int  HAND_LEFT     = 0;
constexpr int  HAND_RIGHT    = 1;
constexpr int  IMU_COUNT     = 11;
constexpr int  IMU_RAW_SIZE  = 14;
constexpr int  HEADER_SIZE   = 7;
constexpr int  EXTRA_OFFSET  = HEADER_SIZE + IMU_COUNT * IMU_RAW_SIZE;  // 161
constexpr int  FRAME_SIZE    = EXTRA_OFFSET + 6 + 1;                    // 168
constexpr int  WRIST_IMU_INDEX = 0;
constexpr int  LEGACY_FRAME_SIZE = 168;
constexpr int  LEGACY_CAPTURE_FRAME_SIZE = 170;
constexpr int  V2_HEADER_SIZE = 18;
constexpr int  V2_EXTRA_OFFSET = 172;
constexpr int  V2_CRC_OFFSET = 178;
constexpr int  V2_FRAME_SIZE = 180;

enum ProtocolKind {
    PROTOCOL_UNKNOWN = 0,
    PROTOCOL_LEGACY_168 = 1,
    PROTOCOL_LEGACY_170 = 2,
    PROTOCOL_V2_180 = 3,
};

constexpr double QUAT_SCALE = 1.0 / 32768.0;
constexpr double ANG_SCALE  = 180.0 / 32768.0;
constexpr double GYRO_SCALE = 2000.0 / 32768.0;

static_assert(EXTRA_OFFSET == 161, "EXTRA_OFFSET 必须为 161");
static_assert(FRAME_SIZE == 168, "FRAME_SIZE 必须为 168");

/// 四元数 (w, x, y, z)，与帧内字节序一致。
struct QuatWXYZ { double w, x, y, z; };

/// 一帧解析结果。Python 侧分 parse_glove_frame / parse_wrist_frame 两次解析，
/// 这里合并为一次。
struct RawFrame {
    int      hand_id      = HAND_LEFT;
    int      bat_percent  = 0;
    uint16_t frame_id     = 0;
    uint64_t device_timestamp_us = 0;
    uint16_t imu_fail_mask = 0;
    bool     recording = false;
    bool     wrist_gyro_valid = true;
    int      protocol = PROTOCOL_UNKNOWN;
    int      protocol_version = 0;
    bool     has_device_timestamp = false;
    bool     has_recording_flag = false;
    bool     has_fail_mask = false;
    std::array<uint8_t, V2_FRAME_SIZE> raw{};
    size_t   raw_size = 0;
    std::array<QuatWXYZ, IMU_COUNT> imu{};
    double   wrist_euler_deg[3] = {0, 0, 0};
    double   wrist_gyro_dps[3]  = {0, 0, 0};
};

inline uint8_t xor_check(const uint8_t* data, size_t len) {
    uint8_t v = 0;
    for (size_t i = 0; i < len; ++i) v ^= data[i];
    return v;
}

inline int16_t read_i16_le(const uint8_t* p) {
    return static_cast<int16_t>(static_cast<uint16_t>(p[0]) |
                                (static_cast<uint16_t>(p[1]) << 8));
}

inline uint16_t read_u16_le(const uint8_t* p) {
    return static_cast<uint16_t>(static_cast<uint16_t>(p[0]) |
                                 (static_cast<uint16_t>(p[1]) << 8));
}

inline uint64_t read_u64_le(const uint8_t* p) {
    uint64_t v = 0;
    for (unsigned i = 0; i < 8; ++i) v |= static_cast<uint64_t>(p[i]) << (8U * i);
    return v;
}

uint16_t crc16_ccitt_false(const uint8_t* data, size_t len);

/// 校验并解析一个 180 字节 V2 或 168/170 字节 legacy 候选帧。
bool parse_frame(const uint8_t* data, size_t len, RawFrame& out);

std::vector<uint8_t> build_test_frame_v2(int hand_id, uint16_t frame_id,
                                         const QuatWXYZ& quat,
                                         uint64_t timestamp_us = 1000000,
                                         bool recording = false,
                                         uint8_t bat_percent = 100,
                                         uint16_t fail_mask = 0,
                                         bool wrist_gyro_valid = true);

/// 构造合法测试帧（对应 Python build_test_frame），供单测与离线数据源使用。
std::vector<uint8_t> build_test_frame(int hand_id, uint16_t frame_id,
                                      const QuatWXYZ& quat, uint8_t bat_percent = 100);

/// 流式重同步器。语义与 Python next_receiver_frame_candidate + 调用方消费逻辑一致：
/// 找 magic -> 丢前导垃圾 -> hand_id 非法丢 1 字节 -> 不足整帧则等待 ->
/// 校验失败只前进 1 字节 -> 无 magic 时保留末尾 FRAME_SIZE 字节。
class FrameSync {
public:
    void feed(const uint8_t* data, size_t len);

    /// 取出下一个校验通过的帧；无则返回 false。
    bool next(RawFrame& out);

    size_t buffered() const { return buf_.size(); }
    void clear() { buf_.clear(); }

private:
    std::vector<uint8_t> buf_;
};

}  // namespace telex
#endif  // TELEX_PROTOCOL_HPP
