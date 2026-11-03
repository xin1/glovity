/**
 * @file  drv_aw8623x.h
 * @brief AW86234FCR 驱动：寄存器读写、上电序列、模式配置、Profile 应用、
 *        波形库装载与回读、F0 与 DET_READ。
 *
 * ⚠️ 分层纪律：本文件**不得包含任何协议解析代码**，只做器件级动作。
 * 所有 I2C 访问都必须经 `ch_xfer()`（选通与传输的原子性）。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "aw_profile.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- 基础寄存器访问 ---- */
esp_err_t aw_read(uint8_t ch, uint8_t reg, uint8_t *out, size_t len);
esp_err_t aw_read_u8(uint8_t ch, uint8_t reg, uint8_t *out);
esp_err_t aw_write(uint8_t ch, uint8_t reg, const uint8_t *data, size_t len);
esp_err_t aw_write_u8(uint8_t ch, uint8_t reg, uint8_t value);

/**
 * 读-改-写：`new = (old & ~mask) | (value & mask)`。
 * 用于保护同寄存器里的其他位（如写 DRV1_LVL 时保住 TRACK_EN、写 0x45 时保住 EN_FIR）。
 */
esp_err_t aw_update(uint8_t ch, uint8_t reg, uint8_t mask, uint8_t value,
                    uint8_t *old_out, uint8_t *new_out);

/* ---- 器件状态 ---- */
esp_err_t aw_read_chipid(uint8_t ch, uint16_t *chipid);
esp_err_t aw_read_vreg_ok(uint8_t ch, uint8_t *vreg_ok);   /* 1 / 0 / 0xFF */
esp_err_t aw_get_glb_state(uint8_t ch, uint8_t *glb_state);
esp_err_t aw_get_sysst(uint8_t ch, uint8_t *sysst);
esp_err_t aw_get_sysst2(uint8_t ch, uint8_t *sysst2);
/** SYSINT（0x02，RC 读清）；bit0 = DONEI 播放结束事件。 */
esp_err_t aw_get_sysint(uint8_t ch, uint8_t *sysint);
esp_err_t aw_clear_sysint(uint8_t ch);

/* ---- 上电序列 ---- */
esp_err_t aw_softrst(uint8_t ch);        /* 写 0x00=0xAA（清配置寄存器，保留 SRAM） */
esp_err_t aw_wake(uint8_t ch);           /* 0x46 bit7 = 1 */
esp_err_t aw_standby(uint8_t ch);        /* 0x46 bit6 = 1 */

/* ---- 配置 ---- */
esp_err_t aw_apply_profile(uint8_t ch, const aw_profile_t *p);
esp_err_t aw_apply_mode(uint8_t ch, uint8_t mode, const aw_profile_t *p);

/**
 * 进入 TRIG 前的**安全前提**（协议 §11.1）：
 * 强制写 `0x39 bit3 TRG1_BRK = 1` 与 `0x3A bit2 TRG1_STOP = 1` 并回读校验。
 * 校验失败返回 ESP_ERR_INVALID_STATE（调用方应转成 ERR_VERIFY_FAILED 并拒绝进入 TRIG）。
 */
esp_err_t aw_ensure_trig_safety(uint8_t ch);

/** 回读三个默认值陷阱位（0x45 TRG1_MODE/INTN_PIN、0x33 TRG1_POS、0x3A TRG1_STOP）。 */
esp_err_t aw_verify_trap_bits(uint8_t ch, uint8_t mode);

/* ---- 播放 ---- */
esp_err_t aw_play_go(uint8_t ch);
esp_err_t aw_stop(uint8_t ch, uint8_t immediate);
esp_err_t aw_write_wave_queue(uint8_t ch, uint8_t count, const uint8_t *ids);
esp_err_t aw_set_ram_loop(uint8_t ch, uint8_t mainloop, uint8_t waitslot,
                          const uint8_t seq_loop[4]);

/* ---- SRAM / 波形库 ---- */
esp_err_t aw_wave_lib_write(uint8_t ch, uint16_t base_addr,
                            const uint8_t *lib, size_t len);
esp_err_t aw_wave_lib_read(uint8_t ch, uint16_t base_addr, uint8_t *out, size_t len);
esp_err_t aw_sram_read(uint8_t ch, uint16_t addr, uint8_t *out, size_t len);
esp_err_t aw_sram_write_stream(uint8_t ch, uint16_t addr, const uint8_t *data, size_t len);
esp_err_t aw_rtp_write(uint8_t ch, const uint8_t *data, size_t len);

/* ---- 测量 ---- */
/** 启动 F0 检测：CONT 模式 + BRK_EN + EN_F0_DET=1 + **GO=1**（手册 §6.9 流程）。 */
esp_err_t aw_f0_enable(uint8_t ch, uint8_t mode0_or_1);
/** F0 检测收尾：停播放 + 清 EN_F0_DET。 */
esp_err_t aw_f0_disable(uint8_t ch);
esp_err_t aw_read_f0_raw(uint8_t ch, uint8_t mode, uint8_t *h, uint8_t *l);
esp_err_t aw_read_cont_raw(uint8_t ch, uint8_t *h, uint8_t *l);
esp_err_t aw_det_read(uint8_t ch, uint8_t seq, uint16_t *adc, uint16_t *avg,
                      uint8_t *d2s_gain, uint32_t wait_ms);

/** 整片 dump（一次事务读 len 字节，调用方保证 addr+len ≤ 手册范围）。 */
esp_err_t aw_regs_read(uint8_t ch, uint8_t addr, uint8_t *out, size_t len);

/** 恢复数据手册默认的 SYSCTRL1 关键位（EN_FIR 等），用于 SET_DEFAULT_PROFILE。 */
esp_err_t aw_apply_register_defaults(uint8_t ch);

#ifdef __cplusplus
}
#endif
