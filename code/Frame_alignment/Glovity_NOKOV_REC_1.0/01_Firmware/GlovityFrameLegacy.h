#pragma once

#include <stddef.h>
#include <stdint.h>

static constexpr uint32_t GLOVITY_LEGACY_RADIO_MAGIC = 0x48444D49UL; // "IMDH"
static constexpr size_t GLOVITY_LEGACY_SERIAL_SIZE = 168;
static constexpr size_t GLOVITY_LEGACY_RADIO_SIZE = 174;

struct __attribute__((packed)) GlovityLegacySerialFrame {
  uint8_t magic[2];
  uint8_t hand_id;
  uint8_t imu_count;
  uint8_t battery_percent;
  uint16_t frame_id;
  int16_t imu[11][7];
  int16_t wrist_gyro[3];
  uint8_t checksum;
};

struct __attribute__((packed)) GlovityLegacyRadioPacket {
  uint32_t magic;
  uint16_t fail_mask;
  GlovityLegacySerialFrame frame;
};

static_assert(sizeof(GlovityLegacySerialFrame) == GLOVITY_LEGACY_SERIAL_SIZE,
              "legacy USB frame must be 168 bytes");
static_assert(sizeof(GlovityLegacyRadioPacket) == GLOVITY_LEGACY_RADIO_SIZE,
              "legacy radio packet must be 174 bytes");

static inline uint8_t GlovityLegacyXor(const uint8_t *data, size_t length) {
  uint8_t value = 0;
  for (size_t i = 0; i < length; ++i) value ^= data[i];
  return value;
}

static inline bool GlovityLegacyRadioValid(const GlovityLegacyRadioPacket &packet) {
  const GlovityLegacySerialFrame &frame = packet.frame;
  return packet.magic == GLOVITY_LEGACY_RADIO_MAGIC &&
         frame.magic[0] == 0xAA && frame.magic[1] == 0x55 &&
         frame.hand_id <= 1 && frame.imu_count == 11 &&
         frame.battery_percent <= 100 && (packet.fail_mask & ~0x07FFU) == 0 &&
         GlovityLegacyXor(reinterpret_cast<const uint8_t *>(&frame),
                          sizeof(frame) - 1) == frame.checksum;
}
