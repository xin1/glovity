// 跨平台文件访问。对外约定所有路径参数均为 UTF-8。
//
// Windows 上窄字符 fopen / stat 走的是系统 ANSI 代码页（中文系统为 GBK），
// UTF-8 的中文路径会直接打开失败，因此必须转成宽字符走 _wfopen 等接口。
// 兜底：不是合法 UTF-8 的路径按 ANSI 代码页解释。集成方直接把 main 的 argv
// 或其它 ANSI API 拿到的路径传进来时也能工作（GBK 中文几乎不可能恰好是合法 UTF-8）。
// 仅含内联函数，测试脚手架（链接最终 DLL、看不到内部符号）也可直接使用。
#ifndef TELEX_FILE_UTIL_HPP
#define TELEX_FILE_UTIL_HPP

#include <cstdio>
#include <string>

#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <sys/stat.h>
#endif

namespace telex {

#if defined(_WIN32)
/// UTF-8 → UTF-16；不是合法 UTF-8 时按 ANSI 代码页转换。失败返回空串。
inline std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) return std::wstring();
    const int len = static_cast<int>(s.size());
    UINT cp = CP_UTF8;
    DWORD flags = MB_ERR_INVALID_CHARS;
    int n = ::MultiByteToWideChar(cp, flags, s.data(), len, nullptr, 0);
    if (n <= 0) {
        cp = CP_ACP;
        flags = 0;
        n = ::MultiByteToWideChar(cp, flags, s.data(), len, nullptr, 0);
        if (n <= 0) return std::wstring();
    }
    std::wstring w(static_cast<size_t>(n), L'\0');
    ::MultiByteToWideChar(cp, flags, s.data(), len, &w[0], n);
    return w;
}
#endif

/// 以 UTF-8 路径打开文件，语义同 std::fopen。
inline std::FILE* open_file(const std::string& path, const char* mode) {
#if defined(_WIN32)
    const std::wstring wpath = utf8_to_wide(path);
    const std::wstring wmode = utf8_to_wide(mode);
    if (wpath.empty() || wmode.empty()) return nullptr;
    std::FILE* f = nullptr;
    if (::_wfopen_s(&f, wpath.c_str(), wmode.c_str()) != 0) return nullptr;
    return f;
#else
    return std::fopen(path.c_str(), mode);
#endif
}

/// 文件修改时间（纳秒级精度，仅用于判断是否变化）。不存在返回 -1。
inline long long file_mtime(const std::string& path) {
#if defined(_WIN32)
    WIN32_FILE_ATTRIBUTE_DATA attr;
    const std::wstring wpath = utf8_to_wide(path);
    if (wpath.empty() ||
        !::GetFileAttributesExW(wpath.c_str(), GetFileExInfoStandard, &attr))
        return -1;
    // FILETIME 单位为 100ns
    const unsigned long long t =
        (static_cast<unsigned long long>(attr.ftLastWriteTime.dwHighDateTime) << 32) |
        attr.ftLastWriteTime.dwLowDateTime;
    return static_cast<long long>(t) * 100LL;
#else
    struct stat st;
    if (::stat(path.c_str(), &st) != 0) return -1;
#  if defined(__APPLE__)
    return static_cast<long long>(st.st_mtimespec.tv_sec) * 1000000000LL +
           st.st_mtimespec.tv_nsec;
#  else
    return static_cast<long long>(st.st_mtim.tv_sec) * 1000000000LL + st.st_mtim.tv_nsec;
#  endif
#endif
}

}  // namespace telex
#endif  // TELEX_FILE_UTIL_HPP
