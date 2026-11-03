#include <Arduino.h>
#include <BLEDevice.h>

#if CONFIG_IDF_TARGET_ESP32C6 && !ARDUINO_USB_CDC_ON_BOOT
#include "HWCDC.h"
static HWCDC ReceiverUSBSerial;
#define HOST_SERIAL ReceiverUSBSerial
#else
#define HOST_SERIAL Serial
#endif

static BLEUUID serviceUuid("8d53dc1d-1db7-4cd3-868b-8a527460aa84");
static BLEUUID commandUuid("8d53dc1e-1db7-4cd3-868b-8a527460aa84");
static BLEAdvertisedDevice *candidate = nullptr;
static BLEClient *client = nullptr;
static BLERemoteCharacteristic *commandCharacteristic = nullptr;
static bool connected = false;
static bool connectRequested = false;
static bool scanRunning = false;
static unsigned long nextScanMs = 0;
static String inputLine;

static void reportReady() {
  HOST_SERIAL.println("EVENT BOARD AW86234_C6_8CH 8");
  HOST_SERIAL.println("EVENT OUTPUT_MASK F0");
  HOST_SERIAL.println("EVENT BLE CONNECTED TELEX-HAPTIC-L");
  HOST_SERIAL.println("EVENT READY");
}

class ReceiverClientCallbacks : public BLEClientCallbacks {
  void onConnect(BLEClient *) override {}
  void onDisconnect(BLEClient *) override {
    connected = false;
    commandCharacteristic = nullptr;
    nextScanMs = millis() + 300;
    HOST_SERIAL.println("EVENT BLE DISCONNECTED");
  }
};

class HapticAdvertisementCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice device) override {
    if (!device.haveServiceUUID() || !device.isAdvertisingService(serviceUuid)) return;
    BLEDevice::getScan()->stop();
    delete candidate;
    candidate = new BLEAdvertisedDevice(device);
    connectRequested = true;
    scanRunning = false;
  }
};

static void startScan() {
  BLEScan *scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new HapticAdvertisementCallbacks(), true);
  scan->setInterval(160);
  scan->setWindow(120);
  scan->setActiveScan(true);
  scanRunning = true;
  scan->start(3, false);
  HOST_SERIAL.println("EVENT BLE SCANNING LEFT");
}

static bool connectHaptic() {
  if (!candidate) return false;
  if (!client) {
    client = BLEDevice::createClient();
    client->setClientCallbacks(new ReceiverClientCallbacks());
  }
  if (!client->connect(candidate)) return false;
  client->setMTU(185);
  BLERemoteService *service = client->getService(serviceUuid);
  if (!service) { client->disconnect(); return false; }
  commandCharacteristic = service->getCharacteristic(commandUuid);
  if (!commandCharacteristic || !commandCharacteristic->canWrite()) {
    client->disconnect();
    return false;
  }
  connected = true;
  reportReady();
  return true;
}

static void acknowledgeCommand(const String &line) {
  int a = 0, b = 0;
  if (line == "HELLO" || line == "INFO") { reportReady(); return; }
  if (line == "STOP" || line == "LONG_RELEASE") { HOST_SERIAL.println("EVENT STOP"); return; }
  if (line.startsWith("PREP ") && sscanf(line.c_str() + 5, "%d %d", &a, &b) == 2) {
    HOST_SERIAL.printf("EVENT PREP %d %d\n", a, b); return;
  }
  if (line.startsWith("PARAM ") && sscanf(line.c_str() + 6, "%d %d", &a, &b) == 2) {
    HOST_SERIAL.printf("EVENT PARAM %d %d\n", a, b); return;
  }
  if (line.startsWith("START ") && sscanf(line.c_str() + 6, "%d %d", &a, &b) == 2) {
    HOST_SERIAL.printf("EVENT START %d %d MASK F0\n", b, a); return;
  }
  if (line.startsWith("HOLD ") && sscanf(line.c_str() + 5, "%d %d", &a, &b) == 2) {
    HOST_SERIAL.printf("EVENT OUTPUT %d %d\n", b, a); return;
  }
  if (line.startsWith("FOUR ")) { HOST_SERIAL.print("EVENT "); HOST_SERIAL.println(line); }
}

static void forwardCommand(String line) {
  line.trim();
  if (!line.length()) return;
  if (!connected || !commandCharacteristic) {
    if (line == "HELLO" || line == "INFO") HOST_SERIAL.println("EVENT WAIT BLE_LEFT");
    else HOST_SERIAL.println("EVENT ERROR BLE_NOT_CONNECTED");
    return;
  }
  if (!commandCharacteristic->writeValue((uint8_t *)line.c_str(), line.length(), false)) {
    HOST_SERIAL.println("EVENT ERROR BLE_WRITE_FAILED");
    return;
  }
  acknowledgeCommand(line);
}

void setup() {
  HOST_SERIAL.begin(115200);
  delay(250);
  HOST_SERIAL.println("EVENT RECEIVER TELEX_C6MINI VER_2 LEFT");
  if (!BLEDevice::init("TELEX-RECEIVER-L")) {
    HOST_SERIAL.println("EVENT ERROR BLE_INIT_FAILED");
    return;
  }
  nextScanMs = millis();
}

void loop() {
  while (HOST_SERIAL.available()) {
    const char c = char(HOST_SERIAL.read());
    if (c == '\n') { forwardCommand(inputLine); inputLine = ""; }
    else if (c != '\r' && inputLine.length() < 120) inputLine += c;
  }
  if (connectRequested) {
    connectRequested = false;
    if (!connectHaptic()) {
      HOST_SERIAL.println("EVENT BLE CONNECT_FAILED");
      nextScanMs = millis() + 500;
    }
  }
  if (!connected && !scanRunning && long(millis() - nextScanMs) >= 0) startScan();
  if (scanRunning && !BLEDevice::getScan()->isScanning()) {
    scanRunning = false;
    nextScanMs = millis() + 300;
  }
  delay(2);
}
