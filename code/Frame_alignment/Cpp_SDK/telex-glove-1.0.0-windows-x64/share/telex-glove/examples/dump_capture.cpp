// 把一段录制流逐帧解算并输出为 CSV，用于离线核对与跨实现对拍。
//
// 与 Hub 不同，这里是**确定性**的逐帧处理：不涉及线程、不丢帧、不受时序影响，
// 因此同一份输入永远得到同一份输出，适合做回归基线与一致性比对。
//
// 只使用公开 C API，可作为集成方做离线批处理的范例。
//
//   ./dump_capture capture.bin [左手校准.npy] [右手校准.npy] > joints.csv
//
// 输出列: frame_index,hand,frame_id,battery,j0x,j0y,j0z,j1x,...,j20z
#include <telex_glove.h>

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <fcntl.h>
#  include <io.h>
#endif

namespace {

constexpr size_t kFrameSize = 168;

std::vector<uint8_t> read_file(const char* path) {
    std::vector<uint8_t> data;
    FILE* f = std::fopen(path, "rb");
    if (!f) return data;
    std::fseek(f, 0, SEEK_END);
    const long sz = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (sz > 0) {
        data.resize(static_cast<size_t>(sz));
        if (std::fread(data.data(), 1, data.size(), f) != data.size()) data.clear();
    }
    std::fclose(f);
    return data;
}

}  // namespace

int main(int argc, char** argv) {
#if defined(_WIN32)
    SetConsoleOutputCP(CP_UTF8);         // 提示信息含中文
    _setmode(_fileno(stdout), _O_BINARY); // CSV 统一用 \n 换行，与 Linux/macOS 产物逐字节可比
#endif
    if (argc < 2) {
        std::fprintf(stderr,
                     "用法: dump_capture <录制流> [左手校准.npy] [右手校准.npy]\n");
        return 2;
    }
    const char* calib_left  = (argc > 2 && argv[2][0]) ? argv[2] : nullptr;
    const char* calib_right = (argc > 3 && argv[3][0]) ? argv[3] : nullptr;

    const std::vector<uint8_t> data = read_file(argv[1]);
    if (data.empty()) {
        std::fprintf(stderr, "读不到录制流: %s\n", argv[1]);
        return 1;
    }

    std::printf("frame_index,hand,frame_id,battery");
    for (int j = 0; j < TELEX_GLOVE_JOINT_COUNT; ++j)
        std::printf(",j%dx,j%dy,j%dz", j, j, j);
    std::printf("\n");

    // 最简帧同步：找 magic，交给 telex_glove_parse_frame 做完整校验。
    // 校验失败只前进 1 字节，与 SDK 内部的重同步策略一致。
    long index = 0;
    size_t i = 0;
    while (i + kFrameSize <= data.size()) {
        if (!(data[i] == 0xAA && data[i + 1] == 0x55)) { ++i; continue; }

        telex_glove_snapshot snap;
        std::memset(&snap, 0, sizeof(snap));
        snap.struct_size = static_cast<uint32_t>(sizeof(snap));

        if (telex_glove_parse_frame(data.data() + i, kFrameSize, &snap) != TELEX_GLOVE_OK) {
            ++i;
            continue;
        }

        const telex_glove_hand hand = static_cast<telex_glove_hand>(snap.hand);
        const char* calib = (hand == TELEX_GLOVE_HAND_RIGHT) ? calib_right : calib_left;

        telex_glove_vec3 joints[TELEX_GLOVE_JOINT_COUNT];
        const telex_glove_status rc =
            telex_glove_compute_joints(snap.imu, hand, calib, joints);
        if (rc != TELEX_GLOVE_OK && calib != nullptr) {
            std::fprintf(stderr, "警告: 校准未生效 (%s)，本帧按未校准输出\n",
                         telex_glove_last_error());
        }

        std::printf("%ld,%s,%u,%d", index,
                    (hand == TELEX_GLOVE_HAND_RIGHT) ? "right" : "left",
                    snap.frame_id, snap.battery_percent);
        for (int j = 0; j < TELEX_GLOVE_JOINT_COUNT; ++j)
            std::printf(",%.17g,%.17g,%.17g", joints[j].x, joints[j].y, joints[j].z);
        std::printf("\n");

        ++index;
        i += kFrameSize;
    }

    std::fprintf(stderr, "已导出 %ld 帧\n", index);
    return 0;
}
