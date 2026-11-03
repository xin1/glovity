// UNO R3 diagnostic: D10 RX, D11 unused. No sensor commands or SYNC.
// Capture for 2 seconds, pause reception, report, then start a new window.
#include <Arduino.h>
#include <SoftwareSerial.h>
#include <string.h>

const uint32_t USB_BAUD = 9600;
const uint32_t SENSOR_BAUD = 9600;
SoftwareSerial sensor(10, 11);
uint8_t raw[96], frame[82];
uint8_t rawCount = 0, used = 0;
uint32_t windowStart, byteCount, crcErrors, discarded, overflows;
uint32_t valid[2] = {0, 0}, unknown;
uint8_t lastUnknown;
float angles[2][3] = {};

uint16_t updateCRC(uint16_t crc, const uint8_t *p, uint8_t n) {
    while (n--) {
        crc ^= (uint16_t)*p++ << 8;
        for (uint8_t i = 0; i < 8; ++i)
            crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
    }
    return crc;
}

void discardOne() {
    --used;
    memmove(frame, frame + 1, used);
    ++discarded;
}

void acceptByte(uint8_t b) {
    ++byteCount;
    if (rawCount < sizeof(raw)) raw[rawCount++] = b;
    frame[used++] = b;
    while (used) {
        if (frame[0] != 0x5A || (used >= 2 && frame[1] != 0xA5) ||
            (used >= 3 && frame[2] != 0x4C) || (used >= 4 && frame[3] != 0) ||
            (used >= 7 && frame[6] != 0x91)) {
            discardOne();
            continue;
        }
        if (used < sizeof(frame)) return;
        uint16_t crc = updateCRC(0, frame, 4);
        crc = updateCRC(crc, frame + 6, 76);
        const uint16_t expected = (uint16_t)frame[4] | ((uint16_t)frame[5] << 8);
        if (crc != expected) { ++crcErrors; discardOne(); continue; }
        // New/Wearable HI91 address layout, already verified by captured data.
        const uint8_t address = frame[10];
        if (address == 1 || address == 2) {
            const uint8_t index = address - 1;
            ++valid[index];
            for (uint8_t i = 0; i < 3; ++i)
                memcpy(&angles[index][i], frame + 54 + i * 4, 4);
        } else { ++unknown; lastUnknown = address; }
        used = 0;
    }
}

void beginWindow() {
    used = rawCount = 0;
    byteCount = crcErrors = discarded = overflows = unknown = 0;
    valid[0] = valid[1] = 0;
    sensor.listen();
    windowStart = millis();
}

void reportWindow() {
    Serial.println(F("--- 2-second capture (window counters) ---"));
    Serial.print(F("bytes=")); Serial.print(byteCount);
    Serial.print(F(" crc_bad=")); Serial.print(crcErrors);
    Serial.print(F(" discard=")); Serial.print(discarded);
    Serial.print(F(" overflow=")); Serial.print(overflows);
    Serial.print(F(" unknown_addr=")); Serial.println(unknown);
    Serial.println(F("RAW (first up to 96 bytes):"));
    for (uint8_t i = 0; i < rawCount; ++i) {
        if (raw[i] < 16) Serial.print('0');
        Serial.print(raw[i], HEX);
        Serial.print(' ');
        if ((i & 15) == 15) Serial.println();
    }
    Serial.println();
    for (uint8_t i = 0; i < 2; ++i) {
        Serial.print(F("ID=0")); Serial.print(i + 1);
        Serial.print(F(" valid=")); Serial.print(valid[i]);
        if (valid[i]) {
            Serial.print(F(" roll=")); Serial.print(angles[i][0], 2);
            Serial.print(F(" pitch=")); Serial.print(angles[i][1], 2);
            Serial.print(F(" yaw=")); Serial.print(angles[i][2], 2);
        }
        Serial.println();
    }
    if (unknown) {
        Serial.print(F("last_unknown_address=0x")); Serial.println(lastUnknown, HEX);
    }
    Serial.println(F("Reception paused during this report; restarting capture."));
}

void setup() {
    Serial.begin(USB_BAUD);
    sensor.begin(SENSOR_BAUD);
    digitalWrite(10, LOW); // Disable SoftwareSerial's internal 5 V RX pull-up.
    sensor.stopListening();
    Serial.print(F("UNO diagnostic USB=")); Serial.print(USB_BAUD);
    Serial.print(F(" sensor=")); Serial.println(SENSOR_BAUD);
    Serial.println(F("D10=RX, D11=unused. No commands sent to sensors."));
    Serial.flush();
    beginWindow();
}

void loop() {
    if (sensor.overflow()) ++overflows;
    while (sensor.available()) acceptByte((uint8_t)sensor.read());
    if ((uint32_t)(millis() - windowStart) >= 2000) {
        sensor.stopListening();
        while (sensor.available()) acceptByte((uint8_t)sensor.read());
        if (sensor.overflow()) ++overflows;
        reportWindow();
        Serial.flush();
        beginWindow();
    }
}
