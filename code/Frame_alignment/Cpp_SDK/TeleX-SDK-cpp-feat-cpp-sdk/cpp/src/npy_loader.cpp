#include "npy_loader.hpp"

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "file_util.hpp"

namespace telex {
namespace {

constexpr size_t MAX_FILE_BYTES = 1u << 20;   // 1 MB 上限
constexpr size_t MAX_OPCODES    = 200000;
constexpr size_t MAX_STACK      = 4096;
constexpr size_t MAX_MEMO       = 4096;
constexpr size_t QUAT_BYTES     = CALIB_POSES * CALIB_SLOTS * 4 * sizeof(double);  // 1536

bool read_whole_file(const std::string& path, std::string& out, std::string& err) {
    FILE* f = open_file(path, "rb");
    if (!f) { err = "cannot open file"; return false; }
    std::fseek(f, 0, SEEK_END);
    const long sz = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (sz < 0) { std::fclose(f); err = "cannot size file"; return false; }
    if (static_cast<size_t>(sz) > MAX_FILE_BYTES) {
        std::fclose(f); err = "file too large"; return false;
    }
    out.resize(static_cast<size_t>(sz));
    const size_t n = out.empty() ? 0 : std::fread(&out[0], 1, out.size(), f);
    std::fclose(f);
    if (n != out.size()) { err = "short read"; return false; }
    return true;
}

// --------------------------------------------------------------------------
// 受限 pickle 虚拟机
// --------------------------------------------------------------------------

struct PObj;
using PRef = std::shared_ptr<PObj>;

struct PObj {
    enum Kind { None, Bool, Int, Str, Bytes, Tuple, List, Dict, Global, Array, Dtype } kind = None;
    bool        b = false;
    long long   i = 0;
    std::string s;                              // Str / Bytes 内容
    std::vector<PRef> items;                    // Tuple / List
    std::vector<std::pair<PRef, PRef>> kv;      // Dict
    std::string gmod, gname;                    // Global
    // Array
    std::vector<size_t> shape;
    std::string dtype_name;                     // "f8" / "O8"
    char        byteorder = '|';
    std::string data;                           // 数值数组的原始字节
    std::vector<PRef> objs;                     // object 数组的元素
};

PRef make(PObj::Kind k) { auto p = std::make_shared<PObj>(); p->kind = k; return p; }

bool global_allowed(const std::string& mod, const std::string& name) {
    // numpy 1.x 是 numpy.core.*，numpy 2.x 改为 numpy._core.*，两者都要认。
    if ((mod == "numpy.core.multiarray" || mod == "numpy._core.multiarray") &&
        (name == "_reconstruct" || name == "scalar")) return true;
    if (mod == "numpy" && (name == "ndarray" || name == "dtype")) return true;
    // pickle protocol 2 用 _codecs.encode(latin1_str, "latin1") 承载原始字节
    if (mod == "_codecs" && name == "encode") return true;
    return false;
}

/// 把 pickle BINUNICODE 里的 UTF-8 串还原为 latin-1 单字节序列。
/// 任一码位 >= 256 说明不是 latin-1 载荷，判为畸形。
bool utf8_to_latin1(const std::string& in, std::string& out) {
    out.clear();
    out.reserve(in.size());
    size_t i = 0;
    while (i < in.size()) {
        const uint8_t c = static_cast<uint8_t>(in[i]);
        if (c < 0x80) { out += static_cast<char>(c); ++i; continue; }
        if ((c & 0xE0) == 0xC0) {
            if (i + 1 >= in.size()) return false;
            const uint8_t c1 = static_cast<uint8_t>(in[i + 1]);
            if ((c1 & 0xC0) != 0x80) return false;
            const unsigned cp = ((c & 0x1Fu) << 6) | (c1 & 0x3Fu);
            if (cp > 0xFF) return false;
            out += static_cast<char>(static_cast<uint8_t>(cp));
            i += 2;
            continue;
        }
        return false;  // 三字节及以上码位不可能是 latin-1
    }
    return true;
}

class Unpickler {
public:
    Unpickler(const std::string& buf, std::string& err) : b_(buf), err_(err) {}

    bool run(PRef& result) {
        size_t ops = 0;
        while (p_ < b_.size()) {
            if (++ops > MAX_OPCODES) return fail("opcode limit");
            const uint8_t op = static_cast<uint8_t>(b_[p_++]);
            switch (op) {
                case 0x80: { uint8_t v; if (!u8(v)) return false; break; }          // PROTO
                case 0x95: { uint64_t n; if (!u64(n)) return false; break; }        // FRAME
                case '.':  {                                                        // STOP
                    if (stack_.empty()) return fail("empty stack at STOP");
                    result = stack_.back();
                    return true;
                }
                case 'N': push(make(PObj::None)); break;                            // NONE
                case 0x88: { auto v = make(PObj::Bool); v->b = true;  push(v); break; }
                case 0x89: { auto v = make(PObj::Bool); v->b = false; push(v); break; }
                case 'K': { uint8_t v; if (!u8(v))  return false; pushint(v); break; }   // BININT1
                case 'M': { uint16_t v; if (!u16(v)) return false; pushint(v); break; }  // BININT2
                case 'J': { int32_t v; if (!i32(v)) return false; pushint(v); break; }   // BININT
                case 0x8a: {                                                        // LONG1
                    uint8_t n; if (!u8(n)) return false;
                    if (!need(n)) return false;
                    long long v = 0;
                    for (int k = n - 1; k >= 0; --k)
                        v = (v << 8) | static_cast<uint8_t>(b_[p_ + k]);
                    if (n > 0 && (static_cast<uint8_t>(b_[p_ + n - 1]) & 0x80))
                        v -= (1LL << (8 * n));
                    p_ += n; pushint(v); break;
                }
                case 0x8c: { uint8_t n;  if (!u8(n))  return false; if (!pushstr(n, PObj::Str))   return false; break; } // SHORT_BINUNICODE
                case 'X':  { uint32_t n; if (!u32(n)) return false; if (!pushstr(n, PObj::Str))   return false; break; } // BINUNICODE
                case 'U':  { uint8_t n;  if (!u8(n))  return false; if (!pushstr(n, PObj::Str))   return false; break; } // SHORT_BINSTRING
                case 'T':  { uint32_t n; if (!u32(n)) return false; if (!pushstr(n, PObj::Str))   return false; break; } // BINSTRING
                case 'C':  { uint8_t n;  if (!u8(n))  return false; if (!pushstr(n, PObj::Bytes)) return false; break; } // SHORT_BINBYTES
                case 'B':  { uint32_t n; if (!u32(n)) return false; if (!pushstr(n, PObj::Bytes)) return false; break; } // BINBYTES
                case '(':  marks_.push_back(stack_.size()); break;                  // MARK
                case ')':  push(make(PObj::Tuple)); break;                          // EMPTY_TUPLE
                case ']':  push(make(PObj::List)); break;                           // EMPTY_LIST
                case '}':  push(make(PObj::Dict)); break;                           // EMPTY_DICT
                case 0x85: if (!tuplen(1)) return false; break;                     // TUPLE1
                case 0x86: if (!tuplen(2)) return false; break;                     // TUPLE2
                case 0x87: if (!tuplen(3)) return false; break;                     // TUPLE3
                case 't':  if (!tuple_mark()) return false; break;                  // TUPLE
                case 0x94: {                                                        // MEMOIZE
                    if (stack_.empty()) return fail("MEMOIZE on empty stack");
                    if (memo_.size() >= MAX_MEMO) return fail("memo limit");
                    memo_.push_back(stack_.back());
                    break;
                }
                case 'q': { uint8_t n;  if (!u8(n))  return false; if (!memo_put(n))  return false; break; } // BINPUT
                case 'r': { uint32_t n; if (!u32(n)) return false; if (!memo_put(n))  return false; break; } // LONG_BINPUT
                case 'h': { uint8_t n;  if (!u8(n))  return false; if (!memo_get(n))  return false; break; } // BINGET
                case 'j': { uint32_t n; if (!u32(n)) return false; if (!memo_get(n))  return false; break; } // LONG_BINGET
                case 0x93: if (!stack_global()) return false; break;                // STACK_GLOBAL
                case 'c':  if (!text_global()) return false; break;                 // GLOBAL
                case 'R':  if (!reduce()) return false; break;                      // REDUCE
                case 'b':  if (!build())  return false; break;                      // BUILD
                case 's':  if (!setitem()) return false; break;                     // SETITEM
                case 'u':  if (!setitems()) return false; break;                    // SETITEMS
                case 'a':  if (!append())  return false; break;                     // APPEND
                case 'e':  if (!appends()) return false; break;                     // APPENDS
                default:
                    return fail("unsupported opcode 0x" + hex2(op));
            }
        }
        return fail("stream ended without STOP");
    }

private:
    const std::string& b_;
    std::string& err_;
    size_t p_ = 0;
    std::vector<PRef> stack_;
    std::vector<PRef> memo_;
    std::vector<size_t> marks_;

    static std::string hex2(uint8_t v) {
        static const char* d = "0123456789abcdef";
        return std::string(1, d[v >> 4]) + std::string(1, d[v & 0xF]);
    }
    bool fail(const std::string& m) { if (err_.empty()) err_ = "pickle: " + m; return false; }
    bool need(size_t n) { return (p_ + n <= b_.size()) ? true : fail("truncated"); }
    bool u8(uint8_t& v)  { if (!need(1)) return false; v = static_cast<uint8_t>(b_[p_++]); return true; }
    bool u16(uint16_t& v){ if (!need(2)) return false; v = static_cast<uint8_t>(b_[p_]) | (static_cast<uint16_t>(static_cast<uint8_t>(b_[p_+1]))<<8); p_+=2; return true; }
    bool u32(uint32_t& v){ if (!need(4)) return false; v = 0; for (int k=3;k>=0;--k) v = (v<<8) | static_cast<uint8_t>(b_[p_+k]); p_+=4; return true; }
    bool i32(int32_t& v) { uint32_t u; if (!u32(u)) return false; v = static_cast<int32_t>(u); return true; }
    bool u64(uint64_t& v){ if (!need(8)) return false; v = 0; for (int k=7;k>=0;--k) v = (v<<8) | static_cast<uint8_t>(b_[p_+k]); p_+=8; return true; }

    void push(const PRef& v) { stack_.push_back(v); }
    void pushint(long long v) { auto o = make(PObj::Int); o->i = v; push(o); }
    bool pushstr(size_t n, PObj::Kind k) {
        if (!need(n)) return false;
        if (stack_.size() >= MAX_STACK) return fail("stack limit");
        auto o = make(k); o->s.assign(b_, p_, n); p_ += n; push(o); return true;
    }
    bool memo_put(size_t idx) {
        if (stack_.empty()) return fail("BINPUT on empty stack");
        if (idx >= MAX_MEMO) return fail("memo index too large");
        if (memo_.size() <= idx) memo_.resize(idx + 1);
        memo_[idx] = stack_.back();
        return true;
    }
    bool memo_get(size_t idx) {
        if (idx >= memo_.size() || !memo_[idx]) return fail("bad memo index");
        push(memo_[idx]); return true;
    }
    bool tuplen(size_t n) {
        if (stack_.size() < n) return fail("stack underflow in TUPLE");
        auto t = make(PObj::Tuple);
        t->items.assign(stack_.end() - static_cast<std::ptrdiff_t>(n), stack_.end());
        stack_.resize(stack_.size() - n);
        push(t); return true;
    }
    bool tuple_mark() {
        if (marks_.empty()) return fail("TUPLE without MARK");
        const size_t m = marks_.back(); marks_.pop_back();
        if (m > stack_.size()) return fail("bad MARK");
        auto t = make(PObj::Tuple);
        t->items.assign(stack_.begin() + static_cast<std::ptrdiff_t>(m), stack_.end());
        stack_.resize(m);
        push(t); return true;
    }
    bool stack_global() {
        if (stack_.size() < 2) return fail("STACK_GLOBAL underflow");
        const PRef name = stack_.back(); stack_.pop_back();
        const PRef mod  = stack_.back(); stack_.pop_back();
        if (mod->kind != PObj::Str || name->kind != PObj::Str) return fail("bad global operands");
        if (!global_allowed(mod->s, name->s)) return fail("global not allowed: " + mod->s + "." + name->s);
        auto g = make(PObj::Global); g->gmod = mod->s; g->gname = name->s; push(g); return true;
    }
    bool text_global() {
        // 'c' module\nname\n
        const size_t e1 = b_.find('\n', p_);
        if (e1 == std::string::npos) return fail("truncated GLOBAL");
        const std::string mod = b_.substr(p_, e1 - p_);
        const size_t e2 = b_.find('\n', e1 + 1);
        if (e2 == std::string::npos) return fail("truncated GLOBAL");
        const std::string name = b_.substr(e1 + 1, e2 - e1 - 1);
        p_ = e2 + 1;
        if (!global_allowed(mod, name)) return fail("global not allowed: " + mod + "." + name);
        auto g = make(PObj::Global); g->gmod = mod; g->gname = name; push(g); return true;
    }
    bool reduce() {
        if (stack_.size() < 2) return fail("REDUCE underflow");
        const PRef args = stack_.back(); stack_.pop_back();
        const PRef fn   = stack_.back(); stack_.pop_back();
        if (fn->kind != PObj::Global) return fail("REDUCE on non-global");
        if (fn->gname == "_reconstruct") { push(make(PObj::Array)); return true; }
        if (fn->gname == "dtype") {
            auto d = make(PObj::Dtype);
            if (!args->items.empty() && args->items[0]->kind == PObj::Str)
                d->dtype_name = args->items[0]->s;
            push(d); return true;
        }
        if (fn->gname == "scalar") { push(make(PObj::None)); return true; }
        if (fn->gname == "encode") {
            // _codecs.encode(s, "latin1") -> bytes。流里的 s 以 UTF-8 存放，
            // 需按码位还原为单字节，这是 protocol 2 的原始数据载体。
            if (args->items.size() < 2 || args->items[0]->kind != PObj::Str ||
                args->items[1]->kind != PObj::Str)
                return fail("bad codecs.encode args");
            const std::string& enc = args->items[1]->s;
            if (enc != "latin1" && enc != "latin-1" && enc != "iso-8859-1")
                return fail("unsupported codec: " + enc);
            auto out = make(PObj::Bytes);
            if (!utf8_to_latin1(args->items[0]->s, out->s)) return fail("bad latin1 payload");
            push(out);
            return true;
        }
        return fail("REDUCE target not supported: " + fn->gname);
    }
    bool build() {
        if (stack_.size() < 2) return fail("BUILD underflow");
        const PRef state = stack_.back(); stack_.pop_back();
        PRef obj = stack_.back();
        if (obj->kind == PObj::Dtype) {
            // dtype.__setstate__: (version, byteorder, ...)
            if (state->kind == PObj::Tuple && state->items.size() >= 2 &&
                state->items[1]->kind == PObj::Str && !state->items[1]->s.empty())
                obj->byteorder = state->items[1]->s[0];
            return true;
        }
        if (obj->kind == PObj::Array) {
            // ndarray.__setstate__: (version, shape, dtype, is_fortran, data)
            if (state->kind != PObj::Tuple || state->items.size() < 5)
                return fail("bad ndarray state");
            const PRef shp = state->items[1];
            if (shp->kind != PObj::Tuple) return fail("bad ndarray shape");
            for (const auto& d : shp->items) {
                if (d->kind != PObj::Int || d->i < 0) return fail("bad shape entry");
                obj->shape.push_back(static_cast<size_t>(d->i));
            }
            const PRef dt = state->items[2];
            if (dt->kind == PObj::Dtype) { obj->dtype_name = dt->dtype_name; obj->byteorder = dt->byteorder; }
            const PRef data = state->items[4];
            if (data->kind == PObj::Bytes)      obj->data = data->s;
            else if (data->kind == PObj::List)  obj->objs = data->items;
            else return fail("bad ndarray payload");
            return true;
        }
        return fail("BUILD on unsupported object");
    }
    bool setitem() {
        if (stack_.size() < 3) return fail("SETITEM underflow");
        const PRef v = stack_.back(); stack_.pop_back();
        const PRef k = stack_.back(); stack_.pop_back();
        if (stack_.back()->kind != PObj::Dict) return fail("SETITEM on non-dict");
        stack_.back()->kv.emplace_back(k, v);
        return true;
    }
    bool setitems() {
        if (marks_.empty()) return fail("SETITEMS without MARK");
        const size_t m = marks_.back(); marks_.pop_back();
        if (m > stack_.size() || ((stack_.size() - m) % 2) != 0) return fail("bad SETITEMS");
        if (m == 0 || stack_[m - 1]->kind != PObj::Dict) return fail("SETITEMS on non-dict");
        PRef d = stack_[m - 1];
        for (size_t i = m; i + 1 < stack_.size(); i += 2) d->kv.emplace_back(stack_[i], stack_[i + 1]);
        stack_.resize(m);
        return true;
    }
    bool append() {
        if (stack_.size() < 2) return fail("APPEND underflow");
        const PRef v = stack_.back(); stack_.pop_back();
        if (stack_.back()->kind != PObj::List) return fail("APPEND on non-list");
        stack_.back()->items.push_back(v);
        return true;
    }
    bool appends() {
        if (marks_.empty()) return fail("APPENDS without MARK");
        const size_t m = marks_.back(); marks_.pop_back();
        if (m == 0 || m > stack_.size() || stack_[m - 1]->kind != PObj::List)
            return fail("bad APPENDS");
        PRef l = stack_[m - 1];
        for (size_t i = m; i < stack_.size(); ++i) l->items.push_back(stack_[i]);
        stack_.resize(m);
        return true;
    }
};

// --------------------------------------------------------------------------
// 载体识别与提取
// --------------------------------------------------------------------------

bool copy_quats(const std::string& raw, char byteorder, CalibFile& out, std::string& err) {
    if (raw.size() != QUAT_BYTES) { err = "calibration payload size mismatch"; return false; }
    if (byteorder == '>') { err = "big-endian payload not supported"; return false; }
    std::memcpy(&out.quats[0][0][0], raw.data(), QUAT_BYTES);
    for (int p = 0; p < CALIB_POSES; ++p)
        for (int s = 0; s < CALIB_SLOTS; ++s)
            for (int c = 0; c < 4; ++c) {
                const double v = out.quats[p][s][c];
                if (!(v == v) || v > 1e6 || v < -1e6) { err = "calibration payload not finite"; return false; }
            }
    return true;
}

/// 解析 npy 头，返回 descr / shape / 数据起点。
bool parse_npy_header(const std::string& buf, std::string& descr,
                      std::vector<size_t>& shape, size_t& data_off, std::string& err) {
    static const char kMagic[] = "\x93NUMPY";
    if (buf.size() < 12 || std::memcmp(buf.data(), kMagic, 6) != 0) { err = "not a npy file"; return false; }
    const uint8_t major = static_cast<uint8_t>(buf[6]);
    size_t hlen_off = 8, hlen = 0;
    if (major == 1) {
        hlen = static_cast<uint8_t>(buf[8]) | (static_cast<size_t>(static_cast<uint8_t>(buf[9])) << 8);
        hlen_off = 10;
    } else if (major == 2 || major == 3) {
        hlen = 0;
        for (int k = 3; k >= 0; --k) hlen = (hlen << 8) | static_cast<uint8_t>(buf[8 + k]);
        hlen_off = 12;
    } else { err = "unsupported npy version"; return false; }
    if (hlen_off + hlen > buf.size()) { err = "npy header out of range"; return false; }

    const std::string head = buf.substr(hlen_off, hlen);
    data_off = hlen_off + hlen;

    const size_t dp = head.find("'descr'");
    if (dp == std::string::npos) { err = "npy header missing descr"; return false; }
    const size_t q1 = head.find('\'', head.find(':', dp));
    const size_t q2 = (q1 == std::string::npos) ? std::string::npos : head.find('\'', q1 + 1);
    if (q2 == std::string::npos) { err = "npy header malformed descr"; return false; }
    descr = head.substr(q1 + 1, q2 - q1 - 1);

    const size_t sp = head.find("'shape'");
    if (sp == std::string::npos) { err = "npy header missing shape"; return false; }
    const size_t o = head.find('(', sp), c = head.find(')', sp);
    if (o == std::string::npos || c == std::string::npos || c < o) { err = "npy header malformed shape"; return false; }
    shape.clear();
    std::string cur;
    for (size_t i = o + 1; i < c; ++i) {
        const char ch = head[i];
        if (ch >= '0' && ch <= '9') cur += ch;
        else if (ch == ',') { if (!cur.empty()) { shape.push_back(std::strtoul(cur.c_str(), nullptr, 10)); cur.clear(); } }
    }
    if (!cur.empty()) shape.push_back(std::strtoul(cur.c_str(), nullptr, 10));
    return true;
}

const PObj* find_array_in_dict(const PObj& d, const char* key, std::string& side, bool& side_known) {
    const PObj* arr = nullptr;
    for (const auto& kv : d.kv) {
        if (!kv.first || kv.first->kind != PObj::Str) continue;
        if (kv.first->s == key && kv.second && kv.second->kind == PObj::Array) arr = kv.second.get();
        if (kv.first->s == "side" && kv.second && kv.second->kind == PObj::Str) {
            side = kv.second->s;
            side_known = true;
        }
    }
    return arr;
}

}  // namespace

bool load_calib_file(const std::string& path, CalibFile& out, std::string& err) {
    err.clear();
    std::string buf;
    if (!read_whole_file(path, buf, err)) return false;

    // 载体 3: 转换脚本产物
    static const char kTgc[] = "TGCAL1";
    if (buf.size() == 8 + 1 + QUAT_BYTES && std::memcmp(buf.data(), kTgc, 6) == 0) {
        out.side = (buf[8] == 'r') ? "right" : "left";
        out.side_known = true;
        return copy_quats(buf.substr(9), '<', out, err);
    }

    std::string descr;
    std::vector<size_t> shape;
    size_t data_off = 0;
    if (!parse_npy_header(buf, descr, shape, data_off, err)) return false;

    // 载体 2: 直接是 float64 (3,16,4)
    if (descr == "<f8" || descr == "=f8" || descr == "f8") {
        if (shape.size() != 3 || shape[0] != CALIB_POSES || shape[1] != CALIB_SLOTS || shape[2] != 4) {
            err = "unexpected calibration shape";
            return false;
        }
        out.side_known = false;
        return copy_quats(buf.substr(data_off), '<', out, err);
    }

    // 载体 1: object 数组，内含 pickle 的 dict
    if (descr != "|O" && descr != "O") { err = "unsupported calibration dtype"; return false; }

    const std::string stream = buf.substr(data_off);
    PRef root;
    Unpickler up(stream, err);
    if (!up.run(root) || !root) { if (err.empty()) err = "pickle decode failed"; return false; }

    // 顶层是 0 维 object 数组，其唯一元素是 payload dict
    const PObj* dict = nullptr;
    if (root->kind == PObj::Array && root->objs.size() == 1 && root->objs[0] &&
        root->objs[0]->kind == PObj::Dict) {
        dict = root->objs[0].get();
    } else if (root->kind == PObj::Dict) {
        dict = root.get();
    }
    if (!dict) { err = "calibration payload is not a mapping"; return false; }

    const PObj* arr = find_array_in_dict(*dict, "imu_calib_quats", out.side, out.side_known);
    if (!arr) { err = "calibration payload missing required entry"; return false; }
    if (arr->shape.size() != 3 || arr->shape[0] != CALIB_POSES ||
        arr->shape[1] != CALIB_SLOTS || arr->shape[2] != 4) {
        err = "unexpected calibration shape";
        return false;
    }
    if (arr->dtype_name != "f8") { err = "unsupported calibration element type"; return false; }
    if (out.side != "left" && out.side != "right") { out.side = "left"; out.side_known = false; }
    return copy_quats(arr->data, arr->byteorder, out, err);
}

}  // namespace telex
