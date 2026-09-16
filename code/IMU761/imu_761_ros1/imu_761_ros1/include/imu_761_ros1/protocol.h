#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <cstddef>
#include <cstdint>
#include <vector>

constexpr size_t IMU_PAYLOAD_LEN = 76;

struct ImuData {
    uint8_t  tag;
    uint16_t main_status;
    int8_t   temperature;
    float    pressure;
    uint32_t timestamp_ms;
    float    acc[3];   
    float    gyr[3];  
    float    mag[3];  
    float    eul[3];  
    float    quat[4];  
};


bool parse_payload(const uint8_t *payload, ImuData &out);

bool try_extract_frame(std::vector<uint8_t> &buf, std::vector<uint8_t> &frame_out);

#endif  // PROTOCOL_H