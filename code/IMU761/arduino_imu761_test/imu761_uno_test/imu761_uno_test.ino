/*
 * Arduino UNO R3 (ATmega328P), IMU761 Wearable/new HI91.
 * Sensor UART: SoftwareSerial D10 RX / D11 TX, 9600 baud.
 * USB monitor: 115200 baud. No sensor commands are sent by this sketch.
 * IMPORTANT: read ../README.md before connecting power.
 */
#include <Arduino.h>
#include <SoftwareSerial.h>
#include "hi91.h"

#if !defined(__AVR_ATmega328P__) && !defined(IMU761_HOST_TEST)
#error "This sketch targets UNO R3 / ATmega328P; adapt it for other UNO variants."
#endif

// 1 = two IMUs in ONMARK mode, separate SYNC_IN lines REQUIRED.
// 0 = passive diagnosis; connect ONLY ONE IMU in ONTIME 0.5 mode.
#ifndef TEST_MODE
#define TEST_MODE 1
#endif

const uint8_t RX_PIN = 10;
const uint8_t TX_PIN = 11;
const uint8_t SYNC_PIN[2] = {6, 7};
const uint8_t ADDRESS[2] = {0x01, 0x02};
const uint32_t IMU_BAUD = 9600;
const uint32_t REPLY_TIMEOUT_MS = 250;
const uint32_t SLOT_MS = 250;  // ~2 Hz per IMU, 82 bytes need ~85.4 ms.

SoftwareSerial imuSerial(RX_PIN, TX_PIN);
Hi91Parser parser;
Hi91Data latest[2] = {};
uint32_t good[2] = {0, 0};
uint32_t totalBytes = 0;
uint32_t overflowCount = 0;
uint32_t timeoutCount = 0;
uint32_t unexpectedCount = 0;
uint32_t lastPrintMs = 0;
uint32_t phaseStartedMs = 0;
uint32_t triggeredMs = 0;
uint8_t current = 0;

enum Phase { BOOT_WAIT, QUIET_CHECK, GAP, WAIT_REPLY, STOPPED };
Phase phase = BOOT_WAIT;

void stopTest(const __FlashStringHelper *reason) {
    phase = STOPPED;
    digitalWrite(SYNC_PIN[0], LOW);
    digitalWrite(SYNC_PIN[1], LOW);
    Serial.print(F("STOP: "));
    Serial.println(reason);
    Serial.println(F("Fix wiring/config, then reset UNO. No automatic retry."));
}

void printStatus() {
    Serial.print(F("bytes=")); Serial.print(totalBytes);
    Serial.print(F(" crc=")); Serial.print(parser.crcErrors);
    Serial.print(F(" discard=")); Serial.print(parser.discarded);
    Serial.print(F(" overflow=")); Serial.print(overflowCount);
    Serial.print(F(" timeout=")); Serial.print(timeoutCount);
    Serial.print(F(" unexpected=")); Serial.println(unexpectedCount);
    for (uint8_t i = 0; i < 2; ++i) {
        Serial.print(F("ID=0")); Serial.print(ADDRESS[i], HEX);
        Serial.print(F(" frames=")); Serial.print(good[i]);
        if (good[i]) {
            Serial.print(F(" imu_ms=")); Serial.print(latest[i].timeMs);
            Serial.print(F(" roll=")); Serial.print(latest[i].roll, 2);
            Serial.print(F(" pitch=")); Serial.print(latest[i].pitch, 2);
            Serial.print(F(" yaw=")); Serial.print(latest[i].yaw, 2);
        }
        Serial.println();
    }
}

void setup() {
    Serial.begin(115200);
    for (uint8_t i = 0; i < 2; ++i) {
        digitalWrite(SYNC_PIN[i], LOW);
        pinMode(SYNC_PIN[i], OUTPUT);
    }
    imuSerial.begin(IMU_BAUD);
    // AVR SoftwareSerial enables the RX pull-up by default. Disable it:
    // the shared diode bus must be pulled up externally to 3.3 V, NOT 5 V.
    digitalWrite(RX_PIN, LOW);
    imuSerial.listen();
    Serial.println(F("IMU761 UNO R3 test: sensor=9600, USB=115200, IDs=01/02"));
#if TEST_MODE == 1
    Serial.println(F("SYNC mode: D6->01, D7->02. Both sensors must be ONMARK."));
    Serial.println(F("Wait 6 s for boot, then require 1.2 s with no UART bytes."));
#else
    Serial.println(F("PASSIVE mode: connect one IMU only, ONTIME 0.5."));
#endif
    Serial.println(F("Send s to stop. Reset UNO to restart."));
    phaseStartedMs = millis();
}

void loop() {
    if (Serial.available()) {
        const char command = (char)Serial.read();
        if (command == 's' || command == 'S') stopTest(F("USER"));
    }

    if (imuSerial.overflow()) {
        ++overflowCount;
#if TEST_MODE == 1
        if (phase != BOOT_WAIT && phase != STOPPED) stopTest(F("RX_OVERFLOW"));
#endif
    }

    while (imuSerial.available()) {
        const uint8_t byte = (uint8_t)imuSerial.read();
        ++totalBytes;
        if (phase == STOPPED) continue;
#if TEST_MODE == 1
        if (phase == BOOT_WAIT) continue;
        if (phase != WAIT_REPLY) {
            ++unexpectedCount;
            stopTest(F("UNSOLICITED_BYTES: check ONMARK / noise / late frame"));
            continue;
        }
#endif
        Hi91Data data;
        const uint32_t crcBefore = parser.crcErrors;
        const bool ready = parser.feed(byte, data);
#if TEST_MODE == 1
        if (parser.crcErrors != crcBefore) {
            stopTest(F("CRC_ERROR"));
            continue;
        }
#else
        (void)crcBefore;
#endif
        if (!ready) continue;
        int8_t index = data.address == ADDRESS[0] ? 0 :
                       data.address == ADDRESS[1] ? 1 : -1;
        if (index < 0) {
            ++unexpectedCount;
            Serial.print(F("Unexpected address=0x")); Serial.println(data.address, HEX);
            stopTest(F("ADDRESS_LAYOUT_OR_CONFIGURATION"));
            continue;
        }
#if TEST_MODE == 1
        if ((uint8_t)index != current) {
            ++unexpectedCount;
            stopTest(F("WRONG_ADDRESS: check swapped SYNC wires"));
            continue;
        }
#endif
        latest[index] = data;
        ++good[index];
#if TEST_MODE == 1
        current ^= 1;
        phase = GAP;
#endif
    }

    const uint32_t now = millis();
#if TEST_MODE == 1
    if (phase == BOOT_WAIT && (uint32_t)(now - phaseStartedMs) >= 6000) {
        parser.reset();
        phase = QUIET_CHECK;
        phaseStartedMs = now;
    } else if (phase == QUIET_CHECK && (uint32_t)(now - phaseStartedMs) >= 1200) {
        phase = GAP;
        triggeredMs = now - SLOT_MS;
        Serial.println(F("Bus quiet. Starting alternating triggers."));
    } else if (phase == WAIT_REPLY && (uint32_t)(now - triggeredMs) >= REPLY_TIMEOUT_MS) {
        ++timeoutCount;
        stopTest(F("TIMEOUT: check baud / power / SYNC / diode / pull-up"));
    }
#endif

    // Print only between replies. Keep loop draining SoftwareSerial during RX.
    if (phase != WAIT_REPLY && (uint32_t)(now - lastPrintMs) >= 1000) {
        lastPrintMs = now;
        printStatus();
    }

#if TEST_MODE == 1
    if (phase == GAP && (uint32_t)(millis() - triggeredMs) >= SLOT_MS) {
        parser.reset();
        phase = WAIT_REPLY;
        triggeredMs = millis();
        digitalWrite(SYNC_PIN[current], HIGH);
        delayMicroseconds(100);
        digitalWrite(SYNC_PIN[current], LOW);
    }
#endif
}
