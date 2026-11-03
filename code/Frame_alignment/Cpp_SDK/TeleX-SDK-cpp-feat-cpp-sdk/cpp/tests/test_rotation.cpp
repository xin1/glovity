// rotation.hpp 对拍 scipy.spatial.transform.Rotation 的金标准。
#include "../src/rotation.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <cstdio>

using telex::Mat3;
using telex::Rot;
using telex::Vec3;

namespace {

Vec3 vec_of(const jmini::Value& v) { return {v[0].d(), v[1].d(), v[2].d()}; }

Rot rot_wxyz(const jmini::Value& v) {
    return Rot::from_quat_wxyz(v[0].d(), v[1].d(), v[2].d(), v[3].d());
}

void check_quat(const Rot& got, const jmini::Value& want, double tol, const std::string& what) {
    // 四元数 q 与 -q 表示同一旋转；scipy 不做符号规范化，故按同号比较。
    const double sign = (got.w() * want[0].d() + got.x() * want[1].d() +
                         got.y() * want[2].d() + got.z() * want[3].d()) < 0.0 ? -1.0 : 1.0;
    tt::close(sign * got.w(), want[0].d(), tol, what + ".w");
    tt::close(sign * got.x(), want[1].d(), tol, what + ".x");
    tt::close(sign * got.y(), want[2].d(), tol, what + ".y");
    tt::close(sign * got.z(), want[3].d(), tol, what + ".z");
}

void check_vec(const Vec3& got, const jmini::Value& want, double tol, const std::string& what) {
    for (int i = 0; i < 3; ++i)
        tt::close(got[i], want[i].d(), tol, what + "[" + std::to_string(i) + "]");
}

}  // namespace

int main() {
    const auto root = jmini::load_file(tt::golden("rotation.json"));
    const double TOL = 1e-14;

    const auto& prims = (*root)["primitives"];
    for (size_t i = 0; i < prims.size(); ++i) {
        const auto& c = prims[i];
        const std::string tag = "prim[" + std::to_string(i) + "]";

        const Vec3 rv_in = vec_of(c["rotvec_in"]);
        const Rot r = Rot::from_rotvec(rv_in);

        check_quat(r, c["quat_wxyz"], TOL, tag + ".from_rotvec");
        check_vec(r.as_rotvec(), c["rotvec_out"], TOL, tag + ".as_rotvec");
        check_vec(r.apply({1.0, 0.0, 0.0}), c["apply_unit_x"], TOL, tag + ".apply_x");
        check_vec(r.apply({0.3, -0.7, 0.2}), c["apply_v"], TOL, tag + ".apply_v");
        check_quat(r.inv(), c["inv_quat_wxyz"], TOL, tag + ".inv");

        // as_matrix 对拍
        const Mat3 m = r.as_matrix();
        const auto& wm = c["matrix"];
        for (int a = 0; a < 3; ++a)
            for (int b = 0; b < 3; ++b)
                tt::close(m[a][b], wm[a][b].d(), TOL,
                          tag + ".as_matrix[" + std::to_string(a) + "][" + std::to_string(b) + "]");

        // from_matrix 往返（Shepperd 分支必须与 scipy 选同一支）
        check_quat(Rot::from_matrix(m), c["matrix_roundtrip_quat_wxyz"], TOL,
                   tag + ".from_matrix");
    }

    const auto& comps = (*root)["compose"];
    for (size_t i = 0; i < comps.size(); ++i) {
        const auto& c = comps[i];
        const std::string tag = "compose[" + std::to_string(i) + "]";
        const Rot a = rot_wxyz(c["a_wxyz"]);
        const Rot b = rot_wxyz(c["b_wxyz"]);
        check_quat(a * b, c["ab_wxyz"], TOL, tag + ".mul");
        check_vec((a * b).as_rotvec(), c["ab_rotvec"], TOL, tag + ".mul.as_rotvec");
    }

    return tt::report("rotation");
}
