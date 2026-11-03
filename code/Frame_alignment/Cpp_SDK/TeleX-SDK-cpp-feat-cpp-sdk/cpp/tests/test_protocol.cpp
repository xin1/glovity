// 协议层对拍金标准 + 重同步状态机的模糊/边界测试。
#include "../src/protocol.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <cstdio>
#include <random>
#include <string>
#include <vector>

using namespace telex;

namespace {

std::vector<uint8_t> hex_to_bytes(const std::string& hex) {
    std::vector<uint8_t> out;
    out.reserve(hex.size() / 2);
    auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i + 1 < hex.size(); i += 2)
        out.push_back(static_cast<uint8_t>(nib(hex[i]) * 16 + nib(hex[i + 1])));
    return out;
}

void test_golden() {
    const auto root = jmini::load_file(tt::golden("protocol.json"));
    tt::check(static_cast<int>((*root)["frame_size"].d()) == FRAME_SIZE, "frame_size==168");

    const auto& cases = (*root)["cases"];
    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i];
        const std::string tag = "case[" + std::to_string(i) + "]";
        const auto bytes = hex_to_bytes(c["frame_hex"].s());
        tt::check(bytes.size() == static_cast<size_t>(FRAME_SIZE), tag + ".size");

        // 1) 我们构造的帧必须与 Python build_test_frame 逐字节一致
        const int hand = (c["hand"].s() == "left") ? HAND_LEFT : HAND_RIGHT;
        const auto& q = c["quat_wxyz_in"];
        const auto mine = build_test_frame(
            hand, static_cast<uint16_t>(c["frame_id_in"].d()),
            QuatWXYZ{q[0].d(), q[1].d(), q[2].d(), q[3].d()});
        bool same = mine.size() == bytes.size();
        for (size_t b = 0; same && b < bytes.size(); ++b) same = (mine[b] == bytes[b]);
        tt::check(same, tag + ".build_test_frame 逐字节一致");
        tt::check(mine[FRAME_SIZE - 1] == static_cast<uint8_t>(c["checksum"].d()),
                  tag + ".checksum");

        // 2) 解析结果对拍
        RawFrame f;
        tt::check(parse_frame(bytes.data(), bytes.size(), f), tag + ".parse 成功");
        const auto& p = c["parsed"];
        tt::check(f.frame_id == static_cast<uint16_t>(p["frame_id"].d()), tag + ".frame_id");
        tt::check(f.hand_id == hand, tag + ".hand");
        tt::check(f.bat_percent == static_cast<int>(p["bat_percent"].d()), tag + ".bat");

        const auto& wq = p["imu_quats_wxyz"];
        for (int k = 0; k < IMU_COUNT; ++k) {
            tt::close(f.imu[k].w, wq[k][0].d(), 1e-15, tag + ".imu" + std::to_string(k) + ".w");
            tt::close(f.imu[k].x, wq[k][1].d(), 1e-15, tag + ".imu" + std::to_string(k) + ".x");
            tt::close(f.imu[k].y, wq[k][2].d(), 1e-15, tag + ".imu" + std::to_string(k) + ".y");
            tt::close(f.imu[k].z, wq[k][3].d(), 1e-15, tag + ".imu" + std::to_string(k) + ".z");
        }
        for (int k = 0; k < 3; ++k) {
            tt::close(f.wrist_euler_deg[k], p["wrist_angle_deg"][k].d(), 1e-15,
                      tag + ".euler" + std::to_string(k));
            tt::close(f.wrist_gyro_dps[k], p["wrist_gyro_dps"][k].d(), 1e-15,
                      tag + ".gyro" + std::to_string(k));
        }
    }
}

void test_reject() {
    auto good = build_test_frame(HAND_LEFT, 1, QuatWXYZ{1, 0, 0, 0});
    RawFrame f;
    tt::check(parse_frame(good.data(), good.size(), f), "合法帧应通过");

    auto bad = good; bad[0] = 0x00;
    tt::check(!parse_frame(bad.data(), bad.size(), f), "magic 错应拒绝");
    bad = good; bad[2] = 7;
    tt::check(!parse_frame(bad.data(), bad.size(), f), "hand_id 非法应拒绝");
    bad = good; bad[3] = 10;
    tt::check(!parse_frame(bad.data(), bad.size(), f), "imu_count 错应拒绝");
    bad = good; bad[FRAME_SIZE - 1] ^= 0xFF;
    tt::check(!parse_frame(bad.data(), bad.size(), f), "校验和错应拒绝");
    tt::check(!parse_frame(good.data(), good.size() - 1, f), "长度不足应拒绝");
}

void test_v2() {
    const uint64_t timestamp = 1234567890123ULL;
    const uint16_t fail_mask = static_cast<uint16_t>(1u << 3);
    auto good = build_test_frame_v2(HAND_RIGHT, 65530, QuatWXYZ{1, 0, 0, 0},
                                    timestamp, true, 88, fail_mask, true);
    tt::check(good.size() == static_cast<size_t>(V2_FRAME_SIZE), "V2 size=180");
    RawFrame f;
    tt::check(parse_frame(good.data(), good.size(), f), "V2合法帧应通过");
    tt::check(f.protocol == PROTOCOL_V2_180 && f.protocol_version == 2, "V2协议标识");
    tt::check(f.hand_id == HAND_RIGHT && f.frame_id == 65530, "V2手别和序号");
    tt::check(f.device_timestamp_us == timestamp, "V2设备时间戳");
    tt::check(f.recording && f.wrist_gyro_valid, "V2 flags");
    tt::check(f.imu_fail_mask == fail_mask, "V2失败掩码");
    tt::check(f.has_device_timestamp && f.has_recording_flag && f.has_fail_mask,
              "V2能力标志");
    tt::check(f.raw_size == V2_FRAME_SIZE && f.raw[0] == 0xAA && f.raw[179] == good[179],
              "V2保留原始帧");
    auto bad = good; bad[178] ^= 1;
    tt::check(!parse_frame(bad.data(), bad.size(), f), "V2拒绝坏CRC");

    FrameSync sync;
    const auto legacy = build_test_frame(HAND_LEFT, 7, QuatWXYZ{1, 0, 0, 0});
    std::vector<uint8_t> mixed{0x00, 0xAA, 0x01};
    mixed.insert(mixed.end(), good.begin(), good.end());
    mixed.insert(mixed.end(), legacy.begin(), legacy.end());
    sync.feed(mixed.data(), mixed.size());
    tt::check(sync.next(f) && f.protocol == PROTOCOL_V2_180 && f.frame_id == 65530,
              "流同步取出V2");
    tt::check(sync.next(f) && f.protocol == PROTOCOL_LEGACY_168 && f.frame_id == 7,
              "流同步继续取出legacy");
}

// 重同步：把 N 帧混入随机垃圾、按随机分片喂入，必须恰好取回 N 帧且内容正确。
void test_resync_fuzz() {
    std::mt19937 rng(20260905);
    std::uniform_int_distribution<int> byte_d(0, 255);
    std::uniform_int_distribution<int> gap_d(0, 40);
    std::uniform_int_distribution<int> chunk_d(1, 300);

    const int N = 60;
    std::vector<uint8_t> stream;
    std::vector<uint16_t> expect_ids;
    for (int i = 0; i < N; ++i) {
        const int gap = gap_d(rng);
        for (int g = 0; g < gap; ++g) {
            uint8_t b = static_cast<uint8_t>(byte_d(rng));
            // 避免随机生成完整合法帧（概率极低，但保证确定性）
            stream.push_back(b);
        }
        const int hand = (i % 2) ? HAND_RIGHT : HAND_LEFT;
        const auto fr = build_test_frame(hand, static_cast<uint16_t>(i + 1),
                                         QuatWXYZ{1, 0, 0, 0});
        stream.insert(stream.end(), fr.begin(), fr.end());
        expect_ids.push_back(static_cast<uint16_t>(i + 1));
    }

    FrameSync sync;
    std::vector<uint16_t> got_ids;
    size_t pos = 0;
    while (pos < stream.size()) {
        const size_t n = std::min<size_t>(chunk_d(rng), stream.size() - pos);
        sync.feed(stream.data() + pos, n);
        pos += n;
        RawFrame f;
        while (sync.next(f)) got_ids.push_back(f.frame_id);
    }

    tt::check(got_ids.size() == expect_ids.size(),
              "重同步取回帧数: got=" + std::to_string(got_ids.size()) +
              " want=" + std::to_string(expect_ids.size()));
    const size_t n = std::min(got_ids.size(), expect_ids.size());
    bool ok = true;
    for (size_t i = 0; i < n; ++i) if (got_ids[i] != expect_ids[i]) ok = false;
    tt::check(ok, "重同步帧序号顺序一致");

    // 缓冲区不得无限增长
    tt::check(sync.buffered() <= static_cast<size_t>(FRAME_SIZE), "无 magic 时缓冲有界");
}

// 截断帧：只喂半帧，不得产出，也不得丢弃后续补齐的数据。
void test_truncated() {
    FrameSync sync;
    const auto fr = build_test_frame(HAND_RIGHT, 999, QuatWXYZ{0, 1, 0, 0});
    sync.feed(fr.data(), 100);
    RawFrame f;
    tt::check(!sync.next(f), "半帧不应产出");
    sync.feed(fr.data() + 100, fr.size() - 100);
    tt::check(sync.next(f), "补齐后应产出");
    tt::check(f.frame_id == 999, "截断重组后 frame_id 正确");
    tt::check(f.hand_id == HAND_RIGHT, "截断重组后 hand 正确");
}

}  // namespace

int main() {
    test_golden();
    test_reject();
    test_v2();
    test_resync_fuzz();
    test_truncated();
    return tt::report("protocol");
}
