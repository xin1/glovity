// 测试脚手架：极简断言 + 金标准路径解析（不参与交付产物）。
#ifndef TELEX_TEST_UTIL_HPP
#define TELEX_TEST_UTIL_HPP

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace tt {

inline int g_failures = 0;
inline int g_checks = 0;

inline std::string golden_dir() {
    const char* env = std::getenv("TELEX_GOLDEN_DIR");
    return env ? std::string(env) : std::string(TELEX_GOLDEN_DIR_DEFAULT);
}
inline std::string golden(const std::string& name) { return golden_dir() + "/" + name; }

inline void check(bool cond, const std::string& what) {
    ++g_checks;
    if (!cond) {
        ++g_failures;
        std::printf("  [FAIL] %s\n", what.c_str());
    }
}

inline void close(double got, double want, double tol, const std::string& what) {
    ++g_checks;
    const double diff = std::fabs(got - want);
    if (!(diff <= tol) || !std::isfinite(got)) {
        ++g_failures;
        std::printf("  [FAIL] %s: got=%.17g want=%.17g diff=%.3g tol=%.3g\n",
                    what.c_str(), got, want, diff, tol);
    }
}

inline int report(const char* suite) {
    if (g_failures == 0) {
        std::printf("[PASS] %s (%d 项断言)\n", suite, g_checks);
        return 0;
    }
    std::printf("[FAIL] %s: %d/%d 项断言失败\n", suite, g_failures, g_checks);
    return 1;
}

}  // namespace tt
#endif
