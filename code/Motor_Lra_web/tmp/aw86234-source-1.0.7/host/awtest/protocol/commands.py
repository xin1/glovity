# -*- coding: utf-8 -*-
"""命令码 / 事件码 / 错误码 / 能力位 / 枚举常量。

全部取自 ``03_串口协议规范_v1.0.md`` §6、§8、§9、§10。
**本文件是这些常量的单一事实来源**，界面与编解码都从这里取，不要散落魔法数字。
"""

from __future__ import annotations

from typing import Dict

# --------------------------------------------------------------------------
# 组基址
# --------------------------------------------------------------------------

GRP_SYS = 0x00
GRP_BUS = 0x10
GRP_CH = 0x18
GRP_REG = 0x20
GRP_DEV = 0x30
GRP_PLAY = 0x40
GRP_PARAM = 0x50
GRP_WAVE = 0x60
GRP_MEAS = 0x70
GRP_TEST = 0x80
GRP_EVT = 0xE0

# --------------------------------------------------------------------------
# 命令码（协议 §8 命令总表）
# --------------------------------------------------------------------------

CMD_PING = 0x01
CMD_GET_CAPS = 0x02
CMD_GET_STATUS = 0x03
CMD_MCU_RESET = 0x04
CMD_SET_LOG_LEVEL = 0x05
CMD_SET_SAFETY = 0x07
CMD_GET_SAFETY = 0x08
CMD_CLEAR_ERRORS = 0x09
CMD_SET_PROTO_MODE = 0x0A

CMD_BUS_PROBE = 0x10
CMD_BUS_SET_SPEED = 0x11
CMD_BUS_SET_PULLUP = 0x12
CMD_BUS_RECOVER = 0x13
CMD_TCA_SET_MASK = 0x14
CMD_TCA_GET_MASK = 0x15
CMD_TCA_RESET = 0x16
CMD_TCA_READ_CTRL = 0x17

CMD_CH_PROBE = 0x18
CMD_CH_PROBE_ALL = 0x19
CMD_CH_GET_INFO = 0x1A

CMD_REG_READ = 0x20
CMD_REG_WRITE = 0x21
CMD_REG_UPDATE = 0x22
CMD_REG_READ_MULTI = 0x23
CMD_REG_DUMP = 0x24
CMD_STREAM_WRITE = 0x26

CMD_AW_SOFTRST = 0x30
CMD_AW_WAKE = 0x31
CMD_AW_STANDBY = 0x32
CMD_AW_INIT_DEFAULT = 0x33
CMD_AW_GET_STATE = 0x34

CMD_SET_MODE = 0x40
CMD_PLAY = 0x41
CMD_STOP = 0x42
CMD_PLAY_MULTI = 0x43
CMD_TRIG_PULSE = 0x44
CMD_TRIG_LEVEL = 0x45
CMD_TRIG_PATTERN = 0x46
CMD_GET_PLAY_STATE = 0x47
CMD_STOP_ALL = 0x49
CMD_TRIG_PARK = 0x4A
CMD_PLAY_TIMED = 0x4B
CMD_BOOT_ARM = 0x4C
CMD_LIVE_CONTROL = 0x4D
CMD_LIVE_KEEPALIVE = 0x4E
CMD_TACTILE_FRAME = 0x4F

CMD_SET_PROFILE = 0x50
CMD_GET_PROFILE = 0x51
CMD_SET_CONT_LIVE = 0x52
CMD_SET_GAIN = 0x53
CMD_SET_VBAT = 0x54
CMD_SET_RAM_PARAMS = 0x55
CMD_SET_CONT_PARAMS = 0x56
CMD_SET_RTP_PARAMS = 0x57
CMD_RTP_DATA = 0x58
CMD_SET_TRIG_PARAMS = 0x59
CMD_SET_DEFAULT_PROFILE = 0x5A

CMD_WAVE_BEGIN = 0x60
CMD_WAVE_ADD = 0x61
CMD_WAVE_COMMIT = 0x62
CMD_WAVE_ABORT = 0x63
CMD_WAVE_GET_LIBRARY = 0x64
CMD_WAVE_READBACK = 0x65
CMD_WAVE_ERASE = 0x66
CMD_WAVE_QUEUE = 0x67
CMD_WAVE_GEN = 0x68

CMD_F0_START = 0x70
CMD_F0_RESULT = 0x71
CMD_DET_READ = 0x72
CMD_MON_START = 0x73
CMD_MON_STOP = 0x74
CMD_SNAPSHOT = 0x75

CMD_SWEEP_AMP = 0x80
CMD_SWEEP_FREQ = 0x81
CMD_SWEEP_ABORT = 0x82
CMD_TEST_RUN = 0x83
CMD_TEST_ABORT = 0x84
CMD_SELFTEST = 0x85

# 事件（设备 → 主机）
EVT_LOG = 0xE0
EVT_PLAY_STARTED = 0xE1
EVT_PLAY_DONE = 0xE2
EVT_MEASURE_RESULT = 0xE3
EVT_SWEEP_PROGRESS = 0xE4
EVT_SWEEP_DONE = 0xE5
EVT_ERROR = 0xE6
EVT_SAFETY_TRIP = 0xE7
EVT_TEST_PROGRESS = 0xE8
EVT_SELFTEST_RESULT = 0xE9
EVT_BUS_RECOVERED = 0xEA

CMD_NAMES: Dict[int, str] = {
    CMD_PING: "PING", CMD_GET_CAPS: "GET_CAPS", CMD_GET_STATUS: "GET_STATUS",
    CMD_MCU_RESET: "MCU_RESET", CMD_SET_LOG_LEVEL: "SET_LOG_LEVEL",
    CMD_SET_SAFETY: "SET_SAFETY", CMD_GET_SAFETY: "GET_SAFETY",
    CMD_CLEAR_ERRORS: "CLEAR_ERRORS", CMD_SET_PROTO_MODE: "SET_PROTO_MODE",
    CMD_BUS_PROBE: "BUS_PROBE", CMD_BUS_SET_SPEED: "BUS_SET_SPEED",
    CMD_BUS_SET_PULLUP: "BUS_SET_PULLUP", CMD_BUS_RECOVER: "BUS_RECOVER",
    CMD_TCA_SET_MASK: "TCA_SET_MASK", CMD_TCA_GET_MASK: "TCA_GET_MASK",
    CMD_TCA_RESET: "TCA_RESET", CMD_TCA_READ_CTRL: "TCA_READ_CTRL",
    CMD_CH_PROBE: "CH_PROBE", CMD_CH_PROBE_ALL: "CH_PROBE_ALL",
    CMD_CH_GET_INFO: "CH_GET_INFO",
    CMD_REG_READ: "REG_READ", CMD_REG_WRITE: "REG_WRITE",
    CMD_REG_UPDATE: "REG_UPDATE", CMD_REG_READ_MULTI: "REG_READ_MULTI",
    CMD_REG_DUMP: "REG_DUMP", CMD_STREAM_WRITE: "STREAM_WRITE",
    CMD_AW_SOFTRST: "AW_SOFTRST", CMD_AW_WAKE: "AW_WAKE",
    CMD_AW_STANDBY: "AW_STANDBY", CMD_AW_INIT_DEFAULT: "AW_INIT_DEFAULT",
    CMD_AW_GET_STATE: "AW_GET_STATE",
    CMD_SET_MODE: "SET_MODE", CMD_PLAY: "PLAY", CMD_STOP: "STOP",
    CMD_PLAY_MULTI: "PLAY_MULTI", CMD_TRIG_PULSE: "TRIG_PULSE",
    CMD_TRIG_LEVEL: "TRIG_LEVEL", CMD_TRIG_PATTERN: "TRIG_PATTERN",
    CMD_GET_PLAY_STATE: "GET_PLAY_STATE", CMD_STOP_ALL: "STOP_ALL",
    CMD_TRIG_PARK: "TRIG_PARK",
    CMD_PLAY_TIMED: "PLAY_TIMED", CMD_BOOT_ARM: "BOOT_ARM",
    CMD_LIVE_CONTROL: "LIVE_CONTROL", CMD_LIVE_KEEPALIVE: "LIVE_KEEPALIVE",
    CMD_SET_PROFILE: "SET_PROFILE", CMD_GET_PROFILE: "GET_PROFILE",
    CMD_SET_CONT_LIVE: "SET_CONT_LIVE", CMD_SET_GAIN: "SET_GAIN",
    CMD_SET_VBAT: "SET_VBAT", CMD_SET_RAM_PARAMS: "SET_RAM_PARAMS",
    CMD_SET_CONT_PARAMS: "SET_CONT_PARAMS", CMD_SET_RTP_PARAMS: "SET_RTP_PARAMS",
    CMD_RTP_DATA: "RTP_DATA", CMD_SET_TRIG_PARAMS: "SET_TRIG_PARAMS",
    CMD_SET_DEFAULT_PROFILE: "SET_DEFAULT_PROFILE",
    CMD_WAVE_BEGIN: "WAVE_BEGIN", CMD_WAVE_ADD: "WAVE_ADD",
    CMD_WAVE_COMMIT: "WAVE_COMMIT", CMD_WAVE_ABORT: "WAVE_ABORT",
    CMD_WAVE_GET_LIBRARY: "WAVE_GET_LIBRARY", CMD_WAVE_READBACK: "WAVE_READBACK",
    CMD_WAVE_ERASE: "WAVE_ERASE", CMD_WAVE_QUEUE: "WAVE_QUEUE",
    CMD_WAVE_GEN: "WAVE_GEN",
    CMD_F0_START: "F0_START", CMD_F0_RESULT: "F0_RESULT",
    CMD_DET_READ: "DET_READ", CMD_MON_START: "MON_START",
    CMD_MON_STOP: "MON_STOP", CMD_SNAPSHOT: "SNAPSHOT",
    CMD_SWEEP_AMP: "SWEEP_AMP", CMD_SWEEP_FREQ: "SWEEP_FREQ",
    CMD_SWEEP_ABORT: "SWEEP_ABORT", CMD_TEST_RUN: "TEST_RUN",
    CMD_TEST_ABORT: "TEST_ABORT", CMD_SELFTEST: "SELFTEST",
    EVT_LOG: "EVT_LOG", EVT_PLAY_STARTED: "EVT_PLAY_STARTED",
    EVT_PLAY_DONE: "EVT_PLAY_DONE", EVT_MEASURE_RESULT: "EVT_MEASURE_RESULT",
    EVT_SWEEP_PROGRESS: "EVT_SWEEP_PROGRESS", EVT_SWEEP_DONE: "EVT_SWEEP_DONE",
    EVT_ERROR: "EVT_ERROR", EVT_SAFETY_TRIP: "EVT_SAFETY_TRIP",
    EVT_TEST_PROGRESS: "EVT_TEST_PROGRESS", EVT_SELFTEST_RESULT: "EVT_SELFTEST_RESULT",
    EVT_BUS_RECOVERED: "EVT_BUS_RECOVERED",
}

# 手册中完全未定义的寄存器地址（协议附录 D 第 11 条）——写入前必须警告
UNDEFINED_REGISTERS = (
    [0x05, 0x06, 0x22, 0x23]
    + list(range(0x28, 0x2C))
    + [0x3B, 0x3D, 0x44]
    + list(range(0x47, 0x4A))
    + list(range(0x54, 0x57))
    + [0x58, 0x59]
    + list(range(0x5B, 0x69))
    + list(range(0x6A, 0x72))
)

# --------------------------------------------------------------------------
# 错误码（协议 §6.1）
# --------------------------------------------------------------------------

ERR_OK = 0x00
ERR_UNKNOWN_CMD = 0x01
ERR_BAD_LENGTH = 0x02
ERR_BAD_PARAM = 0x03
ERR_BAD_CHANNEL = 0x04
ERR_NOT_PRESENT = 0x05
ERR_I2C_TIMEOUT = 0x06
ERR_I2C_NACK = 0x07
ERR_BUS_BUSY = 0x08
ERR_NOT_INITIALIZED = 0x09
ERR_WRONG_MODE = 0x0A
ERR_BUSY = 0x0B
ERR_NOT_PLAYING = 0x0C
ERR_SRAM_OVERFLOW = 0x0D
ERR_CRC_MISMATCH = 0x0E
ERR_UNSUPPORTED = 0x0F
ERR_SAFETY_LIMIT = 0x10
ERR_SAFETY_TIMEOUT = 0x11
ERR_HEARTBEAT_LOST = 0x12
ERR_INTERNAL = 0x13
ERR_VERIFY_FAILED = 0x14
ERR_STORAGE = 0x15
ERR_TCA_NOT_FOUND = 0x16
ERR_CHIPID_MISMATCH = 0x17
ERR_NOT_ARMED = 0x18
ERR_TOO_MANY_CHUNKS = 0x19

ERR_NAMES: Dict[int, str] = {
    0x00: "OK", 0x01: "ERR_UNKNOWN_CMD", 0x02: "ERR_BAD_LENGTH",
    0x03: "ERR_BAD_PARAM", 0x04: "ERR_BAD_CHANNEL", 0x05: "ERR_NOT_PRESENT",
    0x06: "ERR_I2C_TIMEOUT", 0x07: "ERR_I2C_NACK", 0x08: "ERR_BUS_BUSY",
    0x09: "ERR_NOT_INITIALIZED", 0x0A: "ERR_WRONG_MODE", 0x0B: "ERR_BUSY",
    0x0C: "ERR_NOT_PLAYING", 0x0D: "ERR_SRAM_OVERFLOW", 0x0E: "ERR_CRC_MISMATCH",
    0x0F: "ERR_UNSUPPORTED", 0x10: "ERR_SAFETY_LIMIT", 0x11: "ERR_SAFETY_TIMEOUT",
    0x12: "ERR_HEARTBEAT_LOST", 0x13: "ERR_INTERNAL", 0x14: "ERR_VERIFY_FAILED",
    0x15: "ERR_STORAGE", 0x16: "ERR_TCA_NOT_FOUND", 0x17: "ERR_CHIPID_MISMATCH",
    0x18: "ERR_NOT_ARMED", 0x19: "ERR_TOO_MANY_CHUNKS",
}

ERR_CN: Dict[int, str] = {
    ERR_OK: "成功",
    ERR_UNKNOWN_CMD: "未知命令码（可能协议版本不匹配）",
    ERR_BAD_LENGTH: "载荷长度不符",
    ERR_BAD_PARAM: "参数越界或非法",
    ERR_BAD_CHANNEL: "通道号非法",
    ERR_NOT_PRESENT: "该通道无器件应答（0x5A 无 ACK）",
    ERR_I2C_TIMEOUT: "I2C 传输超时",
    ERR_I2C_NACK: "I2C 收到 NACK",
    ERR_BUS_BUSY: "总线被占用",
    ERR_NOT_INITIALIZED: "未探测/未初始化，请先执行总线探测",
    ERR_WRONG_MODE: "当前播放模式不支持该操作",
    ERR_BUSY: "该通道有未完成操作",
    ERR_NOT_PLAYING: "当前没有在播放",
    ERR_SRAM_OVERFLOW: "波形库超过 SRAM 容量",
    ERR_CRC_MISMATCH: "SRAM 回读校验失败",
    ERR_UNSUPPORTED: "硬件不支持（如 RTP 48K）",
    ERR_SAFETY_LIMIT: "超出同时满幅路数限制",
    ERR_SAFETY_TIMEOUT: "播放安全超时触发",
    ERR_HEARTBEAT_LOST: "心跳丢失，已进入安全态",
    ERR_INTERNAL: "固件内部错误",
    ERR_VERIFY_FAILED: "写后回读不一致",
    ERR_STORAGE: "Profile 存取失败",
    ERR_TCA_NOT_FOUND: "TCA9548A（0x70）无应答",
    ERR_CHIPID_MISMATCH: "CHIPID 与预期型号不符",
    ERR_NOT_ARMED: "波形下载会话未开始",
    ERR_TOO_MANY_CHUNKS: "波形分片序号超出",
}

# --------------------------------------------------------------------------
# 能力位（协议 §9.1 GET_CAPS）
# --------------------------------------------------------------------------

CAP_RAM = 1 << 0
CAP_RTP = 1 << 1
CAP_CONT = 1 << 2
CAP_TRIG = 1 << 3
CAP_WAVE_DOWNLOAD = 1 << 4
CAP_F0_DETECT = 1 << 5
CAP_SWEEP = 1 << 6
CAP_LOCAL_TRIG_PULSE = 1 << 7
CAP_REG_ACCESS = 1 << 8
CAP_TCA_ONLINE = 1 << 9
CAP_MOTOR_POWER_GPIO = 1 << 10
CAP_RSTN_GPIO = 1 << 11
CAP_SHARED_TRIG_567 = 1 << 12
CAP_RTP_48K = 1 << 13
CAP_INTERNAL_PULLUP = 1 << 14
CAP_ASCII_MODE = 1 << 15
CAP_TIMED_PLAY = 1 << 16
CAP_BOOT_TRIGGER = 1 << 17
CAP_LIVE_LAB = 1 << 18
CAP_TACTILE = 1 << 19

CAP_LABELS = [
    (CAP_TACTILE, "触觉输入反馈", "逐通道电平，300ms新数据租约"),
    (CAP_LIVE_LAB, "实时触感实验室", "固件合成正弦载波与包络，独立续租保护"),
    (CAP_TIMED_PLAY, "定时播放", "正常到时停止，独立安全保护"),
    (CAP_BOOT_TRIGGER, "BOOT 触发已上传波形", "显式下发后启用"),
    (CAP_RAM, "RAM 模式", "播放 SRAM 波形库中的波形"),
    (CAP_RTP, "RTP 模式", "主机流式喂数据"),
    (CAP_CONT, "CONT 模式", "连续驱动 / F0 检测 / 谐振跟踪"),
    (CAP_TRIG, "TRIG 模式", "硬件引脚触发"),
    (CAP_WAVE_DOWNLOAD, "波形下载", "WAVE_* 自定义波形下发"),
    (CAP_F0_DETECT, "F0 检测", "谐振频率自动检测"),
    (CAP_SWEEP, "幅度/频率扫描", "SWEEP_*"),
    (CAP_LOCAL_TRIG_PULSE, "本地 TRIG 脉冲串", "µs 级脉冲由固件本地生成"),
    (CAP_REG_ACCESS, "寄存器直读直写", "寄存器组命令"),
    (CAP_TCA_ONLINE, "TCA9548A 在线", "8 路分路器"),
    (CAP_MOTOR_POWER_GPIO, "马达电源受 GPIO 控制", "本板为 SW3 手动控制，不可用"),
    (CAP_RSTN_GPIO, "RSTN 受 GPIO 控制", "本板 RSTN 并 VDD，不可用"),
    (CAP_SHARED_TRIG_567, "CH5/6/7 共用 TRIG", "硬触发下三路必然联动"),
    (CAP_RTP_48K, "RTP 48K 可用", "本板只到 400 kHz，不可用"),
    (CAP_INTERNAL_PULLUP, "内部上拉已启用", "上游上拉不足时的降级运行标记"),
    (CAP_ASCII_MODE, "ASCII 模式可用", "上电默认的引导模式"),
]

# 本板期望的能力值（协议 §9.1 表格）
# ⚠️ 2026-09 硬件更新：每路一条独立 TRIG（TRIG0~7 = IO18/19/20/21/22/14/IO0/IO1），
#    固件**不再上报** CAP_SHARED_TRIG_567(bit12)。这里保留该位是作为"原始规格/回退值"，
#    界面上的联动加锁一律以**设备实测 caps** 为准（见 UiContext.cap()），因此实际不会联动。
#    旧固件（共用 GPIO14）仍会带 bit12，联动逻辑因此保留。
CAPS_EXPECTED_BOARD_A = (
    CAP_RAM | CAP_RTP | CAP_CONT | CAP_TRIG | CAP_WAVE_DOWNLOAD | CAP_F0_DETECT
    | CAP_SWEEP | CAP_LOCAL_TRIG_PULSE | CAP_REG_ACCESS | CAP_TCA_ONLINE
    | CAP_SHARED_TRIG_567 | CAP_ASCII_MODE
)

# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------

MODE_RAM = 0
MODE_RTP = 1
MODE_CONT = 2
MODE_TRIG = 3
MODE_UNKNOWN = 0xFF

MODE_NAMES = {0: "RAM", 1: "RTP", 2: "CONT", 3: "TRIG", 0xFF: "未知"}
MODE_CODES = {"RAM": 0, "RTP": 1, "CONT": 2, "TRIG": 3}

# GLB_STATE（0x3F bit3:0，只读）
GLB_STANDBY = 0
GLB_CONT = 6
GLB_RAM = 7
GLB_RTP = 8
GLB_TRIG = 9
GLB_BRAKE = 11

GLB_STATE_NAMES = {
    0: "STANDBY", 6: "CONT", 7: "RAM", 8: "RTP", 9: "TRIG", 11: "BRAKE",
}

GLB_STATE_PLAYING = (GLB_CONT, GLB_RAM, GLB_RTP, GLB_TRIG, GLB_BRAKE)

# 安全状态（GET_STATUS.safety_state）
SAFETY_NORMAL = 0
SAFETY_HEARTBEAT_LOST = 1
SAFETY_BUS_LOCKED = 2

SAFETY_STATE_NAMES = {0: "正常", 1: "心跳丢失安全态", 2: "总线错误锁定", 3: "保留"}

# EVT_PLAY_DONE.reason
PLAY_DONE_NATURAL = 0
PLAY_DONE_STOPPED = 1
PLAY_DONE_SAFETY_TIMEOUT = 2
PLAY_DONE_ERROR = 3
PLAY_DONE_HEARTBEAT_LOST = 4
PLAY_DONE_DURATION = 5

PLAY_DONE_REASON_NAMES = {
    5: "设定时长已到，播放完成",
    0: "自然结束", 1: "收到 STOP", 2: "安全超时", 3: "播放出错", 4: "心跳丢失",
}

# EVT_SAFETY_TRIP.reason
TRIP_PLAY_TIMEOUT = 0
TRIP_HEARTBEAT_LOST = 1
TRIP_TOO_MANY_FULL_AMP = 2
TRIP_BUS_ERROR = 3
TRIP_LIVE_LEASE = 4

TRIP_REASON_NAMES = {
    4: "实时控制失联（专用续约超时）",
    0: "播放超时", 1: "心跳丢失", 2: "同时路数超限", 3: "总线错误锁定",
}

# 日志级别
LOG_OFF = 0
LOG_ERROR = 1
LOG_WARN = 2
LOG_INFO = 3
LOG_DEBUG = 4

LOG_LEVEL_NAMES = {0: "OFF", 1: "ERROR", 2: "WARN", 3: "INFO", 4: "DEBUG"}

# 采样率编码（WAVE_BEGIN / WAVE_GEN / SET_RTP_PARAMS）
RATE_24K = 0
RATE_48K = 1
RATE_12K = 2
RATE_12K_ALT = 3

RATE_NAMES = {0: "24 kHz", 1: "48 kHz", 2: "12 kHz", 3: "12 kHz"}
RATE_HZ = {0: 24000, 1: 48000, 2: 12000, 3: 12000}

# DET_READ seq
DET_SEQ_VBAT = 0
DET_SEQ_RL = 3
DET_SEQ_OS = 4

DET_SEQ_NAMES = {0: "VBAT（电池电压）", 3: "RL（LRA 阻抗）", 4: "OS"}

# RL 检测时固件实际写入 0x50 bit2:0 的 D2S_GAIN 倍率。
# 必须与固件 cfg_defaults.h 的 DEF_RL_D2S_GAIN 保持一致（6 ⇒ ×20）。
# 手册 §LRA 阻抗检测 第 3 步要求"把 D2S_GAIN 设为合适的值"，固件现在按
# RL 2~25 Ω 档写 ×20；改固件常量时这里要同步，否则上位机"本地重算"的
# 显示值会和器件回报的 value_x100 对不上（差 2.5 倍）。
# ★ 权威值始终是固件回报的 value_x100 / 100，本地重算仅用于展示。
DET_RL_D2S_GAIN = 20

# F0 结果寄存器换算基准（F0 = BASE / raw）。
# ⚠️ 实测修正（2026-09，AW86234FCR ×4）：一次性检测寄存器 0x24/0x25 的实际基准是
#    192000 而不是文档写的 384000（相差 2 倍）。证据：修好 GO 后四路 raw≈1096~1184，
#    按 192000 换算得 162~175 Hz，与 1040 标称 170.5 Hz 及 170/340 Hz A/B 主观对比吻合。
#    连续跟踪寄存器 0x26/0x27 仍用文档的 384000（其 raw≈2400~2520 → 152~160 Hz）。
F0_RAW_BASE_ONESHOT = 192000.0   # 0x24/0x25
F0_RAW_BASE_CONT = 384000.0      # 0x26/0x27

# WAVE_GEN shape
SHAPE_NAMES = {
    0: "正弦", 1: "方波", 2: "三角", 3: "锯齿",
    4: "正弦+上升包络", 5: "正弦+下降包络", 6: "点击（单周期衰减）",
}
SHAPE_CODES = {v: k for k, v in SHAPE_NAMES.items()}

# MON_START.fields 位
MON_FIELD_SYSST = 1 << 0
MON_FIELD_SYSST2 = 1 << 1
MON_FIELD_GLB_STATE = 1 << 2
MON_FIELD_DETRD = 1 << 3
MON_FIELD_CONTRD = 1 << 4
MON_FIELD_LDO = 1 << 5

MON_FIELD_LABELS = [
    (MON_FIELD_SYSST, "SYSST"),
    (MON_FIELD_SYSST2, "SYSST2"),
    (MON_FIELD_GLB_STATE, "GLB_STATE"),
    (MON_FIELD_DETRD, "DETRD"),
    (MON_FIELD_CONTRD, "CONTRD"),
    (MON_FIELD_LDO, "LDO/VREG"),
]

# 测试进度状态（EVT_TEST_PROGRESS.status）
TEST_STATUS_NAMES = {0: "通过", 1: "失败", 2: "中止", 3: "跳过"}

# BUS_RECOVER.method
BUS_RECOVER_METHODS = {0: "失败", 1: "9 clock + STOP", 2: "TCA RESET 脉冲", 3: "总线重建成功"}

# 配置常量（协议 §12 与 04 文档 §12）
DEFAULT_PLAY_TIMEOUT_MS = 2000
DEFAULT_MAX_SIMUL_FULL_AMP = 2
DEFAULT_HEARTBEAT_TIMEOUT_MS = 5000

I2C_SPEED_FAST = 400000
I2C_SPEED_SAFE = 100000

SRAM_TOTAL_BYTES = 3072           # 3 kB
WAVE_MAX_CHUNK_SAMPLES = 184      # WAVE_ADD 单片样本上限
WAVE_MAX_WAVES = 127
REG_MAX_LEN = 64

# 满幅判定阈值（协议 §11）
FULL_AMP_CONT_DRV1_LVL = 96
FULL_AMP_RAM_GAIN = 192

# TRIG 联动组：CH5 / CH6 / CH7 共用 GPIO14
TRIG_SHARED_CHANNELS = (5, 6, 7)
TRIG_SHARED_GPIO = 14
TRIG_GPIO_MAP = {0: 18, 1: 19, 2: 20, 3: 21, 4: 22, 5: 14, 6: 14, 7: 14}

CHANNEL_COUNT = 8
BOARD_ID_VIBRATION_TEST_BOARD_A = 1
AW_CHIPID_EXPECTED = 0x2340
AW_I2C_ADDR = 0x5A
TCA_I2C_ADDR = 0x70


def cmd_name(cmd: int) -> str:
    return CMD_NAMES.get(cmd, f"CMD_0x{cmd:02X}")


def err_name(code: int) -> str:
    return ERR_NAMES.get(code, f"ERR_0x{code:02X}")


def err_cn(code: int) -> str:
    return ERR_CN.get(code, "未知错误")


def mode_name(mode: int) -> str:
    return MODE_NAMES.get(mode, f"模式{mode}")


def glb_state_name(state: int) -> str:
    return GLB_STATE_NAMES.get(state, f"保留({state})")


def caps_summary(caps: int) -> str:
    on = [label for bit, label, _ in CAP_LABELS if caps & bit]
    return "、".join(on) if on else "（无）"


def expand_shared_trig_mask(mask: int) -> int:
    """硬触发掩码：含 CH5/6/7 中任意一位时，三位必然一起动。

    与固件 ``svc_play`` 的处理保持一致，供上位机预判真实联动结果。
    """
    if mask & 0xE0:
        return mask | 0xE0
    return mask
