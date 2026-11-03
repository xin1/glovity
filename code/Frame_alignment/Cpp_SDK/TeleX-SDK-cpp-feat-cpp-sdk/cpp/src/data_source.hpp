// 字节流数据源抽象：真实串口，或用于离线集成/回归的文件回放。
#ifndef TELEX_DATA_SOURCE_HPP
#define TELEX_DATA_SOURCE_HPP

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace telex {

class DataSource {
public:
    virtual ~DataSource() = default;
    /// 返回读到的字节数；0 表示本次无数据；-1 表示出错（填 err）。
    virtual long read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err) = 0;
    virtual void close() = 0;
};

/// 按 spec 构造数据源：
///   "file://<path>"  录制流回放（按 rate_hz 节流，用于无硬件集成与一致性验证）
///   其它             当作串口设备路径
std::unique_ptr<DataSource> make_data_source(const std::string& spec, int baud,
                                             std::string& err);

}  // namespace telex
#endif  // TELEX_DATA_SOURCE_HPP
