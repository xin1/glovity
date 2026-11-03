#include <Arduino.h>
#include <Wire.h>
#if CONFIG_IDF_TARGET_ESP32C6
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#endif

// ESP32-C6 exposes the programming port through its hardware USB-Serial/JTAG
// controller. When Arduino's "USB CDC On Boot" menu is disabled, `Serial`
// silently points at UART0 instead. Use HWCDC explicitly so the Web Serial
// protocol always stays on the same COM port that was used for flashing.
#if CONFIG_IDF_TARGET_ESP32C6 && !ARDUINO_USB_CDC_ON_BOOT
#include "HWCDC.h"
static HWCDC HapticUSBSerial;
#define HOST_SERIAL HapticUSBSerial
#else
#define HOST_SERIAL Serial
#endif

// Board variants. The delivered C6 test board has eight TCA branches and its
// four populated motors are CH4..CH7. The original S3 design keeps five
// one-finger branches.
#if CONFIG_IDF_TARGET_ESP32C6
static constexpr uint8_t PIN_HP_SDA = 6;
static constexpr uint8_t PIN_HP_SCL = 7;
static constexpr uint8_t PIN_TCA_RESET = 23;
static constexpr uint8_t CHANNEL_COUNT = 8;
static constexpr uint8_t DEFAULT_OUTPUT_MASK = 0xF0; // CH4, CH5, CH6, CH7
static constexpr const char *BOARD_EVENT = "EVENT BOARD AW86234_C6_8CH 8";
#else
static constexpr uint8_t PIN_HP_SDA = 6;
static constexpr uint8_t PIN_HP_SCL = 7;
static constexpr uint8_t PIN_TCA_RESET = 21;
static constexpr uint8_t PIN_VIB_CTRL = 4; // PMOS gate: LOW enables haptic rail.
static constexpr uint8_t CHANNEL_COUNT = 5;
static constexpr uint8_t DEFAULT_OUTPUT_MASK = 0x00; // selected finger only
static constexpr const char *BOARD_EVENT = "EVENT BOARD TELEX_CORE_LEFT_V2 5";
#endif
static constexpr uint8_t TCA_ADDRESS = 0x70;
static constexpr uint8_t AW_ADDRESS = 0x5A;

static constexpr uint8_t REG_CHIPID_H = 0x57;
static constexpr uint8_t REG_CHIPID_L = 0x69;
static constexpr uint8_t REG_PLAYCFG3 = 0x08;
static constexpr uint8_t REG_PLAYCFG4 = 0x09;
static constexpr uint8_t REG_CONTCFG1 = 0x17;
static constexpr uint8_t REG_F_PRE = 0x18;
static constexpr uint8_t REG_DRV_WIDTH = 0x19;
static constexpr uint8_t REG_WAIT_NUM = 0x1A;
static constexpr uint8_t REG_DRV1_LVL = 0x1C;
static constexpr uint8_t REG_DRV2_LVL = 0x1D;
static constexpr uint8_t REG_DRV1_TIME = 0x1E;
static constexpr uint8_t REG_DRV2_TIME = 0x1F;
static constexpr uint8_t REG_BRK_TIME = 0x20;
static constexpr uint8_t REG_SYSCTRL1 = 0x45;

static uint8_t selectedChannel = 0;
static bool channelPresent[CHANNEL_COUNT] = {};
static bool outputActive = false;
static unsigned long stopAtMs = 0;
static unsigned long lastHostMs = 0;
static String inputLine;
static String timedEffect;
static uint8_t activeMask = 0;
static uint16_t activeFrequency = 0;
static uint8_t activeAmp = 0;
static uint8_t channelAmps[CHANNEL_COUNT] = {};
static uint8_t activeSda = PIN_HP_SDA;
static uint8_t activeScl = PIN_HP_SCL;

#if CONFIG_IDF_TARGET_ESP32C6
static constexpr const char *BLE_SERVICE_UUID = "8d53dc1d-1db7-4cd3-868b-8a527460aa84";
static constexpr const char *BLE_COMMAND_UUID = "8d53dc1e-1db7-4cd3-868b-8a527460aa84";
static portMUX_TYPE bleQueueMux = portMUX_INITIALIZER_UNLOCKED;
static char blePendingCommand[128] = {};
static bool bleCommandReady = false;

class HapticBleCommandCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String value = characteristic->getValue();
    value.trim();
    if (!value.length() || value.length() >= sizeof(blePendingCommand)) return;
    portENTER_CRITICAL(&bleQueueMux);
    memcpy(blePendingCommand, value.c_str(), value.length() + 1);
    bleCommandReady = true;
    portEXIT_CRITICAL(&bleQueueMux);
  }
};

static void beginBleHapticServer() {
  if (!BLEDevice::init("TELEX-HAPTIC-L")) {
    HOST_SERIAL.println("EVENT BLE INIT_FAILED");
    return;
  }
  BLEServer *server = BLEDevice::createServer();
  server->advertiseOnDisconnect(true);
  BLEService *service = server->createService(BLE_SERVICE_UUID);
  BLECharacteristic *command = service->createCharacteristic(
    BLE_COMMAND_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  command->setCallbacks(new HapticBleCommandCallbacks());
  service->start();
  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
  HOST_SERIAL.println("EVENT BLE ADVERTISING TELEX-HAPTIC-L");
}
#endif

static uint8_t presentMask() {
  uint8_t mask = 0;
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    if (channelPresent[channel]) mask |= uint8_t(1U << channel);
  }
  return mask;
}

static uint8_t requestedOutputMask() {
  return DEFAULT_OUTPUT_MASK ? DEFAULT_OUTPUT_MASK : uint8_t(1U << selectedChannel);
}

static bool selectMux(uint8_t channel) {
  if (channel >= CHANNEL_COUNT) return false;
  Wire.beginTransmission(TCA_ADDRESS);
  Wire.write(uint8_t(1U << channel));
  return Wire.endTransmission() == 0;
}

static bool beginHapticBus() {
  Wire.begin(PIN_HP_SDA, PIN_HP_SCL, 400000);
  activeSda = PIN_HP_SDA;
  activeScl = PIN_HP_SCL;
  delay(2);
  if (selectMux(0)) return true;
#if CONFIG_IDF_TARGET_ESP32C6
  // The C6 test board uses a jumper to route the TCA upstream bus. Some
  // revisions are wired to HP GPIO3/2 instead of LP GPIO6/7.
  Wire.end();
  Wire.begin(3, 2, 400000);
  activeSda = 3;
  activeScl = 2;
  delay(2);
  return selectMux(0);
#else
  return false;
#endif
}

static bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(AW_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static bool readRegister(uint8_t reg, uint8_t &value) {
  Wire.beginTransmission(AW_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(AW_ADDRESS, uint8_t(1)) != 1) return false;
  value = Wire.read();
  return true;
}

static bool updateRegister(uint8_t reg, uint8_t mask, uint8_t value) {
  uint8_t current = 0;
  return readRegister(reg, current) && writeRegister(reg, (current & ~mask) | (value & mask));
}

static uint16_t clampFrequency(int value) { return constrain(value, 50, 300); }
static uint8_t clampAmp(int value) { return constrain(value, 25, 47); }

static uint8_t frequencyCode(uint16_t hz) {
  // AW86234: F0 = 24000 / F_PRE.
  return constrain((24000U + hz / 2U) / hz, 1U, 255U);
}

static uint8_t driveLevel(uint8_t amp) {
  // The verified 1.0.7 test-bench used level 18..40 for wearable feedback.
  // Keep that conservative physical range behind the legacy UI's AMP 25..47.
  return map(clampAmp(amp), 25, 47, 18, 40);
}

static bool configureOutput(uint8_t channel, uint16_t frequency) {
  if (!channelPresent[channel] || !selectMux(channel)) return false;
  const uint8_t fCode = frequencyCode(frequency);
  return writeRegister(REG_PLAYCFG4, 0x02) && // stop immediately
         writeRegister(REG_PLAYCFG3, 0x0A) && // immediate stop + CONT, brake off
         writeRegister(REG_CONTCFG1, 0x10) && // cosine, F0 detection off
         writeRegister(REG_F_PRE, fCode) &&
         writeRegister(REG_DRV_WIDTH, 80) &&  // verified fixed-width drive (~160 Hz)
         writeRegister(REG_WAIT_NUM, 0) &&
         writeRegister(REG_DRV1_LVL, 0) &&    // silent preparation; GO follows level write
         writeRegister(REG_DRV2_LVL, 0) &&
         writeRegister(REG_DRV1_TIME, 255) &&
         writeRegister(REG_DRV2_TIME, 0) &&
         writeRegister(REG_BRK_TIME, 0) &&
         updateRegister(REG_SYSCTRL1, 0x02, 0x02); // keep EN_FIR enabled
}

static bool beginBrake(uint8_t channel) {
  if (!channelPresent[channel] || !selectMux(channel)) return false;
  return writeRegister(REG_BRK_TIME, 4) &&
         updateRegister(REG_PLAYCFG3, 0x04, 0x04) &&
         updateRegister(REG_DRV1_LVL, 0x7F, 0) &&
         writeRegister(REG_PLAYCFG4, 0x02);
}

static bool finishBrake(uint8_t channel) {
  if (!channelPresent[channel] || !selectMux(channel)) return false;
  return updateRegister(REG_PLAYCFG3, 0x04, 0) &&
         writeRegister(REG_BRK_TIME, 0) &&
         writeRegister(REG_PLAYCFG4, 0x02);
}

static void stopAll(bool report = true) {
  const uint8_t brakingMask = outputActive ? activeMask : 0;
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    if (!channelPresent[channel]) continue;
    if (brakingMask & (1U << channel)) beginBrake(channel);
    else if (selectMux(channel)) writeRegister(REG_PLAYCFG4, 0x02);
  }
  if (brakingMask) {
    delay(16); // one shared brake window; do not serialize four 16 ms waits
    for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
      if (brakingMask & (1U << channel)) finishBrake(channel);
    }
  }
  outputActive = false;
  activeMask = 0;
  activeFrequency = 0;
  activeAmp = 0;
  memset(channelAmps, 0, sizeof(channelAmps));
  stopAtMs = 0;
  timedEffect = "";
  if (report) HOST_SERIAL.println("EVENT STOP");
}

#if CONFIG_IDF_TARGET_ESP32C6
// Independently drive the four populated C6 outputs: index/middle/ring/little
// map to TCA CH4/CH5/CH6/CH7. A value of zero releases that motor.
static bool applyFourAmps(const uint8_t amps[4], uint16_t frequency) {
  const uint8_t requiredMask = 0xF0;
  if ((presentMask() & requiredMask) != requiredMask) {
    HOST_SERIAL.printf("EVENT ERROR FOUR_CHANNELS_UNAVAILABLE %02X\n", presentMask());
    return false;
  }

  uint8_t stoppingMask = 0;
  for (uint8_t i = 0; i < 4; ++i) {
    const uint8_t channel = i + 4;
    if (!amps[i] && channelAmps[channel]) {
      beginBrake(channel);
      stoppingMask |= uint8_t(1U << channel);
    }
  }
  if (stoppingMask) {
    delay(16);
    for (uint8_t channel = 4; channel < 8; ++channel) {
      if (stoppingMask & (1U << channel)) finishBrake(channel);
    }
  }

  uint8_t nextMask = 0;
  for (uint8_t i = 0; i < 4; ++i) {
    const uint8_t channel = i + 4;
    const uint8_t amp = amps[i];
    if (!amp) { channelAmps[channel] = 0; continue; }
    const bool wasOff = channelAmps[channel] == 0;
    if ((wasOff && !configureOutput(channel, frequency)) ||
        (!selectMux(channel)) ||
        !updateRegister(REG_DRV1_LVL, 0x7F, driveLevel(amp)) ||
        (wasOff && !writeRegister(REG_PLAYCFG4, 0x01))) {
      stopAll(false);
      HOST_SERIAL.printf("EVENT ERROR CHANNEL_%u_UPDATE_FAILED\n", channel);
      return false;
    }
    channelAmps[channel] = amp;
    nextMask |= uint8_t(1U << channel);
  }
  activeMask = nextMask;
  outputActive = nextMask != 0;
  activeFrequency = frequency;
  stopAtMs = 0;
  lastHostMs = millis();
  HOST_SERIAL.printf("EVENT FOUR %u %u %u %u\n", amps[0], amps[1], amps[2], amps[3]);
  return true;
}
#endif

static bool startOutput(uint16_t frequency, uint8_t amp, unsigned long durationMs = 0) {
  frequency = clampFrequency(frequency);
  amp = clampAmp(amp);
  const uint8_t level = driveLevel(amp);
  const uint8_t targetMask = requestedOutputMask();

  if ((presentMask() & targetMask) != targetMask) {
    HOST_SERIAL.printf("EVENT ERROR OUTPUT_MASK_UNAVAILABLE %02X %02X\n", targetMask, presentMask());
    return false;
  }

  // During sustained contact, change only the drive level (and F_PRE when
  // requested). Do not STOP/GO on every pressure sample.
  if (outputActive && activeMask == targetMask) {
    for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
      if (!(targetMask & (1U << channel))) continue;
      if (!selectMux(channel) ||
          (frequency != activeFrequency && !writeRegister(REG_F_PRE, frequencyCode(frequency))) ||
          !updateRegister(REG_DRV1_LVL, 0x7F, level)) {
        stopAll(false);
        HOST_SERIAL.printf("EVENT ERROR CHANNEL_%u_UPDATE_FAILED\n", channel);
        return false;
      }
      channelAmps[channel] = amp;
    }
    activeFrequency = frequency;
    activeAmp = amp;
    stopAtMs = durationMs ? millis() + durationMs : 0;
    lastHostMs = millis();
    HOST_SERIAL.printf("EVENT OUTPUT %u %u\n", frequency, amp);
    return true;
  }

  if (outputActive) stopAll(false);
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    if (!(targetMask & (1U << channel))) continue;
    if (!configureOutput(channel, frequency) ||
        !updateRegister(REG_DRV1_LVL, 0x7F, level)) {
      stopAll(false);
      HOST_SERIAL.printf("EVENT ERROR CHANNEL_%u_UNAVAILABLE\n", channel);
      return false;
    }
  }
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    if (!(targetMask & (1U << channel))) continue;
    if (!selectMux(channel) || !writeRegister(REG_PLAYCFG4, 0x01)) {
      stopAll(false);
      HOST_SERIAL.printf("EVENT ERROR CHANNEL_%u_GO_FAILED\n", channel);
      return false;
    }
    channelAmps[channel] = amp;
  }
  outputActive = true;
  activeMask = targetMask;
  activeFrequency = frequency;
  activeAmp = amp;
  stopAtMs = durationMs ? millis() + durationMs : 0;
  lastHostMs = millis();
  HOST_SERIAL.printf("EVENT START %u %u MASK %02X\n", frequency, amp, targetMask);
  return true;
}

static bool parseTwo(const String &text, int &a, int &b) {
  return sscanf(text.c_str(), "%d %d", &a, &b) == 2;
}

static void processCommand(String line) {
  line.trim();
  if (!line.length()) return;
  lastHostMs = millis();

  if (line == "HELLO" || line == "INFO") {
    HOST_SERIAL.println(BOARD_EVENT);
    HOST_SERIAL.printf("EVENT OUTPUT_MASK %02X\n", requestedOutputMask());
    HOST_SERIAL.println("EVENT READY");
    return;
  }
  if (line == "STOP" || line == "LONG_RELEASE") { stopAll(); return; }
  if (line == "DETECT_F0" || line == "F0_DET") {
    HOST_SERIAL.println("EVENT ERROR F0_UNSUPPORTED_TELEX");
    return;
  }
  if (line.startsWith("CHANNEL ")) {
#if CONFIG_IDF_TARGET_ESP32C6
    const int channel = line.substring(8).toInt();
    if (channel < 4 || channel > 7 || !channelPresent[channel]) {
      HOST_SERIAL.printf("EVENT ERROR CHANNEL_%d_UNAVAILABLE\n", channel);
      return;
    }
    if (outputActive) stopAll(false);
    selectedChannel = channel;
    HOST_SERIAL.printf("EVENT CHANNEL %u\n", selectedChannel);
    return;
#else
    const int channel = line.substring(8).toInt();
    if (channel < 0 || channel >= CHANNEL_COUNT || !channelPresent[channel]) {
      HOST_SERIAL.printf("EVENT ERROR CHANNEL_%d_UNAVAILABLE\n", channel);
      return;
    }
    if (outputActive) stopAll(false);
    selectedChannel = channel;
    HOST_SERIAL.printf("EVENT CHANNEL %u\n", selectedChannel);
    return;
#endif
  }
  if (line.startsWith("FOUR ")) {
#if CONFIG_IDF_TARGET_ESP32C6
    int a = 0, b = 0, c = 0, d = 0, hz = 0;
    if (sscanf(line.c_str() + 5, "%d %d %d %d %d", &a, &b, &c, &d, &hz) == 5) {
      const int raw[4] = {a, b, c, d};
      uint8_t amps[4] = {};
      for (uint8_t i = 0; i < 4; ++i) amps[i] = raw[i] <= 0 ? 0 : clampAmp(raw[i]);
      applyFourAmps(amps, clampFrequency(hz));
    } else HOST_SERIAL.println("EVENT ERROR FOUR_FORMAT");
#else
    HOST_SERIAL.println("EVENT ERROR FOUR_UNSUPPORTED");
#endif
    return;
  }
  if (line.startsWith("PREP ") || line.startsWith("PARAM ")) {
    const bool parameter = line.startsWith("PARAM ");
    int frequency, amp;
    if (!parseTwo(line.substring(parameter ? 6 : 5), frequency, amp)) {
      HOST_SERIAL.println("EVENT ERROR PARAM_FORMAT"); return;
    }
    frequency = clampFrequency(frequency); amp = clampAmp(amp);
    if ((presentMask() & requestedOutputMask()) != requestedOutputMask()) {
      HOST_SERIAL.println("EVENT ERROR PARAM_APPLY"); return;
    }
    HOST_SERIAL.printf(parameter ? "EVENT PARAM %d %d\n" : "EVENT PREP %d %d\n", frequency, amp);
    return;
  }

  int frequency = 0, amp = 0, duration = 0, force = 0, minHz = 0, maxHz = 0;
  if (line.startsWith("START ") || line.startsWith("HOLD ")) {
    const int offset = line.startsWith("START ") ? 6 : 5; // legacy order: AMP FREQ
    if (parseTwo(line.substring(offset), amp, frequency)) startOutput(frequency, amp);
    else HOST_SERIAL.println("EVENT ERROR START_FORMAT");
    return;
  }
  if (line.startsWith("LONG_START ") || line.startsWith("LONG_HOLD ")) {
    const int offset = line.startsWith("LONG_START ") ? 11 : 10;
    if (parseTwo(line.substring(offset), frequency, amp)) startOutput(frequency, amp);
    else HOST_SERIAL.println("EVENT ERROR LONG_FORMAT");
    return;
  }
  if (line.startsWith("PULSE ")) {
    if (sscanf(line.c_str() + 6, "%d %d %d", &frequency, &amp, &duration) == 3) {
      if (startOutput(frequency, amp, constrain(duration, 10, 5000))) {
        timedEffect = "SHORT"; HOST_SERIAL.println("EVENT EFFECT SHORT START");
      }
    } else HOST_SERIAL.println("EVENT ERROR PULSE_FORMAT");
    return;
  }
  if (line.startsWith("SAW_PLAY ")) {
    if (parseTwo(line.substring(9), frequency, amp)) {
      if (startOutput(frequency, amp, 1200)) {
        timedEffect = "SAW"; HOST_SERIAL.println("EVENT EFFECT SAW START");
      }
    } else HOST_SERIAL.println("EVENT ERROR SAW_FORMAT");
    return;
  }
  if (line.startsWith("COLLISION ")) {
    if (sscanf(line.c_str() + 10, "%d %d %d", &force, &frequency, &amp) >= 1) {
      if (force >= 60) {
        if (!frequency) frequency = 170; if (!amp) amp = 43;
        if (startOutput(frequency, amp, 28)) {
          timedEffect = "COLLISION"; HOST_SERIAL.println("EVENT EFFECT COLLISION START");
        }
      } else HOST_SERIAL.println("EVENT COLLISION BLOCKED BELOW_6N");
    } else HOST_SERIAL.println("EVENT ERROR COLLISION_FORMAT");
    return;
  }
  if (line.startsWith("FRICTION_START ") || line.startsWith("FRICTION_HOLD ")) {
    const int offset = line.startsWith("FRICTION_START ") ? 15 : 14;
    if (sscanf(line.c_str() + offset, "%d %d %d %d", &force, &minHz, &maxHz, &amp) == 4) {
      force = constrain(force, 0, 100);
      frequency = minHz + (maxHz - minHz) * force / 100;
      amp = 25 + (clampAmp(amp) - 25) * force / 100;
      startOutput(frequency, amp);
    } else HOST_SERIAL.println("EVENT ERROR FRICTION_FORMAT");
    return;
  }
  HOST_SERIAL.print("EVENT ERROR UNKNOWN_COMMAND "); HOST_SERIAL.println(line);
}

static void discoverDrivers() {
#if !CONFIG_IDF_TARGET_ESP32C6
  digitalWrite(PIN_VIB_CTRL, LOW);
  delay(5);
#endif
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    uint8_t high = 0, low = 0;
    channelPresent[channel] = selectMux(channel) &&
      readRegister(REG_CHIPID_H, high) && readRegister(REG_CHIPID_L, low) &&
      high == 0x23 && low == 0x40;
    HOST_SERIAL.printf("EVENT DRIVER %u %s", channel, channelPresent[channel] ? "OK" : "MISSING");
    if (channelPresent[channel]) HOST_SERIAL.printf(" %02X%02X", high, low);
    HOST_SERIAL.println();
  }
}

void setup() {
#if !CONFIG_IDF_TARGET_ESP32C6
  pinMode(PIN_VIB_CTRL, OUTPUT);
  digitalWrite(PIN_VIB_CTRL, HIGH);
#endif
  pinMode(PIN_TCA_RESET, OUTPUT);
  digitalWrite(PIN_TCA_RESET, LOW);
  HOST_SERIAL.begin(115200);
  // The mux must be out of reset before probing its upstream I2C pins.
  delay(2);
  digitalWrite(PIN_TCA_RESET, HIGH);
  delay(5);
  const bool tcaOnline = beginHapticBus();
  delay(250);
  HOST_SERIAL.println(BOARD_EVENT);
  HOST_SERIAL.printf("EVENT I2C SDA %u SCL %u TCA %s\n", activeSda, activeScl,
                     tcaOnline ? "OK" : "MISSING");
  discoverDrivers();
  HOST_SERIAL.printf("EVENT OUTPUT_MASK %02X\n", requestedOutputMask());
#if CONFIG_IDF_TARGET_ESP32C6
  beginBleHapticServer();
#endif
  HOST_SERIAL.println("EVENT READY");
}

void loop() {
#if CONFIG_IDF_TARGET_ESP32C6
  if (bleCommandReady) {
    char command[sizeof(blePendingCommand)] = {};
    portENTER_CRITICAL(&bleQueueMux);
    memcpy(command, blePendingCommand, sizeof(command));
    bleCommandReady = false;
    portEXIT_CRITICAL(&bleQueueMux);
    processCommand(String(command));
  }
#endif
  while (HOST_SERIAL.available()) {
    const char c = char(HOST_SERIAL.read());
    if (c == '\n') { processCommand(inputLine); inputLine = ""; }
    else if (c != '\r' && inputLine.length() < 120) inputLine += c;
  }
  if (outputActive && stopAtMs && long(millis() - stopAtMs) >= 0) {
    const String completedEffect = timedEffect;
    stopAll(false);
    if (completedEffect.length()) HOST_SERIAL.printf("EVENT EFFECT %s DONE\n", completedEffect.c_str());
  }
  if (outputActive && millis() - lastHostMs > 1500) {
    stopAll(false);
    HOST_SERIAL.println("EVENT STOP WATCHDOG");
  }
}
