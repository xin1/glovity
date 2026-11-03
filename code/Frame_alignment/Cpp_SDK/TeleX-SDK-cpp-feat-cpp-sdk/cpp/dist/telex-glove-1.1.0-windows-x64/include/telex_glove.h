/*
 * TeleX Glove SDK — C ABI
 *
 * 纯 C 接口，不依赖 C++ 运行时，任何编译器均可链接。
 * 线程安全约定见 docs/集成指南.md。
 *
 * 坐标与单位：
 *   joints  21 个关节，单位米，腕部局部系，joints[0] 恒为原点
 *           指尖 +Y，手背 +Z，拇指侧：左手 +X / 右手 -X
 *   四元数  (w, x, y, z)
 *
 * Copyright (c) TeleX. All rights reserved.
 */
#ifndef TELEX_GLOVE_H
#define TELEX_GLOVE_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(TELEX_GLOVE_BUILDING)
#    define TELEX_GLOVE_API __declspec(dllexport)
#  else
#    define TELEX_GLOVE_API __declspec(dllimport)
#  endif
#else
#  define TELEX_GLOVE_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define TELEX_GLOVE_IMU_COUNT   11
#define TELEX_GLOVE_JOINT_COUNT 21
#define TELEX_GLOVE_ABI_VERSION 2

/* ---------------------------------------------------------------- 错误码 */
typedef enum telex_glove_status {
    TELEX_GLOVE_OK                 = 0,
    TELEX_GLOVE_E_INVALID_ARG      = -1,  /* 空指针 / 非法枚举 / struct_size 不符 */
    TELEX_GLOVE_E_PORT_OPEN        = -2,  /* 端口打不开（不存在 / 权限不足 / 被占用） */
    TELEX_GLOVE_E_PORT_CONFIG      = -3,  /* 波特率或串口参数设置失败 */
    TELEX_GLOVE_E_NOT_STARTED      = -4,  /* 未 start 就取数据 */
    TELEX_GLOVE_E_TIMEOUT          = -5,  /* wait_next 超时 */
    TELEX_GLOVE_E_NO_DATA          = -6,  /* 该手当前离线 */
    TELEX_GLOVE_E_CALIB_IO         = -7,  /* 校准文件读不到 */
    TELEX_GLOVE_E_CALIB_FORMAT     = -8,  /* 校准文件格式或形状不符 */
    TELEX_GLOVE_E_DISCONNECTED     = -9,  /* 运行中链路中断 */
    TELEX_GLOVE_E_OUT_OF_MEMORY    = -10,
    TELEX_GLOVE_E_INTERNAL         = -99
} telex_glove_status;

typedef enum telex_glove_hand {
    TELEX_GLOVE_HAND_LEFT  = 0,
    TELEX_GLOVE_HAND_RIGHT = 1
} telex_glove_hand;

typedef enum telex_glove_protocol {
    TELEX_GLOVE_PROTOCOL_UNKNOWN    = 0,
    TELEX_GLOVE_PROTOCOL_LEGACY_168 = 1,
    TELEX_GLOVE_PROTOCOL_LEGACY_170 = 2,
    TELEX_GLOVE_PROTOCOL_V2_180     = 3
} telex_glove_protocol;

/* ---------------------------------------------------------------- 数据结构 */
typedef struct telex_glove_quat { double w, x, y, z; } telex_glove_quat;
typedef struct telex_glove_vec3 { double x, y, z; }    telex_glove_vec3;

/*
 * 一手的完整快照。调用方在栈上分配，并必须把 struct_size 置为
 * sizeof(telex_glove_snapshot)。ABI 2 在尾部追加 V2 元数据；DLL仍接受ABI 1尺寸。
 */
typedef struct telex_glove_snapshot {
    uint32_t struct_size;

    uint64_t timestamp_ns;       /* 单调时钟，接收时刻 */
    uint64_t seq;                /* 本手快照更新序号，单调递增，可作排序键 */
    uint32_t frame_id;           /* 设备帧序号，uint16 回绕，勿作排序键 */
    int32_t  hand;               /* telex_glove_hand */
    int32_t  battery_percent;    /* 0-100 */
    int32_t  online;             /* 0/1 */
    int32_t  calibrated;         /* 本帧 FK 是否应用了三步校准 */

    telex_glove_quat imu[TELEX_GLOVE_IMU_COUNT];      /* 11 路原始四元数 */
    telex_glove_vec3 joints[TELEX_GLOVE_JOINT_COUNT]; /* 21 关节，米 */
    telex_glove_vec3 wrist_euler_deg;                 /* 腕部欧拉角，度 */
    telex_glove_vec3 wrist_gyro_dps;                  /* 腕部角速度，度/秒 */

    /* ABI 2 appended fields. Old ABI-1 callers remain accepted via struct_size. */
    uint64_t device_timestamp_us; /* 固件设备时间；仅 has_device_timestamp=1 时有效 */
    uint16_t imu_fail_mask;       /* bit0..10；仅 has_fail_mask=1 时有效 */
    uint16_t raw_frame_size;      /* 168/170/180 */
    int32_t  protocol;            /* telex_glove_protocol */
    int32_t  protocol_version;    /* legacy=1, V2=2 */
    int32_t  recording;           /* 0/1；仅 has_recording_flag=1 时有效 */
    int32_t  wrist_gyro_valid;    /* 0/1 */
    int32_t  has_device_timestamp;
    int32_t  has_recording_flag;
    int32_t  has_fail_mask;
    int32_t  joints_valid;        /* 失败掩码非零时为0，joints不得使用 */
    uint8_t  raw_frame[180];      /* 经校验的原始帧，长度见 raw_frame_size */
} telex_glove_snapshot;

/* ABI-1结构到此偏移结束；用于旧二进制调用方兼容与诊断。 */
#define TELEX_GLOVE_SNAPSHOT_ABI1_SIZE \
    ((uint32_t)offsetof(telex_glove_snapshot, device_timestamp_us))

typedef struct telex_glove_stats {
    uint32_t struct_size;
    int32_t  connected;
    int32_t  online;
    uint64_t rx_total;
    double   rx_rate_hz;
    double   last_rx_age_s;      /* 从未收到数据时为 -1 */
} telex_glove_stats;

typedef struct telex_glove_hub telex_glove_hub;

/* ---------------------------------------------------------------- 生命周期 */
/*
 * port: 串口设备（Linux 如 "/dev/ttyACM0"，Windows 如 "COM3"），
 *       或 "file://<路径>" 回放一段录制流（无需硬件即可跑通集成，也用于一致性回归）。
 *
 * 所有路径参数（port 中的回放路径、npy_path）均按 UTF-8 解释。
 * Windows 上若传入的不是合法 UTF-8，则按系统 ANSI 代码页解释，
 * 因此直接传 main() 的 argv 也能打开中文路径。
 * baud: 0 表示用默认值 2000000。
 */
TELEX_GLOVE_API telex_glove_status
telex_glove_hub_create(const char* port, int32_t baud, telex_glove_hub** out_hub);

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_start(telex_glove_hub* hub, double startup_timeout_s);

TELEX_GLOVE_API void
telex_glove_hub_stop(telex_glove_hub* hub);

/* 可在任意状态调用；内部会先 stop。传 NULL 是合法的空操作。 */
TELEX_GLOVE_API void
telex_glove_hub_destroy(telex_glove_hub* hub);

/* ---------------------------------------------------------------- 校准 */
/*
 * 设置某手的三步校准文件。npy_path 为 NULL 或空串表示取消校准。
 * 文件内容按 mtime 热重载，无需重启。
 *
 * 注意：校准文件自带手别，若与 hand 参数不符，几何以文件为准
 *       （与 Python 侧行为一致），此时返回 OK 但 last_error 会留下说明。
 * 返回非 OK 时数据流不受影响，该手退回未校准 FK。
 */
TELEX_GLOVE_API telex_glove_status
telex_glove_hub_set_calibration(telex_glove_hub* hub, telex_glove_hand hand,
                                const char* npy_path);

/* ---------------------------------------------------------------- 取数 */
/* 非阻塞取最新缓存。该手离线返回 E_NO_DATA。 */
TELEX_GLOVE_API telex_glove_status
telex_glove_hub_latest(telex_glove_hub* hub, telex_glove_hand hand,
                       telex_glove_snapshot* out);

/* 阻塞等待下一帧。超时返回 E_TIMEOUT。 */
TELEX_GLOVE_API telex_glove_status
telex_glove_hub_wait_next(telex_glove_hub* hub, telex_glove_hand hand,
                          double timeout_s, telex_glove_snapshot* out);

TELEX_GLOVE_API telex_glove_status
telex_glove_hub_stats(telex_glove_hub* hub, telex_glove_hand hand,
                      telex_glove_stats* out);

/* ---------------------------------------------------------------- 纯函数 */
/* 不需要 hub：解析单帧、或直接由 11 路四元数算关节。便于离线处理与自测。 */
TELEX_GLOVE_API telex_glove_status
telex_glove_parse_frame(const uint8_t* data, size_t len, telex_glove_snapshot* out);

TELEX_GLOVE_API telex_glove_status
telex_glove_compute_joints(const telex_glove_quat* imu11, telex_glove_hand hand,
                           const char* npy_path,
                           telex_glove_vec3* out_joints21);

/* ---------------------------------------------------------------- 元信息 */
TELEX_GLOVE_API const char* telex_glove_version(void);
TELEX_GLOVE_API uint32_t    telex_glove_abi_version(void);
TELEX_GLOVE_API const char* telex_glove_status_string(telex_glove_status s);

/* 最近一次失败的诊断信息。thread_local，仅供排查，勿用于控制流。 */
TELEX_GLOVE_API const char* telex_glove_last_error(void);

/* UTF-8 标签。下标越界返回 NULL。 */
TELEX_GLOVE_API const char* telex_glove_joint_name(int32_t index); /* 0..20 */
TELEX_GLOVE_API const char* telex_glove_imu_name(int32_t index);   /* 0..10 */

#ifdef __cplusplus
}  /* extern "C" */
#endif
#endif /* TELEX_GLOVE_H */
