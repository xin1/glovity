// 旋转数学：与 scipy.spatial.transform.Rotation 逐分支等价。
//
// 本文件刻意不依赖 Eigen。原因是 FK 的三步校准里存在 ~180 度旋转，
// 正落在 rotvec 轴向符号的退化点上，必须复刻 scipy 的具体分支才能
// 与既有 Python 管线逐位对齐；通用矩阵库在此处的取舍与 scipy 不同。
// 接口保持稳定，若日后要换回 Eigen 只需替换本文件的实现。
//
// 约定：内部四元数一律 (w, x, y, z)。scipy 对外是 (x, y, z, w)，
// 转换只发生在与金标准比对的边界处。
#ifndef TELEX_ROTATION_HPP
#define TELEX_ROTATION_HPP

#include <array>
#include <cmath>

namespace telex {

using Vec3 = std::array<double, 3>;
using Mat3 = std::array<std::array<double, 3>, 3>;  // 行优先: m[row][col]

inline Vec3 vsub(const Vec3& a, const Vec3& b) { return {a[0]-b[0], a[1]-b[1], a[2]-b[2]}; }
inline Vec3 vadd(const Vec3& a, const Vec3& b) { return {a[0]+b[0], a[1]+b[1], a[2]+b[2]}; }
inline Vec3 vscale(const Vec3& a, double s)    { return {a[0]*s, a[1]*s, a[2]*s}; }
inline double vdot(const Vec3& a, const Vec3& b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
inline Vec3 vcross(const Vec3& a, const Vec3& b) {
    return {a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]};
}
inline double vnorm(const Vec3& a) { return std::sqrt(vdot(a, a)); }

/// 单位四元数表示的旋转。语义与 scipy Rotation 一致。
class Rot {
public:
    Rot() : w_(1.0), x_(0.0), y_(0.0), z_(0.0) {}

    static Rot identity() { return Rot(); }

    /// scipy R.from_quat([x,y,z,w])。零范数退化为单位元（Python 侧对零四元数亦如此兜底）。
    static Rot from_quat_xyzw(double x, double y, double z, double w) {
        return from_quat_wxyz(w, x, y, z);
    }

    static Rot from_quat_wxyz(double w, double x, double y, double z) {
        const double n = std::sqrt(w*w + x*x + y*y + z*z);
        if (!(n > 1e-15)) return Rot();
        Rot r; r.w_ = w/n; r.x_ = x/n; r.y_ = y/n; r.z_ = z/n;
        return r;
    }

    /// scipy R.from_rotvec(v)。含 scipy 的小角泰勒分支（阈值 1e-3）。
    static Rot from_rotvec(const Vec3& v) {
        const double norm = vnorm(v);
        double scale;
        if (norm <= 1e-3) {
            const double n2 = norm * norm;
            scale = 0.5 - n2 / 48.0 + (n2 * n2) / 3840.0;
        } else {
            scale = std::sin(norm / 2.0) / norm;
        }
        Rot r;
        r.w_ = std::cos(norm / 2.0);
        r.x_ = scale * v[0];
        r.y_ = scale * v[1];
        r.z_ = scale * v[2];
        return r;
    }

    /// scipy r.as_rotvec()。先把 w 取正保证 angle∈[0,pi]，这一步正是
    /// 三步校准中 ~180 度旋转轴向符号的来源，不可省略。
    Vec3 as_rotvec() const {
        double w = w_, x = x_, y = y_, z = z_;
        if (w < 0.0) { w = -w; x = -x; y = -y; z = -z; }
        const double vn = std::sqrt(x*x + y*y + z*z);
        const double angle = 2.0 * std::atan2(vn, w);
        double scale;
        if (angle <= 1e-3) {
            const double a2 = angle * angle;
            scale = 2.0 + a2 / 12.0 + 7.0 * a2 * a2 / 2880.0;
        } else {
            scale = angle / std::sin(angle / 2.0);
        }
        return {scale * x, scale * y, scale * z};
    }

    /// scipy R.from_matrix(M)。逐字复刻 scipy 的 Shepperd 分支选择。
    static Rot from_matrix(const Mat3& m) {
        const double d0 = m[0][0], d1 = m[1][1], d2 = m[2][2];
        const double d3 = d0 + d1 + d2;
        double q[4];  // scipy 内部序: x, y, z, w
        int choice = 0;
        double best = d0;
        if (d1 > best) { best = d1; choice = 1; }
        if (d2 > best) { best = d2; choice = 2; }
        if (d3 > best) { best = d3; choice = 3; }

        if (choice != 3) {
            const int i = choice, j = (choice + 1) % 3, k = (j + 1) % 3;
            q[i] = 1.0 - d3 + 2.0 * m[i][i];
            q[j] = m[j][i] + m[i][j];
            q[k] = m[k][i] + m[i][k];
            q[3] = m[k][j] - m[j][k];
        } else {
            q[0] = m[2][1] - m[1][2];
            q[1] = m[0][2] - m[2][0];
            q[2] = m[1][0] - m[0][1];
            q[3] = 1.0 + d3;
        }
        return from_quat_xyzw(q[0], q[1], q[2], q[3]);
    }

    Mat3 as_matrix() const {
        const double x = x_, y = y_, z = z_, w = w_;
        const double x2 = x*x, y2 = y*y, z2 = z*z, w2 = w*w;
        const double xy = x*y, zw = z*w, xz = x*z, yw = y*w, yz = y*z, xw = x*w;
        return {{
            {{x2 - y2 - z2 + w2, 2.0*(xy - zw),     2.0*(xz + yw)    }},
            {{2.0*(xy + zw),     -x2 + y2 - z2 + w2, 2.0*(yz - xw)   }},
            {{2.0*(xz - yw),     2.0*(yz + xw),     -x2 - y2 + z2 + w2}}
        }};
    }

    Rot inv() const { Rot r; r.w_ = w_; r.x_ = -x_; r.y_ = -y_; r.z_ = -z_; return r; }

    /// 四元数乘法，等价 scipy 的 a * b（先施加 b 再施加 a）。
    Rot operator*(const Rot& o) const {
        Rot r;
        r.w_ = w_*o.w_ - x_*o.x_ - y_*o.y_ - z_*o.z_;
        r.x_ = w_*o.x_ + x_*o.w_ + y_*o.z_ - z_*o.y_;
        r.y_ = w_*o.y_ - x_*o.z_ + y_*o.w_ + z_*o.x_;
        r.z_ = w_*o.z_ + x_*o.y_ - y_*o.x_ + z_*o.w_;
        return r;
    }

    /// scipy r.apply(v)。scipy 走的是旋转矩阵而非四元数夹心，
    /// 为对齐到 1e-15 这里同样走矩阵。
    Vec3 apply(const Vec3& v) const {
        const Mat3 m = as_matrix();
        return {
            m[0][0]*v[0] + m[0][1]*v[1] + m[0][2]*v[2],
            m[1][0]*v[0] + m[1][1]*v[1] + m[1][2]*v[2],
            m[2][0]*v[0] + m[2][1]*v[1] + m[2][2]*v[2]
        };
    }

    double w() const { return w_; }
    double x() const { return x_; }
    double y() const { return y_; }
    double z() const { return z_; }

private:
    double w_, x_, y_, z_;
};

}  // namespace telex
#endif  // TELEX_ROTATION_HPP
