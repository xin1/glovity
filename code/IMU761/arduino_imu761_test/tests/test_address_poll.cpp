#include <assert.h>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#define IMU761_HOST_TEST 1
#include "../imu761_uno_address_poll/imu761_uno_address_poll.ino"

const uint8_t captured[] = {
    0x5A,0xA5,0x4C,0x00,0x19,0x75,0x91,0x08,0x18,0x21,0x02,0x00,0x00,0x00,0xEB,0x2C,
    0x00,0x00,0x29,0x1A,0x75,0xBF,0x5B,0x06,0x32,0x3E,0x20,0x32,0x64,0x3E,0x2B,0x8D,
    0xF2,0xBE,0x6A,0x51,0x99,0xBE,0x4E,0x05,0x65,0xBD,0x05,0xED,0x89,0xC1,0x79,0x75,
    0x79,0x41,0xF2,0xE7,0x3A,0xC2,0x18,0x89,0x99,0x42,0xC1,0xFA,0x1C,0x41,0xB9,0xC2,
    0xCE,0x40,0xF5,0xD9,0x46,0x3F,0x8E,0x52,0x03,0x3D,0x2A,0x17,0x1F,0x3F,0x35,0xB6,
    0xC6,0x3D
};

std::vector<uint8_t> sample(uint8_t address) {
    std::vector<uint8_t> f(captured, captured + sizeof(captured));
    f[10] = address;
    const uint16_t crc = Hi91Parser::crcUpdate(
        Hi91Parser::crcUpdate(0, f.data(), 4), f.data() + 6, 76);
    f[4] = (uint8_t)crc;
    f[5] = (uint8_t)(crc >> 8);
    return f;
}

int main() {
    setup();
    sensor.onWrite = [](const uint8_t *data, size_t n) {
        assert(n == 5 && data[0] == 0x68 && data[1] == 4 && data[3] == 3);
        assert(data[4] == (uint8_t)(data[1] + data[2] + data[3]));
        std::vector<uint8_t> f = sample(data[2]);
        for (uint8_t b : f) sensor.input.push_back(b);
    };
    loop();
    assert(!stopped && success[0] == 1 && success[1] == 1);
    assert(sensor.sent == std::vector<uint8_t>({0x68,4,1,3,8,0x68,4,2,3,9}));
    assert(latest[1].roll > 76.7f && latest[1].roll < 76.8f);
    assert(parser.crcErrors == 0);

    sensor.onWrite = [](const uint8_t *, size_t) {
        for (uint8_t b : sample(2)) sensor.input.push_back(b);
    };
    Hi91Data out;
    assert(!readOne(1, out));
    assert(stopped && wrongAddressCount[0] == 1);
    assert(success[0] == 1);
    std::cout << "PASS: 01/02 binary queries, CRC-valid captured HI91 replies, wrong-address stop\n";
}
