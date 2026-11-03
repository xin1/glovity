// 最小可跑示例：连接 Hub，持续打印双手 21 关节与链路状态。
//
//   ./monitor_hands /dev/ttyACM0                     # Windows: monitor_hands.exe COM3
//   ./monitor_hands /dev/ttyACM0 calib_left.npy calib_right.npy
//   ./monitor_hands file://sample_capture.bin        # 无硬件，回放录制流
#include <telex_glove.hpp>

#include <cstdio>
#include <string>

#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  define TELEX_DEFAULT_PORT "COM3"
#else
#  define TELEX_DEFAULT_PORT "/dev/ttyACM0"
#endif

int main(int argc, char** argv) {
#if defined(_WIN32)
    SetConsoleOutputCP(CP_UTF8);   // 输出含中文，Windows 控制台默认按 GBK 显示
#endif
    const std::string port = (argc > 1) ? argv[1] : TELEX_DEFAULT_PORT;
    std::printf("TeleX Glove SDK %s — port=%s\n", telex::version().c_str(), port.c_str());

    try {
        telex::GloveHub hub(port);

        if (argc > 2) hub.set_calibration(telex::Hand::Left,  argv[2]);
        if (argc > 3) hub.set_calibration(telex::Hand::Right, argv[3]);

        hub.start(3.0);

        for (int tick = 0; tick < 200; ++tick) {
            for (const auto hand : {telex::Hand::Left, telex::Hand::Right}) {
                const char* name = (hand == telex::Hand::Left) ? "左" : "右";
                telex::Snapshot s;
                if (!hub.try_wait_next(hand, 0.5, s)) {
                    std::printf("[%s手] 离线\n", name);
                    continue;
                }
                const telex::Stats st = hub.stats(hand);
                const auto& tip = s.joints[4];   // 食指尖
                const char* protocol = s.protocol == TELEX_GLOVE_PROTOCOL_V2_180 ? "v2-180" :
                                       s.protocol == TELEX_GLOVE_PROTOCOL_LEGACY_170 ? "legacy-170" :
                                       s.protocol == TELEX_GLOVE_PROTOCOL_LEGACY_168 ? "legacy-168" : "unknown";
                std::printf("[%s手] protocol=%s frame_seq=%-5u timestamp_us=",
                            name, protocol, s.frame_id);
                if (s.has_device_timestamp) std::printf("%llu", static_cast<unsigned long long>(s.device_timestamp_us));
                else std::printf("N/A");
                std::printf(" recording=");
                if (s.has_recording_flag) std::printf("%d", s.recording ? 1 : 0);
                else std::printf("N/A");
                std::printf(" 电量=%3d%% fail_mask=", s.battery_percent);
                if (s.has_fail_mask) std::printf("0x%03X", s.imu_fail_mask);
                else std::printf("N/A");
                std::printf(" gyro_valid=%d 校准=%s %.1fHz ", s.wrist_gyro_valid ? 1 : 0,
                            s.calibrated ? "是" : "否", st.rx_rate_hz);
                if (s.joints_valid)
                    std::printf("%s=(%+.4f, %+.4f, %+.4f)\n",
                                telex::joint_name(4).c_str(), tip.x, tip.y, tip.z);
                else
                    std::printf("joints=N/A (IMU failure)\n");
            }
        }
    } catch (const telex::Error& e) {
        std::fprintf(stderr, "错误: %s\n", e.what());
        return 1;
    }
    return 0;
}
