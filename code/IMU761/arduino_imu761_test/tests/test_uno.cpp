#include <assert.h>
#include <cmath>
#include <iostream>
#include "Arduino.h"
uint32_t testTime = 0;
std::vector<PinEvent> pinEvents;
FakeSerial Serial;
#define IMU761_HOST_TEST 1
#include "../imu761_uno_test/imu761_uno_test.ino"

// Existing captured frame from imu761_address_tool/test_protocol.py.
const uint8_t captured[] = {
    0x5A,0xA5,0x4C,0x00,0xD1,0x87,0x91,0x08,0x18,0x20,0x05,0x00,0x00,0x00,0xAB,0x49,0x00,0x00,
    0xB7,0x2B,0xFB,0x3B,0xBA,0x29,0xD0,0xBE,0x5A,0x5B,0x6A,0x3F,0xA6,0xC1,0x02,0xC0,
    0x57,0x67,0xE6,0x3D,0x7A,0xAD,0x11,0xBC,0x78,0x7E,0xB7,0xC1,0x0C,0xF9,0xD9,0xC1,
    0xCC,0x95,0x5B,0xC2,0x20,0xAB,0x1F,0xBF,0xC1,0xA6,0xBF,0xC1,0xC6,0x88,0x70,0xBF,
    0x8F,0x6A,0x7A,0x3F,0x19,0x8E,0x54,0xBE,0xFD,0x66,0x6D,0xBB,0x13,0xCF,0xE1,0xBB
};

std::vector<uint8_t> frameFor(uint8_t address) {
    std::vector<uint8_t> f(captured, captured + sizeof(captured));
    f[10] = address;
    uint16_t crc = Hi91Parser::crcUpdate(0, f.data(), 4);
    crc = Hi91Parser::crcUpdate(crc, f.data() + 6, 76);
    f[4] = (uint8_t)crc; f[5] = (uint8_t)(crc >> 8);
    return f;
}

void restart() {
    testTime = 0; pinEvents.clear(); Serial.input.clear(); Serial.output.clear();
    imuSerial.input.clear(); imuSerial.overflowFlag = false;
    parser = Hi91Parser(); good[0] = good[1] = 0;
    totalBytes = overflowCount = timeoutCount = unexpectedCount = 0;
    lastPrintMs = phaseStartedMs = triggeredMs = 0;
    current = 0; phase = BOOT_WAIT; setup();
}
void startSampling() {
    restart(); testTime = 6000; loop(); assert(phase == QUIET_CHECK);
    testTime = 7200; loop(); assert(phase == WAIT_REPLY); assert(current == 0);
}
void sendBytes(const std::vector<uint8_t> &f) {
    // Deliver in fragments below the real SoftwareSerial buffer size.
    for (uint8_t b : f) { imuSerial.input.push_back(b); loop(); }
}
size_t triggerCount() {
    size_t n = 0;
    for (auto e : pinEvents) if ((e.pin == 6 || e.pin == 7) && e.level == HIGH) ++n;
    return n;
}
int main() {
    static_assert(sizeof(captured) == 82, "captured frame size");
    Hi91Parser p; Hi91Data d; int n = 0;
    for (uint8_t b : captured) if (p.feed(b, d)) ++n;
    assert(n == 1 && d.address == 5 && d.timeMs == 0x49AB);
    assert(std::fabs(d.roll - (-0.62370491f)) < 0.0001f);
    assert(std::fabs(d.pitch - (-23.9564228f)) < 0.0001f);
    assert(std::fabs(d.yaw - (-0.939587f)) < 0.0001f);
    auto bad = frameFor(1); bad[30] ^= 1;
    for (uint8_t b : bad) assert(!p.feed(b, d));
    assert(p.crcErrors == 1);
    for (uint8_t b : {0x5A,0x5A,0xA5,0xFF,0x00}) assert(!p.feed(b, d));
    for (uint8_t addr : {1, 2}) {
        n = 0; for (uint8_t b : frameFor(addr)) if (p.feed(b, d)) ++n;
        assert(n == 1 && d.address == addr);
    }
    // Long noise cannot grow the fixed receive buffer.
    for (int i = 0; i < 10000; ++i) p.feed((uint8_t)(i * 73), d);
    n = 0; for (uint8_t b : captured) if (p.feed(b, d)) ++n; assert(n == 1);

    startSampling(); assert(triggerCount() == 1);
    sendBytes(frameFor(1)); assert(good[0] == 1 && phase == GAP);
    testTime += 250; loop(); assert(current == 1 && phase == WAIT_REPLY);
    sendBytes(frameFor(2)); assert(good[1] == 1 && phase == GAP);
    assert(triggerCount() == 2);

    startSampling(); sendBytes(frameFor(2)); assert(phase == STOPPED);
    assert(unexpectedCount == 1 && good[0] == 0 && good[1] == 0);
    testTime += 1000; loop(); assert(triggerCount() == 1);
    startSampling(); testTime += 250; loop(); assert(timeoutCount == 1 && phase == STOPPED);
    startSampling(); sendBytes(bad); assert(parser.crcErrors == 1 && phase == STOPPED);
    restart(); testTime = 6000; loop(); imuSerial.input.push_back(0x5A); loop();
    assert(phase == STOPPED && unexpectedCount == 1 && triggerCount() == 0);
    startSampling(); sendBytes(frameFor(1)); imuSerial.input.push_back(0); loop();
    assert(phase == STOPPED && unexpectedCount == 1);
    startSampling(); imuSerial.overflowFlag = true; loop(); assert(phase == STOPPED && overflowCount == 1);
    startSampling(); Serial.input.push_back('s'); loop(); assert(phase == STOPPED);
    std::cout << "PASS: captured HI91, CRC/resync/noise, alternating IDs, wrong address, timeout, unsolicited bytes, overflow, user stop\n";
}
