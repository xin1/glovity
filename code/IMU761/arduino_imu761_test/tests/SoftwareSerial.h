#pragma once
#include "Arduino.h"
#include <functional>
class SoftwareSerial : public FakeSerial {
public:
    bool overflowFlag = false;
    std::vector<uint8_t> sent;
    std::function<void(const uint8_t *, size_t)> onWrite;
    SoftwareSerial(uint8_t, uint8_t) {}
    bool listen() { return true; }
    bool stopListening() { return true; }
    bool overflow() { bool was = overflowFlag; overflowFlag = false; return was; }
    size_t write(const uint8_t *data, size_t n) {
        sent.insert(sent.end(), data, data + n);
        if (onWrite) onWrite(data, n);
        return n;
    }
};
