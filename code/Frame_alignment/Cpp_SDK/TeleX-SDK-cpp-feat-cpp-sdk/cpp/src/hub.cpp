#include "hub.hpp"

#include <chrono>
#include <utility>

#include "npy_loader.hpp"

namespace telex {

double monotonic_s() {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

namespace {
uint64_t monotonic_ns() {
    using namespace std::chrono;
    return static_cast<uint64_t>(
        duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count());
}
}  // namespace

Hub::Hub(std::string port, int baud) : port_(std::move(port)), baud_(baud) {
    slots_[0].snap.hand_id = HAND_LEFT;
    slots_[1].snap.hand_id = HAND_RIGHT;
}

Hub::~Hub() { stop(); }

bool Hub::start(double startup_timeout_s, std::string& err) {
    if (running_.load()) return true;
    error_.clear();

    source_ = make_data_source(port_, baud_, err);
    if (!source_) {
        error_ = err;
        return false;
    }
    connected_.store(true);
    running_.store(true);
    thread_ = std::thread(&Hub::run, this);

    // 串口已打开即视为就绪；额外给一小段时间让首帧到达（不强制）
    const double deadline = monotonic_s() + (startup_timeout_s > 0 ? startup_timeout_s : 0.0);
    while (monotonic_s() < deadline) {
        {
            std::lock_guard<std::mutex> lk(mutex_);
            if (slots_[0].has_data || slots_[1].has_data) break;
        }
        if (!running_.load()) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (!running_.load()) {
        err = error_.empty() ? "hub thread stopped during startup" : error_;
        return false;
    }
    return true;
}

void Hub::stop() {
    running_.store(false);
    if (thread_.joinable()) thread_.join();
    if (source_) source_->close();
    source_.reset();
    connected_.store(false);
    cv_.notify_all();
}

bool Hub::set_calibration(int hand_id, const std::string& path, std::string& err) {
    {
        std::lock_guard<std::mutex> lk(mutex_);
        const int i = (hand_id == HAND_RIGHT) ? 1 : 0;
        pending_calib_[i] = path;
        pending_dirty_[i] = true;
    }
    if (path.empty()) return true;

    // 预检：立刻验证文件可读且格式正确，让集成方尽早发现问题。
    // 失败不影响数据流，后台线程仍会退回未校准 FK。
    CalibFile probe;
    if (!load_calib_file(path, probe, err)) return false;
    return true;
}

bool Hub::latest(int hand_id, HandSnapshot& out) const {
    std::lock_guard<std::mutex> lk(mutex_);
    const HandSlot& s = slot(hand_id);
    if (!s.has_data) return false;
    const double age = monotonic_s() - s.last_rx_s;
    if (age > DEFAULT_OFFLINE_TIMEOUT) return false;
    out = s.snap;
    out.online = true;
    return true;
}

bool Hub::wait_next(int hand_id, double timeout_s, HandSnapshot& out) const {
    std::unique_lock<std::mutex> lk(mutex_);
    const HandSlot& s = slot(hand_id);
    const uint64_t seen = s.seq;
    const auto dur = std::chrono::duration<double>(timeout_s > 0 ? timeout_s : 0.0);
    const bool got = cv_.wait_for(lk, dur, [&] { return s.seq != seen || !running_.load(); });
    if (!got || s.seq == seen || !s.has_data) return false;
    out = s.snap;
    out.online = true;
    return true;
}

HandStats Hub::stats(int hand_id) const {
    std::lock_guard<std::mutex> lk(mutex_);
    const HandSlot& s = slot(hand_id);
    HandStats st;
    st.connected  = connected_.load();
    st.rx_total   = s.rx_total;
    st.rx_rate_hz = s.rx_rate_hz;
    if (s.has_data) {
        st.last_rx_age_s = monotonic_s() - s.last_rx_s;
        st.online = st.last_rx_age_s <= DEFAULT_OFFLINE_TIMEOUT;
    }
    return st;
}

void Hub::update(const RawFrame& f, double now_s) {
    HandSlot& s = slot(f.hand_id);

    // 校准配置变更（在后台线程内应用，避免跨线程持有 CalibrationStore）
    const int i = (f.hand_id == HAND_RIGHT) ? 1 : 0;
    if (pending_dirty_[i]) {
        s.calib_path = pending_calib_[i];
        pending_dirty_[i] = false;
    }

    const Side requested = side_from_hand_id(f.hand_id);
    const Calibration* calib = s.calib_path.empty()
                                   ? nullptr
                                   : s.store.get(s.calib_path, requested);

    HandSnapshot& snap = s.snap;
    snap.timestamp_ns    = monotonic_ns();
    snap.frame_id        = f.frame_id;
    snap.device_timestamp_us = f.device_timestamp_us;
    snap.imu_fail_mask   = f.imu_fail_mask;
    snap.recording       = f.recording;
    snap.wrist_gyro_valid = f.wrist_gyro_valid;
    snap.protocol        = f.protocol;
    snap.protocol_version = f.protocol_version;
    snap.has_device_timestamp = f.has_device_timestamp;
    snap.has_recording_flag = f.has_recording_flag;
    snap.has_fail_mask   = f.has_fail_mask;
    snap.raw             = f.raw;
    snap.raw_size        = f.raw_size;
    snap.hand_id         = f.hand_id;
    snap.battery_percent = f.bat_percent;
    snap.online          = true;
    snap.calibrated      = (calib != nullptr);
    snap.imu             = f.imu;
    for (int k = 0; k < 3; ++k) {
        snap.wrist_euler_deg[k] = f.wrist_euler_deg[k];
        snap.wrist_gyro_dps[k]  = f.wrist_gyro_dps[k];
    }
    snap.joints_valid = !f.has_fail_mask || f.imu_fail_mask == 0;
    snap.joint_local_rotations_valid = snap.joints_valid;
    if (snap.joints_valid) {
        compute_skeleton_joints(f.imu, IMU_COUNT, requested, calib, snap.joints);
        compute_joint_local_rotations(f.imu, IMU_COUNT, requested, calib,
                                      snap.joint_local_rotations);
    } else {
        for (int j = 0; j < NUM_JOINTS; ++j) snap.joints[j] = Vec3{};
        for (int j = 0; j < NUM_SLOTS; ++j) snap.joint_local_rotations[j] = Rot::identity();
    }

    s.has_data  = true;
    s.rx_total += 1;
    s.last_rx_s = now_s;
    s.seq      += 1;
    snap.seq    = s.seq;

    s.stamps.push_back(now_s);
    if (s.stamps.size() > RATE_WINDOW) s.stamps.pop_front();
    if (s.stamps.size() >= 2) {
        const double span = s.stamps.back() - s.stamps.front();
        if (span > 0.0) s.rx_rate_hz = static_cast<double>(s.stamps.size() - 1) / span;
    }
}

void Hub::run() {
    uint8_t buf[1024];
    std::string err;
    while (running_.load()) {
        const long n = source_->read(buf, sizeof(buf), 200, err);
        if (n < 0) {
            error_ = err;
            break;
        }
        if (n == 0) continue;

        sync_.feed(buf, static_cast<size_t>(n));
        RawFrame f;
        bool any = false;
        {
            std::lock_guard<std::mutex> lk(mutex_);
            const double now_s = monotonic_s();
            while (sync_.next(f)) {
                update(f, now_s);
                any = true;
            }
        }
        if (any) cv_.notify_all();
    }
    connected_.store(false);
    running_.store(false);
    cv_.notify_all();
}

}  // namespace telex
