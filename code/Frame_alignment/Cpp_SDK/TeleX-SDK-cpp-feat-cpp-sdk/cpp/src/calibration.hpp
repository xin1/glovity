// 三步校准的加载与缓存。
//
// 与 Python 侧一致的降级语义：加载失败不抛异常、不中断数据流，
// 仅记录一次诊断并退回未校准 FK；文件 mtime 变化后自动重新加载。
#ifndef TELEX_CALIBRATION_HPP
#define TELEX_CALIBRATION_HPP

#include <string>

#include "fk.hpp"

namespace telex {

/// 按 (绝对路径, mtime) 缓存。path 为空表示不使用校准。
/// 返回的指针在下次对同一 store 调用前保持有效；失败时返回 nullptr。
class CalibrationStore {
public:
    /// requested_side 仅在校准文件未携带 side 时作为兜底。
    const Calibration* get(const std::string& path, Side requested_side);

    /// 最近一次失败的诊断信息（中性英文，可经 C API 暴露）。
    const std::string& last_error() const { return last_error_; }

private:
    std::string  path_;
    long long    mtime_ = -2;
    bool         loaded_ = false;
    bool         warned_ = false;
    Side         requested_ = Side::Left;
    Calibration  calib_;
    std::string  last_error_;
};

}  // namespace telex
#endif  // TELEX_CALIBRATION_HPP
