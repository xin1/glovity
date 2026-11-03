// Fixed right-hand build entry point.  The implementation is shared so fixes
// remain synchronized, but HAND_ID cannot accidentally fall back to 0/left
// when the right-hand image is built.
#define HAND_ID 1
#ifndef GLOVITY_DEVICE_ID
#define GLOVITY_DEVICE_ID "UNASSIGNED_RIGHT_GLOVE"
#endif

// ---- RemoteCal_Glove.ino (merged) ----
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_timer.h>

#include "RemoteCalProtocol.h"
#include "../../GlovityFrameV2.h"

// Shared implementation.  Compile RemoteCal_Glove_Left.ino or
// RemoteCal_Glove_Right.ino instead of compiling this file directly.
#ifndef HAND_ID
#error "Compile RemoteCal_Glove_Left.ino or RemoteCal_Glove_Right.ino"
#endif

#if HAND_ID == 0
#define HAND_NAME "left"
#else
#define HAND_NAME "right"
#endif

// Set this before compiling each physical glove.  Use a unique value such as
// "TELEX_2A_001"; do not use commas because the identity reply is CSV-like.
#ifndef GLOVITY_DEVICE_ID
#define GLOVITY_DEVICE_ID "UNASSIGNED_GLOVE"
#endif
#define GLOVITY_DEVICE_ROLE "GLOVE"
#define GLOVITY_FIRMWARE_VERSION "RemoteCal_Glove_v2"
#define GLOVITY_DEVICE_PROTOCOL "GLOVITY_FRAME_2"

#define USB_TEXT_BAUD 921600
#define PC_SERIAL Serial
#define REC_PIN 5
#define OUTPUT_EN_PIN 4
#define I2C_SDA 8
#define I2C_SCL 9
#define I2C_FREQ 400000
#define IMU_POWER_CTRL 1
#define IR_POWER_CTRL 42
#define POWER_CTRL 41
#define PMOS_ON_LEVEL LOW
#define BAT_ADC_PIN 2
#define VBAT_DIVIDER_RATIO 1.454545f
#define LOW_BATTERY_VOLTAGE 3.50f
#define ESPNOW_CHANNEL 1
// Keep this rate synchronized with the left glove.
#define SEND_HZ 30
#define SEND_INTERVAL_US (1000000UL / SEND_HZ)
#define CAL_SAMPLE_INTERVAL_MS 500UL
#define REMOTE_CAL_TIMEOUT_MS 60000UL

#define REG_GX 0x37
#define REG_ROLL 0x3D
#define REG_Q0 0x51
#define REG_SAVE 0x00
#define REG_CALSW 0x01
#define REG_HX 0x3A
#define REG_KEY 0x69

static const uint8_t IMU_ADDR_LIST[11] = {
  0x50, 0x51, 0x52, 0x53, 0x54, 0x55,
  0x56, 0x57, 0x58, 0x59, 0x60
};
#define IMU_COUNT 11

// The production flasher must patch this with the actual receiver MAC.
static const uint8_t RECEIVER_ADDR[] = {0x14, 0xC1, 0x9F, 0x2E, 0x79, 0x98};

static_assert(sizeof(GlovityFrameV2) <= 250, "V2 frame too large for ESP-NOW");
static_assert(sizeof(RemoteCalEvent) <= 250, "RemoteCalEvent too large");

enum RunMode : uint8_t {
  MODE_NORMAL = 0,
  MODE_MAG_CALIBRATING = 1,
};

static RunMode g_mode = MODE_NORMAL;
static GlovityFrameV2 g_frame;
static GlovityFrameV2 g_usbFrame;
static size_t g_usbOffset = sizeof(GlovityFrameV2);
static uint16_t g_frameId = 0;
static uint32_t g_nextSendUs = 0;
static uint8_t g_cachedBatteryPercent = 0;
static uint32_t g_lastMagPrintMs = 0;
static uint32_t g_calStartedMs = 0;
static uint8_t g_activeImus[IMU_COUNT] = {};
static uint8_t g_activeCount = 0;
static uint16_t g_activeMask = 0;
static bool g_remoteCalibration = false;

static RemoteCalCommand g_remoteCommands[4];
static volatile uint8_t g_remoteHead = 0;
static volatile uint8_t g_remoteTail = 0;
static portMUX_TYPE g_remoteMux = portMUX_INITIALIZER_UNLOCKED;

static char g_usbCommand[80];
static uint8_t g_usbCommandLength = 0;
static bool g_poweringOff = false;
static uint32_t g_lastBatteryCheckMs = 0;
static uint32_t g_lowBatterySinceMs = 0;

static bool ReadRegs(uint8_t address, uint8_t startRegister, int16_t *values, uint8_t count) {
  Wire.beginTransmission(address);
  Wire.write(startRegister);
  if (Wire.endTransmission(false) != 0) return false;
  uint8_t requested = (uint8_t)(count * 2);
  if (Wire.requestFrom(address, requested) < requested) return false;
  for (uint8_t i = 0; i < count; ++i) {
    uint8_t lo = Wire.read();
    uint8_t hi = Wire.read();
    values[i] = (int16_t)((hi << 8) | lo);
  }
  return true;
}

static bool WriteReg(uint8_t address, uint8_t reg, uint16_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write((uint8_t)(value & 0xFF));
  Wire.write((uint8_t)(value >> 8));
  return Wire.endTransmission() == 0;
}

static bool ProbeImu(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

static void UnlockActiveImus() {
  for (uint8_t i = 0; i < g_activeCount; ++i) WriteReg(g_activeImus[i], REG_KEY, 0xB588);
  delay(100);
}

static uint8_t ScanActiveImus(bool verbose) {
  g_activeCount = 0;
  g_activeMask = 0;
  for (uint8_t i = 0; i < IMU_COUNT; ++i) {
    bool found = ProbeImu(IMU_ADDR_LIST[i]);
    if (verbose) PC_SERIAL.printf("$GLOVITY,CAL,SCAN,0x%02X,%s\r\n", IMU_ADDR_LIST[i], found ? "FOUND" : "MISS");
    if (found) {
      g_activeImus[g_activeCount++] = IMU_ADDR_LIST[i];
      g_activeMask |= (uint16_t)(1U << i);
    }
  }
  if (verbose) PC_SERIAL.printf("$GLOVITY,CAL,SCAN_DONE,%u,%u\r\n", g_activeCount, IMU_COUNT);
  return g_activeCount;
}

static bool SetCalibrationSwitch(uint16_t mode, bool verbose) {
  UnlockActiveImus();
  bool allOk = true;
  for (uint8_t i = 0; i < g_activeCount; ++i) {
    bool ok = WriteReg(g_activeImus[i], REG_CALSW, mode);
    allOk = allOk && ok;
    if (verbose) PC_SERIAL.printf("$GLOVITY,CAL,CALSW,0x%02X,0x%02X,%s\r\n",
                                  g_activeImus[i], mode, ok ? "OK" : "FAIL");
  }
  delay(100);
  return allOk;
}

static bool SaveCalibration(bool verbose) {
  UnlockActiveImus();
  bool allOk = true;
  for (uint8_t i = 0; i < g_activeCount; ++i) {
    bool ok = WriteReg(g_activeImus[i], REG_SAVE, 0x0000);
    allOk = allOk && ok;
    if (verbose) PC_SERIAL.printf("$GLOVITY,CAL,SAVE,0x%02X,%s\r\n", g_activeImus[i], ok ? "OK" : "FAIL");
  }
  delay(500);
  return allOk;
}

static bool ReadOneImu(uint8_t address, int16_t output[7]) {
  int16_t euler[3] = {};
  int16_t quat[4] = {};
  bool okEuler = ReadRegs(address, REG_ROLL, euler, 3);
  bool okQuat = ReadRegs(address, REG_Q0, quat, 4);
  if (!okEuler || !okQuat) return false;
  memcpy(output, euler, sizeof(euler));
  memcpy(output + 3, quat, sizeof(quat));
  return true;
}

static float ReadBatteryVoltage(uint8_t samples) {
  uint32_t totalMv = 0;
  for (uint8_t i = 0; i < samples; ++i) {
    totalMv += analogReadMilliVolts(BAT_ADC_PIN);
    if (samples > 1) delay(2);
  }
  return ((float)totalMv / (float)samples / 1000.0f) * VBAT_DIVIDER_RATIO;
}

static void AutoPowerOff() {
  g_poweringOff = true;
  PC_SERIAL.println("battery low, auto power off");
  PC_SERIAL.flush();
  esp_now_deinit();
  esp_wifi_stop();
  WiFi.mode(WIFI_OFF);
  digitalWrite(IR_POWER_CTRL, !PMOS_ON_LEVEL);
  digitalWrite(IMU_POWER_CTRL, !PMOS_ON_LEVEL);
  digitalWrite(POWER_CTRL, HIGH);
  delay(3000);
  while (true) {
    digitalWrite(POWER_CTRL, HIGH);
    delay(1000);
  }
}

static void CheckLowBattery() {
  uint32_t now = millis();
  if (g_poweringOff || now - g_lastBatteryCheckMs < 1000UL) return;
  g_lastBatteryCheckMs = now;
  // One ADC conversion per second keeps battery monitoring off the 30 Hz
  // frame's 16 ms eight-sample delay path.
  float voltage = ReadBatteryVoltage(1);
  int percent = (int)lroundf((voltage - 3.30f) * 100.0f / 0.90f);
  g_cachedBatteryPercent = (uint8_t)constrain(percent, 0, 100);
  if (voltage < LOW_BATTERY_VOLTAGE) {
    if (g_lowBatterySinceMs == 0) g_lowBatterySinceMs = now;
    if (now - g_lowBatterySinceMs >= 8000UL) AutoPowerOff();
  } else {
    g_lowBatterySinceMs = 0;
  }
}

static bool SendCalEvent(uint8_t type, uint8_t flags, uint16_t sequence, uint32_t sessionId) {
  RemoteCalEvent event = {};
  event.magic = REMOTE_CAL_EVENT_MAGIC;
  event.version = REMOTE_CAL_PROTOCOL_VERSION;
  event.type = type;
  event.hand_id = HAND_ID;
  event.flags = flags;
  event.sequence = sequence;
  event.session_id = sessionId;
  event.timestamp_ms = millis();
  event.active_mask = g_activeMask;
  event.checksum = RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&event), sizeof(event) - 1);
  return esp_now_send(RECEIVER_ADDR, reinterpret_cast<const uint8_t *>(&event), sizeof(event)) == ESP_OK;
}

static bool SendMagSample(uint16_t sequence, uint32_t sessionId) {
  RemoteCalEvent event = {};
  event.magic = REMOTE_CAL_EVENT_MAGIC;
  event.version = REMOTE_CAL_PROTOCOL_VERSION;
  event.type = REMOTE_CAL_EVT_MAG;
  event.hand_id = HAND_ID;
  event.sequence = sequence;
  event.session_id = sessionId;
  event.timestamp_ms = millis();
  event.active_mask = g_activeMask;
  for (uint8_t i = 0; i < IMU_COUNT; ++i) {
    if (!(g_activeMask & (1U << i))) continue;
    int16_t mag[3] = {};
    if (ReadRegs(IMU_ADDR_LIST[i], REG_HX, mag, 3)) {
      event.mag[i][0] = mag[0];
      event.mag[i][1] = mag[1];
      event.mag[i][2] = mag[2];
    } else {
      event.active_mask &= (uint16_t)~(1U << i);
    }
  }
  event.checksum = RemoteCalChecksum(reinterpret_cast<const uint8_t *>(&event), sizeof(event) - 1);
  return esp_now_send(RECEIVER_ADDR, reinterpret_cast<const uint8_t *>(&event), sizeof(event)) == ESP_OK;
}

static void PushRemoteCommand(const RemoteCalCommand &command) {
  portENTER_CRITICAL_ISR(&g_remoteMux);
  uint8_t next = (uint8_t)((g_remoteHead + 1) % 4);
  if (next != g_remoteTail) {
    g_remoteCommands[g_remoteHead] = command;
    g_remoteHead = next;
  }
  portEXIT_CRITICAL_ISR(&g_remoteMux);
}

static bool PopRemoteCommand(RemoteCalCommand &command) {
  bool ok = false;
  portENTER_CRITICAL(&g_remoteMux);
  if (g_remoteTail != g_remoteHead) {
    command = g_remoteCommands[g_remoteTail];
    g_remoteTail = (uint8_t)((g_remoteTail + 1) % 4);
    ok = true;
  }
  portEXIT_CRITICAL(&g_remoteMux);
  return ok;
}

static void OnRemoteCommand(const esp_now_recv_info_t *recvInfo, const uint8_t *data, int len) {
  if (recvInfo == nullptr || len != (int)sizeof(RemoteCalCommand)) return;
  if (memcmp(recvInfo->src_addr, RECEIVER_ADDR, 6) != 0) return;
  RemoteCalCommand command;
  memcpy(&command, data, sizeof(command));
  if (!RemoteCalCommandValid(command) || command.hand_id != HAND_ID) return;
  PushRemoteCommand(command);
}

static void SendRemoteError(const RemoteCalCommand &command, uint8_t errorCode) {
  SendCalEvent(REMOTE_CAL_EVT_ERROR, errorCode, command.sequence, command.session_id);
}

static void PrintDeviceInfo(const char *requestId) {
  String mac = WiFi.macAddress();
  PC_SERIAL.printf("$GLOVITY,DEVICE,INFO,%s,%s,%s,%s,%u,%s,%s,%s\r\n",
                   requestId,
                   GLOVITY_DEVICE_ID,
                   GLOVITY_DEVICE_ROLE,
                   HAND_NAME,
                   HAND_ID,
                   GLOVITY_FIRMWARE_VERSION,
                   GLOVITY_DEVICE_PROTOCOL,
                   mac.c_str());
}

static void StartRemoteCalibration(const RemoteCalCommand &command) {
  if (g_mode == MODE_MAG_CALIBRATING) {
    SendRemoteError(command, REMOTE_CAL_ERR_ALREADY_RUNNING);
    return;
  }
  if (ScanActiveImus(false) == 0) {
    SendRemoteError(command, REMOTE_CAL_ERR_NO_IMU);
    return;
  }
  if (!SetCalibrationSwitch(0x0007, false)) {
    SendRemoteError(command, REMOTE_CAL_ERR_NO_IMU);
    return;
  }
  g_mode = MODE_MAG_CALIBRATING;
  g_remoteCalibration = true;
  g_calStartedMs = millis();
  g_lastMagPrintMs = 0;
  SendCalEvent(REMOTE_CAL_EVT_STARTED, 0, command.sequence, command.session_id);
}

static void StopRemoteCalibration(const RemoteCalCommand &command, bool save) {
  if (g_mode != MODE_MAG_CALIBRATING) {
    SendRemoteError(command, REMOTE_CAL_ERR_NOT_RUNNING);
    return;
  }
  bool switchOk = SetCalibrationSwitch(0x0000, false);
  bool saveOk = !save || SaveCalibration(false);
  g_mode = MODE_NORMAL;
  g_remoteCalibration = false;
  g_nextSendUs = micros();
  if (!switchOk || !saveOk) {
    SendRemoteError(command, REMOTE_CAL_ERR_SAVE);
  } else if (save) {
    SendCalEvent(REMOTE_CAL_EVT_COMPLETE, 0, command.sequence, command.session_id);
  } else {
    SendCalEvent(REMOTE_CAL_EVT_ABORTED, 0, command.sequence, command.session_id);
  }
}

static void StartWiredCalibration() {
  if (g_mode == MODE_MAG_CALIBRATING) {
    PC_SERIAL.println("$GLOVITY,CAL,ALREADY_RUNNING");
    return;
  }
  PC_SERIAL.println("$GLOVITY,CAL,STARTING");
  if (ScanActiveImus(true) == 0) {
    PC_SERIAL.println("$GLOVITY,CAL,ERROR,NO_IMU");
    return;
  }
  SetCalibrationSwitch(0x0007, true);
  g_mode = MODE_MAG_CALIBRATING;
  g_remoteCalibration = false;
  g_calStartedMs = millis();
  g_lastMagPrintMs = 0;
  PC_SERIAL.println("$GLOVITY,CAL,STARTED");
}

static void StopWiredCalibration() {
  if (g_mode != MODE_MAG_CALIBRATING) {
    PC_SERIAL.println("$GLOVITY,CAL,ERROR,NOT_RUNNING");
    return;
  }
  PC_SERIAL.println("$GLOVITY,CAL,STOPPING");
  SetCalibrationSwitch(0x0000, true);
  SaveCalibration(true);
  g_mode = MODE_NORMAL;
  g_remoteCalibration = false;
  g_nextSendUs = micros();
  PC_SERIAL.println("$GLOVITY,CAL,COMPLETE");
  PC_SERIAL.println("$GLOVITY,MODE,NORMAL");
}

static void ProcessUsbCommand(char *command) {
  while (*command == ' ' || *command == '\t') ++command;
  for (char *p = command; *p; ++p) {
    if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 'a' + 'A');
  }
  const char *queryPrefix = "$GLOVITY,DEVICE,QUERY";
  if (strncmp(command, queryPrefix, strlen(queryPrefix)) == 0 &&
      (command[strlen(queryPrefix)] == '\0' || command[strlen(queryPrefix)] == ',')) {
    const char *requestId = command[strlen(queryPrefix)] == ','
                              ? command + strlen(queryPrefix) + 1
                              : "0";
    PrintDeviceInfo(*requestId ? requestId : "0");
    return;
  }
  if (strcmp(command, "$GLOVITY,CAL,START") == 0 || strcmp(command, "CAL,START") == 0) {
    StartWiredCalibration();
  } else if (strcmp(command, "$GLOVITY,CAL,STOP") == 0 || strcmp(command, "CAL,STOP") == 0) {
    StopWiredCalibration();
  } else if (strcmp(command, "$GLOVITY,CAL,STATUS") == 0 || strcmp(command, "CAL,STATUS") == 0) {
    ScanActiveImus(false);
    PC_SERIAL.printf("$GLOVITY,CAL,STATUS,%s,%u,%u,%s,%u\r\n",
                     g_mode == MODE_MAG_CALIBRATING ? "RUNNING" : "IDLE",
                     g_activeCount, IMU_COUNT, HAND_NAME, HAND_ID);
  } else if (strcmp(command, "$GLOVITY,NORMAL") == 0 || strcmp(command, "NORMAL") == 0) {
    if (g_mode == MODE_MAG_CALIBRATING) StopWiredCalibration();
    else PC_SERIAL.println("$GLOVITY,MODE,NORMAL");
  } else if (*command != '\0') {
    PC_SERIAL.printf("$GLOVITY,CMD,UNKNOWN,%s\r\n", command);
  }
}

static void HandleUsbCommands() {
  while (PC_SERIAL.available()) {
    char value = (char)PC_SERIAL.read();
    if (value == '\r' || value == '\n') {
      if (g_usbCommandLength == 0) continue;
      g_usbCommand[g_usbCommandLength] = '\0';
      ProcessUsbCommand(g_usbCommand);
      g_usbCommandLength = 0;
    } else if (g_usbCommandLength < sizeof(g_usbCommand) - 1) {
      g_usbCommand[g_usbCommandLength++] = value;
    } else {
      g_usbCommandLength = 0;
      PC_SERIAL.println("$GLOVITY,CMD,ERROR,TOO_LONG");
    }
  }
}

static void BuildNormalFrame() {
  memset(&g_frame, 0, sizeof(g_frame));
  g_frame.magic[0] = 0xAA;
  g_frame.magic[1] = 0x55;
  g_frame.version = GLOVITY_FRAME_VERSION;
  g_frame.hand_id = HAND_ID;
  // Snapshot REC, sequence, and time before the first I2C transaction.
  g_frame.flags = digitalRead(REC_PIN) == HIGH ? GLOVITY_FLAG_RECORDING : 0;
  g_frame.frame_seq = g_frameId++;
  g_frame.timestamp_us = (uint64_t)esp_timer_get_time();
  g_frame.battery_percent = g_cachedBatteryPercent;
  for (uint8_t i = 0; i < IMU_COUNT; ++i) {
    if (!ReadOneImu(IMU_ADDR_LIST[i], g_frame.imu[i])) {
      g_frame.imu_fail_mask |= (uint16_t)(1U << i);
      memset(g_frame.imu[i], 0, sizeof(g_frame.imu[i]));
    }
    if (i == 0) {
      if (ReadRegs(IMU_ADDR_LIST[i], REG_GX, g_frame.wrist_gyro, 3))
        g_frame.flags |= GLOVITY_FLAG_WRIST_GYRO_VALID;
      else
        memset(g_frame.wrist_gyro, 0, sizeof(g_frame.wrist_gyro));
    }
  }
  g_frame.crc16 = GlovityCrc16(reinterpret_cast<const uint8_t *>(&g_frame),
                               offsetof(GlovityFrameV2, crc16));
}

static void LoopNormal() {
  uint32_t now = micros();
  if ((int32_t)(now - g_nextSendUs) < 0) return;
  g_nextSendUs += SEND_INTERVAL_US;
  if ((int32_t)(micros() - g_nextSendUs) >= 0) g_nextSendUs = micros() + SEND_INTERVAL_US;
  BuildNormalFrame();
  if (digitalRead(OUTPUT_EN_PIN) != HIGH) return;
  esp_now_send(RECEIVER_ADDR, reinterpret_cast<const uint8_t *>(&g_frame), sizeof(g_frame));
  // If USB is behind, keep the frame already in progress intact and drop
  // this newer USB copy; radio capture continues independently.
  if (g_usbOffset == sizeof(g_usbFrame)) {
    g_usbFrame = g_frame;
    g_usbOffset = 0;
  }
}

static void DrainUsbFrame() {
  if (g_usbOffset == sizeof(g_usbFrame)) return;
  if (g_usbOffset == 0 && digitalRead(OUTPUT_EN_PIN) != HIGH) {
    g_usbOffset = sizeof(g_usbFrame);
    return;
  }
  int writable = PC_SERIAL.availableForWrite();
  if (writable <= 0) return;
  size_t remaining = sizeof(g_usbFrame) - g_usbOffset;
  size_t count = (size_t)writable < remaining ? (size_t)writable : remaining;
  g_usbOffset += PC_SERIAL.write(reinterpret_cast<const uint8_t *>(&g_usbFrame) + g_usbOffset, count);
}

static uint32_t g_remoteSession = 0;
static uint16_t g_remoteSequence = 0;

static void PrintWiredMagFrame() {
  PC_SERIAL.printf("$GLOVITY,CAL,MAG,%lu", (unsigned long)(millis() / 1000));
  for (uint8_t i = 0; i < IMU_COUNT; ++i) {
    if (!(g_activeMask & (1U << i))) continue;
    int16_t mag[3] = {};
    if (ReadRegs(IMU_ADDR_LIST[i], REG_HX, mag, 3)) {
      PC_SERIAL.printf(",0x%02X:%d:%d:%d", IMU_ADDR_LIST[i], mag[0], mag[1], mag[2]);
    } else {
      PC_SERIAL.printf(",0x%02X:ERR", IMU_ADDR_LIST[i]);
    }
  }
  PC_SERIAL.println();
}

static void LoopWiredMagSamples() {
  uint32_t now = millis();
  if (now - g_lastMagPrintMs >= CAL_SAMPLE_INTERVAL_MS) {
    g_lastMagPrintMs = now;
    PrintWiredMagFrame();
  }
}

static void LoopRemoteMagSamples() {
  uint32_t now = millis();
  if (now - g_calStartedMs >= REMOTE_CAL_TIMEOUT_MS) {
    SetCalibrationSwitch(0x0000, false);
    g_mode = MODE_NORMAL;
    g_remoteCalibration = false;
    SendCalEvent(REMOTE_CAL_EVT_ERROR, REMOTE_CAL_ERR_TIMEOUT, g_remoteSequence, g_remoteSession);
    return;
  }
  if (now - g_lastMagPrintMs >= CAL_SAMPLE_INTERVAL_MS) {
    g_lastMagPrintMs = now;
    SendMagSample(g_remoteSequence, g_remoteSession);
  }
}

static void InitEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) ESP.restart();
  esp_now_register_recv_cb(OnRemoteCommand);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, RECEIVER_ADDR, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  esp_now_add_peer(&peer);
}

void setup() {
  PC_SERIAL.begin(USB_TEXT_BAUD);
  pinMode(REC_PIN, INPUT_PULLDOWN);
  pinMode(OUTPUT_EN_PIN, INPUT_PULLUP);
  pinMode(POWER_CTRL, OUTPUT);
  digitalWrite(POWER_CTRL, LOW);
  pinMode(IMU_POWER_CTRL, OUTPUT);
  digitalWrite(IMU_POWER_CTRL, PMOS_ON_LEVEL);
  pinMode(IR_POWER_CTRL, OUTPUT);
  digitalWrite(IR_POWER_CTRL, PMOS_ON_LEVEL);
  delay(1200);
  Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
  Wire.setTimeOut(10);
  analogReadResolution(12);
  InitEspNow();
  g_nextSendUs = micros();
}

void loop() {
  CheckLowBattery();
  // Never insert a text response inside a partially emitted binary frame.
  DrainUsbFrame();
  if (g_usbOffset == sizeof(g_usbFrame)) HandleUsbCommands();

  RemoteCalCommand command;
  while (PopRemoteCommand(command)) {
    g_remoteSession = command.session_id;
    g_remoteSequence = command.sequence;
    if (command.type == REMOTE_CAL_CMD_STATUS) {
      ScanActiveImus(false);
      SendCalEvent(REMOTE_CAL_EVT_STATUS, g_mode == MODE_MAG_CALIBRATING ? 1 : 0,
                   command.sequence, command.session_id);
    } else if (command.type == REMOTE_CAL_CMD_START) {
      StartRemoteCalibration(command);
    } else if (command.type == REMOTE_CAL_CMD_STOP) {
      StopRemoteCalibration(command, true);
    } else if (command.type == REMOTE_CAL_CMD_ABORT) {
      StopRemoteCalibration(command, false);
    } else {
      SendRemoteError(command, REMOTE_CAL_ERR_UNKNOWN_COMMAND);
    }
  }

  if (g_mode == MODE_MAG_CALIBRATING) {
    if (g_remoteCalibration) LoopRemoteMagSamples();
    else LoopWiredMagSamples();
  } else {
    LoopNormal();
  }
  DrainUsbFrame();
}

