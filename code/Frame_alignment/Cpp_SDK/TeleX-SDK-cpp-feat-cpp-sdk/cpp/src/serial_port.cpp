#include "serial_port.hpp"

#include <cerrno>
#include <cstring>

#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>

#if defined(__APPLE__)
#  include <IOKit/serial/ioss.h>
#endif

namespace telex {
namespace {

/// 把数值波特率映射为 termios 常量；无对应常量时返回 0（走平台专用路径）。
speed_t baud_constant(int baud) {
    switch (baud) {
        case 9600:    return B9600;
        case 19200:   return B19200;
        case 38400:   return B38400;
        case 57600:   return B57600;
        case 115200:  return B115200;
        case 230400:  return B230400;
#ifdef B460800
        case 460800:  return B460800;
#endif
#ifdef B500000
        case 500000:  return B500000;
#endif
#ifdef B921600
        case 921600:  return B921600;
#endif
#ifdef B1000000
        case 1000000: return B1000000;
#endif
#ifdef B1500000
        case 1500000: return B1500000;
#endif
#ifdef B2000000
        case 2000000: return B2000000;
#endif
#ifdef B3000000
        case 3000000: return B3000000;
#endif
        default:      return 0;
    }
}

}  // namespace

SerialPort::~SerialPort() { close(); }

bool SerialPort::is_open() const { return fd_ >= 0; }

bool SerialPort::open(const std::string& port, int baud, std::string& err) {
    close();
    fd_ = ::open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd_ < 0) {
        err = "open failed: " + std::string(std::strerror(errno));
        return false;
    }

    termios tio{};
    if (::tcgetattr(fd_, &tio) != 0) {
        err = "tcgetattr failed: " + std::string(std::strerror(errno));
        close();
        return false;
    }
    ::cfmakeraw(&tio);
    tio.c_cflag |= (CLOCAL | CREAD);
    tio.c_cflag &= ~static_cast<tcflag_t>(CSTOPB);      // 1 停止位
    tio.c_cflag &= ~static_cast<tcflag_t>(PARENB);      // 无校验
    tio.c_cflag &= ~static_cast<tcflag_t>(CSIZE);
    tio.c_cflag |= CS8;                                 // 8 数据位
#ifdef CRTSCTS
    tio.c_cflag &= ~static_cast<tcflag_t>(CRTSCTS);     // 无硬件流控
#endif
    tio.c_iflag &= ~static_cast<tcflag_t>(IXON | IXOFF | IXANY);  // 无软件流控
    tio.c_cc[VMIN]  = 0;
    tio.c_cc[VTIME] = 0;                                // 非阻塞，超时交给 poll

    const speed_t sp = baud_constant(baud);
    if (sp != 0) {
        if (::cfsetispeed(&tio, sp) != 0 || ::cfsetospeed(&tio, sp) != 0) {
            err = "cfsetspeed failed: " + std::string(std::strerror(errno));
            close();
            return false;
        }
    }
    if (::tcsetattr(fd_, TCSANOW, &tio) != 0) {
        err = "tcsetattr failed: " + std::string(std::strerror(errno));
        close();
        return false;
    }

    if (sp == 0) {
#if defined(__APPLE__)
        // macOS 没有 B2000000 之类常量，需用 IOSSIOSPEED 设置任意波特率
        speed_t custom = static_cast<speed_t>(baud);
        if (::ioctl(fd_, IOSSIOSPEED, &custom) == -1) {
            err = "IOSSIOSPEED failed: " + std::string(std::strerror(errno));
            close();
            return false;
        }
#else
        err = "unsupported baud rate";
        close();
        return false;
#endif
    }

    // 清 DTR/RTS，避免复位 Receiver_RX 板卡
    int bits = TIOCM_DTR | TIOCM_RTS;
    if (::ioctl(fd_, TIOCMBIC, &bits) != 0) {
        // 部分虚拟串口不支持调制解调器控制线，不视为致命错误
    }

    ::tcflush(fd_, TCIOFLUSH);
    return true;
}

void SerialPort::close() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}

long SerialPort::read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err) {
    if (fd_ < 0) { err = "port not open"; return -1; }

    pollfd pfd{};
    pfd.fd = fd_;
    pfd.events = POLLIN;
    const int pr = ::poll(&pfd, 1, timeout_ms);
    if (pr < 0) {
        if (errno == EINTR) return 0;
        err = "poll failed: " + std::string(std::strerror(errno));
        return -1;
    }
    if (pr == 0) return 0;
    if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        err = "port disconnected";
        return -1;
    }

    const ssize_t n = ::read(fd_, buf, cap);
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return 0;
        err = "read failed: " + std::string(std::strerror(errno));
        return -1;
    }
    return static_cast<long>(n);
}

}  // namespace telex
