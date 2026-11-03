#ifndef IMU761_HI91_H
#define IMU761_HI91_H

#include <stdint.h>
#include <string.h>

struct Hi91Data {
    uint8_t address;
    uint32_t timeMs;
    float roll, pitch, yaw;
};

class Hi91Parser {
public:
    uint32_t crcErrors;
    uint32_t discarded;
    Hi91Parser() : crcErrors(0), discarded(0), used(0) {}

    static uint16_t crcUpdate(uint16_t crc, const uint8_t *p, uint8_t n) {
        while (n--) {
            crc ^= (uint16_t)*p++ << 8;
            for (uint8_t bit = 0; bit < 8; ++bit)
                crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
        }
        return crc;
    }

    bool feed(uint8_t byte, Hi91Data &out) {
        buf[used++] = byte;
        while (used) {
            if (buf[0] != 0x5A || (used >= 2 && buf[1] != 0xA5) ||
                (used >= 3 && buf[2] != 0x4C) || (used >= 4 && buf[3] != 0x00) ||
                (used >= 7 && buf[6] != 0x91)) {
                dropOne();
                continue;
            }
            if (used < sizeof(buf)) return false;

            uint16_t crc = crcUpdate(0, buf, 4);
            crc = crcUpdate(crc, buf + 6, 76);
            const uint16_t expected = (uint16_t)buf[4] | ((uint16_t)buf[5] << 8);
            if (crc != expected) {
                ++crcErrors;
                dropOne();
                continue;
            }

            out.address = buf[10];
            out.timeMs = readU32(buf + 14);
            out.roll = readFloat(buf + 54);
            out.pitch = readFloat(buf + 58);
            out.yaw = readFloat(buf + 62);
            used = 0;
            return true;
        }
        return false;
    }

private:
    uint8_t buf[82];
    uint8_t used;

    void dropOne() {
        --used;
        memmove(buf, buf + 1, used);
        ++discarded;
    }

    static uint32_t readU32(const uint8_t *p) {
        return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
               ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
    }

    static float readFloat(const uint8_t *p) {
        static_assert(sizeof(float) == 4, "HI91 needs 32-bit float");
        const uint32_t bits = readU32(p);
        float value;
        memcpy(&value, &bits, 4);
        return value;
    }
};

#endif
