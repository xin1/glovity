// 三步校准文件读取。支持三种载体，按文件头自动识别（不看扩展名）:
//   1. .npy(object)  numpy 用 allow_pickle=True 存的 dict
//                    {"imu_calib_quats": float64(3,16,4), "side": "left"|"right"}
//                    —— 这是 Remote_USB_ThreeStepCalibration 的原生产物
//   2. .npy(plain)   直接是 float64 (3,16,4) 的数组，side 未知
//   3. .tgc          转换脚本产物，magic "TGCAL1" + side + 原始 float64
//
// 出于安全考虑，pickle 只在受限虚拟机中执行，且全局符号走白名单；
// 任何越界或未知构造一律拒绝，绝不执行任意对象构造。
#ifndef TELEX_NPY_LOADER_HPP
#define TELEX_NPY_LOADER_HPP

#include <string>

namespace telex {

constexpr int CALIB_POSES = 3;
constexpr int CALIB_SLOTS = 16;

/// 校准文件的原始内容。quats 为 scipy 的 (x,y,z,w) 序，与 Python 侧一致。
struct CalibFile {
    double quats[CALIB_POSES][CALIB_SLOTS][4] = {};
    std::string side;       // "left"/"right"；载体未携带时为空
    bool side_known = false;
};

/// 读取并校验。失败返回 false，err 填中性诊断信息（不泄露算法语义）。
bool load_calib_file(const std::string& path, CalibFile& out, std::string& err);

}  // namespace telex
#endif  // TELEX_NPY_LOADER_HPP
