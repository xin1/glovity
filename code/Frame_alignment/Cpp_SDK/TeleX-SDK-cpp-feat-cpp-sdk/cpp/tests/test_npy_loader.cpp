// 校准文件读取：对拍金标准 + 恶意/畸形输入必须被拒绝。
#include "../src/file_util.hpp"
#include "../src/npy_loader.hpp"
#include "json_mini.hpp"
#include "test_util.hpp"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using namespace telex;

namespace {

void write_file(const std::string& path, const std::string& data) {
    FILE* f = open_file(path, "wb");
    if (f) { std::fwrite(data.data(), 1, data.size(), f); std::fclose(f); }
}

std::string read_file(const std::string& path) {
    FILE* f = open_file(path, "rb");
    if (!f) return {};
    std::string out; char buf[8192]; size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) out.append(buf, n);
    std::fclose(f);
    return out;
}

std::string tmp(const char* name) {
    return std::string(TELEX_TEST_TMP_DIR) + "/telex_npy_test_" + name;
}

void test_golden(const char* side) {
    const std::string npy = tt::golden(std::string("imu_calibration_") + side + ".npy");
    const std::string json = tt::golden(std::string("calib_") + side + ".json");

    CalibFile cf; std::string err;
    const bool ok = load_calib_file(npy, cf, err);
    tt::check(ok, std::string(side) + ": 读取原生 .npy 成功 (err=" + err + ")");
    if (!ok) return;

    tt::check(cf.side_known, std::string(side) + ": side 已从文件读出");
    tt::check(cf.side == side, std::string(side) + ": side 值正确, got=" + cf.side);

    const auto root = jmini::load_file(json);
    const auto& want = (*root)["imu_calib_quats_xyzw"];
    for (int p = 0; p < CALIB_POSES; ++p)
        for (int s = 0; s < CALIB_SLOTS; ++s)
            for (int c = 0; c < 4; ++c)
                tt::close(cf.quats[p][s][c], want[p][s][c].d(), 0.0,
                          std::string(side) + ".quats[" + std::to_string(p) + "][" +
                          std::to_string(s) + "][" + std::to_string(c) + "]");
}

// 转换脚本产物 (.tgc) 与原生 .npy 必须给出完全相同的数值
void test_tgc_roundtrip() {
    CalibFile from_npy; std::string err;
    if (!load_calib_file(tt::golden("imu_calibration_left.npy"), from_npy, err)) {
        tt::check(false, "tgc 前置: 读原生 npy 失败");
        return;
    }
    std::string blob = "TGCAL1\0\0"; blob.resize(8);
    blob += 'l';
    blob.append(reinterpret_cast<const char*>(&from_npy.quats[0][0][0]),
                sizeof(from_npy.quats));
    const std::string path = tmp("roundtrip.tgc");
    write_file(path, blob);

    CalibFile from_tgc;
    tt::check(load_calib_file(path, from_tgc, err), "读取 .tgc 成功 (err=" + err + ")");
    tt::check(from_tgc.side == "left" && from_tgc.side_known, ".tgc side 正确");
    bool same = true;
    for (int p = 0; p < CALIB_POSES; ++p)
        for (int s = 0; s < CALIB_SLOTS; ++s)
            for (int c = 0; c < 4; ++c)
                if (from_tgc.quats[p][s][c] != from_npy.quats[p][s][c]) same = false;
    tt::check(same, ".tgc 与 .npy 数值逐位一致");
    std::remove(path.c_str());
}

void expect_reject(const std::string& blob, const char* what) {
    const std::string path = tmp("reject.bin");
    write_file(path, blob);
    CalibFile cf; std::string err;
    const bool ok = load_calib_file(path, cf, err);
    tt::check(!ok, std::string("必须拒绝: ") + what);
    tt::check(!err.empty(), std::string("拒绝时必须给出诊断: ") + what);
    std::remove(path.c_str());
}

void test_malicious() {
    const std::string good = read_file(tt::golden("imu_calibration_left.npy"));
    tt::check(!good.empty(), "前置: 金标准 npy 非空");

    expect_reject("", "空文件");
    expect_reject("not a npy at all", "非 npy 文件");
    expect_reject(good.substr(0, 64), "截断的 npy 头");
    expect_reject(good.substr(0, good.size() / 2), "截断的 pickle 流");

    // 头部声明超大 header 长度
    {
        std::string b = good;
        b[8] = static_cast<char>(0xFF); b[9] = static_cast<char>(0xFF);
        expect_reject(b, "header 长度越界");
    }
    // 注入一个不在白名单里的 global（os.system）
    {
        std::string b = good;
        const size_t at = b.find("numpy._core.multiarray");
        if (at != std::string::npos) {
            // 同长度替换，保持流结构，只把模块名改成不允许的
            const std::string evil = "os.system............";
            b.replace(at, evil.size(), evil);
            expect_reject(b, "非白名单 global");
        } else {
            tt::check(false, "前置: 未找到 numpy._core.multiarray");
        }
    }
    // 超大文件
    {
        std::string b(2u << 20, '\0');
        std::memcpy(&b[0], "\x93NUMPY", 6);
        expect_reject(b, "超过大小上限");
    }
    // 形状不对的 plain npy
    {
        std::string hdr = "\x93NUMPY";
        hdr.resize(6);
        hdr += '\x01'; hdr += '\x00';
        std::string dict = "{'descr': '<f8', 'fortran_order': False, 'shape': (2, 16, 4), }";
        dict.resize(118, ' '); dict[117] = '\n';
        hdr += static_cast<char>(dict.size() & 0xFF);
        hdr += static_cast<char>((dict.size() >> 8) & 0xFF);
        hdr += dict;
        hdr.append(2 * 16 * 4 * 8, '\0');
        expect_reject(hdr, "plain npy 形状不符");
    }
}

// pickle protocol 2 / numpy.core.multiarray（numpy 1.x 工具产出）必须与
// protocol 4 原生文件给出逐位相同的数值。
void test_proto2_equivalence() {
    CalibFile p4, p2;
    std::string e4, e2;
    const bool ok4 = load_calib_file(tt::golden("imu_calibration_left.npy"), p4, e4);
    const bool ok2 = load_calib_file(tt::golden("imu_calibration_left_proto2.npy"), p2, e2);
    tt::check(ok4, "proto4 读取成功 (" + e4 + ")");
    tt::check(ok2, "proto2 读取成功 (" + e2 + ")");
    if (!ok4 || !ok2) return;

    tt::check(p2.side == p4.side && p2.side_known, "proto2 side 一致");
    bool same = true;
    for (int p = 0; p < CALIB_POSES; ++p)
        for (int s = 0; s < CALIB_SLOTS; ++s)
            for (int c = 0; c < 4; ++c)
                if (p2.quats[p][s][c] != p4.quats[p][s][c]) same = false;
    tt::check(same, "proto2 与 proto4 数值逐位一致");
}

}  // namespace

int main() {
    test_golden("left");
    test_golden("right");
    test_proto2_equivalence();
    test_tgc_roundtrip();
    test_malicious();
    return tt::report("npy_loader");
}
