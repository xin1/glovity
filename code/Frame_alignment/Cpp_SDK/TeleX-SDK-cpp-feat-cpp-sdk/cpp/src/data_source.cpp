#include "data_source.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

#include "file_util.hpp"
#include "protocol.hpp"
#include "serial_port.hpp"

namespace telex {
namespace {

class SerialSource : public DataSource {
public:
    bool open(const std::string& port, int baud, std::string& err) {
        return port_.open(port, baud, err);
    }
    long read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err) override {
        return port_.read(buf, cap, timeout_ms, err);
    }
    void close() override { port_.close(); }

private:
    SerialPort port_;
};

/// 文件回放。整段读入内存，按目标帧率吐出，循环播放。
class FileSource : public DataSource {
public:
    bool open(const std::string& path, std::string& err) {
        FILE* f = open_file(path, "rb");
        if (!f) { err = "cannot open replay file"; return false; }
        std::fseek(f, 0, SEEK_END);
        const long sz = std::ftell(f);
        std::fseek(f, 0, SEEK_SET);
        if (sz <= 0) { std::fclose(f); err = "replay file empty"; return false; }
        data_.resize(static_cast<size_t>(sz));
        const size_t n = std::fread(data_.data(), 1, data_.size(), f);
        std::fclose(f);
        if (n != data_.size()) { err = "replay file short read"; return false; }
        return true;
    }

    long read(uint8_t* buf, size_t cap, int timeout_ms, std::string& err) override {
        (void)err;
        if (data_.empty()) return 0;
        // 以约 200 Hz/手的节奏吐出，贴近真实链路，避免回放瞬间灌满
        const size_t chunk = std::min(cap, static_cast<size_t>(FRAME_SIZE) * 2);
        const int sleep_ms = std::min(timeout_ms, 5);
        if (sleep_ms > 0)
            std::this_thread::sleep_for(std::chrono::milliseconds(sleep_ms));

        const size_t n = std::min(chunk, data_.size() - pos_);
        std::memcpy(buf, data_.data() + pos_, n);
        pos_ += n;
        if (pos_ >= data_.size()) pos_ = 0;   // 循环
        return static_cast<long>(n);
    }
    void close() override { data_.clear(); pos_ = 0; }

private:
    std::vector<uint8_t> data_;
    size_t pos_ = 0;
};

}  // namespace

std::unique_ptr<DataSource> make_data_source(const std::string& spec, int baud,
                                             std::string& err) {
    static const std::string kFilePrefix = "file://";
    if (spec.size() > kFilePrefix.size() &&
        spec.compare(0, kFilePrefix.size(), kFilePrefix) == 0) {
        auto src = std::unique_ptr<FileSource>(new FileSource());
        if (!src->open(spec.substr(kFilePrefix.size()), err)) return nullptr;
        return std::unique_ptr<DataSource>(src.release());
    }
    auto src = std::unique_ptr<SerialSource>(new SerialSource());
    if (!src->open(spec, baud, err)) return nullptr;
    return std::unique_ptr<DataSource>(src.release());
}

}  // namespace telex
