// 串口封装（Win32 Comm API）。行为与 POSIX 版 serial_port.cpp 对齐：
//   - 8N1、无任何流控
//   - DTR/RTS 保持拉低，避免复位 Receiver_RX 板卡（与 Python 侧 dtr=False/rts=False 一致）
//   - read 在 timeout_ms 内等到首个字节即返回，超时返回 0，断开返回 -1
//
// 诊断信息只输出英文 + Win32 错误码，不用 FormatMessage：
// 后者返回的是系统语言、ANSI 代码页文本，会破坏 last_error 的 UTF-8 约定。
#include "serial_port.hpp"

#include <cstring>
#include <string>

#include "file_util.hpp"   // 引入 <windows.h>（已定义 WIN32_LEAN_AND_MEAN / NOMINMAX）

namespace telex {
namespace {

HANDLE as_handle(void* h) { return static_cast<HANDLE>(h); }

std::string win_error(const char* what, DWORD code) {
    std::string msg = what;
    switch (code) {
        case ERROR_FILE_NOT_FOUND:
        case ERROR_PATH_NOT_FOUND:     msg += ": no such port"; break;
        case ERROR_ACCESS_DENIED:      msg += ": port busy or access denied"; break;
        case ERROR_INVALID_PARAMETER:  msg += ": invalid parameter"; break;
        case ERROR_BAD_COMMAND:
        case ERROR_DEVICE_REMOVED:
        case ERROR_OPERATION_ABORTED:
        case ERROR_GEN_FAILURE:        msg += ": port disconnected"; break;
        default:                       break;
    }
    msg += " (win32 error " + std::to_string(static_cast<unsigned long>(code)) + ")";
    return msg;
}

/// "COM3" → "\\.\COM3"。COM10 及以上必须带此前缀，带了也不影响 COM1–9。
std::string device_path(const std::string& port) {
    if (port.compare(0, 4, "\\\\.\\") == 0) return port;
    return "\\\\.\\" + port;
}

}  // namespace

SerialPort::~SerialPort() { close(); }

bool SerialPort::is_open() const { return handle_ != nullptr; }

bool SerialPort::open(const std::string& port, int baud, std::string& err) {
    close();
    if (baud <= 0) { err = "unsupported baud rate"; return false; }

    const std::wstring wpath = utf8_to_wide(device_path(port));
    if (wpath.empty()) { err = "open failed: invalid port name"; return false; }

    HANDLE h = ::CreateFileW(wpath.c_str(), GENERIC_READ | GENERIC_WRITE,
                             0,                     // 独占
                             nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        err = win_error("open failed", ::GetLastError());
        return false;
    }
    handle_ = h;
    timeout_ms_ = -1;

    // 加大驱动侧接收缓冲，降低宿主短暂卡顿时的丢帧（失败不致命）
    ::SetupComm(h, 1 << 16, 1 << 12);

    DCB dcb;
    std::memset(&dcb, 0, sizeof(dcb));
    dcb.DCBlength = sizeof(dcb);
    if (!::GetCommState(h, &dcb)) {
        err = win_error("GetCommState failed", ::GetLastError());
        close();
        return false;
    }
    dcb.BaudRate        = static_cast<DWORD>(baud);   // Win32 直接接受任意数值
    dcb.ByteSize        = 8;
    dcb.Parity          = NOPARITY;
    dcb.StopBits        = ONESTOPBIT;
    dcb.fBinary         = TRUE;
    dcb.fParity         = FALSE;
    dcb.fOutxCtsFlow    = FALSE;
    dcb.fOutxDsrFlow    = FALSE;
    dcb.fDtrControl     = DTR_CONTROL_DISABLE;        // 关键：不得置位 DTR
    dcb.fRtsControl     = RTS_CONTROL_DISABLE;
    dcb.fDsrSensitivity = FALSE;
    dcb.fTXContinueOnXoff = TRUE;
    dcb.fOutX           = FALSE;
    dcb.fInX            = FALSE;
    dcb.fErrorChar      = FALSE;
    dcb.fNull           = FALSE;
    dcb.fAbortOnError   = FALSE;                      // 线路错误不挂起后续读写
    if (!::SetCommState(h, &dcb)) {
        const DWORD code = ::GetLastError();
        err = (code == ERROR_INVALID_PARAMETER) ? std::string("unsupported baud rate")
                                                : win_error("SetCommState failed", code);
        close();
        return false;
    }

    // 再显式拉低一次。部分 USB-CDC 驱动在 CreateFile 时会短暂置位，
    // SetCommState 不一定会主动下发；不支持控制线的虚拟串口失败也无妨。
    ::EscapeCommFunction(h, CLRDTR);
    ::EscapeCommFunction(h, CLRRTS);

    ::PurgeComm(h, PURGE_RXCLEAR | PURGE_TXCLEAR | PURGE_RXABORT | PURGE_TXABORT);
    return true;
}

void SerialPort::close() {
    if (handle_ != nullptr) {
        ::CloseHandle(as_handle(handle_));
        handle_ = nullptr;
    }
}

long SerialPort::read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err) {
    if (handle_ == nullptr) { err = "port not open"; return -1; }
    HANDLE h = as_handle(handle_);

    if (timeout_ms != timeout_ms_) {
        // 这组取值的语义（见 COMMTIMEOUTS 文档）：
        //   缓冲区已有数据 → 立即返回已有字节；
        //   否则等到首个字节到达即返回，最长等 ReadTotalTimeoutConstant 毫秒。
        // 与 POSIX 版 poll(timeout) + 非阻塞 read 等价。
        COMMTIMEOUTS to;
        std::memset(&to, 0, sizeof(to));
        to.ReadIntervalTimeout         = MAXDWORD;
        to.ReadTotalTimeoutMultiplier  = MAXDWORD;
        to.ReadTotalTimeoutConstant    = static_cast<DWORD>(timeout_ms > 0 ? timeout_ms : 1);
        to.WriteTotalTimeoutConstant   = 1000;
        if (!::SetCommTimeouts(h, &to)) {
            err = win_error("SetCommTimeouts failed", ::GetLastError());
            return -1;
        }
        timeout_ms_ = timeout_ms;
    }

    const DWORD want = cap > MAXDWORD ? MAXDWORD : static_cast<DWORD>(cap);
    DWORD got = 0;
    if (!::ReadFile(h, buf, want, &got, nullptr)) {
        const DWORD code = ::GetLastError();
        // 线路错误（帧错、溢出等）会挂起后续读，清掉后按"本次无数据"处理，
        // 由上层的帧同步自行恢复。拔线等真正的断开走 -1。
        DWORD line_errors = 0;
        if (::ClearCommError(h, &line_errors, nullptr) && line_errors != 0 &&
            code != ERROR_ACCESS_DENIED && code != ERROR_BAD_COMMAND &&
            code != ERROR_DEVICE_REMOVED && code != ERROR_OPERATION_ABORTED &&
            code != ERROR_GEN_FAILURE)
            return 0;
        err = win_error("read failed", code);
        return -1;
    }
    if (got == 0) {
        // 部分 USB 串口驱动拔线后 ReadFile 仍"成功"返回 0 字节，
        // 此时句柄已失效，ClearCommError 会失败，据此识别断开。
        DWORD line_errors = 0;
        if (!::ClearCommError(h, &line_errors, nullptr)) {
            err = win_error("port disconnected", ::GetLastError());
            return -1;
        }
    }
    return static_cast<long>(got);
}

}  // namespace telex
