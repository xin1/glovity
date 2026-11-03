// 公开 API 测试：链接最终 .so，验证交付物本身。
// 覆盖 C ABI 滥用防护、纯函数路径的金标准、file:// 回放端到端、非 ASCII 路径。
#include <telex_glove.h>
#include <telex_glove.hpp>

#include "../src/file_util.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <cstring>
#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

namespace {

uint16_t test_crc16(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]) << 8;
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                                 : static_cast<uint16_t>(crc << 1);
    }
    return crc;
}

std::vector<uint8_t> public_v2_frame() {
    std::vector<uint8_t> b(180, 0);
    b[0] = 0xAA; b[1] = 0x55; b[2] = 2; b[3] = 1; b[4] = 3; b[5] = 88;
    b[6] = 0x34; b[7] = 0x12;
    const uint64_t ts = 1234567890123ULL;
    for (unsigned i = 0; i < 8; ++i) b[8 + i] = static_cast<uint8_t>(ts >> (8 * i));
    b[16] = 0x08;  // IMU 3 failed
    for (int imu = 0; imu < 11; ++imu) {
        const size_t q0 = 18 + static_cast<size_t>(imu) * 14 + 6;
        b[q0 + 1] = 0x7F;  // w=32512/32768, enough for API metadata test
    }
    const uint16_t crc = test_crc16(b.data(), 178);
    b[178] = static_cast<uint8_t>(crc);
    b[179] = static_cast<uint8_t>(crc >> 8);
    return b;
}

telex_glove_snapshot fresh() {
    telex_glove_snapshot s;
    std::memset(&s, 0, sizeof(s));
    s.struct_size = static_cast<uint32_t>(sizeof(s));
    return s;
}

// ---- C ABI 滥用：任何组合都必须返回错误码而非崩溃 ----
void test_abuse() {
    tt::check(telex_glove_abi_version() == 3u, "ABI version=3");
    telex_glove_hub* hub = nullptr;

    tt::check(telex_glove_hub_create(nullptr, 0, &hub) == TELEX_GLOVE_E_INVALID_ARG,
              "create(NULL port) 应拒绝");
    tt::check(telex_glove_hub_create("", 0, &hub) == TELEX_GLOVE_E_INVALID_ARG,
              "create(空 port) 应拒绝");
    tt::check(telex_glove_hub_create("/dev/ttyACM0", 0, nullptr) == TELEX_GLOVE_E_INVALID_ARG,
              "create(NULL out) 应拒绝");
    tt::check(telex_glove_hub_start(nullptr, 1.0) == TELEX_GLOVE_E_INVALID_ARG,
              "start(NULL) 应拒绝");

    // NULL 的销毁/停止必须是安全空操作
    telex_glove_hub_stop(nullptr);
    telex_glove_hub_destroy(nullptr);
    tt::check(true, "NULL 上的 stop/destroy 不崩溃");

    const std::string spec = "file://" + tt::golden("sample_capture.bin");
    tt::check(telex_glove_hub_create(spec.c_str(), 0, &hub) == TELEX_GLOVE_OK,
              "create(file://) 成功");
    if (hub == nullptr) return;

    telex_glove_snapshot snap = fresh();
    tt::check(telex_glove_hub_latest(hub, TELEX_GLOVE_HAND_LEFT, &snap) ==
                  TELEX_GLOVE_E_NOT_STARTED,
              "未 start 取数应返回 NOT_STARTED");

    // struct_size 不符必须被拒（前向兼容保护）
    telex_glove_snapshot bad = fresh();
    bad.struct_size = 4;
    tt::check(telex_glove_hub_latest(hub, TELEX_GLOVE_HAND_LEFT, &bad) ==
                  TELEX_GLOVE_E_INVALID_ARG,
              "struct_size 不符应拒绝");
    tt::check(telex_glove_hub_latest(hub, TELEX_GLOVE_HAND_LEFT, nullptr) ==
                  TELEX_GLOVE_E_INVALID_ARG,
              "NULL 输出缓冲应拒绝");

    // 非法 hand
    tt::check(telex_glove_hub_latest(hub, static_cast<telex_glove_hand>(9), &snap) ==
                  TELEX_GLOVE_E_INVALID_ARG,
              "非法 hand 应拒绝");

    // 运行中直接 destroy（不先 stop）必须安全
    tt::check(telex_glove_hub_start(hub, 1.0) == TELEX_GLOVE_OK, "file:// start 成功");
    telex_glove_hub_destroy(hub);
    tt::check(true, "运行中 destroy 不崩溃、不泄漏线程");
}

// ---- 纯函数路径走公开 API 对拍金标准 ----
void test_pure_functions() {
    const auto root = jmini::load_file(tt::golden("fk_uncalib.json"));
    const double tol = (*root)["tol"].d();
    const auto& cases = (*root)["cases"];

    int checked = 0;
    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i];
        const auto& qs = c["imu_quats_wxyz"];
        if (qs.size() != TELEX_GLOVE_IMU_COUNT) continue;   // 跳过短帧用例
        const telex_glove_hand hand = (c["side"].s() == "right") ? TELEX_GLOVE_HAND_RIGHT
                                                                 : TELEX_GLOVE_HAND_LEFT;
        telex_glove_quat imu[TELEX_GLOVE_IMU_COUNT];
        for (int k = 0; k < TELEX_GLOVE_IMU_COUNT; ++k)
            imu[k] = telex_glove_quat{qs[k][0].d(), qs[k][1].d(), qs[k][2].d(), qs[k][3].d()};

        telex_glove_vec3 joints[TELEX_GLOVE_JOINT_COUNT];
        tt::check(telex_glove_compute_joints(imu, hand, nullptr, joints) == TELEX_GLOVE_OK,
                  c["name"].s() + ": compute_joints 成功");

        const auto& want = c["joints"];
        for (int j = 0; j < TELEX_GLOVE_JOINT_COUNT; ++j) {
            tt::close(joints[j].x, want[j][0].d(), tol, c["name"].s() + ".x" + std::to_string(j));
            tt::close(joints[j].y, want[j][1].d(), tol, c["name"].s() + ".y" + std::to_string(j));
            tt::close(joints[j].z, want[j][2].d(), tol, c["name"].s() + ".z" + std::to_string(j));
        }

        telex_glove_quat rotations[TELEX_GLOVE_JOINT_ROTATION_COUNT];
        tt::check(telex_glove_compute_joint_local_rotations(
                      imu, hand, nullptr, rotations) == TELEX_GLOVE_OK,
                  c["name"].s() + ": compute_joint_local_rotations 成功");
        tt::close(rotations[TELEX_GLOVE_ROT_WRIST].w, 1.0, 1e-14,
                  c["name"].s() + ": wrist.w=1");
        tt::close(rotations[TELEX_GLOVE_ROT_WRIST].x, 0.0, 1e-14,
                  c["name"].s() + ": wrist.x=0");
        for (int j = 0; j < TELEX_GLOVE_JOINT_ROTATION_COUNT; ++j) {
            const auto& q = rotations[j];
            const double norm = std::sqrt(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z);
            tt::close(norm, 1.0, 1e-12,
                      c["name"].s() + ": rotation norm " + std::to_string(j));
        }
        ++checked;
    }
    tt::check(checked >= 8, "已覆盖 " + std::to_string(checked) + " 个姿势");
}

// ---- parse_frame 走公开 API ----
void test_parse_frame() {
    const auto root = jmini::load_file(tt::golden("protocol.json"));
    const auto& cases = (*root)["cases"];
    const auto& c = cases[0];
    const std::string hex = c["frame_hex"].s();
    std::vector<uint8_t> bytes;
    auto nib = [](char ch) { return ch <= '9' ? ch - '0' : (ch | 32) - 'a' + 10; };
    for (size_t i = 0; i + 1 < hex.size(); i += 2)
        bytes.push_back(static_cast<uint8_t>(nib(hex[i]) * 16 + nib(hex[i + 1])));

    telex_glove_snapshot s = fresh();
    tt::check(telex_glove_parse_frame(bytes.data(), bytes.size(), &s) == TELEX_GLOVE_OK,
              "parse_frame 成功");
    tt::check(s.frame_id == static_cast<uint32_t>(c["parsed"]["frame_id"].d()),
              "parse_frame frame_id 正确");
    tt::close(s.joints[0].x, 0.0, 0.0, "parse_frame joints[0] 为原点");

    // 畸形输入
    bytes[0] = 0;
    tt::check(telex_glove_parse_frame(bytes.data(), bytes.size(), &s) != TELEX_GLOVE_OK,
              "parse_frame 拒绝坏 magic");
    tt::check(telex_glove_parse_frame(nullptr, 168, &s) == TELEX_GLOVE_E_INVALID_ARG,
              "parse_frame 拒绝 NULL");

    const auto v2 = public_v2_frame();
    s = fresh();
    tt::check(telex_glove_parse_frame(v2.data(), v2.size(), &s) == TELEX_GLOVE_OK,
              "公开API解析V2成功");
    tt::check(s.protocol == TELEX_GLOVE_PROTOCOL_V2_180 && s.protocol_version == 2,
              "公开API V2协议字段");
    tt::check(s.frame_id == 0x1234 && s.device_timestamp_us == 1234567890123ULL,
              "公开API V2序号和设备时间");
    tt::check(s.recording == 1 && s.wrist_gyro_valid == 1 && s.imu_fail_mask == 0x08,
              "公开API V2 flags和失败掩码");
    tt::check(s.has_device_timestamp && s.has_recording_flag && s.has_fail_mask,
              "公开API V2能力标志");
    tt::check(s.joints_valid == 0 && s.raw_frame_size == 180 && s.raw_frame[179] == v2[179],
              "公开API失败时禁用关节并保留原始帧");
    tt::check(s.joint_local_rotations_valid == 0,
              "公开API失败时禁用父子局部旋转");
}

// ---- file:// 回放端到端：跑通 C++ 糖层的完整生命周期 ----
void test_replay_end_to_end() {
    const std::string spec = "file://" + tt::golden("sample_capture.bin");
    try {
        telex::GloveHub hub(spec);
        hub.set_calibration(telex::Hand::Left, tt::golden("imu_calibration_left.npy"));
        hub.start(3.0);

        int got_left = 0, got_right = 0;
        bool calibrated_seen = false;
        for (int i = 0; i < 60; ++i) {
            telex::Snapshot s;
            if (hub.try_wait_next(telex::Hand::Left, 0.5, s)) {
                ++got_left;
                calibrated_seen = calibrated_seen || s.calibrated;
                tt::close(s.joints[0].x, 0.0, 0.0, "回放: 腕部 x 为 0");
                tt::close(s.joints[0].y, 0.0, 0.0, "回放: 腕部 y 为 0");
                tt::close(s.joints[0].z, 0.0, 0.0, "回放: 腕部 z 为 0");
                tt::check(s.joint_local_rotations_valid, "回放: 父子局部旋转有效");
                tt::close(s.joint_local_rotations[0].w, 1.0, 1e-14,
                          "回放: 腕部局部旋转为单位元");
            }
            if (hub.try_wait_next(telex::Hand::Right, 0.5, s)) ++got_right;
        }
        tt::check(got_left  > 0, "回放取到左手帧: " + std::to_string(got_left));
        tt::check(got_right > 0, "回放取到右手帧: " + std::to_string(got_right));
        tt::check(calibrated_seen, "回放中校准已生效");

        const telex::Stats st = hub.stats(telex::Hand::Left);
        tt::check(st.rx_total > 0, "统计 rx_total > 0");
        tt::check(st.online, "统计 online 为真");
        hub.stop();
    } catch (const telex::Error& e) {
        tt::check(false, std::string("回放抛异常: ") + e.what());
    }
}

// ---- 非 ASCII 路径：对外约定路径为 UTF-8 ----
// Windows 上窄字符 fopen 走 ANSI 代码页，UTF-8 中文路径会打不开；
// 集成方的工程目录、校准文件名含中文很常见，必须覆盖。
bool copy_file(const std::string& from, const std::string& to) {
    std::FILE* in = telex::open_file(from, "rb");
    if (!in) return false;
    std::FILE* out = telex::open_file(to, "wb");
    if (!out) { std::fclose(in); return false; }
    char buf[8192];
    size_t n;
    bool ok = true;
    while ((n = std::fread(buf, 1, sizeof(buf), in)) > 0)
        ok = ok && std::fwrite(buf, 1, n, out) == n;
    std::fclose(in);
    std::fclose(out);
    return ok;
}

void test_non_ascii_paths() {
    const std::string dir = TELEX_TEST_TMP_DIR;
    const std::string capture = dir + "/回放_录制流.bin";
    const std::string calib = dir + "/校准_左手.npy";
    if (!copy_file(tt::golden("sample_capture.bin"), capture) ||
        !copy_file(tt::golden("imu_calibration_left.npy"), calib)) {
        tt::check(false, "非 ASCII 路径: 准备测试文件失败");
        return;
    }
    // 用与 SDK 无关的标准库实现独立确认文件名确实是那几个汉字。否则若 SDK 把
    // 路径按错误的代码页转换，写入与读取会"错得一致"，上面的读写测试照样能过。
    tt::check(std::filesystem::exists(std::filesystem::u8path(capture)),
              "非 ASCII 路径: 文件按 UTF-8 文件名落盘");

    try {
        telex::GloveHub hub("file://" + capture);
        hub.set_calibration(telex::Hand::Left, calib);
        hub.start(3.0);
        bool calibrated_seen = false;
        for (int i = 0; i < 60 && !calibrated_seen; ++i) {
            telex::Snapshot s;
            if (hub.try_wait_next(telex::Hand::Left, 0.5, s)) calibrated_seen = s.calibrated;
        }
        tt::check(calibrated_seen, "非 ASCII 路径: 回放出数且校准生效");
        hub.stop();
    } catch (const telex::Error& e) {
        tt::check(false, std::string("非 ASCII 路径抛异常: ") + e.what());
    }
}

// ---- 元信息 ----
void test_metadata() {
    tt::check(std::string(telex_glove_version()).size() > 0, "version 非空");
    tt::check(telex_glove_abi_version() == 3u, "abi_version == 3");
    tt::check(telex_glove_joint_name(0) != nullptr, "joint_name(0) 非空");
    tt::check(telex_glove_joint_name(20) != nullptr, "joint_name(20) 非空");
    tt::check(telex_glove_joint_name(21) == nullptr, "joint_name(21) 越界返回 NULL");
    tt::check(telex_glove_joint_name(-1) == nullptr, "joint_name(-1) 越界返回 NULL");
    tt::check(telex_glove_imu_name(10) != nullptr, "imu_name(10) 非空");
    tt::check(telex_glove_imu_name(11) == nullptr, "imu_name(11) 越界返回 NULL");
    tt::check(std::string(telex_glove_status_string(TELEX_GLOVE_OK)) == "ok", "status_string");
}

}  // namespace

int main() {
    test_abuse();
    test_pure_functions();
    test_parse_frame();
    test_replay_end_to_end();
    test_non_ascii_paths();
    test_metadata();
    return tt::report("public_api");
}
