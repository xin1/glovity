// TeleX Glove SDK — C++ 便利封装
//
// 本文件是 header-only 的纯语法糖，只调用 telex_glove.h 里的 C 函数，
// 不含任何算法。因此它由你自己的编译器编译，与库之间没有 C++ ABI 约束：
// 无论你用 GCC / Clang / MSVC、libstdc++ / libc++、哪个 STL 版本都可以。
//
// 语言标准：C++11 即可编译。检测到 C++17 时会额外提供 std::optional 接口。
#ifndef TELEX_GLOVE_HPP
#define TELEX_GLOVE_HPP

#include "telex_glove.h"

#if !defined(__cplusplus)
#  error "telex_glove.hpp 是 C++ 头文件；C 语言请改用 telex_glove.h"
#elif __cplusplus < 201103L && !(defined(_MSC_VER) && _MSC_VER >= 1900)
#  error "telex_glove.hpp 需要 C++11 或更高标准。请为编译器加上 -std=c++11（或更高），\
 CMake 用户可设置 CMAKE_CXX_STANDARD >= 11。若不便升级标准，可改用纯 C 接口 telex_glove.h。"
#endif

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

// MSVC 默认不更新 __cplusplus（恒为 199711L，除非 /Zc:__cplusplus），需看 _MSVC_LANG
#if (defined(__cplusplus) && __cplusplus >= 201703L) || \
    (defined(_MSVC_LANG) && _MSVC_LANG >= 201703L)
#  include <optional>
#  define TELEX_GLOVE_HAS_OPTIONAL 1
#endif

namespace telex {

enum class Hand : int32_t {
    Left  = TELEX_GLOVE_HAND_LEFT,
    Right = TELEX_GLOVE_HAND_RIGHT,
};

// 显式构造函数而非默认成员初始化器：后者会让类型失去聚合性质，
// 在 C++11 下无法用 Vec3{a,b,c} 初始化（C++14 才放宽）。
struct Vec3 {
    double x, y, z;
    Vec3() : x(0), y(0), z(0) {}
    Vec3(double xv, double yv, double zv) : x(xv), y(yv), z(zv) {}
};
struct Quat {
    double w, x, y, z;
    Quat() : w(1), x(0), y(0), z(0) {}
    Quat(double wv, double xv, double yv, double zv) : w(wv), x(xv), y(yv), z(zv) {}
};

/// 库返回非 OK 时抛出。code() 为原始错误码，便于分支处理。
class Error : public std::runtime_error {
public:
    Error(telex_glove_status code, const std::string& what)
        : std::runtime_error(what), code_(code) {}
    telex_glove_status code() const { return code_; }

private:
    telex_glove_status code_;
};

struct Snapshot {
    uint64_t timestamp_ns   = 0;
    uint64_t seq            = 0;   ///< 单调递增，可作排序键
    uint32_t frame_id       = 0;   ///< 设备帧序号，uint16 回绕，勿作排序键
    Hand     hand           = Hand::Left;
    int      battery_percent = 0;
    bool     online          = false;
    bool     calibrated      = false;

    std::array<Quat, TELEX_GLOVE_IMU_COUNT>   imu{};
    std::array<Vec3, TELEX_GLOVE_JOINT_COUNT> joints{};  ///< 米，joints[0] 为原点
    Vec3 wrist_euler_deg;
    Vec3 wrist_gyro_dps;
};

struct Stats {
    bool     connected      = false;
    bool     online         = false;
    uint64_t rx_total       = 0;
    double   rx_rate_hz     = 0.0;
    double   last_rx_age_s  = -1.0;
};

namespace detail {

inline void throw_if(telex_glove_status s, const char* op) {
    if (s == TELEX_GLOVE_OK) return;
    const char* detail = telex_glove_last_error();
    std::string msg = std::string(op) + ": " + telex_glove_status_string(s);
    if (detail != nullptr && detail[0] != '\0') msg += " (" + std::string(detail) + ")";
    throw Error(s, msg);
}

inline Snapshot convert(const telex_glove_snapshot& c) {
    Snapshot s;
    s.timestamp_ns    = c.timestamp_ns;
    s.seq             = c.seq;
    s.frame_id        = c.frame_id;
    s.hand            = (c.hand == TELEX_GLOVE_HAND_RIGHT) ? Hand::Right : Hand::Left;
    s.battery_percent = c.battery_percent;
    s.online          = c.online != 0;
    s.calibrated      = c.calibrated != 0;
    for (int i = 0; i < TELEX_GLOVE_IMU_COUNT; ++i)
        s.imu[i] = Quat{c.imu[i].w, c.imu[i].x, c.imu[i].y, c.imu[i].z};
    for (int i = 0; i < TELEX_GLOVE_JOINT_COUNT; ++i)
        s.joints[i] = Vec3{c.joints[i].x, c.joints[i].y, c.joints[i].z};
    s.wrist_euler_deg = Vec3{c.wrist_euler_deg.x, c.wrist_euler_deg.y, c.wrist_euler_deg.z};
    s.wrist_gyro_dps  = Vec3{c.wrist_gyro_dps.x,  c.wrist_gyro_dps.y,  c.wrist_gyro_dps.z};
    return s;
}

inline telex_glove_snapshot fresh_snapshot() {
    telex_glove_snapshot c = telex_glove_snapshot();  // 全零，避免失败路径读到脏值
    c.struct_size = static_cast<uint32_t>(sizeof(c));
    return c;
}

}  // namespace detail

/// 手套 Hub。串口的唯一所有者，同一端口不要创建第二个实例。
class GloveHub {
public:
    /// port 可为串口路径，或 "file://<路径>" 回放录制流（无需硬件）。
    explicit GloveHub(const std::string& port, int baud = 0) {
        detail::throw_if(telex_glove_hub_create(port.c_str(), baud, &h_), "create");
    }
    ~GloveHub() { telex_glove_hub_destroy(h_); }

    GloveHub(const GloveHub&) = delete;
    GloveHub& operator=(const GloveHub&) = delete;
    GloveHub(GloveHub&& o) noexcept : h_(o.h_) { o.h_ = nullptr; }
    GloveHub& operator=(GloveHub&& o) noexcept {
        if (this != &o) { telex_glove_hub_destroy(h_); h_ = o.h_; o.h_ = nullptr; }
        return *this;
    }

    void start(double startup_timeout_s = 2.0) {
        detail::throw_if(telex_glove_hub_start(h_, startup_timeout_s), "start");
    }
    void stop() { telex_glove_hub_stop(h_); }

    /// 设置三步校准文件；空路径表示取消。失败抛 Error，但数据流不受影响
    /// （该手退回未校准 FK）。
    void set_calibration(Hand hand, const std::string& npy_path) {
        detail::throw_if(
            telex_glove_hub_set_calibration(
                h_, static_cast<telex_glove_hand>(hand), npy_path.c_str()),
            "set_calibration");
    }

    /// 非阻塞取最新。该手离线时 ok 为 false。
    bool try_latest(Hand hand, Snapshot& out) const {
        telex_glove_snapshot c = detail::fresh_snapshot();
        const telex_glove_status s =
            telex_glove_hub_latest(h_, static_cast<telex_glove_hand>(hand), &c);
        if (s == TELEX_GLOVE_OK) { out = detail::convert(c); return true; }
        if (s == TELEX_GLOVE_E_NO_DATA) return false;
        detail::throw_if(s, "latest");
        return false;
    }

    /// 阻塞等待下一帧；超时返回 false。
    bool try_wait_next(Hand hand, double timeout_s, Snapshot& out) const {
        telex_glove_snapshot c = detail::fresh_snapshot();
        const telex_glove_status s = telex_glove_hub_wait_next(
            h_, static_cast<telex_glove_hand>(hand), timeout_s, &c);
        if (s == TELEX_GLOVE_OK) { out = detail::convert(c); return true; }
        if (s == TELEX_GLOVE_E_TIMEOUT || s == TELEX_GLOVE_E_NO_DATA) return false;
        detail::throw_if(s, "wait_next");
        return false;
    }

#ifdef TELEX_GLOVE_HAS_OPTIONAL
    std::optional<Snapshot> latest(Hand hand) const {
        Snapshot s;
        if (try_latest(hand, s)) return s;
        return std::nullopt;
    }
    std::optional<Snapshot> wait_next(Hand hand, double timeout_s) const {
        Snapshot s;
        if (try_wait_next(hand, timeout_s, s)) return s;
        return std::nullopt;
    }
#endif

    Stats stats(Hand hand) const {
        telex_glove_stats c = telex_glove_stats();
        c.struct_size = static_cast<uint32_t>(sizeof(c));
        detail::throw_if(
            telex_glove_hub_stats(h_, static_cast<telex_glove_hand>(hand), &c), "stats");
        Stats s;
        s.connected     = c.connected != 0;
        s.online        = c.online != 0;
        s.rx_total      = c.rx_total;
        s.rx_rate_hz    = c.rx_rate_hz;
        s.last_rx_age_s = c.last_rx_age_s;
        return s;
    }

private:
    telex_glove_hub* h_ = nullptr;
};

inline std::string version() { return telex_glove_version(); }
inline std::string joint_name(int i) {
    const char* n = telex_glove_joint_name(i);
    return n ? n : std::string();
}
inline std::string imu_name(int i) {
    const char* n = telex_glove_imu_name(i);
    return n ? n : std::string();
}

}  // namespace telex
#endif  // TELEX_GLOVE_HPP
