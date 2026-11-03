// FK 未校准路径对拍 Python 金标准（容差 1e-12）。
#include "../src/fk.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <string>

using namespace telex;

int main() {
    const auto root = jmini::load_file(tt::golden("fk_uncalib.json"));
    const double tol = (*root)["tol"].d();

    const auto& cases = (*root)["cases"];
    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i];
        const std::string name = c["name"].s();
        const Side side = (c["side"].s() == "right") ? Side::Right : Side::Left;

        const auto& qs = c["imu_quats_wxyz"];
        std::array<QuatWXYZ, IMU_COUNT> imu{};
        const int valid = static_cast<int>(qs.size());
        for (int k = 0; k < valid && k < IMU_COUNT; ++k)
            imu[k] = QuatWXYZ{qs[k][0].d(), qs[k][1].d(), qs[k][2].d(), qs[k][3].d()};

        Vec3 joints[NUM_JOINTS];
        compute_skeleton_joints(imu, valid, side, nullptr, joints);

        const auto& want = c["joints"];
        for (int j = 0; j < NUM_JOINTS; ++j)
            for (int a = 0; a < 3; ++a)
                tt::close(joints[j][a], want[j][a].d(), tol,
                          name + ".joints[" + std::to_string(j) + "][" +
                          std::to_string(a) + "]");
    }
    return tt::report("fk_uncalib");
}
