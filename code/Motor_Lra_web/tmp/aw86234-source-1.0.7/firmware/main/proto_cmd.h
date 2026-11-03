/**
 * @file  proto_cmd.h
 * @brief 命令码 / 事件码 / 错误码 / 能力位 / 枚举常量。
 *
 * 与上位机 `host/awtest/protocol/commands.py` **一一对应**，
 * 依据 `03_串口协议规范_v1.0.md` §6/§8/§9/§10。
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- 系统组 ---- */
#define CMD_PING                    0x01u
#define CMD_GET_CAPS                0x02u
#define CMD_GET_STATUS              0x03u
#define CMD_MCU_RESET               0x04u
#define CMD_SET_LOG_LEVEL           0x05u
#define CMD_SET_SAFETY              0x07u
#define CMD_GET_SAFETY              0x08u
#define CMD_CLEAR_ERRORS            0x09u
#define CMD_SET_PROTO_MODE          0x0Au

/* ---- 总线组 ---- */
#define CMD_BUS_PROBE               0x10u
#define CMD_BUS_SET_SPEED           0x11u
#define CMD_BUS_SET_PULLUP          0x12u
#define CMD_BUS_RECOVER             0x13u
#define CMD_TCA_SET_MASK            0x14u
#define CMD_TCA_GET_MASK            0x15u
#define CMD_TCA_RESET               0x16u
#define CMD_TCA_READ_CTRL           0x17u

/* ---- 通道组 ---- */
#define CMD_CH_PROBE                0x18u
#define CMD_CH_PROBE_ALL            0x19u
#define CMD_CH_GET_INFO             0x1Au

/* ---- 寄存器组 ---- */
#define CMD_REG_READ                0x20u
#define CMD_REG_WRITE               0x21u
#define CMD_REG_UPDATE              0x22u
#define CMD_REG_READ_MULTI          0x23u
#define CMD_REG_DUMP                0x24u
#define CMD_STREAM_WRITE            0x26u

/* ---- 器件组 ---- */
#define CMD_AW_SOFTRST              0x30u
#define CMD_AW_WAKE                 0x31u
#define CMD_AW_STANDBY              0x32u
#define CMD_AW_INIT_DEFAULT         0x33u
#define CMD_AW_GET_STATE            0x34u

/* ---- 播放组 ---- */
#define CMD_SET_MODE                0x40u
#define CMD_PLAY                    0x41u
#define CMD_STOP                    0x42u
#define CMD_PLAY_MULTI              0x43u
#define CMD_TRIG_PULSE              0x44u
#define CMD_TRIG_LEVEL              0x45u
#define CMD_TRIG_PATTERN            0x46u
#define CMD_GET_PLAY_STATE          0x47u
#define CMD_STOP_ALL                0x49u
#define CMD_PLAY_TIMED             0x4Bu
#define CMD_BOOT_ARM               0x4Cu
#define CMD_LIVE_CONTROL           0x4Du
#define CMD_LIVE_KEEPALIVE         0x4Eu
#define CMD_TACTILE_FRAME          0x4Fu
#define CMD_TRIG_PARK               0x4Au

/* ---- 参数组 ---- */
#define CMD_SET_PROFILE             0x50u
#define CMD_GET_PROFILE             0x51u
#define CMD_SET_CONT_LIVE           0x52u
#define CMD_SET_GAIN                0x53u
#define CMD_SET_VBAT                0x54u
#define CMD_SET_RAM_PARAMS          0x55u
#define CMD_SET_CONT_PARAMS         0x56u
#define CMD_SET_RTP_PARAMS          0x57u
#define CMD_RTP_DATA                0x58u
#define CMD_SET_TRIG_PARAMS         0x59u
#define CMD_SET_DEFAULT_PROFILE     0x5Au

/* ---- 波形组 ---- */
#define CMD_WAVE_BEGIN              0x60u
#define CMD_WAVE_ADD                0x61u
#define CMD_WAVE_COMMIT             0x62u
#define CMD_WAVE_ABORT              0x63u
#define CMD_WAVE_GET_LIBRARY        0x64u
#define CMD_WAVE_READBACK           0x65u
#define CMD_WAVE_ERASE              0x66u
#define CMD_WAVE_QUEUE              0x67u
#define CMD_WAVE_GEN                0x68u

/* ---- 测量组 ---- */
#define CMD_F0_START                0x70u
#define CMD_F0_RESULT               0x71u
#define CMD_DET_READ                0x72u
#define CMD_MON_START               0x73u
#define CMD_MON_STOP                0x74u
#define CMD_SNAPSHOT                0x75u

/* ---- 测试组 ---- */
#define CMD_SWEEP_AMP               0x80u
#define CMD_SWEEP_FREQ              0x81u
#define CMD_SWEEP_ABORT             0x82u
#define CMD_TEST_RUN                0x83u
#define CMD_TEST_ABORT              0x84u
#define CMD_SELFTEST                0x85u

/* ---- 事件（设备 → 主机） ---- */
#define EVT_LOG                     0xE0u
#define EVT_PLAY_STARTED            0xE1u
#define EVT_PLAY_DONE               0xE2u
#define EVT_MEASURE_RESULT          0xE3u
#define EVT_SWEEP_PROGRESS          0xE4u
#define EVT_SWEEP_DONE              0xE5u
#define EVT_ERROR                   0xE6u
#define EVT_SAFETY_TRIP             0xE7u
#define EVT_TEST_PROGRESS           0xE8u
#define EVT_SELFTEST_RESULT         0xE9u
#define EVT_BUS_RECOVERED           0xEAu

/* ---- 错误码（协议 §6.1） ---- */
#define ERR_OK                      0x00u
#define ERR_UNKNOWN_CMD             0x01u
#define ERR_BAD_LENGTH              0x02u
#define ERR_BAD_PARAM               0x03u
#define ERR_BAD_CHANNEL             0x04u
#define ERR_NOT_PRESENT             0x05u
#define ERR_I2C_TIMEOUT             0x06u
#define ERR_I2C_NACK                0x07u
#define ERR_BUS_BUSY                0x08u
#define ERR_NOT_INITIALIZED         0x09u
#define ERR_WRONG_MODE              0x0Au
#define ERR_BUSY                    0x0Bu
#define ERR_NOT_PLAYING             0x0Cu
#define ERR_SRAM_OVERFLOW           0x0Du
#define ERR_CRC_MISMATCH            0x0Eu
#define ERR_UNSUPPORTED             0x0Fu
#define ERR_SAFETY_LIMIT            0x10u
#define ERR_SAFETY_TIMEOUT          0x11u
#define ERR_HEARTBEAT_LOST          0x12u
#define ERR_INTERNAL                0x13u
#define ERR_VERIFY_FAILED           0x14u
#define ERR_STORAGE                 0x15u
#define ERR_TCA_NOT_FOUND           0x16u
#define ERR_CHIPID_MISMATCH         0x17u
#define ERR_NOT_ARMED               0x18u
#define ERR_TOO_MANY_CHUNKS         0x19u

/* ---- 能力位（GET_CAPS） ---- */
#define CAP_RAM                     (1u << 0)
#define CAP_RTP                     (1u << 1)
#define CAP_CONT                    (1u << 2)
#define CAP_TRIG                    (1u << 3)
#define CAP_WAVE_DOWNLOAD           (1u << 4)
#define CAP_F0_DETECT               (1u << 5)
#define CAP_SWEEP                   (1u << 6)
#define CAP_LOCAL_TRIG_PULSE        (1u << 7)
#define CAP_REG_ACCESS              (1u << 8)
#define CAP_TCA_ONLINE              (1u << 9)
#define CAP_MOTOR_POWER_GPIO        (1u << 10)  /* 本板 = 0（SW3 手动控制） */
#define CAP_RSTN_GPIO               (1u << 11)  /* 本板 = 0（RSTN 并 VDD） */
#define CAP_SHARED_TRIG_567         (1u << 12)  /* 本板 = 1 */
#define CAP_RTP_48K                 (1u << 13)  /* 本板 = 0 */
#define CAP_INTERNAL_PULLUP         (1u << 14)  /* 运行期由探测结果决定 */
#define CAP_TIMED_PLAY             (1u << 16)
#define CAP_BOOT_TRIGGER           (1u << 17)
#define CAP_LIVE_LAB               (1u << 18)
#define CAP_TACTILE                (1u << 19)
#define CAP_ASCII_MODE              (1u << 15)

/* ---- 播放模式 ---- */
#define MODE_RAM                    0u
#define MODE_RTP                    1u
#define MODE_CONT                   2u
#define MODE_TRIG                   3u
#define MODE_UNKNOWN                0xFFu

/* ---- GLB_STATE（0x3F bit3:0，只读） ---- */
#define GLB_STANDBY                 0u
#define GLB_CONT                    6u
#define GLB_RAM                     7u
#define GLB_RTP                     8u
#define GLB_TRIG                    9u
#define GLB_BRAKE                   11u

/* 是否处于"正在播放"状态。
 * ⚠️ 实测（2026-09，AW86234FCR ×4）：WAKE=1 时 GLB_STATE 无论空闲/播放中/停止后
 *    都读 3（未定义值），WAKE=0 时恒为 0 —— 这颗芯片的 GLB_STATE 不反映播放状态。
 *    所以本宏只用于"排除正在播放"，**不能**作为播放结束的可靠判据；
 *    播放结束请用 SYSINT(REG_SYSINT) bit0 DONEI。 */
#define GLB_IS_PLAYING(g)   ((g) == GLB_CONT || (g) == GLB_RAM || (g) == GLB_RTP || \
                             (g) == GLB_TRIG || (g) == GLB_BRAKE)

/* ---- 安全状态 ---- */
#define SAFETY_NORMAL               0u
#define SAFETY_HEARTBEAT_LOST       1u
#define SAFETY_BUS_LOCKED           2u

/* ---- EVT_PLAY_DONE.reason ---- */
#define PLAY_DONE_DURATION         5u
#define PLAY_DONE_NATURAL           0u
#define PLAY_DONE_STOPPED           1u
#define PLAY_DONE_SAFETY_TIMEOUT    2u
#define PLAY_DONE_ERROR             3u
#define PLAY_DONE_HEARTBEAT_LOST    4u

/* ---- EVT_SAFETY_TRIP.reason ---- */
#define TRIP_PLAY_TIMEOUT           0u
#define TRIP_HEARTBEAT_LOST         1u
#define TRIP_TOO_MANY_FULL_AMP      2u
#define TRIP_BUS_ERROR              3u
#define TRIP_LIVE_LEASE             4u

/* ---- 日志级别 ---- */
#define LOG_OFF                     0u
#define LOG_ERROR                   1u
#define LOG_WARN                    2u
#define LOG_INFO                    3u
#define LOG_DEBUG                   4u

/* ---- 采样率编码 ---- */
#define RATE_24K                    0u
#define RATE_48K                    1u
#define RATE_12K                    2u
#define RATE_12K_ALT                3u

/* ---- DET_READ seq ---- */
#define DET_SEQ_VBAT                0u
#define DET_SEQ_RL                  3u
#define DET_SEQ_OS                  4u

/* ---- MON_START.fields ---- */
#define MON_FIELD_SYSST             (1u << 0)
#define MON_FIELD_SYSST2            (1u << 1)
#define MON_FIELD_GLB_STATE         (1u << 2)
#define MON_FIELD_DETRD             (1u << 3)
#define MON_FIELD_CONTRD            (1u << 4)
#define MON_FIELD_LDO               (1u << 5)

/* ---- WAVE_GEN shape ---- */
#define SHAPE_SINE                  0u
#define SHAPE_SQUARE                1u
#define SHAPE_TRIANGLE              2u
#define SHAPE_SAWTOOTH              3u
#define SHAPE_SINE_RISE             4u
#define SHAPE_SINE_FALL             5u
#define SHAPE_CLICK                 6u

/* ---- 寄存器地址（只列固件要用的） ---- */
#define REG_RSTCFG                  0x00u
#define REG_SYSST                   0x01u
#define REG_SYSINT                  0x02u   /* RC，读清；bit0 = DONEI 播放结束事件 */
#define BIT_DONEI                   0x01u   /* REG_SYSINT bit0 */
#define REG_SYSST2                  0x04u
#define REG_PLAYCFG2                0x07u
#define REG_PLAYCFG3                0x08u
#define REG_PLAYCFG4                0x09u
#define REG_WAVCFG1                 0x0Au
#define REG_WAVCFG9                 0x12u
#define REG_WAVCFG12                0x15u   /* bit7:4 SEQ7LOOP, bit3:0 SEQ8LOOP */
/* ★ 0x16 WAVCFG13 = WAITSLOT(bit5:4) + MAINLOOP(bit3:0)。
 *   注意 0x15 是 WAVCFG12（SEQ7/SEQ8LOOP），**不是** MAINLOOP —— 早期实现把
 *   MAINLOOP 写到了 0x15，导致 0x16 恒为 0（= 只播 1 次），"持续振动"永远做不到。
 *   实测（2026-09，AW86234FCR）：0x16=0x0F 时 RAM 播放 3 秒不报 DONE，持续有效。 */
#define REG_WAVCFG13                0x16u
#define REG_CONTCFG1                0x17u
#define REG_F_LRA_F0_H              0x24u
#define REG_F_LRA_F0_L              0x25u
#define REG_CONT_F0_H               0x26u
#define REG_CONT_F0_L               0x27u
#define REG_BASE_ADDR_H             0x2Cu
#define REG_BASE_ADDR_L             0x2Du
#define REG_RTPDATA                 0x32u
#define REG_TRGCFG1                 0x33u
#define REG_TRGCFG4                 0x36u
#define REG_TRGCFG7                 0x39u
#define REG_TRGCFG8                 0x3Au
#define REG_GLBCFG2                 0x3Cu
#define REG_GLBCFG4                 0x3Eu
#define REG_GLBRD5                  0x3Fu
#define REG_RAMADDRH                0x40u
#define REG_RAMADDRL                0x41u
#define REG_RAMDATA                 0x42u
#define REG_TRGCFG9                 0x43u
#define REG_SYSCTRL1                0x45u
#define REG_SYSCTRL2                0x46u
#define REG_VBATCTRL                0x4Eu
#define REG_DETCFG1                 0x4Fu
#define REG_DETCFG2                 0x50u
#define REG_DETRD1                  0x51u
#define REG_DETRD2                  0x52u
#define REG_DETRD3                  0x53u
#define REG_IDH                     0x57u
#define REG_IDL                     0x69u

/* PLAYCFG3(0x08) 位 */
#define BIT_PLAY_MODE_0             0x01u
#define BIT_PLAY_MODE_1             0x02u
#define BIT_BRK_EN                  0x04u
#define BIT_STOP_MODE               0x08u
/* PLAYCFG4(0x09) 位 */
#define BIT_GO                      0x01u
#define BIT_STOP                    0x02u
/* SYSCTRL1(0x45) 位 */
#define BIT_INTN_PIN                0x01u
#define BIT_EN_FIR                  0x02u
#define BIT_EN_RAMINIT              0x04u
#define SHIFT_TRG1_MODE             4
#define MASK_TRG1_MODE              0x70u
/* SYSCTRL2(0x46) 位 */
#define BIT_WAVDAT_MODE_0           0x01u
#define BIT_WAVDAT_MODE_1           0x02u
#define BIT_STANDBY                 0x40u
#define BIT_WAKE                    0x80u
/* TRGCFG7(0x39) 位 */
#define BIT_TRG1_BRK                0x08u
#define BIT_TRG1_LEV                0x10u
#define BIT_TRG1_POLAR              0x20u
/* TRGCFG8(0x3A) 位 */
#define BIT_TRG1_STOP               0x04u
/* TRGCFG1(0x33) 位 */
#define BIT_TRG1_POS                0x80u
/* CONTCFG1(0x17) 位 */
#define BIT_EN_F0_DET               0x20u
/* SYSST2(0x04) 位 */
#define BIT_LDO_OK                  0x08u

/* 立即停止语义 */
#define STOP_MODE_IMMEDIATE         1u
#define STOP_MODE_FINISH_WAVE       0u

#ifdef __cplusplus
}
#endif
