#include <assert.h>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#define IMU761_HOST_TEST 1
#include "../imu761_uno_no_sync/imu761_uno_no_sync.ino"

const uint8_t captured[] = {
    0x5A,0xA5,0x4C,0x00,0xD1,0x87,0x91,0x08,0x18,0x20,0x05,0x00,0x00,0x00,0xAB,0x49,0x00,0x00,
    0xB7,0x2B,0xFB,0x3B,0xBA,0x29,0xD0,0xBE,0x5A,0x5B,0x6A,0x3F,0xA6,0xC1,0x02,0xC0,
    0x57,0x67,0xE6,0x3D,0x7A,0xAD,0x11,0xBC,0x78,0x7E,0xB7,0xC1,0x0C,0xF9,0xD9,0xC1,
    0xCC,0x95,0x5B,0xC2,0x20,0xAB,0x1F,0xBF,0xC1,0xA6,0xBF,0xC1,0xC6,0x88,0x70,0xBF,
    0x8F,0x6A,0x7A,0x3F,0x19,0x8E,0x54,0xBE,0xFD,0x66,0x6D,0xBB,0x13,0xCF,0xE1,0xBB
};
std::vector<uint8_t> makeFrame(uint8_t address) {
    std::vector<uint8_t> f(captured, captured + sizeof(captured));
    f[10] = address;
    uint16_t crc = Hi91Parser::crcUpdate(0, f.data(), 4);
    crc = Hi91Parser::crcUpdate(crc, f.data() + 6, 76);
    f[4] = (uint8_t)crc; f[5] = (uint8_t)(crc >> 8);
    return f;
}
void deliver(SoftwareSerial &port, const std::vector<uint8_t> &frame) {
    for (uint8_t b : frame) { port.input.push_back(b); loop(); }
}
int main() {
    setup();
    assert(active == 0);
    deliver(imu01, makeFrame(1));
    assert(good[0] == 1 && good[1] == 0);
    testTime = 1500; loop(); assert(active == 1);
    deliver(imu02, makeFrame(2));
    assert(good[1] == 1);
    testTime = 3000; loop(); assert(active == 0);
    deliver(imu01, makeFrame(2));
    assert(wrongAddress[0] == 1 && good[0] == 1);
    auto bad = makeFrame(1); bad[20] ^= 1; deliver(imu01, bad);
    assert(parsers[0].crcErrors == 1);
    imu01.overflowFlag = true; loop(); assert(overflowCount[0] == 1);
    std::cout << "PASS: no-SYNC dual independent RX, port switching, IDs, CRC, overflow\n";
}
