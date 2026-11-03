#!/usr/bin/env bash
# 在 Ubuntu 20.04 容器里构建发布产物：全部测试 + 全部自检门禁 + 打包。
# 任一环节失败即中止，不会产出半成品。
#
# 用法（在 cpp/ 目录）: bash packaging/build_release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG=telex-glove-build:focal
VER=$(grep -m1 'project(TelexGlove VERSION' "$ROOT/CMakeLists.txt" \
      | sed 's/.*VERSION \([0-9.]*\).*/\1/')
PKG="telex-glove-${VER}-linux-x86_64"

echo "== 构建 ${PKG} =="
docker build --platform linux/amd64 -t "$IMG" \
             -f "$ROOT/packaging/Dockerfile.ubuntu2004" "$ROOT/packaging"

# 全流程都在容器内完成，避免依赖宿主机的 GNU 工具（macOS 上 sha256sum 等不存在）
docker run --rm --platform linux/amd64 -v "$ROOT:/work" -w /work "$IMG" \
  bash -euo pipefail -c "
    VER='${VER}'; PKG='${PKG}'

    rm -rf build-rel dist
    cmake -S . -B build-rel \
          -DCMAKE_BUILD_TYPE=Release \
          -DTELEX_HARDENED=ON \
          -DTELEX_BUILD_TESTS=ON \
          -DTELEX_BUILD_EXAMPLES=ON
    cmake --build build-rel -j\$(nproc)

    # CMake 3.16 没有 ctest --test-dir
    (cd build-rel && ctest --output-on-failure)

    bash packaging/verify_artifact.sh \"build-rel/libtelex_glove.so.\${VER}\"

    mkdir -p dist
    cmake --install build-rel --prefix \"dist/\${PKG}\"

    # 交付物中绝不能混入实现源码。
    # 有意交付的只有两类: include/telex_glove.hpp（header-only 糖层，
    # 不含算法）与 examples/ 下的示例。其余任何 .cpp/.hpp 都是泄漏。
    LEAK=\$(find \"dist/\${PKG}\" \\( -name '*.cpp' -o -name '*.hpp' \\) \
            ! -path '*/examples/*' \
            ! -path '*/include/telex_glove.hpp' || true)
    if [ -n \"\${LEAK}\" ]; then
      echo '错误: 产物中混入了实现源码' >&2
      echo \"\${LEAK}\" >&2
      exit 1
    fi

    tar czf \"dist/\${PKG}.tar.gz\" -C dist \"\${PKG}\"
    (cd dist && sha256sum \"\${PKG}.tar.gz\" > \"\${PKG}.tar.gz.sha256\")

    echo
    echo '== 产物内容 =='
    tar tzf \"dist/\${PKG}.tar.gz\"
    echo
    echo '== 校验和 =='
    cat \"dist/\${PKG}.tar.gz.sha256\"
  "

echo
echo "== 完成: cpp/dist/${PKG}.tar.gz =="
