// 真机验收自检：接上 Receiver_RX 后跑一遍，自动判定是否达标。
//
//   ./hw_selftest /dev/ttyACM0                       # Windows: hw_selftest.exe COM3
//   ./hw_selftest /dev/ttyACM0 imu_calibration_left.npy imu_calibration_right.npy
//
// 依次检查：
//   1. 端口能打开、链路连通
//   2. 板卡未被复位（连续 5 秒持续出数，无长空档）—— 若 DTR 处理有误，
//      表现为反复掉线，这一项会失败
//   3. 帧率、丢帧率
//   4. 电量读数合理
//   5. joints[0] 恒为原点、全部关节有限
//   6. 骨长守恒（与手势无关的硬性几何约束）
//   7. 平展姿势共面性（需要配合摆姿势，会提示；未校准时跳过）
//
// 退出码 0 表示全部通过。
#include <telex_glove.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

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

namespace {

int g_fail = 0;

void ok(const std::string& what)            { std::printf("  [通过] %s\n", what.c_str()); }
void bad(const std::string& what)           { std::printf("  [失败] %s\n", what.c_str()); ++g_fail; }
void skip(const std::string& what)          { std::printf("  [跳过] %s\n", what.c_str()); }
void check(bool c, const std::string& what) { c ? ok(what) : bad(what); }

double dist(const telex::Vec3& a, const telex::Vec3& b) {
    const double dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

struct HandReport {
    int      frames      = 0;
    double   max_gap_s   = 0.0;
    double   rate_hz     = 0.0;
    int      battery     = -1;
    bool     calibrated  = false;
    double   max_wrist   = 0.0;   // joints[0] 偏离原点的最大值
    double   max_bone_err = 0.0;  // 骨长偏差最大值
    bool     any_nonfinite = false;
    std::vector<telex::Snapshot> samples;
};

// 食指三节骨长，是与手势无关的硬性约束
const double kIndexBones[3] = {0.040, 0.025, 0.020};

void observe(HandReport& r, const telex::Snapshot& s) {
    ++r.frames;
    r.battery    = s.battery_percent;
    r.calibrated = r.calibrated || s.calibrated;

    r.max_wrist = std::max({r.max_wrist, std::fabs(s.joints[0].x),
                            std::fabs(s.joints[0].y), std::fabs(s.joints[0].z)});

    for (const auto& j : s.joints)
        if (!std::isfinite(j.x) || !std::isfinite(j.y) || !std::isfinite(j.z))
            r.any_nonfinite = true;

    for (int k = 0; k < 3; ++k) {
        const double got = dist(s.joints[k + 2], s.joints[k + 1]);
        r.max_bone_err = std::max(r.max_bone_err, std::fabs(got - kIndexBones[k]));
    }
    if (r.samples.size() < 8) r.samples.push_back(s);
}

void report_hand(const char* name, const HandReport& r, const telex::Stats& st) {
    std::printf("\n--- %s手 ---\n", name);
    if (r.frames == 0) {
        bad(std::string(name) + "手全程无数据（手套未开机？未配对？）");
        return;
    }
    ok(std::string(name) + "手收到 " + std::to_string(r.frames) + " 帧");

    check(st.rx_rate_hz > 50.0,
          "帧率 " + std::to_string(static_cast<int>(st.rx_rate_hz)) + " Hz（需 > 50）");

    // 板卡若被复位，会出现明显空档
    check(r.max_gap_s < 0.5,
          "最大数据空档 " + std::to_string(r.max_gap_s) + " 秒（需 < 0.5，"
          "超出通常意味着板卡被复位或链路不稳）");

    check(r.battery >= 0 && r.battery <= 100,
          "电量读数 " + std::to_string(r.battery) + "%（需在 0-100）");

    check(!r.any_nonfinite, "关节坐标全部为有限值");
    check(r.max_wrist == 0.0, "joints[0] 恒为原点");
    check(r.max_bone_err < 1e-9,
          "食指骨长守恒，最大偏差 " + std::to_string(r.max_bone_err) + " 米");

    if (r.calibrated) {
        ok("三步校准已生效");
    } else {
        skip("未提供校准文件（姿态会带贴装偏差；平展共面性检查跳过）");
    }
}

// 平展姿势共面性：需要人配合，单独一步
void flat_pose_check(telex::GloveHub& hub, telex::Hand hand, const char* name,
                     bool calibrated) {
    if (!calibrated) return;

    std::printf("\n请把%s手平摊伸直，保持 3 秒……", name);
    std::fflush(stdout);
    std::this_thread::sleep_for(std::chrono::seconds(1));

    double best_zmax = 1e9;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline) {
        telex::Snapshot s;
        if (!hub.try_wait_next(hand, 0.2, s)) continue;
        double zmax = 0.0;
        for (const auto& j : s.joints) zmax = std::max(zmax, std::fabs(j.z));
        best_zmax = std::min(best_zmax, zmax);
    }
    std::printf("\n");
    if (best_zmax > 1e8) {
        bad(std::string(name) + "手平展检查期间无数据");
        return;
    }
    check(best_zmax < 0.005,
          std::string(name) + "手平展时全部关节 |z| = " + std::to_string(best_zmax) +
          "（需 < 0.005；若超标请确认校准文件与手别匹配、且姿势确实摊平）");
}

}  // namespace

int main(int argc, char** argv) {
#if defined(_WIN32)
    SetConsoleOutputCP(CP_UTF8);   // 输出含中文，Windows 控制台默认按 GBK 显示
#endif
    const std::string port = (argc > 1) ? argv[1] : TELEX_DEFAULT_PORT;
    std::printf("TeleX Glove SDK %s 真机自检\n端口: %s\n\n",
                telex::version().c_str(), port.c_str());

    try {
        telex::GloveHub hub(port);
        ok("端口打开成功");

        for (int i = 2; i <= 3 && i < argc; ++i) {
            const telex::Hand h = (i == 2) ? telex::Hand::Left : telex::Hand::Right;
            const char* n = (i == 2) ? "左" : "右";
            try {
                hub.set_calibration(h, argv[i]);
                ok(std::string("加载") + n + "手校准: " + argv[i]);
            } catch (const telex::Error& e) {
                bad(std::string("加载") + n + "手校准失败: " + e.what());
            }
        }

        hub.start(3.0);
        ok("Hub 已启动");

        std::printf("\n采集 5 秒……\n");
        HandReport left, right;
        double last_l = 0.0, last_r = 0.0;
        const auto t0 = std::chrono::steady_clock::now();
        const auto deadline = t0 + std::chrono::seconds(5);

        while (std::chrono::steady_clock::now() < deadline) {
            const double now = std::chrono::duration<double>(
                                   std::chrono::steady_clock::now() - t0).count();
            telex::Snapshot s;
            if (hub.try_wait_next(telex::Hand::Left, 0.05, s)) {
                if (last_l > 0.0) left.max_gap_s = std::max(left.max_gap_s, now - last_l);
                last_l = now;
                observe(left, s);
            }
            if (hub.try_wait_next(telex::Hand::Right, 0.05, s)) {
                if (last_r > 0.0) right.max_gap_s = std::max(right.max_gap_s, now - last_r);
                last_r = now;
                observe(right, s);
            }
        }

        report_hand("左", left,  hub.stats(telex::Hand::Left));
        report_hand("右", right, hub.stats(telex::Hand::Right));

        flat_pose_check(hub, telex::Hand::Left,  "左", left.calibrated);
        flat_pose_check(hub, telex::Hand::Right, "右", right.calibrated);

        hub.stop();
        ok("\nHub 已正常停止");
    } catch (const telex::Error& e) {
        std::printf("  [失败] %s\n", e.what());
        ++g_fail;
    }

    std::printf("\n========================================\n");
    if (g_fail == 0) {
        std::printf("真机自检全部通过\n");
        return 0;
    }
    std::printf("有 %d 项未通过，详见上文\n", g_fail);
    return 1;
}
