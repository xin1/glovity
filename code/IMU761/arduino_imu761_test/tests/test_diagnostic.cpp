#include <assert.h>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#include "../imu761_uno_diagnostic/imu761_uno_diagnostic.ino"

int main() {
    // User's actual CRC-valid capture at 9600, address 02.
    uint8_t f[] = {
        0x5A,0xA5,0x4C,0x00,0x19,0x75,0x91,0x08,0x18,0x21,0x02,0x00,0x00,0x00,0xEB,0x2C,
        0x00,0x00,0x29,0x1A,0x75,0xBF,0x5B,0x06,0x32,0x3E,0x20,0x32,0x64,0x3E,0x2B,0x8D,
        0xF2,0xBE,0x6A,0x51,0x99,0xBE,0x4E,0x05,0x65,0xBD,0x05,0xED,0x89,0xC1,0x79,0x75,
        0x79,0x41,0xF2,0xE7,0x3A,0xC2,0x18,0x89,0x99,0x42,0xC1,0xFA,0x1C,0x41,0xB9,0xC2,
        0xCE,0x40,0xF5,0xD9,0x46,0x3F,0x8E,0x52,0x03,0x3D,0x2A,0x17,0x1F,0x3F,0x35,0xB6,
        0xC6,0x3D
    };
    setup();
    for (uint8_t b : f) { sensor.input.push_back(b); loop(); }
    assert(valid[1] == 1 && valid[0] == 0 && crcErrors == 0);
    assert(byteCount == 82 && rawCount == 82 && angles[1][0] > 76.7f);
    f[25] ^= 1;
    for (uint8_t b : f) acceptByte(b);
    assert(crcErrors == 1 && valid[1] == 1 && rawCount == 96);
    f[25] ^= 1;
    f[10] = 1;
    uint16_t crc = updateCRC(updateCRC(0, f, 4), f + 6, 76);
    f[4] = (uint8_t)crc; f[5] = (uint8_t)(crc >> 8);
    for (uint8_t b : f) acceptByte(b);
    assert(valid[0] == 1);
    reportWindow();
    assert(Serial.output.find("ID=01 valid=1") != std::string::npos);
    assert(Serial.output.find("ID=02 valid=1") != std::string::npos);
    testTime = 2000; loop();
    assert(valid[0] == 0 && valid[1] == 0 && byteCount == 0 && rawCount == 0);
    for (uint8_t b : f) acceptByte(b);
    assert(valid[0] == 1);
    std::cout << "PASS: real 9600 capture, both IDs, corruption recovery, raw cap, report and new window\n";
}
