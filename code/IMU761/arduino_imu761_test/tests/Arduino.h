#pragma once
// Host test shim, NOT an Arduino library and never copied into the sketch folder.
#include <stdint.h>
#include <deque>
#include <string>
#include <vector>
#include <sstream>
#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define HEX 16
class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper *>(s))
extern uint32_t testTime;
struct PinEvent { uint8_t pin; uint8_t level; };
extern std::vector<PinEvent> pinEvents;
inline uint32_t millis() { return testTime; }
inline void delay(unsigned long ms) { testTime += (uint32_t)ms; }
inline void delayMicroseconds(unsigned int) {}
inline void digitalWrite(uint8_t pin, uint8_t level) { pinEvents.push_back({pin, level}); }
inline void pinMode(uint8_t, uint8_t) {}
class FakeSerial {
public:
    std::deque<uint8_t> input;
    std::string output;
    void begin(uint32_t) {}
    void flush() {}
    int available() { return (int)input.size(); }
    int read() { int c = input.front(); input.pop_front(); return c; }
    void print(const __FlashStringHelper *s) { output += reinterpret_cast<const char *>(s); }
    template<class T> void print(T x) { std::ostringstream s; s << +x; output += s.str(); }
    template<class T> void print(T x, int) { print(x); }
    void println() { output += '\n'; }
    template<class T> void println(T x) { print(x); println(); }
    template<class T> void println(T x, int base) { print(x, base); println(); }
};
extern FakeSerial Serial;
