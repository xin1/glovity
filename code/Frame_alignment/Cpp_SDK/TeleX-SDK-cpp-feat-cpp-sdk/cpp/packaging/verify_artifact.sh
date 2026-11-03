#!/usr/bin/env bash
# 交付前自检。每一条都是硬门禁，任何一条不过就不许发布。
set -uo pipefail

SO="${1:?用法: verify_artifact.sh <libtelex_glove.so 路径>}"
FAILED=0

pass() { printf '  [OK]   %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED=1; }

echo "== 交付物自检: $SO =="

# 1) glibc 版本下限必须 <= 2.31（Ubuntu 20.04），保证 22.04/24.04 可用
MAXG=$(objdump -T "$SO" 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' \
       | sort -V | tail -1)
if [ -z "$MAXG" ]; then
  pass "无 GLIBC 版本化符号依赖"
elif [ "$(printf '%s\n2.31\n' "$MAXG" | sort -V | tail -1)" = "2.31" ]; then
  pass "glibc 下限 $MAXG <= 2.31"
else
  fail "glibc 下限 $MAXG > 2.31，将无法在 Ubuntu 20.04 运行"
fi

# 2) 导出符号必须全部是 telex_glove_*
#    这不只是防逆向：静态链入的 libstdc++ 符号若泄漏到 .dynsym，
#    会与宿主程序的 libstdc++ 发生插入，导致跨堆 new/delete 随机崩溃。
LEAK=$(nm -D --defined-only "$SO" 2>/dev/null | grep -E ' [TWiB] ' \
       | awk '{print $NF}' | grep -v '^telex_glove_' || true)
NEXP=$(nm -D --defined-only "$SO" 2>/dev/null | grep -cE ' [TWiB] ' || echo 0)
if [ -z "$LEAK" ] && [ "$NEXP" -gt 0 ]; then
  pass "导出符号 $NEXP 个，全部为 telex_glove_*"
else
  fail "存在非 telex_glove_* 导出符号:"
  echo "$LEAK" | head -20 | sed 's/^/         /'
fi

# 3) 动态依赖只允许 libc 系
DEPS=$(ldd "$SO" 2>/dev/null | grep '=>' \
       | grep -vE 'libc\.so|libm\.so|libpthread\.so|libdl\.so|librt\.so|ld-linux|linux-vdso' || true)
if [ -z "$DEPS" ]; then
  pass "动态依赖仅 libc 系"
else
  fail "存在意外的动态依赖:"; echo "$DEPS" | sed 's/^/         /'
fi
if ldd "$SO" 2>/dev/null | grep -q 'libstdc++'; then
  fail "libstdc++ 未静态链接（跨编译器部署会出问题）"
else
  pass "libstdc++ 已静态链接"
fi

# 4) 已 strip
if file "$SO" | grep -q 'stripped' && ! file "$SO" | grep -q 'not stripped'; then
  pass "已 strip"
else
  fail "未 strip，符号表仍在"
fi
if readelf -S "$SO" 2>/dev/null | grep -q '\.debug_'; then
  fail "仍含 .debug_* 段"
else
  pass "无调试段"
fi

# 5) 字符串审计：除白名单标签外不得出现中文；不得泄露源码路径与算法术语
WL="$(dirname "$0")/allowed_cjk.txt"
# CJK 的 UTF-8 编码固定为 E4-E9 开头 + 两个续字节。这里用字节级匹配，
# 因为 Ubuntu 20.04 的 grep -P 不支持 \x{} 的 UTF-8 模式。
# 另外两点很关键：
#   - 必须用 strings -e S（8 位），默认的 7 位 ASCII 模式根本看不见中文；
#   - 必须要求"连续 2 个以上汉字"，否则机器码里碰巧形似 UTF-8 的字节会大量误报。
CJK_RE='([\xe4-\xe9][\x80-\xbf]{2}){2,}'
CJK=$(strings -a -e S -n 2 "$SO" | grep -P "$CJK_RE" || true)

# 自检：白名单里的标签必须真能被匹配到。否则说明提取方式失效，
# 此时"没发现问题"是绿灯谎报，比直接失败更危险。
if ! printf '%s\n' "$CJK" | grep -qF "$(head -1 "$WL")"; then
  fail "中文字符串检查失效（未能提取到已知标签），无法判定，按不通过处理"
else
  # 逐行剔除白名单标签，剩余仍含连续中文者即违规
  UNEXPECTED=$(printf '%s\n' "$CJK" | while IFS= read -r line; do
      while IFS= read -r label; do
        [ -n "$label" ] && line=${line//"$label"/}
      done < "$WL"
      if printf '%s' "$line" | grep -qP "$CJK_RE"; then
        printf '%s\n' "$line"
      fi
    done)
  if [ -z "$(printf '%s' "$UNEXPECTED" | tr -d '[:space:]')" ]; then
    NCJK=$(printf '%s\n' "$CJK" | grep -c . )
    pass "中文字符串仅限标签白名单（提取到 $NCJK 条，自检命中）"
  else
    fail "出现非白名单中文字符串（可能泄露算法语义）:"
    printf '%s\n' "$UNEXPECTED" | head -10 | sed 's/^/         /'
  fi
fi

PATHLEAK=$(strings -n 6 "$SO" | grep -E '/(home|Users|work)/|\.cpp$|\.hpp$' || true)
if [ -z "$PATHLEAK" ]; then pass "无源码路径泄露"
else fail "泄露源码路径:"; echo "$PATHLEAK" | head -5 | sed 's/^/         /'; fi

# 注：numpy / NUMPY 字符串是 .npy 格式解析的必需品，且集成方本就知道要传
# .npy 校准文件，不构成泄露，故不在禁列。禁的是会暴露算法结构的术语。
HINT=$(strings -n 5 "$SO" | grep -iE 'scipy|eigen|installation_error|skeleton|rotvec|bone_length|dip_coupling' || true)
if [ -z "$HINT" ]; then pass "无算法线索字符串"
else fail "泄露算法线索:"; echo "$HINT" | head -5 | sed 's/^/         /'; fi

# 6) 安全加固位
readelf -lW "$SO" 2>/dev/null | grep -q 'GNU_RELRO' && pass "启用 RELRO" || fail "缺少 RELRO"
readelf -dW "$SO" 2>/dev/null | grep -q 'BIND_NOW'  && pass "启用 BIND_NOW" || fail "缺少 BIND_NOW"
if readelf -lW "$SO" 2>/dev/null | grep 'GNU_STACK' | grep -q 'RWE'; then
  fail "存在可执行栈"
else
  pass "栈不可执行"
fi

# 7) 体积合理性（异常偏大通常意味着漏 strip 或误链）
SZ=$(stat -c%s "$SO" 2>/dev/null || stat -f%z "$SO")
if [ "$SZ" -lt 3000000 ]; then pass "体积 $SZ 字节"
else fail "体积 $SZ 字节，异常偏大"; fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "== 全部门禁通过，可以发布 =="
else
  echo "== 存在未通过项，禁止发布 =="
fi
exit "$FAILED"
