#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "RemoteCalProtocol.h"

#define ESPNOW_CHANNEL 1
#define STATUS_LED_PIN 36
#define LED_ACTIVE_HIGH 1
#define HAND_COUNT 2
#define IMU_COUNT 11
#define HAND_TIMEOUT_MS 1000UL
#define CAL_LINK_TIMEOUT_MS 1000UL
#define PC_SERIAL_BAUD 2000000
#define PC_SERIAL Serial

// Set this before compiling each physical receiver.  Use a unique value such
// as "TELEX_2A_001"; do not use commas because the identity reply is CSV-like.
#ifndef GLOVITY_DEVICE_ID
#define GLOVITY_DEVICE_ID "UNASSIGNED_RECEIVER"
#endif
#define GLOVITY_DEVICE_ROLE "RECEIVER"
#define GLOVITY_FIRMWARE_VERSION "RemoteCal_Receiver_v1"
#define GLOVITY_DEVICE_PROTOCOL "REMOTE_CAL_1"

struct __attribute__((packed)) SerialFrame {
  uint8_t magic[2];
  uint8_t hand_id;
  uint8_t imu_count;
  uint8_t bat_percent;
  uint16_t frame_id;
  int16_t imu[IMU_COUNT][7];
  int16_t wrist_gyro[3];
  uint8_t checksum;
};

struct __attribute__((packed)) RadioPacket {
  uint32_t magic;
  uint16_t fail_mask;
  SerialFrame frame;
};

static_assert(sizeof(SerialFrame) == 168, "SerialFrame must be 168 bytes");
static_assert(sizeof(RadioPacket) <= 250, "RadioPacket too large");
static_assert(sizeof(RemoteCalCommand) < 250, "RemoteCalCommand too large");
static_assert(sizeof(RemoteCalEvent) < 250, "RemoteCalEvent too large");

static const uint32_t RADIO_MAGIC = 0x48444D49UL; // "IMDH"

// These are the production example MACs from the current receiver firmware.
// The production flasher must replace them with the actual paired glove MACs.
static const uint8_t LEFT_TX_ADDR[] = {0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x02};
static const uint8_t RIGHT_TX_ADDR[] = {0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x03};

static const uint8_t IMU_ADDR_LIST[IMU_COUNT] = {
  0x50, 0x51, 0x52, 0x53, 0x54, 0x55,
  0x56, 0x57, 0x58, 0x59, 0x60
};

struct PendingFrame {
  bool used;
  SerialFrame frame;
};

struct PendingEvent {
  bool used;
  RemoteCalEvent event;
};

static PendingFrame g_frameQueue[8];
static PendingEvent g_eventQueue[8];
static volatile uint8_t g_frameHead = 0;
static volatile uint8_t g_frameTail = 0;
static volatile uint8_t g_eventHead = 0;
static volatile uint8_t g_eventTail = 0;
static portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;

static uint32_t g_lastRxMs[HAND_COUNT] = {};
static uint16_t g_failMask[HAND_COUNT] = {0x07FF, 0x07FF};
static uint8_t g_battery[HAND_COUNT] = {};

static bool g_maintenance = false;
static bool g_calActive = false;
static uint8_t g_expectedHand = 0;
static uint32_t g_expectedSession = 0;
static uint16_t g_expectedSequence = 0;
static uint32_t g_lastCalEventMs = 0;

static char g_cmdBuf[160];
static uint8_t g_cmdLen = 0;

static bool MacEquals(const uint8_t *a, const uint8_t *b) {
  for (uint8_t i = 0; i < 6; ++i) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

static int8_t HandForSender(const uint8_t *mac) {
  if (MacEquals(mac, LEFT_TX_ADDR)) return 0;
  if (MacEquals(mac, RIGHT_TX_ADDR)) return 1;
  return -1;
}

static const uint8_t *MacForHand(uint8_t handId) {
  return handId == 0 ? LEFT_TX_ADDR : RIGHT_TX_ADDR;
}

static const char *SideName(uint8_t handId) {
  return handId == 0 ? "left" : "right";
}

static void SetLed(bool on) {
#if LED_ACTIVE_HIGH
  digitalWrite(STATUS_LED_PIN, on ? HIGH : LOW);
#else
  digitalWrite(STATUS_LED_PIN, on ? LOW : HIGH);
#endif
}

static void ClearFrameQueue() {
  portENTER_CRITICAL(&g_mux);
  g_frameHead = 0;
  g_frameTail = 0;
  for (uint8_t i = 0; i < 8; ++i) g_frameQueue[i].used = false;
  portEXIT_CRITICAL(&g_mux);
}

static void ClearEventQueue() {
  portENTER_CRITICAL(&g_mux);
  g_eventHead = 0;
  g_eventTail = 0;
  for (uint8_t i = 0; i < 8; ++i) g_eventQueue[i].used = false;
  portEXIT_CRITICAL(&g_mux);
}

static void PushFrame(const SerialFrame &frame) {
  portENTER_CRITICAL_ISR(&g_mux);
  uint8_t next = (uint8_t)((g_frameHead + 1) % 8);
  if (next != g_frameTail) {
    g_frameQueue[g_frameHead].frame = frame;
    g_frameQueue[g_frameHead].used = true;
    g_frameHead = next;
  }
  portEXIT_CRITICAL_ISR(&g_mux);
}

static bool PopFrame(SerialFrame &frame) {
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  if (g_frameTail != g_frameHead && g_frameQueue[g_frameTail].used) {
    frame = g_frameQueue[g_frameTail].frame;
    g_frameQueue[g_frameTail].used = false;
    g_frameTail = (uint8_t)((g_frameTail + 1) % 8);
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  return ok;
}

static void PushEvent(const RemoteCalEvent &event) {
  portENTER_CRITICAL_ISR(&g_mux);
  uint8_t next = (uint8_t)((g_eventHead + 1) % 8);
  if (next != g_eventTail) {
    g_eventQueue[g_eventHead].event = event;
    g_eventQueue[g_eventHead].used = true;
    g_eventHead = next;
  }
  portEXIT_CRITICAL_ISR(&g_mux);
}

static bool PopEvent(RemoteCalEvent &event) {
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  if (g_eventTail != g_eventHead && g_eventQueue[g_eventTail].used) {
    event = g_eventQueue[g_eventTail].event;
    g_eventQueue[g_eventTail].used = false;
    g_eventTail = (uint8_t)((g_eventTail + 1) % 8);
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  return ok;
}

static bool RadioFrameValid(const RadioPacket &packet) {
  if (packet.magic != RADIO_MAGIC) return false;
  if (packet.frame.magic[0] != 0xAA || packet.frame.magic[1] != 0x55) return false;
  if (packet.frame.imu_count != IMU_COUNT || packet.frame.hand_id >= HAND_COUNT) return false;
  return RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&packet.frame), sizeof(SerialFrame) - 1) == packet.frame.checksum;
}

static void OnDataRecv(const esp_now_recv_info_t *recvInfo, const uint8_t *data, int len) {
  if (recvInfo == nullptr) return;
  int8_t senderHand = HandForSender(recvInfo->src_addr);
  if (senderHand < 0) return;

  if (len == (int)sizeof(RadioPacket)) {
    RadioPacket packet;
    memcpy(&packet, data, sizeof(packet));
    if (!RadioFrameValid(packet)) return;
    // Protect the LED/data slots from a left/right firmware mix-up.  A right
    // MAC must carry hand_id=1, and a left MAC must carry hand_id=0.
    if (packet.frame.hand_id != (uint8_t)senderHand) return;
    uint8_t hand = packet.frame.hand_id;
    g_lastRxMs[hand] = millis();
    g_failMask[hand] = packet.fail_mask;
    g_battery[hand] = packet.frame.bat_percent;
    if (!g_maintenance) PushFrame(packet.frame);
    return;
  }

  if (len == (int)sizeof(RemoteCalEvent)) {
    RemoteCalEvent event;
    memcpy(&event, data, sizeof(event));
    if (RemoteCalEventValid(event) && event.hand_id == (uint8_t)senderHand) {
      // During remote calibration a glove intentionally stops sending the
      // normal RadioPacket stream and sends RemoteCalEvent packets instead.
      // Treat valid calibration events as link heartbeats so the status LED
      // does not falsely report the calibrating glove as disconnected.
      g_lastRxMs[event.hand_id] = millis();
      if (g_calActive && event.hand_id == g_expectedHand &&
          event.session_id == g_expectedSession) {
        g_lastCalEventMs = millis();
      }
      PushEvent(event);
    }
  }
}

static bool AddPeer(const uint8_t *address) {
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, address, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  esp_err_t result = esp_now_add_peer(&peer);
  return result == ESP_OK || result == ESP_ERR_ESPNOW_EXIST;
}

static void InitEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) ESP.restart();
  esp_now_register_recv_cb(OnDataRecv);
  AddPeer(LEFT_TX_ADDR);
  AddPeer(RIGHT_TX_ADDR);
}

static bool SendRemoteCommand(uint8_t type, uint8_t handId, uint32_t sessionId, uint16_t sequence) {
  RemoteCalCommand command = {};
  command.magic = REMOTE_CAL_CMD_MAGIC;
  command.version = REMOTE_CAL_PROTOCOL_VERSION;
  command.type = type;
  command.hand_id = handId;
  command.sequence = sequence;
  command.session_id = sessionId;
  command.checksum = RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&command), sizeof(command) - 1);
  esp_err_t result = esp_now_send(MacForHand(handId), reinterpret_cast<const uint8_t *>(&command), sizeof(command));
  return result == ESP_OK;
}

static void PrintCalError(uint8_t handId, uint32_t sessionId, uint16_t sequence, const char *reason) {
  PC_SERIAL.printf("$GLOVITY,RX,CAL,ERROR,%s,%u,%lu,%u,%s\r\n",
                   SideName(handId), handId, (unsigned long)sessionId, sequence, reason);
}

static void PrintDeviceInfo(const char *requestId) {
  String mac = WiFi.macAddress();
  PC_SERIAL.printf("$GLOVITY,DEVICE,INFO,%s,%s,%s,none,-1,%s,%s,%s\r\n",
                   requestId,
                   GLOVITY_DEVICE_ID,
                   GLOVITY_DEVICE_ROLE,
                   GLOVITY_FIRMWARE_VERSION,
                   GLOVITY_DEVICE_PROTOCOL,
                   mac.c_str());
}

static void PrintRemoteEvent(const RemoteCalEvent &event) {
  if (!g_maintenance) return;
  if (event.hand_id >= HAND_COUNT) return;
  if (event.session_id != g_expectedSession || event.hand_id != g_expectedHand) return;

  const char *side = SideName(event.hand_id);
  if (event.type == REMOTE_CAL_EVT_STATUS) {
    uint8_t active = 0;
    for (uint8_t i = 0; i < IMU_COUNT; ++i) {
      if (event.active_mask & (1U << i)) ++active;
    }
    PC_SERIAL.printf("$GLOVITY,RX,CAL,STATUS,%s,%u,%s,%u,%u,%lu,%u\r\n",
                     side, event.hand_id, event.flags ? "RUNNING" : "IDLE",
                     active, IMU_COUNT, (unsigned long)event.session_id, event.sequence);
  } else if (event.type == REMOTE_CAL_EVT_STARTED) {
    g_calActive = true;
    PC_SERIAL.printf("$GLOVITY,RX,CAL,STARTED,%s,%u,%lu,%u\r\n",
                     side, event.hand_id, (unsigned long)event.session_id, event.sequence);
  } else if (event.type == REMOTE_CAL_EVT_MAG) {
    PC_SERIAL.printf("$GLOVITY,RX,CAL,RAW9,%s,%u,%lu,%u,%lu",
                     side, event.hand_id, (unsigned long)event.session_id,
                     event.sequence, (unsigned long)event.timestamp_ms);
    for (uint8_t i = 0; i < IMU_COUNT; ++i) {
      if (!(event.active_mask & (1U << i))) continue;
      PC_SERIAL.printf(",0x%02X:%d:%d:%d:%d:%d:%d:%d:%d:%d", IMU_ADDR_LIST[i],
                       event.raw9[i][0], event.raw9[i][1], event.raw9[i][2],
                       event.raw9[i][3], event.raw9[i][4], event.raw9[i][5],
                       event.raw9[i][6], event.raw9[i][7], event.raw9[i][8]);
    }
    PC_SERIAL.println();
  } else if (event.type == REMOTE_CAL_EVT_COMPLETE) {
    g_calActive = false;
    g_lastCalEventMs = 0;
    PC_SERIAL.printf("$GLOVITY,RX,CAL,COMPLETE,%s,%u,%lu,%u\r\n",
                     side, event.hand_id, (unsigned long)event.session_id, event.sequence);
  } else if (event.type == REMOTE_CAL_EVT_ABORTED) {
    g_calActive = false;
    g_lastCalEventMs = 0;
    PC_SERIAL.printf("$GLOVITY,RX,CAL,ABORTED,%s,%u,%lu,%u\r\n",
                     side, event.hand_id, (unsigned long)event.session_id, event.sequence);
  } else if (event.type == REMOTE_CAL_EVT_ERROR) {
    g_calActive = false;
    g_lastCalEventMs = 0;
    PC_SERIAL.printf("$GLOVITY,RX,CAL,ERROR,%s,%u,%lu,%u,%u\r\n",
                     side, event.hand_id, (unsigned long)event.session_id,
                     event.sequence, event.flags);
  }
}

static void ProcessRemoteEvents() {
  RemoteCalEvent event;
  while (PopEvent(event)) PrintRemoteEvent(event);
}

static bool ParseHand(const char *text, uint8_t &handId) {
  if (strcmp(text, "left") == 0 || strcmp(text, "0") == 0) {
    handId = 0;
    return true;
  }
  if (strcmp(text, "right") == 0 || strcmp(text, "1") == 0) {
    handId = 1;
    return true;
  }
  return false;
}

static void ProcessCalCommand(const char *command) {
  char operation[16] = {};
  char side[12] = {};
  unsigned long session = 0;
  unsigned sequence = 0;
  int count = sscanf(command, "$GLOVITY,RX,CAL,%15[^,],%11[^,],%lu,%u", operation, side, &session, &sequence);
  if (count != 4) {
    PrintCalError(0, 0, 0, "BAD_COMMAND");
    return;
  }

  uint8_t handId = 0;
  if (!ParseHand(side, handId)) {
    PrintCalError(0, session, (uint16_t)sequence, "BAD_SIDE");
    return;
  }

  uint8_t type = 0;
  if (strcmp(operation, "STATUS") == 0) type = REMOTE_CAL_CMD_STATUS;
  else if (strcmp(operation, "START") == 0) type = REMOTE_CAL_CMD_START;
  else if (strcmp(operation, "STOP") == 0) type = REMOTE_CAL_CMD_STOP;
  else if (strcmp(operation, "ABORT") == 0) type = REMOTE_CAL_CMD_ABORT;
  else {
    PrintCalError(handId, session, (uint16_t)sequence, "BAD_OPERATION");
    return;
  }

  if (!g_maintenance) {
    PrintCalError(handId, session, (uint16_t)sequence, "NOT_IN_MAINTENANCE");
    return;
  }
  if (type == REMOTE_CAL_CMD_START && g_calActive) {
    PrintCalError(handId, session, (uint16_t)sequence, "BUSY");
    return;
  }
  if (type == REMOTE_CAL_CMD_STOP && !g_calActive) {
    PrintCalError(handId, session, (uint16_t)sequence, "NOT_RUNNING");
    return;
  }

  g_expectedHand = handId;
  g_expectedSession = (uint32_t)session;
  g_expectedSequence = (uint16_t)sequence;
  if (type == REMOTE_CAL_CMD_START) g_calActive = true;
  if (type == REMOTE_CAL_CMD_ABORT) g_calActive = false;
  if (type == REMOTE_CAL_CMD_START) g_lastCalEventMs = millis();
  if (type == REMOTE_CAL_CMD_ABORT) g_lastCalEventMs = 0;

  if (!SendRemoteCommand(type, handId, (uint32_t)session, (uint16_t)sequence)) {
    g_calActive = false;
    g_lastCalEventMs = 0;
    PrintCalError(handId, (uint32_t)session, (uint16_t)sequence, "SEND_FAILED");
    return;
  }
  PC_SERIAL.printf("$GLOVITY,RX,CAL,FORWARD,%s,%s,%lu,%u\r\n",
                   operation, SideName(handId), session, sequence);
}

static void ProcessUsbCommand(char *command) {
  while (*command == ' ' || *command == '\t') ++command;
  const char *queryPrefix = "$GLOVITY,DEVICE,QUERY";
  if (strncmp(command, queryPrefix, strlen(queryPrefix)) == 0 &&
      (command[strlen(queryPrefix)] == '\0' || command[strlen(queryPrefix)] == ',')) {
    const char *requestId = command[strlen(queryPrefix)] == ','
                              ? command + strlen(queryPrefix) + 1
                              : "0";
    PrintDeviceInfo(*requestId ? requestId : "0");
    return;
  }
  if (strcmp(command, "$GLOVITY,RX,MAINT,ENTER") == 0) {
    if (g_calActive) {
      PC_SERIAL.println("$GLOVITY,RX,MAINT,BUSY");
      return;
    }
    g_maintenance = true;
    g_calActive = false;
    g_lastCalEventMs = 0;
    ClearFrameQueue();
    ClearEventQueue();
    PC_SERIAL.println("$GLOVITY,RX,MAINT,READY,1");
    return;
  }
  if (strcmp(command, "$GLOVITY,RX,MAINT,EXIT") == 0) {
    if (g_calActive) {
      // Best-effort safety abort so a lost host process cannot leave the
      // glove in calibration mode indefinitely.
      SendRemoteCommand(REMOTE_CAL_CMD_ABORT, g_expectedHand, g_expectedSession, g_expectedSequence);
      g_calActive = false;
    }
    g_maintenance = false;
    ClearFrameQueue();
    ClearEventQueue();
    g_lastCalEventMs = 0;
    PC_SERIAL.println("$GLOVITY,RX,MAINT,EXITED");
    return;
  }
  if (strncmp(command, "$GLOVITY,RX,CAL,", 16) == 0) {
    ProcessCalCommand(command);
    return;
  }
  if (*command != '\0') PC_SERIAL.printf("$GLOVITY,RX,CMD,UNKNOWN,%s\r\n", command);
}

static void HandleUsbCommands() {
  while (PC_SERIAL.available()) {
    char value = (char)PC_SERIAL.read();
    if (value == '\r' || value == '\n') {
      if (g_cmdLen == 0) continue;
      g_cmdBuf[g_cmdLen] = '\0';
      ProcessUsbCommand(g_cmdBuf);
      g_cmdLen = 0;
    } else if (g_cmdLen < sizeof(g_cmdBuf) - 1) {
      g_cmdBuf[g_cmdLen++] = value;
    } else {
      g_cmdLen = 0;
      PC_SERIAL.println("$GLOVITY,RX,CMD,ERROR,TOO_LONG");
    }
  }
}

static void UpdateStatusLed() {
  static uint32_t lastToggleMs = 0;
  static bool blink = false;
  enum LedPattern : uint8_t {
    LED_FAST_BLINK,
    LED_SLOW_BLINK,
    LED_STEADY_ON,
  };
  static LedPattern previousPattern = LED_FAST_BLINK;

  uint32_t now = millis();

  bool leftConnected = g_lastRxMs[0] != 0 && (now - g_lastRxMs[0]) <= HAND_TIMEOUT_MS;
  bool rightConnected = g_lastRxMs[1] != 0 && (now - g_lastRxMs[1]) <= HAND_TIMEOUT_MS;
  bool leftNormal = leftConnected && g_failMask[0] == 0 && g_battery[0] >= 15;
  bool rightNormal = rightConnected && g_failMask[1] == 0 && g_battery[1] >= 15;

  LedPattern pattern;
  if (!leftConnected && !rightConnected) {
    pattern = LED_FAST_BLINK;
  } else if (!leftConnected || !rightConnected) {
    pattern = LED_SLOW_BLINK;
  } else if (leftNormal && rightNormal) {
    pattern = LED_STEADY_ON;
  } else {
    // Both radios are present, but at least one glove reports an IMU/battery
    // issue; keep the slower warning pattern instead of claiming "normal".
    pattern = LED_SLOW_BLINK;
  }

  if (pattern != previousPattern) {
    previousPattern = pattern;
    lastToggleMs = now;
    blink = false;
    SetLed(pattern == LED_STEADY_ON);
  }

  if (pattern == LED_STEADY_ON) {
    SetLed(true);
    return;
  }

  uint32_t intervalMs = pattern == LED_FAST_BLINK ? 120UL : 600UL;
  if (now - lastToggleMs >= intervalMs) {
    lastToggleMs = now;
    blink = !blink;
    SetLed(blink);
  }
}

void setup() {
  PC_SERIAL.begin(PC_SERIAL_BAUD);
  pinMode(STATUS_LED_PIN, OUTPUT);
  SetLed(false);
  InitEspNow();
}

void loop() {
  HandleUsbCommands();
  ProcessRemoteEvents();

  if (!g_maintenance) {
    SerialFrame frame;
    while (PopFrame(frame)) PC_SERIAL.write((const uint8_t *)&frame, sizeof(frame));
  } else {
    ClearFrameQueue();
  }

  UpdateStatusLed();
  delay(1);
}
