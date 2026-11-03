# Glovity / TeleX Windows C++ SDK 使用说明

版本：1.2.0  
平台：Windows 10 / 11 x64  
协议：Glovity V2 180字节，同时兼容 legacy 168/170字节

## 1. SDK功能

SDK独占接收器USB串口，在后台解析左右手数据，并向应用提供：

- 左右手、设备帧序号和电量；
- 11路IMU四元数；
- 16个逐级父子局部旋转（关节角）；
- 21个手部关节三维坐标，单位为米；
- V2设备时间戳、REC录制状态和IMU失败掩码；
- 腕部欧拉角、腕部角速度及其有效性；
- 主机单调接收时间、在线状态和接收帧率；
- 通过校验的原始168/170/180字节帧；
- `.npy` / `.tgc` 三步校准文件加载与热更新。

SDK不需要Python、OpenCV、Eigen或VC++运行库。DLL使用C ABI，C和C++工程均可调用。

## 2. 交付目录

```text
telex-glove-1.2.0-windows-x64/
├─ bin/telex_glove.dll              运行时DLL
├─ lib/telex_glove.lib              MSVC导入库
├─ include/telex_glove.h            C接口
├─ include/telex_glove.hpp          C++封装
├─ lib/cmake/TelexGlove/            CMake包配置
└─ share/telex-glove/
   ├─ docs/                          使用文档
   ├─ examples/                      C/C++示例源码
   ├─ tools/                         校准转换工具
   └─ sample_capture.bin             无硬件回放文件
```

只支持x64。`telex_glove.dll` 必须位于应用程序EXE同目录，或位于系统 `PATH` 中。

## 3. CMake接入

应用工程的 `CMakeLists.txt`：

```cmake
cmake_minimum_required(VERSION 3.20)
project(glove_demo LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
find_package(TelexGlove 1.1 REQUIRED)

add_executable(glove_demo main.cpp)
target_link_libraries(glove_demo PRIVATE TelexGlove::telex_glove)

if(MSVC)
  target_compile_options(glove_demo PRIVATE /utf-8)
endif()
```

配置和编译：

```powershell
cmake -S . -B build -A x64 -DCMAKE_PREFIX_PATH=D:\SDK\telex-glove-1.2.0-windows-x64
cmake --build build --config Release
Copy-Item D:\SDK\telex-glove-1.2.0-windows-x64\bin\telex_glove.dll build\Release\
```

不用CMake时，将 `include` 加入附加包含目录，链接 `lib\telex_glove.lib`，并把
`bin\telex_glove.dll` 复制到EXE目录。

## 4. C++最小示例

```cpp
#include <telex_glove.hpp>
#include <cstdio>

int main() {
    try {
        telex::GloveHub hub("COM52", 2000000);
        hub.start(3.0);

        for (;;) {
            telex::Snapshot s;
            if (!hub.try_wait_next(telex::Hand::Left, 0.5, s)) continue;

            std::printf("seq=%u time=%llu rec=%d bat=%d fail=0x%03X\n",
                s.frame_id,
                static_cast<unsigned long long>(s.device_timestamp_us),
                s.recording ? 1 : 0,
                s.battery_percent,
                s.imu_fail_mask);

            if (s.joints_valid) {
                const telex::Vec3& tip = s.joints[4];
                std::printf("index_tip=(%.4f, %.4f, %.4f) m\n",
                            tip.x, tip.y, tip.z);
            }
        }
    } catch (const telex::Error& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
```

接收器默认使用 `2000000` baud。COM10及以上可直接写成 `"COM52"`，SDK内部会转换为
Windows设备路径。

同一COM口只能由一个程序打开。运行SDK前必须关闭Arduino串口监视器、串口助手、Python
诊断程序和其他SDK实例。

## 5. C接口关键用法

```c
telex_glove_hub* hub = NULL;
telex_glove_hub_create("COM52", 2000000, &hub);
telex_glove_hub_start(hub, 3.0);

telex_glove_snapshot snap;
memset(&snap, 0, sizeof(snap));
snap.struct_size = (uint32_t)sizeof(snap);

if (telex_glove_hub_wait_next(
        hub, TELEX_GLOVE_HAND_LEFT, 0.5, &snap) == TELEX_GLOVE_OK) {
    /* 使用snap字段 */
}

telex_glove_hub_destroy(hub);
```

`struct_size` 必须设置。1.2.0的ABI版本为3；DLL仍接受ABI 1/2的旧结构尺寸，但旧头文件无法
访问新增V2字段。新项目应始终同时更新DLL、LIB和两个头文件。

## 6. Snapshot字段

| 字段 | 含义 |
|---|---|
| `protocol` | `V2_180`、`LEGACY_168`、`LEGACY_170` |
| `protocol_version` | V2为2，legacy为1 |
| `frame_id` | 固件 `frame_seq`，uint16自然回绕 |
| `seq` | SDK进程内单调序号，可用于等待新帧 |
| `timestamp_ns` | PC单调接收时间，单位ns |
| `device_timestamp_us` | 手套本地采样时间，单位µs |
| `recording` | 固件REC状态 |
| `battery_percent` | 电量0–100 |
| `imu_fail_mask` | bit0..10对应11路IMU；1表示该路失败 |
| `wrist_gyro_valid` | 腕部角速度是否有效 |
| `imu[11]` | 11路四元数，顺序 `(w,x,y,z)` |
| `joints[21]` | 21关节坐标，单位米 |
| `joints_valid` | 是否允许使用关节结果 |
| `joint_local_rotations[16]` | 父子局部旋转，`inverse(parent) * child`，顺序见 `telex_glove_joint_rotation` |
| `joint_local_rotations_valid` | 是否允许使用16个父子局部旋转 |
| `raw_frame_size` | 原始帧长度168、170或180 |
| `raw_frame[180]` | 经协议和校验验证的原始帧 |

只有相应 `has_device_timestamp`、`has_recording_flag`、`has_fail_mask` 为真时，V2元数据才
有效。legacy协议没有这些字段，不能把其默认零值当成真实数据。

当V2的 `imu_fail_mask != 0` 时：

- 原始IMU和原始帧仍返回；
- `joints_valid=false`；
- `joint_local_rotations_valid=false`；
- 调用方不得使用 `joints` 或 `joint_local_rotations`。

`timestamp_ns` 与 `device_timestamp_us` 属于不同时间域，不能直接相互替代。左右手的
`device_timestamp_us` 也来自各自独立时钟，不能直接相减声明同步精度。

## 7. 协议枚举

```cpp
TELEX_GLOVE_PROTOCOL_UNKNOWN    = 0
TELEX_GLOVE_PROTOCOL_LEGACY_168 = 1
TELEX_GLOVE_PROTOCOL_LEGACY_170 = 2
TELEX_GLOVE_PROTOCOL_V2_180     = 3
```

正常新固件应显示：

```text
protocol=v2-180
```

## 8. 关节布局

| 下标 | 关节 |
|---|---|
| 0 | 腕部，恒为原点 |
| 1–4 | 食指 MCP/PIP/DIP/TIP |
| 5–8 | 中指 MCP/PIP/DIP/TIP |
| 9–12 | 小指 MCP/PIP/DIP/TIP |
| 13–16 | 无名指 MCP/PIP/DIP/TIP |
| 17–20 | 拇指 CMC/MCP/IP/TIP |

坐标单位为米。指尖方向为+Y，手背方向为+Z；拇指侧左手为+X、右手为-X。

## 9. 校准文件

```cpp
hub.set_calibration(telex::Hand::Left,  "imu_calibration_left.npy");
hub.set_calibration(telex::Hand::Right, "imu_calibration_right.npy");
```

未提供校准文件时仍持续出数，但只执行未校准FK。使用 `snapshot.calibrated` 判断本帧是否
应用了校准。校准加载失败不会中断串口数据流。

## 10. 无硬件回放

端口参数可改为 `file://`：

```cpp
telex::GloveHub hub("file://D:/capture.bin");
```

随包旧协议样例：

```powershell
monitor_hands.exe file://D:\SDK\telex-glove-1.2.0-windows-x64\share\telex-glove\sample_capture.bin
```

也可用真实180字节V2录制文件验证REC、时间戳和失败掩码。

## 11. 运行结果

新固件和接收器正常时，`monitor_hands` 输出类似：

```text
[左手] protocol=v2-180 frame_seq=28293 timestamp_us=284182843 recording=1
电量=92% fail_mask=0x000 gyro_valid=1 校准=否 30.0Hz 食指-TIP=(0.0204,0.0790,-0.0796)
```

成功标准：

1. `protocol=v2-180`；
2. `frame_seq` 和 `timestamp_us` 持续推进；
3. REC电平变化后 `recording` 在0/1之间变化；
4. 正常状态 `fail_mask=0x000`、`joints_valid=true`；
5. 手套运动时关节坐标变化；
6. 单手帧率约30 Hz。

## 12. 常见错误

| 错误 | 处理 |
|---|---|
| `port busy or access denied` | 关闭占用COM口的串口监视器或其他程序 |
| `no such port` | 在设备管理器确认实际COM号 |
| `unsupported baud rate` | 确认驱动及接收器串口配置，默认2000000 |
| `hand offline` | 检查手套供电、MAC配对、ESP-NOW信道和距离 |
| `joints_valid=false` | 检查 `imu_fail_mask` 对应IMU及I²C连接 |
| 只有左手数据 | 检查右手MAC是否同时写入右手和接收器固件 |
| DLL无法加载 | 将 `telex_glove.dll` 复制到EXE同目录，并确认应用为x64 |

## 13. 版本检查

```cpp
std::printf("SDK=%s ABI=%u\n",
            telex_glove_version(), telex_glove_abi_version());
```

本版本应返回：

```text
SDK=1.2.0 ABI=3
```

正式交付必须整包使用 `telex-glove-1.2.0-windows-x64`，不要混用旧版DLL、LIB或头文件。
