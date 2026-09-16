#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <imu_761_ros2/msg/imu_package.hpp>
#include <imu_761_ros2/protocol.h>

#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <sys/ioctl.h>

#if defined(__linux__)
#include <asm/termbits.h>
#endif

static const size_t READ_BUF_SIZE = 512;

static std::string hex_byte(uint8_t v) {
    char buf[8];
    std::snprintf(buf, sizeof(buf), "0x%02X", v);
    return std::string(buf);
}

static std::string hex_u16(uint16_t v) {
    char buf[10];
    std::snprintf(buf, sizeof(buf), "0x%04X", v);
    return std::string(buf);
}

static void print_frame(const rclcpp::Logger &logger, const ImuData &d) {
    RCLCPP_INFO(logger, "=====  IMU frame OK =====");
    RCLCPP_INFO(logger, "status=0x%04X temp=%dC pressure=%.1fPa t=%ums",
                d.main_status, (int)d.temperature, d.pressure, d.timestamp_ms);
    RCLCPP_INFO(logger, "acc(G): %.4f %.4f %.4f", d.acc[0], d.acc[1], d.acc[2]);
    RCLCPP_INFO(logger, "gyr(deg/s): %.3f %.3f %.3f", d.gyr[0], d.gyr[1], d.gyr[2]);
    RCLCPP_INFO(logger, "eul(deg): roll=%.3f pitch=%.3f yaw=%.3f",
                d.eul[0], d.eul[1], d.eul[2]);
    RCLCPP_INFO(logger, "quat(WXYZ): %.4f %.4f %.4f %.4f",
                d.quat[0], d.quat[1], d.quat[2], d.quat[3]);
    RCLCPP_INFO(logger, "=========================");
}

static void rfu_to_flu_vec3(double xr, double yr, double zr,
                            double &xf, double &yf, double &zf) {
    xf = yr;
    yf = -xr;
    zf = zr;
}

static void rfu_to_flu_quat(double qw, double qx, double qy, double qz,
                            double &ow, double &ox, double &oy, double &oz) {
    const double s = 0.7071067811865476;
    const double fw = s, fx = 0.0, fy = 0.0, fz = -s;

    double tw, tx, ty, tz;
    tw = fw * qw - fx * qx - fy * qy - fz * qz;
    tx = fw * qx + fx * qw + fy * qz - fz * qy;
    ty = fw * qy - fx * qz + fy * qw + fz * qx;
    tz = fw * qz + fx * qy - fy * qx + fz * qw;

    ow = tw * fw - tx * fx - ty * fy - tz * (-fz);
    ox = tw * fx + tx * fw + ty * (-fz) - tz * fy;
    oy = tw * fy - tx * (-fz) + ty * fw + tz * fx;
    oz = tw * (-fz) + tx * fy - ty * fx + tz * fw;
}

static void fill_imu_msg(const ImuData &d,
                         const std::string &frame_id,
                         const rclcpp::Time &stamp,
                         sensor_msgs::msg::Imu &msg,
                         bool use_ros_flu) {
    msg.header.stamp = stamp;
    msg.header.frame_id = frame_id;

    const double DEG2RAD = M_PI / 180.0;
    const double G0 = 9.80665;

    double qw = d.quat[0], qx = d.quat[1], qy = d.quat[2], qz = d.quat[3];
    double gx = d.gyr[0] * DEG2RAD, gy = d.gyr[1] * DEG2RAD, gz = d.gyr[2] * DEG2RAD;
    double ax = d.acc[0] * G0, ay = d.acc[1] * G0, az = d.acc[2] * G0;

    if (use_ros_flu) {
        rfu_to_flu_vec3(gx, gy, gz, gx, gy, gz);
        rfu_to_flu_vec3(ax, ay, az, ax, ay, az);
        rfu_to_flu_quat(qw, qx, qy, qz, qw, qx, qy, qz);
    }

    msg.orientation.w = qw;
    msg.orientation.x = qx;
    msg.orientation.y = qy;
    msg.orientation.z = qz;

    msg.angular_velocity.x = gx;
    msg.angular_velocity.y = gy;
    msg.angular_velocity.z = gz;

    msg.linear_acceleration.x = ax;
    msg.linear_acceleration.y = ay;
    msg.linear_acceleration.z = az;

    for (int i = 0; i < 9; ++i) {
        msg.orientation_covariance[i] = 0;
        msg.angular_velocity_covariance[i] = 0;
        msg.linear_acceleration_covariance[i] = 0;
    }

}

static void fill_package_msg(const ImuData &d,
                             const std::string &frame_id,
                             const rclcpp::Time &stamp,
                             imu_761_ros2::msg::ImuPackage &msg,
                             bool use_ros_flu) {
    msg.header.stamp = stamp;
    msg.header.frame_id = frame_id;

    msg.tag = hex_byte(d.tag);
    msg.main_status = hex_u16(d.main_status);
    msg.temperature = d.temperature;
    msg.pressure = d.pressure;
    msg.timestamp_ms = d.timestamp_ms;

    double ax = d.acc[0], ay = d.acc[1], az = d.acc[2];
    double gx = d.gyr[0], gy = d.gyr[1], gz = d.gyr[2];
    double mx = d.mag[0], my = d.mag[1], mz = d.mag[2];
    double roll = d.eul[0], pitch = d.eul[1], yaw = d.eul[2];
    double qw = d.quat[0], qx = d.quat[1], qy = d.quat[2], qz = d.quat[3];

    if (use_ros_flu) {
        rfu_to_flu_vec3(ax, ay, az, ax, ay, az);
        rfu_to_flu_vec3(gx, gy, gz, gx, gy, gz);
        rfu_to_flu_vec3(mx, my, mz, mx, my, mz);
        pitch = -pitch;
        rfu_to_flu_quat(qw, qx, qy, qz, qw, qx, qy, qz);
    }

    msg.acc_x = ax;
    msg.acc_y = ay;
    msg.acc_z = az;
    msg.gyr_x = gx;
    msg.gyr_y = gy;
    msg.gyr_z = gz;
    msg.mag_x = mx;
    msg.mag_y = my;
    msg.mag_z = mz;
    msg.roll = roll;
    msg.pitch = pitch;
    msg.yaw = yaw;
    msg.quat_w = qw;
    msg.quat_x = qx;
    msg.quat_y = qy;
    msg.quat_z = qz;
}

static bool baud_allowed(int baud) {
    switch (baud) {
        case 4800:
        case 9600:
        case 115200:
        case 230400:
        case 256000:
        case 460800:
        case 921600:
        case 2500000:
            return true;
        default:
            return false;
    }
}

#if defined(__linux__)
static int set_baud_termios2(int fd, int baud) {
    struct termios2 tio;
    if (ioctl(fd, TCGETS2, &tio) != 0) {
        return -1;
    }

    tio.c_cflag &= ~(PARENB | CSTOPB | CSIZE | CBAUD);
    tio.c_cflag |= CS8 | CREAD | CLOCAL;
    tio.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    tio.c_iflag &= ~(IXON | IXOFF | IXANY | INLCR | ICRNL);
    tio.c_oflag &= ~(OPOST | ONLCR | OCRNL);
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 0;

    tio.c_cflag |= BOTHER;
    tio.c_ispeed = baud;
    tio.c_ospeed = baud;

    if (ioctl(fd, TCSETS2, &tio) != 0) {
        return -1;
    }
    return 0;
}
#endif

static int open_port(const rclcpp::Logger &logger,
                     const std::string &port_device,
                     int baud) {
    if (!baud_allowed(baud)) {
        RCLCPP_ERROR(logger, "Unsupported baud_rate: %d", baud);
        return -1;
    }

    int fd = open(port_device.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        RCLCPP_ERROR(logger, "open_port failed: %s (%s)",
                     port_device.c_str(), strerror(errno));
        return -1;
    }

#if defined(__linux__)
    if (set_baud_termios2(fd, baud) != 0) {
        RCLCPP_ERROR(logger, "set baud %d failed: %s", baud, strerror(errno));
        close(fd);
        return -1;
    }
#else
    RCLCPP_ERROR(logger, "Custom baud requires Linux termios2");
    close(fd);
    return -1;
#endif

    return fd;
}

static void read_and_publish(
    const rclcpp::Node::SharedPtr &node,
    int fd,
    struct pollfd *pfd,
    std::vector<uint8_t> &stream_buf,
    std::vector<uint8_t> &frame,
    int imu_data_show_flag,
    bool imu_enable,
    bool package_enable,
    const std::string &frame_id,
    const rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr &imu_pub,
    const rclcpp::Publisher<imu_761_ros2::msg::ImuPackage>::SharedPtr &package_pub,
    bool use_ros_flu) {

    int rpoll = poll(pfd, 1, 5);
    if (rpoll == 0) {
        return;
    }
    if (rpoll < 0) {
        if (errno == EINTR) {
            return;
        }
        RCLCPP_ERROR_THROTTLE(node->get_logger(), *node->get_clock(), 1000,
                              "poll failed: %s", strerror(errno));
        return;
    }

    uint8_t read_buf[READ_BUF_SIZE];
    ssize_t n = read(fd, read_buf, sizeof(read_buf));
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return;
        }
        RCLCPP_ERROR_THROTTLE(node->get_logger(), *node->get_clock(), 1000,
                              "read failed: %s", strerror(errno));
        return;
    }
    if (n == 0) {
        return;
    }

    stream_buf.insert(stream_buf.end(), read_buf, read_buf + n);

    while (try_extract_frame(stream_buf, frame)) {
        uint16_t payload_len = (uint16_t)(frame[2] | (frame[3] << 8));
        if (payload_len != IMU_PAYLOAD_LEN) {
            continue;
        }

        ImuData d;
        if (!parse_payload(frame.data() + 6, d)) {
            continue;
        }

        if (imu_data_show_flag) {
            print_frame(node->get_logger(), d);
        }

        const rclcpp::Time stamp = node->now();

        if (imu_enable && imu_pub) {
            sensor_msgs::msg::Imu imu_msg;
            fill_imu_msg(d, frame_id, stamp, imu_msg, use_ros_flu);
            imu_pub->publish(imu_msg);
        }
        if (package_enable && package_pub) {
            imu_761_ros2::msg::ImuPackage pkg_msg;
            fill_package_msg(d, frame_id, stamp, pkg_msg, use_ros_flu);
            package_pub->publish(pkg_msg);
        }
    }
}

int main(int argc, char **argv) {
    rclcpp::init(argc, argv);
    auto node = rclcpp::Node::make_shared("imu_761_node");

    const std::string port = node->declare_parameter<std::string>("port", "/dev/ttyUSB0");
    const int baud_rate = node->declare_parameter<int>("baud_rate", 115200);
    const int imu_data_show_flag = node->declare_parameter<int>("imu_data_show_flag", 0);
    const std::string frame_id = node->declare_parameter<std::string>("frame_id", "imu_link");
    const bool imu_enable = node->declare_parameter<bool>("imu_enable", true);
    const bool package_enable = node->declare_parameter<bool>("package_enable", true);
    const std::string imu_topic = node->declare_parameter<std::string>("imu_topic", "imu/data");
    const std::string package_topic = node->declare_parameter<std::string>("package_topic", "imu/package");
    const bool use_ros_flu = node->declare_parameter<bool>("use_ros_flu", true);

    RCLCPP_INFO(node->get_logger(), "port = %s", port.c_str());
    RCLCPP_INFO(node->get_logger(), "baud_rate = %d", baud_rate);
    RCLCPP_INFO(node->get_logger(), "frame_id = %s", frame_id.c_str());
    RCLCPP_INFO(node->get_logger(), "imu_enable = %d, imu_topic = %s",
                imu_enable, imu_topic.c_str());
    RCLCPP_INFO(node->get_logger(), "package_enable = %d, package_topic = %s",
                package_enable, package_topic.c_str());
    RCLCPP_INFO(node->get_logger(), "use_ros_flu = %d (1=ROS FLU, 0=device RFU)", use_ros_flu);

    rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr imu_pub;
    rclcpp::Publisher<imu_761_ros2::msg::ImuPackage>::SharedPtr package_pub;

    if (imu_enable) {
        imu_pub = node->create_publisher<sensor_msgs::msg::Imu>(imu_topic, 10);
    }
    if (package_enable) {
        package_pub = node->create_publisher<imu_761_ros2::msg::ImuPackage>(package_topic, 10);
    }
    if (!imu_enable && !package_enable) {
        RCLCPP_ERROR(node->get_logger(),
                     "Both imu_enable and package_enable are false, nothing to publish.");
        rclcpp::shutdown();
        return -1;
    }

    const int fd = open_port(node->get_logger(), port, baud_rate);
    if (fd < 0) {
        rclcpp::shutdown();
        return -1;
    }
    RCLCPP_INFO(node->get_logger(), "Serial open success!");

    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = POLLIN;

    std::vector<uint8_t> stream_buf;
    std::vector<uint8_t> frame;

    while (rclcpp::ok()) {
        read_and_publish(node, fd, &pfd, stream_buf, frame,
                         imu_data_show_flag, imu_enable, package_enable,
                         frame_id, imu_pub, package_pub, use_ros_flu);
        rclcpp::spin_some(node);
    }

    close(fd);
    rclcpp::shutdown();
    return 0;
}