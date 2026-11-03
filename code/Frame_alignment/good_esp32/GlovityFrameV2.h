#pragma once

#include <stddef.h>
#include <stdint.h>

// Normal glove data, identical over ESP-NOW and USB. All multi-byte values
// are little-endian on the ESP32-S3. Calibration uses RemoteCalProtocol.h.
static constexpr uint8_t GLOVITY_FRAME_VERSION = 2;
static constexpr uint8_t GLOVITY_FLAG_RECORDING = 0x01;
static constexpr uint8_t GLOVITY_FLAG_WRIST_GYRO_VALID = 0x02;

struct __attribute__((packed)) GlovityFrameV2 {
  uint8_t magic[2];
  uint8_t version;
  uint8_t hand_id;
  uint8_t flags;
  uint8_t battery_percent;
  uint16_t frame_seq;
  uint64_t timestamp_us;
  uint16_t imu_fail_mask;
  int16_t imu[11][7];
  int16_t wrist_gyro[3];
  uint16_t crc16;
};

static_assert(sizeof(GlovityFrameV2) == 180, "V2 frame must be 180 bytes");
static_assert(offsetof(GlovityFrameV2, imu) == 18, "V2 IMU offset");
static_assert(offsetof(GlovityFrameV2, wrist_gyro) == 172, "V2 gyro offset");
static_assert(offsetof(GlovityFrameV2, crc16) == 178, "V2 CRC offset");

static inline uint16_t GlovityCrc16(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = crc & 0x8000 ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

static inline bool GlovityFrameValid(const GlovityFrameV2 &frame) {
  if (frame.magic[0] != 0xAA || frame.magic[1] != 0x55 ||
      frame.version != GLOVITY_FRAME_VERSION || frame.hand_id > 1 ||
      (frame.flags & ~0x03) != 0 || frame.battery_percent > 100 ||
      (frame.imu_fail_mask & ~0x07FF) != 0) return false;
  return GlovityCrc16(reinterpret_cast<const uint8_t *>(&frame),
                      offsetof(GlovityFrameV2, crc16)) == frame.crc16;
}
