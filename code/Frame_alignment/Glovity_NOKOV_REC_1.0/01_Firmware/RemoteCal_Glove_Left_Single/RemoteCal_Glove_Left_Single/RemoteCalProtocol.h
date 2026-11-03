#pragma once

#include <stdint.h>
#include <stddef.h>

static const uint32_t REMOTE_CAL_CMD_MAGIC = 0x444D4347UL;   // "GCMD"
static const uint32_t REMOTE_CAL_EVENT_MAGIC = 0x4C414347UL; // "GCAL"
static const uint8_t REMOTE_CAL_PROTOCOL_VERSION = 1;

enum RemoteCalCommandType : uint8_t {
  REMOTE_CAL_CMD_STATUS = 1,
  REMOTE_CAL_CMD_START = 2,
  REMOTE_CAL_CMD_STOP = 3,
  REMOTE_CAL_CMD_ABORT = 4,
};

enum RemoteCalEventType : uint8_t {
  REMOTE_CAL_EVT_STATUS = 1,
  REMOTE_CAL_EVT_STARTED = 2,
  REMOTE_CAL_EVT_MAG = 3,
  REMOTE_CAL_EVT_COMPLETE = 4,
  REMOTE_CAL_EVT_ERROR = 5,
  REMOTE_CAL_EVT_ABORTED = 6,
};

enum RemoteCalErrorCode : uint8_t {
  REMOTE_CAL_ERR_NONE = 0,
  REMOTE_CAL_ERR_NO_IMU = 1,
  REMOTE_CAL_ERR_NOT_RUNNING = 2,
  REMOTE_CAL_ERR_ALREADY_RUNNING = 3,
  REMOTE_CAL_ERR_SAVE = 4,
  REMOTE_CAL_ERR_UNKNOWN_COMMAND = 5,
  REMOTE_CAL_ERR_TIMEOUT = 6,
};

struct __attribute__((packed)) RemoteCalCommand {
  uint32_t magic;
  uint8_t version;
  uint8_t type;
  uint8_t hand_id;
  uint8_t flags;
  uint16_t sequence;
  uint32_t session_id;
  uint8_t checksum;
};

struct __attribute__((packed)) RemoteCalEvent {
  uint32_t magic;
  uint8_t version;
  uint8_t type;
  uint8_t hand_id;
  uint8_t flags;
  uint16_t sequence;
  uint32_t session_id;
  uint32_t timestamp_ms;
  uint16_t active_mask;
  int16_t mag[11][3];
  uint8_t checksum;
};

static inline uint8_t RemoteCalChecksum(const uint8_t *data, size_t len) {
  uint8_t result = 0;
  for (size_t i = 0; i < len; ++i) result ^= data[i];
  return result;
}

static inline bool RemoteCalCommandValid(const RemoteCalCommand &packet) {
  return packet.magic == REMOTE_CAL_CMD_MAGIC &&
         packet.version == REMOTE_CAL_PROTOCOL_VERSION &&
         RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&packet), sizeof(packet) - 1) == packet.checksum;
}

static inline bool RemoteCalEventValid(const RemoteCalEvent &packet) {
  return packet.magic == REMOTE_CAL_EVENT_MAGIC &&
         packet.version == REMOTE_CAL_PROTOCOL_VERSION &&
         RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&packet), sizeof(packet) - 1) == packet.checksum;
}
