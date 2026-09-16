#include <imu_761_ros1/protocol.h>

#include <cstring>
#include <vector>

static const uint8_t SOF0 = 0x5A;
static const uint8_t SOF1 = 0xA5;
static const uint8_t FRAME_TAG = 0x91;  
static const size_t  FRAME_HDR_LEN = 6;  

static uint16_t read_u16_le(const uint8_t *p) {
    uint16_t v;
    std::memcpy(&v, p, 2);
    return v;
}
static uint32_t read_u32_le(const uint8_t *p) {
    uint32_t v;
    std::memcpy(&v, p, 4);
    return v;
}
static float read_f32_le(const uint8_t *p) {
    float v;
    std::memcpy(&v, p, 4);
    return v;
}

static void crc16_update(uint16_t *crc, const uint8_t *src, uint32_t len) {
    uint16_t c = *crc;
    for (uint32_t j = 0; j < len; ++j) {
        c ^= (uint16_t)(src[j] << 8);
        for (int i = 0; i < 8; ++i) {
            uint16_t temp = (uint16_t)(c << 1);
            if (c & 0x8000) temp ^= 0x1021;
            c = temp;
        }
    }
    *crc = c;
}

static bool verify_frame_crc(const uint8_t *frame, uint16_t payload_len) {
    uint16_t crc = 0;
    crc16_update(&crc, frame, 4);
    crc16_update(&crc, frame + 6, payload_len);
    uint16_t crc_in_frame = (uint16_t)(frame[4] | (frame[5] << 8));
    return crc == crc_in_frame;
}

bool parse_payload(const uint8_t *payload, ImuData &out) {
    if (payload[0] != FRAME_TAG) return false;

    out.tag = payload[0];
    out.main_status  = read_u16_le(payload + 1);
    out.temperature  = (int8_t)payload[3];
    out.pressure     = read_f32_le(payload + 4);
    out.timestamp_ms = read_u32_le(payload + 8);

    for (int i = 0; i < 3; ++i) {
        out.acc[i] = read_f32_le(payload + 12 + 4 * i);
        out.gyr[i] = read_f32_le(payload + 24 + 4 * i);
        out.mag[i] = read_f32_le(payload + 36 + 4 * i);
    }
    out.eul[0] = read_f32_le(payload + 48);
    out.eul[1] = read_f32_le(payload + 52);
    out.eul[2] = read_f32_le(payload + 56);

    out.quat[0] = read_f32_le(payload + 60);
    out.quat[1] = read_f32_le(payload + 64);
    out.quat[2] = read_f32_le(payload + 68);
    out.quat[3] = read_f32_le(payload + 72);
    return true;
}

bool try_extract_frame(std::vector<uint8_t> &buf, std::vector<uint8_t> &frame_out) {
    while (buf.size() >= FRAME_HDR_LEN) {
        size_t start = 0;
        bool found = false;
        for (size_t i = 0; i + 1 < buf.size(); ++i) {
            if (buf[i] == SOF0 && buf[i + 1] == SOF1) {
                start = i;
                found = true;
                break;
            }
        }
        if (!found) {
            buf.clear();
            return false;
        }
        if (start > 0) buf.erase(buf.begin(), buf.begin() + start);

        if (buf.size() < 4) return false;

        uint16_t payload_len = (uint16_t)(buf[2] | (buf[3] << 8));
        if (payload_len == 0 || payload_len > 4096) {
            buf.erase(buf.begin());
            continue;
        }

        size_t frame_len = FRAME_HDR_LEN + payload_len;
        if (buf.size() < frame_len) return false;

        frame_out.assign(buf.begin(), buf.begin() + frame_len);

        if (!verify_frame_crc(frame_out.data(), payload_len)) {
            buf.erase(buf.begin());
            continue;
        }

        buf.erase(buf.begin(), buf.begin() + frame_len);
        return true;
    }
    return false;
}