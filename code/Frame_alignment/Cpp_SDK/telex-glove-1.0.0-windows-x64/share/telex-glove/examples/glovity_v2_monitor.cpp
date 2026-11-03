#include <glovity_frame_v2.hpp>
#include <telex_glove.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <array>
#include <atomic>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace {

std::atomic<bool> running{true};

BOOL WINAPI on_console_event(DWORD) {
    running = false;
    return TRUE;
}

class Input {
public:
    ~Input() { if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_); }

    bool open(const std::string& source, int baud) {
        if (source.rfind("file://", 0) == 0) {
            file_.open(source.substr(7), std::ios::binary);
            return file_.is_open();
        }
        std::string name = source;
        if (name.rfind("\\\\.\\", 0) != 0) name = "\\\\.\\" + name;
        handle_ = CreateFileA(name.c_str(), GENERIC_READ, 0, nullptr, OPEN_EXISTING, 0, nullptr);
        if (handle_ == INVALID_HANDLE_VALUE) return false;
        DCB dcb{};
        dcb.DCBlength = sizeof(dcb);
        if (!GetCommState(handle_, &dcb)) return false;
        dcb.BaudRate = static_cast<DWORD>(baud);
        dcb.ByteSize = 8;
        dcb.Parity = NOPARITY;
        dcb.StopBits = ONESTOPBIT;
        dcb.fBinary = TRUE;
        dcb.fDtrControl = DTR_CONTROL_DISABLE;
        dcb.fRtsControl = RTS_CONTROL_DISABLE;
        if (!SetCommState(handle_, &dcb)) return false;
        COMMTIMEOUTS timeouts{};
        timeouts.ReadIntervalTimeout = 20;
        timeouts.ReadTotalTimeoutConstant = 100;
        if (!SetCommTimeouts(handle_, &timeouts)) return false;
        PurgeComm(handle_, PURGE_RXCLEAR);
        return true;
    }

    std::size_t read(std::uint8_t* dst, std::size_t capacity) {
        if (file_.is_open()) {
            file_.read(reinterpret_cast<char*>(dst), static_cast<std::streamsize>(capacity));
            return static_cast<std::size_t>(file_.gcount());
        }
        DWORD got = 0;
        if (!ReadFile(handle_, dst, static_cast<DWORD>(capacity), &got, nullptr)) return 0;
        return static_cast<std::size_t>(got);
    }

    bool eof() const { return file_.is_open() && file_.eof(); }

private:
    HANDLE handle_ = INVALID_HANDLE_VALUE;
    std::ifstream file_;
};

double host_time_s() {
    LARGE_INTEGER now{}, frequency{};
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return static_cast<double>(now.QuadPart) / static_cast<double>(frequency.QuadPart);
}

void show_frame(const glovity::V2Frame& frame, const char* left_calib, const char* right_calib) {
    telex_glove_quat quats[TELEX_GLOVE_IMU_COUNT]{};
    for (std::size_t i = 0; i < 11; ++i) {
        quats[i].w = frame.imu[i][3] / 32768.0;
        quats[i].x = frame.imu[i][4] / 32768.0;
        quats[i].y = frame.imu[i][5] / 32768.0;
        quats[i].z = frame.imu[i][6] / 32768.0;
    }
    const double gx = frame.wrist_gyro[0] / 32768.0 * 2000.0;
    const double gy = frame.wrist_gyro[1] / 32768.0 * 2000.0;
    const double gz = frame.wrist_gyro[2] / 32768.0 * 2000.0;
    const char* hand_name = frame.hand_id == 0 ? "left" : "right";

    std::printf("[%s] protocol=v2-180 frame_seq=%5u timestamp_us=%llu host_receive_s=%.6f "
                "recording=%u battery=%u%% fail_mask=0x%03X gyro_valid=%u",
                hand_name, static_cast<unsigned>(frame.frame_seq),
                static_cast<unsigned long long>(frame.timestamp_us), host_time_s(),
                frame.recording() ? 1U : 0U, static_cast<unsigned>(frame.battery_percent),
                static_cast<unsigned>(frame.imu_fail_mask), frame.wrist_gyro_valid() ? 1U : 0U);
    if (frame.wrist_gyro_valid()) std::printf(" gyro_dps=(%.2f,%.2f,%.2f)", gx, gy, gz);
    else std::printf(" gyro_dps=N/A");

    if (frame.imu_fail_mask != 0) {
        std::printf(" joints=N/A (IMU failure)\n");
        return;
    }
    telex_glove_vec3 joints[TELEX_GLOVE_JOINT_COUNT]{};
    const auto hand = frame.hand_id == 0 ? TELEX_GLOVE_HAND_LEFT : TELEX_GLOVE_HAND_RIGHT;
    const char* calib = frame.hand_id == 0 ? left_calib : right_calib;
    const telex_glove_status status = telex_glove_compute_joints(quats, hand, calib, joints);
    if (status != TELEX_GLOVE_OK) {
        std::printf(" joints=ERROR(%s: %s)\n", telex_glove_status_string(status),
                    telex_glove_last_error());
        return;
    }
    std::printf(" index_tip=(%.4f,%.4f,%.4f)\n", joints[4].x, joints[4].y, joints[4].z);
}

}  // namespace

int main(int argc, char** argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCtrlHandler(on_console_event, TRUE);
    if (argc < 2) {
        std::fprintf(stderr, "Usage: glovity_v2_monitor.exe <COM3|file://capture.bin> [baud] [left.npy] [right.npy]\n");
        return 2;
    }
    const int baud = argc > 2 ? std::atoi(argv[2]) : 2000000;
    const char* left_calib = argc > 3 && argv[3][0] ? argv[3] : nullptr;
    const char* right_calib = argc > 4 && argv[4][0] ? argv[4] : nullptr;
    Input input;
    if (!input.open(argv[1], baud)) {
        std::fprintf(stderr, "Cannot open %s (Windows error %lu)\n", argv[1], GetLastError());
        return 1;
    }

    std::vector<std::uint8_t> stream;
    stream.reserve(8192);
    std::array<std::uint8_t, 4096> chunk{};
    std::uint64_t valid = 0, rejected = 0;
    while (running) {
        const std::size_t count = input.read(chunk.data(), chunk.size());
        if (count == 0) {
            if (input.eof()) break;
            continue;
        }
        stream.insert(stream.end(), chunk.begin(), chunk.begin() + count);
        std::size_t pos = 0;
        while (pos + glovity::kV2FrameSize <= stream.size()) {
            if (stream[pos] != 0xAA || stream[pos + 1] != 0x55 || stream[pos + 2] != 2) {
                ++pos;
                continue;
            }
            glovity::V2Frame frame;
            if (!glovity::parse_v2_frame(stream.data() + pos, glovity::kV2FrameSize, frame)) {
                ++rejected;
                ++pos;
                continue;
            }
            show_frame(frame, left_calib, right_calib);
            ++valid;
            pos += glovity::kV2FrameSize;
        }
        if (pos) stream.erase(stream.begin(), stream.begin() + static_cast<std::ptrdiff_t>(pos));
    }
    std::fprintf(stderr, "Done: valid=%llu rejected_candidates=%llu buffered=%zu\n",
                 static_cast<unsigned long long>(valid),
                 static_cast<unsigned long long>(rejected), stream.size());
    return valid ? 0 : 3;
}
