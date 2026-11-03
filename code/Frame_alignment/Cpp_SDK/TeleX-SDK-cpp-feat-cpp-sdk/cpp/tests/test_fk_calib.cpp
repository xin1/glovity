// FK 校准路径：对拍金标准 + 翻译自 tests/test_glove_fk.py 的性质测试。
#include "../src/calibration.hpp"
#include "../src/fk.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <cmath>
#include <string>

using namespace telex;

namespace {

double dist(const Vec3& a, const Vec3& b) { return vnorm(vsub(a, b)); }

void load_imu(const jmini::Value& qs, std::array<QuatWXYZ, IMU_COUNT>& imu) {
    for (int k = 0; k < IMU_COUNT; ++k)
        imu[k] = QuatWXYZ{qs[k][0].d(), qs[k][1].d(), qs[k][2].d(), qs[k][3].d()};
}

// 金标准逐点对拍
void test_golden(CalibrationStore& store) {
    const auto root = jmini::load_file(tt::golden("fk_calib.json"));
    const double tol = (*root)["tol"].d();
    const auto& cases = (*root)["cases"];

    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i];
        const std::string name = c["name"].s();
        const Side side = (c["side"].s() == "right") ? Side::Right : Side::Left;
        const std::string npy = tt::golden(c["calib_npy"].s());

        const Calibration* calib = store.get(npy, side);
        tt::check(calib != nullptr, name + ": 校准加载成功 (" + store.last_error() + ")");

        std::array<QuatWXYZ, IMU_COUNT> imu{};
        load_imu(c["imu_quats_wxyz"], imu);
        Vec3 joints[NUM_JOINTS];
        compute_skeleton_joints(imu, IMU_COUNT, side, calib, joints);

        const auto& want = c["joints"];
        for (int j = 0; j < NUM_JOINTS; ++j)
            for (int a = 0; a < 3; ++a)
                tt::close(joints[j][a], want[j][a].d(), tol,
                          name + ".joints[" + std::to_string(j) + "][" +
                          std::to_string(a) + "]");
    }
}

// 从金标准里取一个具名用例的关节
bool joints_of(const jmini::Value& cases, const std::string& want_name,
               CalibrationStore& store, Vec3 out[NUM_JOINTS]) {
    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i];
        if (c["name"].s() != want_name) continue;
        const Side side = (c["side"].s() == "right") ? Side::Right : Side::Left;
        const Calibration* calib = store.get(tt::golden(c["calib_npy"].s()), side);
        std::array<QuatWXYZ, IMU_COUNT> imu{};
        load_imu(c["imu_quats_wxyz"], imu);
        compute_skeleton_joints(imu, IMU_COUNT, side, calib, out);
        return true;
    }
    return false;
}

// tests/test_glove_fk.py 的 6 个用例
void test_properties(CalibrationStore& store) {
    const auto root = jmini::load_file(tt::golden("fk_calib.json"));
    const auto& cases = (*root)["cases"];

    Vec3 flat[NUM_JOINTS], bent[NUM_JOINTS], rigid[NUM_JOINTS], flat_r[NUM_JOINTS];
    tt::check(joints_of(cases, "flat_left", store, flat), "取 flat_left");
    tt::check(joints_of(cases, "index_bend_left", store, bent), "取 index_bend_left");
    tt::check(joints_of(cases, "rigid_rot_left", store, rigid), "取 rigid_rot_left");
    tt::check(joints_of(cases, "flat_right", store, flat_r), "取 flat_right");

    // 1) shape / 腕部原点 / 有限
    tt::close(vnorm(flat[0]), 0.0, 0.0, "test_shape: joints[0] 为原点");
    for (int j = 0; j < NUM_JOINTS; ++j)
        for (int a = 0; a < 3; ++a)
            tt::check(std::isfinite(flat[j][a]), "test_shape: 全部有限");

    // 2) 平展 + 校准 -> 共面 & 四指沿 +Y 递增 & 食指尖距腕 ≈ 掌长+三节
    double zmax = 0.0;
    for (int j = 0; j < NUM_JOINTS; ++j) zmax = std::max(zmax, std::fabs(flat[j][2]));
    tt::check(zmax < 0.005, "test_flat: 全部关节 |z| < 0.005, got " + std::to_string(zmax));

    const int chains[4][4] = {{1,2,3,4}, {5,6,7,8}, {9,10,11,12}, {13,14,15,16}};
    for (const auto& ch : chains) {
        bool inc = true;
        for (int k = 0; k + 1 < 4; ++k) if (!(flat[ch[k+1]][1] > flat[ch[k]][1])) inc = false;
        tt::check(inc, "test_flat: 链 " + std::to_string(ch[0]) + " 沿 +Y 递增");
    }
    const double expect_tip = 0.085 + 0.040 + 0.025 + 0.020;
    tt::check(std::fabs(vnorm(flat[4]) - expect_tip) < 0.01,
              "test_flat: 食指尖距腕 ≈ " + std::to_string(expect_tip) +
              ", got " + std::to_string(vnorm(flat[4])));

    // 3) 食指弯曲 -> 指尖显著位移且朝掌心(-Z)；中指不动；骨长守恒
    tt::check(dist(bent[4], flat[4]) > 0.03, "test_bend: 指尖位移 > 0.03");
    tt::check(bent[4][2] < -0.01, "test_bend: 指尖朝 -Z 卷曲, got " + std::to_string(bent[4][2]));
    for (int j = 5; j < 9; ++j)
        tt::close(dist(bent[j], flat[j]), 0.0, 1e-6, "test_bend: 中指关节 " + std::to_string(j) + " 未动");
    const double bones[3] = {0.040, 0.025, 0.020};
    for (int k = 0; k < 3; ++k)
        tt::close(dist(bent[k + 2], bent[k + 1]), bones[k], 1e-9,
                  "test_bend: 食指第 " + std::to_string(k) + " 节骨长守恒");

    // 4) 整手刚性转动 -> 骨架不变
    for (int j = 0; j < NUM_JOINTS; ++j)
        tt::close(dist(rigid[j], flat[j]), 0.0, 1e-6,
                  "test_rigid: 关节 " + std::to_string(j) + " 不变");

    // 5) 左右手拇指镜像
    tt::check(flat[17][0] > 0.0, "test_mirror: 左手拇指 CMC 在 +X, got " + std::to_string(flat[17][0]));
    tt::check(flat_r[17][0] < 0.0, "test_mirror: 右手拇指 CMC 在 -X, got " + std::to_string(flat_r[17][0]));
    double zmax_r = 0.0;
    for (int j = 0; j < NUM_JOINTS; ++j) zmax_r = std::max(zmax_r, std::fabs(flat_r[j][2]));
    tt::check(zmax_r < 0.005, "test_mirror: 右手平展共面");
}

// 6) 缺失/损坏校准文件 -> 降级为未校准，且与显式未校准结果一致
void test_fallback() {
    CalibrationStore store;
    std::array<QuatWXYZ, IMU_COUNT> imu{};
    for (auto& q : imu) q = QuatWXYZ{1, 0, 0, 0};

    Vec3 none[NUM_JOINTS], missing[NUM_JOINTS];
    compute_skeleton_joints(imu, IMU_COUNT, Side::Left, nullptr, none);

    const Calibration* c = store.get("/no/such/file.npy", Side::Left);
    tt::check(c == nullptr, "缺失文件应返回 nullptr（降级）");
    tt::check(!store.last_error().empty(), "降级时应留下诊断");
    compute_skeleton_joints(imu, IMU_COUNT, Side::Left, c, missing);

    for (int j = 0; j < NUM_JOINTS; ++j)
        for (int a = 0; a < 3; ++a)
            tt::close(missing[j][a], none[j][a], 0.0,
                      "降级结果与未校准逐位一致 j=" + std::to_string(j));
}

// 缓存：同路径重复取应返回同一对象；不同路径应切换
void test_cache() {
    CalibrationStore store;
    const Calibration* a = store.get(tt::golden("imu_calibration_left.npy"), Side::Left);
    const Calibration* b = store.get(tt::golden("imu_calibration_left.npy"), Side::Left);
    tt::check(a != nullptr && a == b, "同路径同 mtime 命中缓存");
    const Calibration* c = store.get(tt::golden("imu_calibration_right.npy"), Side::Right);
    tt::check(c != nullptr && c->side == Side::Right, "切换到右手校准");
}

}  // namespace

int main() {
    CalibrationStore store;
    test_golden(store);
    test_properties(store);
    test_fallback();
    test_cache();
    return tt::report("fk_calib");
}
