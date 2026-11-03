// 测试专用的极简 JSON 读取器（不参与交付产物）。
// 只支持金标准文件用到的子集：对象/数组/字符串/数字/true/false/null。
#ifndef TELEX_TEST_JSON_MINI_HPP
#define TELEX_TEST_JSON_MINI_HPP

#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "../src/file_util.hpp"

namespace jmini {

struct Value;
using Ptr = std::shared_ptr<Value>;

struct Value {
    enum Kind { Null, Bool, Num, Str, Arr, Obj } kind = Null;
    bool b = false;
    double num = 0.0;
    std::string str;
    std::vector<Ptr> arr;
    std::map<std::string, Ptr> obj;

    const Value& operator[](size_t i) const {
        if (kind != Arr || i >= arr.size()) throw std::runtime_error("json: 数组下标越界");
        return *arr[i];
    }
    // int 字面量下标：避免与 const char* 重载歧义
    const Value& operator[](int i) const { return (*this)[static_cast<size_t>(i)]; }
    const Value& operator[](const char* k) const {
        if (kind != Obj) throw std::runtime_error("json: 非对象");
        auto it = obj.find(k);
        if (it == obj.end()) throw std::runtime_error(std::string("json: 缺少键 ") + k);
        return *it->second;
    }
    bool has(const char* k) const { return kind == Obj && obj.count(k) > 0; }
    size_t size() const { return kind == Arr ? arr.size() : obj.size(); }
    double d() const {
        if (kind != Num) throw std::runtime_error("json: 非数字");
        return num;
    }
    const std::string& s() const {
        if (kind != Str) throw std::runtime_error("json: 非字符串");
        return str;
    }
};

class Parser {
public:
    explicit Parser(const std::string& text) : t_(text) {}

    Ptr parse() {
        skip();
        Ptr v = value();
        skip();
        return v;
    }

private:
    const std::string& t_;
    size_t p_ = 0;

    void skip() {
        while (p_ < t_.size() && (t_[p_] == ' ' || t_[p_] == '\n' || t_[p_] == '\t' || t_[p_] == '\r'))
            ++p_;
    }
    char peek() const {
        if (p_ >= t_.size()) throw std::runtime_error("json: 意外结束");
        return t_[p_];
    }
    void expect(char c) {
        if (peek() != c) throw std::runtime_error(std::string("json: 期望 ") + c);
        ++p_;
    }

    Ptr value() {
        skip();
        const char c = peek();
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') {
            auto v = std::make_shared<Value>();
            v->kind = Value::Str;
            v->str = string();
            return v;
        }
        if (c == 't' || c == 'f') {
            auto v = std::make_shared<Value>();
            v->kind = Value::Bool;
            v->b = (c == 't');
            p_ += (c == 't') ? 4 : 5;
            return v;
        }
        if (c == 'n') {
            auto v = std::make_shared<Value>();
            v->kind = Value::Null;
            p_ += 4;
            return v;
        }
        return number();
    }

    Ptr object() {
        auto v = std::make_shared<Value>();
        v->kind = Value::Obj;
        expect('{');
        skip();
        if (peek() == '}') { ++p_; return v; }
        for (;;) {
            skip();
            std::string key = string();
            skip();
            expect(':');
            v->obj[key] = value();
            skip();
            if (peek() == ',') { ++p_; continue; }
            expect('}');
            return v;
        }
    }

    Ptr array() {
        auto v = std::make_shared<Value>();
        v->kind = Value::Arr;
        expect('[');
        skip();
        if (peek() == ']') { ++p_; return v; }
        for (;;) {
            v->arr.push_back(value());
            skip();
            if (peek() == ',') { ++p_; continue; }
            expect(']');
            return v;
        }
    }

    std::string string() {
        expect('"');
        std::string out;
        while (p_ < t_.size() && t_[p_] != '"') {
            if (t_[p_] == '\\') {
                ++p_;
                const char e = t_[p_++];
                switch (e) {
                    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;
                    case 'r': out += '\r'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'u': {
                        // 金标准里的非 ASCII 已用 ensure_ascii=False 直出 UTF-8，
                        // 这里仅兜底跳过 \uXXXX。
                        p_ += 4;
                        out += '?';
                        break;
                    }
                    default: out += e; break;
                }
            } else {
                out += t_[p_++];
            }
        }
        expect('"');
        return out;
    }

    Ptr number() {
        const size_t start = p_;
        while (p_ < t_.size() && (std::isdigit(static_cast<unsigned char>(t_[p_])) ||
                                  t_[p_] == '-' || t_[p_] == '+' || t_[p_] == '.' ||
                                  t_[p_] == 'e' || t_[p_] == 'E'))
            ++p_;
        auto v = std::make_shared<Value>();
        v->kind = Value::Num;
        v->num = std::strtod(t_.substr(start, p_ - start).c_str(), nullptr);
        return v;
    }
};

inline Ptr load_file(const std::string& path) {
    FILE* f = telex::open_file(path, "rb");
    if (!f) throw std::runtime_error("打不开金标准文件: " + path);
    std::string text;
    char buf[8192];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) text.append(buf, n);
    std::fclose(f);
    Parser p(text);
    return p.parse();
}

}  // namespace jmini
#endif
