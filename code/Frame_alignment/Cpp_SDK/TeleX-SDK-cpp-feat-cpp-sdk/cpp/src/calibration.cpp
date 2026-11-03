#include "calibration.hpp"

#include <array>

#include "file_util.hpp"
#include "npy_loader.hpp"

namespace telex {

const Calibration* CalibrationStore::get(const std::string& path, Side requested_side) {
    if (path.empty()) {
        loaded_ = false;
        calib_.valid = false;
        return nullptr;
    }

    const long long mt = file_mtime(path);
    if (loaded_ && path == path_ && mt == mtime_ && requested_side == requested_) {
        return calib_.valid ? &calib_ : nullptr;
    }
    if (path != path_) warned_ = false;   // 换文件后允许再告警一次

    path_ = path;
    mtime_ = mt;
    requested_ = requested_side;
    loaded_ = true;
    calib_.valid = false;

    CalibFile cf;
    std::string err;
    if (!load_calib_file(path, cf, err)) {
        last_error_ = err;
        warned_ = true;
        return nullptr;   // 退回未校准
    }

    std::array<std::array<Rot, NUM_SLOTS>, CALIB_POSES> poses{};
    for (int p = 0; p < CALIB_POSES; ++p)
        for (int s = 0; s < NUM_SLOTS; ++s)
            poses[p][s] = Rot::from_quat_xyzw(cf.quats[p][s][0], cf.quats[p][s][1],
                                              cf.quats[p][s][2], cf.quats[p][s][3]);

    Side side = requested_side;
    if (cf.side_known) side = (cf.side == "right") ? Side::Right : Side::Left;

    calib_ = installation_error(poses, side);
    last_error_.clear();
    return calib_.valid ? &calib_ : nullptr;
}

}  // namespace telex
