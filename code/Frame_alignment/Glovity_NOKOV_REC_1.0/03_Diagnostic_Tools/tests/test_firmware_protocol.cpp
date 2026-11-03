#include "../good_esp32/GlovityFrameV2.h"
#include "../good_esp32/GlovityFrameLegacy.h"

#include <cstring>

int main() {
  static const uint8_t check[] = "123456789";
  if (GlovityCrc16(check, 9) != 0x29B1) return 1;

  GlovityFrameV2 frame = {};
  frame.magic[0] = 0xAA;
  frame.magic[1] = 0x55;
  frame.version = GLOVITY_FRAME_VERSION;
  frame.hand_id = 0;
  frame.flags = GLOVITY_FLAG_RECORDING | GLOVITY_FLAG_WRIST_GYRO_VALID;
  frame.battery_percent = 100;
  frame.frame_seq = 1;
  frame.timestamp_us = 1000000;
  frame.crc16 = GlovityCrc16(reinterpret_cast<const uint8_t *>(&frame),
                             offsetof(GlovityFrameV2, crc16));
  if (frame.crc16 != 0x491A || !GlovityFrameValid(frame)) return 2;
  frame.imu[0][0] = 1;
  if (GlovityFrameValid(frame)) return 3;
  GlovityLegacyRadioPacket legacy = {};
  legacy.magic = GLOVITY_LEGACY_RADIO_MAGIC;
  legacy.frame.magic[0] = 0xAA;
  legacy.frame.magic[1] = 0x55;
  legacy.frame.hand_id = 1;
  legacy.frame.imu_count = 11;
  legacy.frame.battery_percent = 90;
  legacy.frame.checksum = GlovityLegacyXor(
      reinterpret_cast<const uint8_t *>(&legacy.frame), sizeof(legacy.frame) - 1);
  if (!GlovityLegacyRadioValid(legacy)) return 4;
  legacy.frame.checksum ^= 1;
  if (GlovityLegacyRadioValid(legacy)) return 5;
  return 0;
}
