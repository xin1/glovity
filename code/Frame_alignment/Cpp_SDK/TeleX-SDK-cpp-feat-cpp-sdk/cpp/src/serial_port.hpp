// 串口封装：POSIX termios（serial_port.cpp）/ Win32 Comm API（serial_port_win.cpp）。
//
// 关键行为：打开后立即清 DTR/RTS。Receiver_RX 是 Arduino 类板卡，
// 若 open 时置位 DTR 会触发复位，表现为反复掉线，务必保留。
#ifndef TELEX_SERIAL_PORT_HPP
#define TELEX_SERIAL_PORT_HPP

#include <cstddef>
#include <cstdint>
#include <string>

namespace telex {

class SerialPort {
public:
    SerialPort() = default;
    ~SerialPort();
    SerialPort(const SerialPort&) = delete;
    SerialPort& operator=(const SerialPort&) = delete;

    /// 打开并配置为 8N1、无流控。失败返回 false 并填 err。
    bool open(const std::string& port, int baud, std::string& err);
    void close();
    bool is_open() const;

    /// 最多等待 timeout_ms 后读取。返回读到的字节数；
    /// 0 表示超时无数据；-1 表示出错（err 被填充）。
    long read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err);

private:
#if defined(_WIN32)
    void* handle_ = nullptr;          // HANDLE；nullptr 表示未打开
    int timeout_ms_ = -1;             // 已下发给驱动的读超时，避免每次读都重设
#else
    int fd_ = -1;
#endif
};

}  // namespace telex
#endif  // TELEX_SERIAL_PORT_HPP
