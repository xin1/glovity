#include <assert.h>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#define IMU761_HOST_TEST 1
#include "../imu761_uno_shared_rx_experiment/imu761_uno_shared_rx_experiment.ino"

const uint8_t sample[] = {
    0x5A,0xA5,0x4C,0x00,0xD1,0x87,0x91,0x08,0x18,0x20,0x05,0x00,0x00,0x00,0xAB,0x49,0x00,0x00,
    0xB7,0x2B,0xFB,0x3B,0xBA,0x29,0xD0,0xBE,0x5A,0x5B,0x6A,0x3F,0xA6,0xC1,0x02,0xC0,
    0x57,0x67,0xE6,0x3D,0x7A,0xAD,0x11,0xBC,0x78,0x7E,0xB7,0xC1,0x0C,0xF9,0xD9,0xC1,
    0xCC,0x95,0x5B,0xC2,0x20,0xAB,0x1F,0xBF,0xC1,0xA6,0xBF,0xC1,0xC6,0x88,0x70,0xBF,
    0x8F,0x6A,0x7A,0x3F,0x19,0x8E,0x54,0xBE,0xFD,0x66,0x6D,0xBB,0x13,0xCF,0xE1,0xBB
};
std::vector<uint8_t> frame(uint8_t address) {
    std::vector<uint8_t> f(sample, sample + sizeof(sample));
    f[10] = address;
    uint16_t crc = Hi91Parser::crcUpdate(0, f.data(), 4);
    crc = Hi91Parser::crcUpdate(crc, f.data() + 6, 76);
    f[4] = (uint8_t)crc; f[5] = (uint8_t)(crc >> 8);
    return f;
}
void deliver(const std::vector<uint8_t> &f) {
    for (uint8_t b : f) { imuSerial.input.push_back(b); loop(); }
}
int main() {
    setup();
    deliver(frame(1));
    deliver(frame(2));
    assert(frames[0] == 1 && frames[1] == 1);
    auto bad = frame(1); bad[30] ^= 1; deliver(bad);
    assert(parser.crcErrors == 1);
    deliver(frame(3)); assert(unknownAddress == 1);
    imuSerial.overflowFlag = true; loop(); assert(overflowCount == 1);
    testTime = 1000; loop();
    assert(Serial.output.find("ID=01 frames=1") != std::string::npos);
    assert(Serial.output.find("ID=02 frames=1") != std::string::npos);
    std::cout << "PASS: shared RX extracts IDs 01/02 and rejects corrupt frames\n";
}
