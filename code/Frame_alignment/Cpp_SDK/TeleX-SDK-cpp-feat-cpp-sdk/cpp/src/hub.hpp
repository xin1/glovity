// 共享 Hub：独占一个串口，后台线程解析左右手帧并缓存最新快照。
//
// 语义对齐 Python SharedImuHub：只保留最新帧（旧帧丢弃）、
// 0.35s 无有效帧即视为离线、消费者轮询。C++ 侧额外提供 wait_next，
// 让集成方不必忙等。
#ifndef TELEX_HUB_HPP
#define TELEX_HUB_HPP

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <memory>
#include <string>
#include <thread>

#include "calibration.hpp"
#include "fk.hpp"
#include "protocol.hpp"
#include "data_source.hpp"

namespace telex {

constexpr int    DEFAULT_BAUD            = 2000000;
constexpr double DEFAULT_OFFLINE_TIMEOUT = 0.35;
constexpr size_t RATE_WINDOW             = 80;

/// 一手的完整快照（含 FK 结果）。
struct HandSnapshot {
    uint64_t timestamp_ns  = 0;
    uint64_t seq           = 0;
    uint32_t frame_id      = 0;
    uint64_t device_timestamp_us = 0;
    uint16_t imu_fail_mask = 0;
    bool     recording = false;
    bool     wrist_gyro_valid = false;
    int      protocol = PROTOCOL_UNKNOWN;
    int      protocol_version = 0;
    bool     has_device_timestamp = false;
    bool     has_recording_flag = false;
    bool     has_fail_mask = false;
    bool     joints_valid = false;
    bool     joint_local_rotations_valid = false;
    std::array<uint8_t, V2_FRAME_SIZE> raw{};
    size_t   raw_size = 0;
    int      hand_id       = HAND_LEFT;
    int      battery_percent = 0;
    bool     online        = false;
    bool     calibrated    = false;
    std::array<QuatWXYZ, IMU_COUNT> imu{};
    Vec3     joints[NUM_JOINTS]{};
    Rot      joint_local_rotations[NUM_SLOTS]{};
    Vec3     wrist_euler_deg{};
    Vec3     wrist_gyro_dps{};
};

struct HandStats {
    bool     connected = false;
    bool     online    = false;
    uint64_t rx_total  = 0;
    double   rx_rate_hz = 0.0;
    double   last_rx_age_s = -1.0;
};

class Hub {
public:
    Hub(std::string port, int baud);
    ~Hub();
    Hub(const Hub&) = delete;
    Hub& operator=(const Hub&) = delete;

    /// 启动后台线程并等待串口就绪。失败返回 false 并填 err。
    bool start(double startup_timeout_s, std::string& err);
    void stop();

    /// 设置某手的三步校准文件路径；空字符串表示取消校准。
    /// 立即尝试加载以便尽早报错；加载失败仍会返回 false，但不影响数据流。
    bool set_calibration(int hand_id, const std::string& path, std::string& err);

    /// 取最新缓存。该手离线返回 false。
    bool latest(int hand_id, HandSnapshot& out) const;

    /// 阻塞等待该手的下一帧（frame_id 变化）。超时返回 false。
    bool wait_next(int hand_id, double timeout_s, HandSnapshot& out) const;

    HandStats stats(int hand_id) const;
    bool connected() const { return connected_.load(); }
    const std::string& error() const { return error_; }

private:
    struct HandSlot {
        HandSnapshot snap;
        bool     has_data   = false;
        uint64_t rx_total   = 0;
        double   last_rx_s  = 0.0;
        double   rx_rate_hz = 0.0;
        uint64_t seq        = 0;    // 每次更新自增，供 wait_next 判定
        std::deque<double> stamps;
        // 校准由后台线程独占使用，配置经 pending_ 传入
        CalibrationStore store;
        std::string calib_path;
    };

    void run();
    void update(const RawFrame& f, double now_s);
    HandSlot& slot(int hand_id) { return slots_[hand_id == HAND_RIGHT ? 1 : 0]; }
    const HandSlot& slot(int hand_id) const { return slots_[hand_id == HAND_RIGHT ? 1 : 0]; }

    std::string port_;
    int         baud_;
    std::unique_ptr<DataSource> source_;
    FrameSync   sync_;

    mutable std::mutex mutex_;
    mutable std::condition_variable cv_;
    std::array<HandSlot, 2> slots_;
    std::array<std::string, 2> pending_calib_;
    std::array<bool, 2>        pending_dirty_{{false, false}};

    std::thread       thread_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    std::string       error_;
};

/// 单调时钟秒。
double monotonic_s();

}  // namespace telex
#endif  // TELEX_HUB_HPP
