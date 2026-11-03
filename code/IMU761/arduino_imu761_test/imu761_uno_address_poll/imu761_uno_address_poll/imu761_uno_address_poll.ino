/*
 * Two IMU761 units on one TTL UART, new addressed query firmware.
 * Arduino UNO R3: D10 shared RX, D11 TX through 5V->3.3V divider.
 * Firmware must remain silent until queried. USB monitor: 115200.
 */
#include <Arduino.h>
#include <SoftwareSerial.h>
#include "hi91.h"

#if !defined(__AVR_ATmega328P__) && !defined(IMU761_HOST_TEST)
#error "Use the classic UNO R3 / ATmega328P, or port this sketch."
#endif

const uint32_t SENSOR_BAUD = 115200;
const uint32_t MONITOR_BAUD = 115200;
const uint32_t REPLY_TIMEOUT_MS = 200;
const uint32_t BETWEEN_QUERIES_MS = 20;
const uint32_t BETWEEN_ROUNDS_MS = 300;

SoftwareSerial sensor(10, 11);
Hi91Parser parser;
Hi91Data latest[2] = {};
uint32_t success[2] = {0, 0};
uint32_t timeoutCount[2] = {0, 0};
uint32_t wrongAddressCount[2] = {0, 0};
uint32_t overflowCount = 0;
uint32_t strayBytes = 0;
uint8_t lastStray = 0;
uint16_t lastReplyBytes = 0;
uint8_t lastReplyRaw[16] = {};
uint8_t lastReplyRawCount = 0;
bool stopped = false;

void buildQuery(uint8_t address, uint8_t out[5]) {
    out[0] = 0x68;
    out[1] = 0x04;
    out[2] = address;
    out[3] = 0x03;
    out[4] = (uint8_t)(out[1] + out[2] + out[3]);
}

bool readOne(uint8_t address, Hi91Data &out) {
    // A previous reply appearing here would be assigned to the wrong query.
    uint16_t stale = 0;
    while (sensor.available()) { lastStray = (uint8_t)sensor.read(); ++stale; }
    if (stale) {
        strayBytes += stale;
        stopped = true;
        return false;
    }
    parser.reset();
    lastReplyBytes = 0;
    lastReplyRawCount = 0;

    uint8_t command[5];
    buildQuery(address, command);
    if (sensor.write(command, sizeof(command)) != sizeof(command)) {
        stopped = true;
        return false;
    }

    const uint32_t started = millis();
    while ((uint32_t)(millis() - started) < REPLY_TIMEOUT_MS) {
        while (sensor.available()) {
            Hi91Data frame;
            const uint8_t byte = (uint8_t)sensor.read();
            ++lastReplyBytes;
            if (lastReplyRawCount < sizeof(lastReplyRaw))
                lastReplyRaw[lastReplyRawCount++] = byte;
            if (!parser.feed(byte, frame)) continue;
            if (frame.address != address) {
                ++wrongAddressCount[address - 1];
                stopped = true;
                return false;
            }
            out = frame;
            ++success[address - 1];
            return true;
        }
        if (sensor.overflow()) {
            ++overflowCount;
            stopped = true;
            return false;
        }
    }
    ++timeoutCount[address - 1];
    stopped = true; // Do not query the other IMU while a late reply is possible.
    return false;
}

void printResult(uint8_t address, bool ok) {
    const uint8_t i = address - 1;
    Serial.print(F("ID=0")); Serial.print(address);
    Serial.print(F(" ok=")); Serial.print(success[i]);
    Serial.print(F(" timeout=")); Serial.print(timeoutCount[i]);
    Serial.print(F(" wrong_addr=")); Serial.print(wrongAddressCount[i]);
    Serial.print(F(" crc_bad=")); Serial.print(parser.crcErrors);
    Serial.print(F(" discarded=")); Serial.print(parser.discarded);
    Serial.print(F(" overflow=")); Serial.print(overflowCount);
    Serial.print(F(" rx_bytes=")); Serial.print(lastReplyBytes);
    if (ok) {
        Serial.print(F(" imu_ms=")); Serial.print(latest[i].timeMs);
        Serial.print(F(" roll=")); Serial.print(latest[i].roll, 2);
        Serial.print(F(" pitch=")); Serial.print(latest[i].pitch, 2);
        Serial.print(F(" yaw=")); Serial.print(latest[i].yaw, 2);
    }
    Serial.println();
    if (!ok) {
        Serial.print(F("RX first bytes: "));
        for (uint8_t j = 0; j < lastReplyRawCount; ++j) {
            if (lastReplyRaw[j] < 0x10) Serial.print('0');
            Serial.print(lastReplyRaw[j], HEX);
            Serial.print(' ');
        }
        Serial.println();
    }
}

void setup() {
    Serial.begin(MONITOR_BAUD);
    sensor.begin(SENSOR_BAUD);
    // AVR SoftwareSerial normally pulls RX up to 5 V. Use external 3.3 V pull-up.
    digitalWrite(10, LOW);
    sensor.listen();
    Serial.println(F("IMU761 two-address query: 01 / 02"));
    Serial.println(F("IMU=115200, USB monitor=115200, 8N1"));
    Serial.println(F("TX01=68 04 01 03 08; TX02=68 04 02 03 09"));
    Serial.println(F("Query-mode firmware only; reset UNO to retry after STOP."));
    delay(1000);
    // A quiet bus before the first query is the expected query-mode behavior.
    uint16_t unsolicited = 0;
    while (sensor.available()) { lastStray = (uint8_t)sensor.read(); ++unsolicited; }
    if (unsolicited) {
        strayBytes += unsolicited;
        stopped = true;
        Serial.print(F("STOP: bytes appeared before any query: "));
        Serial.println(unsolicited);
    }
}

void loop() {
    if (stopped) {
        delay(100);
        return;
    }

    for (uint8_t address = 1; address <= 2; ++address) {
        const bool ok = readOne(address, latest[address - 1]);
        printResult(address, ok);
        if (stopped) break;
        delay(BETWEEN_QUERIES_MS);
    }
    if (stopped) {
        Serial.print(F("STOP: stray=")); Serial.print(strayBytes);
        Serial.print(F(" last_byte=0x")); Serial.print(lastStray, HEX);
        Serial.println(F("; inspect wiring, timing and actual firmware mode."));
        return;
    }
    delay(BETWEEN_ROUNDS_MS);
}
