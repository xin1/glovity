#include <assert.h>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#define IMU761_HOST_TEST 1
#define TEST_MODE 0
#include "../imu761_uno_test/imu761_uno_test.ino"

int main() {
    setup();
    uint8_t frame[82] = {};
    frame[0] = 0x5A; frame[1] = 0xA5; frame[2] = 0x4C;
    frame[6] = 0x91; frame[10] = 0x02;
    uint16_t crc = Hi91Parser::crcUpdate(0, frame, 4);
    crc = Hi91Parser::crcUpdate(crc, frame + 6, 76);
    frame[4] = (uint8_t)crc; frame[5] = (uint8_t)(crc >> 8);
    for (uint8_t b : frame) { imuSerial.input.push_back(b); loop(); }
    assert(good[0] == 0 && good[1] == 1);
    testTime = 10000; loop();
    for (auto e : pinEvents) assert(e.level != HIGH);
    assert(timeoutCount == 0 && phase != STOPPED);
    frame[30] ^= 1;
    for (uint8_t b : frame) { imuSerial.input.push_back(b); loop(); }
    assert(parser.crcErrors == 1 && good[1] == 1);
    assert(phase != STOPPED);
    std::cout << "PASS: passive mode decodes ID 02, counts CRC errors, never triggers or times out\n";
}
