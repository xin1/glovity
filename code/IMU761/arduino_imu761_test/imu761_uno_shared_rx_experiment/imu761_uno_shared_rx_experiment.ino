/*
 * EXPERIMENT ONLY: two continuously transmitting IMU761 units share one RX.
 * The sketch does not prevent bus collisions. It extracts CRC-valid HI91
 * frames from the mixed byte stream and classifies address 0x01 / 0x02.
 *
 * UNO D10 = shared software UART RX, 9600 baud
 * UNO D11 = unused software UART TX; leave it disconnected
 * USB Serial = 115200 baud
 */
#include <Arduino.h>
#include <SoftwareSerial.h>
#include "hi91.h"

#if !defined(__AVR_ATmega328P__) && !defined(IMU761_HOST_TEST)
#error "This sketch targets classic UNO R3 / ATmega328P."
#endif

const uint8_t IMU_RX_PIN = 10;
const uint8_t UNUSED_TX_PIN = 11;
const uint32_t IMU_BAUD = 9600;
const uint8_t ADDRESS[2] = {0x01, 0x02};

SoftwareSerial imuSerial(IMU_RX_PIN, UNUSED_TX_PIN);
Hi91Parser parser;
Hi91Data latest[2] = {};
uint32_t frames[2] = {0, 0};
uint32_t lastFrameAt[2] = {0, 0};
uint32_t totalBytes = 0;
uint32_t unknownAddress = 0;
uint32_t overflowCount = 0;
uint32_t lastPrintAt = 0;

int8_t addressIndex(uint8_t address) {
    if (address == ADDRESS[0]) return 0;
    if (address == ADDRESS[1]) return 1;
    return -1;
}

void printOne(uint8_t i) {
    Serial.print(F("ID=0"));
    Serial.print(ADDRESS[i], HEX);
    Serial.print(F(" frames="));
    Serial.print(frames[i]);
    if (frames[i]) {
        Serial.print(F(" age_ms="));
        Serial.print(millis() - lastFrameAt[i]);
        Serial.print(F(" imu_ms="));
        Serial.print(latest[i].timeMs);
        Serial.print(F(" roll="));
        Serial.print(latest[i].roll, 2);
        Serial.print(F(" pitch="));
        Serial.print(latest[i].pitch, 2);
        Serial.print(F(" yaw="));
        Serial.print(latest[i].yaw, 2);
    }
    Serial.println();
}

void setup() {
    Serial.begin(9600);
    imuSerial.begin(IMU_BAUD);

    // SoftwareSerial normally enables UNO's internal 5 V pull-up on RX.
    // Disable it. Use the external bus bias required by the actual wiring.
    digitalWrite(IMU_RX_PIN, LOW);
    imuSerial.listen();

    Serial.println(F("IMU761 shared-RX collision experiment"));
    Serial.println(F("sensor=9600, USB=115200, IDs=01/02"));
    Serial.println(F("Only CRC-valid frames are counted."));
}

void loop() {
    if (imuSerial.overflow()) ++overflowCount;

    // Always drain the software UART before printing.
    while (imuSerial.available()) {
        const uint8_t byte = (uint8_t)imuSerial.read();
        ++totalBytes;

        Hi91Data data;
        if (!parser.feed(byte, data)) continue;

        const int8_t index = addressIndex(data.address);
        if (index < 0) {
            ++unknownAddress;
            continue;
        }

        latest[index] = data;
        ++frames[index];
        lastFrameAt[index] = millis();
    }

    const uint32_t now = millis();
    if ((uint32_t)(now - lastPrintAt) >= 1000) {
        lastPrintAt = now;
        Serial.print(F("bytes="));
        Serial.print(totalBytes);
        Serial.print(F(" crc="));
        Serial.print(parser.crcErrors);
        Serial.print(F(" discard="));
        Serial.print(parser.discarded);
        Serial.print(F(" overflow="));
        Serial.print(overflowCount);
        Serial.print(F(" unknown_addr="));
        Serial.println(unknownAddress);
        printOne(0);
        printOne(1);
    }
}
