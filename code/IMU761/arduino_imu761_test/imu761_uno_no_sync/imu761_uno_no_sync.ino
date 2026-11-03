/*
 * Arduino UNO R3 + two IMU761, no SYNC_IN and no shared RX bus.
 * IMU 01 TX -> D8, IMU 02 TX -> D10. Both IMUs: 9600, HI91 ONTIME 0.5.
 * D9/D11 are unused SoftwareSerial TX pins and must NOT be connected.
 */
#include <Arduino.h>
#include <SoftwareSerial.h>
#include "hi91.h"

#if !defined(__AVR_ATmega328P__) && !defined(IMU761_HOST_TEST)
#error "This sketch targets the classic UNO R3 / ATmega328P."
#endif

const uint8_t RX_PIN[2] = {8, 10};
const uint8_t UNUSED_TX_PIN[2] = {9, 11};
const uint8_t EXPECTED_ADDRESS[2] = {0x01, 0x02};
const uint32_t IMU_BAUD = 9600;
const uint32_t LISTEN_WINDOW_MS = 1500;

SoftwareSerial imu01(RX_PIN[0], UNUSED_TX_PIN[0]);
SoftwareSerial imu02(RX_PIN[1], UNUSED_TX_PIN[1]);
SoftwareSerial *ports[2] = {&imu01, &imu02};
Hi91Parser parsers[2];
Hi91Data latest[2] = {};
uint32_t good[2] = {0, 0};
uint32_t wrongAddress[2] = {0, 0};
uint32_t overflowCount[2] = {0, 0};
uint32_t bytesSeen[2] = {0, 0};
uint32_t lastFrameAt[2] = {0, 0};
uint32_t windowStartedAt = 0;
uint32_t lastPrintAt = 0;
uint8_t active = 0;

void selectPort(uint8_t index) {
    active = index;
    parsers[index].reset();
    ports[index]->listen();
    windowStartedAt = millis();
}

void printOne(uint8_t i) {
    Serial.print(F("ID=0")); Serial.print(EXPECTED_ADDRESS[i], HEX);
    Serial.print(F(" bytes=")); Serial.print(bytesSeen[i]);
    Serial.print(F(" frames=")); Serial.print(good[i]);
    Serial.print(F(" crc=")); Serial.print(parsers[i].crcErrors);
    Serial.print(F(" discard=")); Serial.print(parsers[i].discarded);
    Serial.print(F(" overflow=")); Serial.print(overflowCount[i]);
    Serial.print(F(" wrong_addr=")); Serial.print(wrongAddress[i]);
    if (good[i]) {
        Serial.print(F(" age_ms=")); Serial.print(millis() - lastFrameAt[i]);
        Serial.print(F(" roll=")); Serial.print(latest[i].roll, 2);
        Serial.print(F(" pitch=")); Serial.print(latest[i].pitch, 2);
        Serial.print(F(" yaw=")); Serial.print(latest[i].yaw, 2);
    }
    Serial.println();
}

void setup() {
    Serial.begin(115200);
    imu01.begin(IMU_BAUD);
    imu02.begin(IMU_BAUD);

    // SoftwareSerial enables 5 V pull-ups on RX by default. Disable them.
    digitalWrite(RX_PIN[0], LOW);
    digitalWrite(RX_PIN[1], LOW);

    selectPort(0);
    Serial.println(F("IMU761 UNO no-SYNC test"));
    Serial.println(F("IMU01 TX->D8, IMU02 TX->D10; separate wires, no diodes"));
    Serial.println(F("Both IMUs must be 9600 and HI91 ONTIME 0.5"));
}

void loop() {
    SoftwareSerial &port = *ports[active];
    if (port.overflow()) ++overflowCount[active];

    while (port.available()) {
        const uint8_t byte = (uint8_t)port.read();
        ++bytesSeen[active];
        Hi91Data data;
        if (!parsers[active].feed(byte, data)) continue;
        if (data.address != EXPECTED_ADDRESS[active]) {
            ++wrongAddress[active];
            continue;
        }
        latest[active] = data;
        ++good[active];
        lastFrameAt[active] = millis();
    }

    const uint32_t now = millis();
    if ((uint32_t)(now - windowStartedAt) >= LISTEN_WINDOW_MS)
        selectPort(active ^ 1);

    // Print after draining the active receiver. Keep the output compact.
    if ((uint32_t)(now - lastPrintAt) >= 1000) {
        lastPrintAt = now;
        Serial.print(F("listening=0")); Serial.println(EXPECTED_ADDRESS[active], HEX);
        printOne(0);
        printOne(1);
    }
}
