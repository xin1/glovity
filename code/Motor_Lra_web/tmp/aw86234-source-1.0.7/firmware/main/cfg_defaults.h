/**
 * @file  cfg_defaults.h
 * @brief 默认参数、版本号、编译期开关。
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/* ---- 版本 ---- */
#define FW_VERSION_MAJOR    1
#define FW_VERSION_MINOR    0
#define FW_VERSION_PATCH    7
#define FW_VERSION_STR      "1.0.7"

#define BOARD_ID_VIBRATION_TEST_BOARD_A     1

/* 协议帧头 VER 定义在 proto_frame.h（PROTO_VERSION = 0x01） */

/* ---- 默认 Profile 值（协议附录 B 的默认列） ---- */
#define DEF_GAIN                128     /* 0x07，1.00× */
#define DEF_BRK_EN              1
#define DEF_STOP_MODE           0
#define DEF_VBAT_MODE           0
#define DEF_VBAT_REF            2       /* 4.0 V */
#define DEF_EN_FIR              1

#define DEF_EN_F0_DET           0
#define DEF_SIN_MODE            0
#define DEF_EDGE_FRE            0
#define DEF_F_PRE               141     /* 24000/141 ≈ 170.2 Hz */
#define DEF_DRV_WIDTH           106
#define DEF_WAIT_NUM            6
#define DEF_BRK_GAIN            8
#define DEF_TRACK_EN            1
#define DEF_DRV1_LVL            127
#define DEF_DRV2_LVL            80
#define DEF_DRV1_TIME           4
#define DEF_DRV2_TIME           6
#define DEF_BRK_TIME            8
#define DEF_TRACK_MARGIN        0x11

/* RL（LRA 阻抗）检测专用 D2S_GAIN —— 手册 §LRA 阻抗检测 第 3 步要求
 * "把 D2S_GAIN 设为合适的值"，漏做就会沿用默认 ×8（b100），算出的阻抗偏大约 2.5 倍。
 * 手册 Table 2：RL 2~25 Ω → D2S_GAIN = 20（b110 = 6）；20~55 Ω → 10（b101 = 5）。
 * 本板 1040 马达实测 8~11 Ω，取 ×20：AVG≈270 → 8.0 Ω。 */
#define DEF_RL_D2S_GAIN         6       /* 0x50 bit2:0 = b110 ⇒ ×20 */

#define DEF_WAVDAT_MODE         2       /* 12 kHz（本板 48K 不可用） */
#define DEF_RTP_FLAGS           0
#define DEF_BASE_ADDR           0x0800
#define DEF_FIFO_AE             512
#define DEF_FIFO_AF             1536

/* TRIG 段默认值（★ 三个默认值陷阱的"正确值"，见 02 文档 §2） */
#define DEF_TRIG1_POS_EN        1       /* 0x33 bit7：默认 0 = 不触发，必须置 1（T-3） */
#define DEF_TRIG1_SEQ_P         1
#define DEF_TRIG1_NEG_EN        0
#define DEF_TRIG1_SEQ_N         1
#define DEF_TRIG1_POLAR         0
#define DEF_TRIG1_LEV           0
#define DEF_TRIG1_BRK           1       /* 0x39 bit3：TRIG 模式必须为 1 */
#define DEF_TRIG1_STOP          1       /* 0x3A bit2：默认 0 = 停不下来，必须置 1（T-4） */
#define DEF_TRIG1_PRIO          3
#define DEF_TRG_GAIN            0x80
#define DEF_TRIG1_MODE          3       /* 0x45 bit6:4：默认 7(None)，必须写 3（T-1） */
#define DEF_INTN_PIN            0       /* 0x45 bit0：默认 1(INTN)，本板必须 0（T-2） */
#define DEF_START_DLY           8

/* ---- 安全 ---- */
#define DEF_LOG_LEVEL           2       /* warn；二进制模式下建议降到 1 或 0 */

/* ---- 满幅判定阈值（协议 §11）----
 * 放在这个"纯逻辑"头里，因为 aw_profile.c 也用它，而 cfg_board.h 依赖 ESP 的
 * driver/gpio.h，纯逻辑模块不能包含。 */
#define BOARD_FULL_AMP_CONT_DRV1_LVL        96
#define BOARD_FULL_AMP_RAM_GAIN             192

/* ---- 播放结束轮询（协议 §6.2） ---- */
#define PLAY_POLL_INTERVAL_MS_FEW   2   /* 1~3 路 */
#define PLAY_POLL_INTERVAL_MS_MANY  5   /* ≥4 路 */

/* ---- 任务优先级与栈 ---- */
#define TASK_PRIO_SAFETY        15
#define TASK_PRIO_PROTO         10
#define TASK_PRIO_MON           5
#define TASK_STACK_SAFETY       3072
#define TASK_STACK_PROTO        6144
#define TASK_STACK_MON          4096

/* ---- 缓冲区 ---- */
#define MAX_PLAY_CHANNELS       8
#define EVENT_QUEUE_DEPTH       32
#define WAVE_BUF_BYTES          3072    /* 波形会话累积缓冲 */
#define ASCII_LINE_MAX          256

/* ---- 编译期开关 ----
 * PROTO_ALLOW_RAW_LOG：二进制模式下允许裸 printf 写协议口。
 *   ⚠️ 打开会撕裂协议帧，**只**在裸机排查时临时置 1。
 */
#define PROTO_ALLOW_RAW_LOG     0

/* 主循环任务周期 */
#define SAFETY_TICK_MS          10
#define MON_TICK_MS             5

#ifdef __cplusplus
}
#endif
