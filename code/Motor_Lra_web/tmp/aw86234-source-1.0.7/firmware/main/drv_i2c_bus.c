/**
 * @file  drv_i2c_bus.c
 * @brief I2C 总线驱动实现（新版 i2c_master 驱动）。
 *
 * 关键点：
 *   - 探测期用 100 kHz（上拉不足时 100 kHz 能通而 400 kHz 不通，这本身就是诊断信息）
 *   - 100 kHz 也不通时尝试使能**内部上拉**再试一次，成功则标记"降级运行"（对应硬件问题 V-1）
 *   - 总线恢复三级递进，含 GPIO23 硬复位 TCA（`i2c_master_bus_reset` 复位不了 TCA 的通道状态）
 */

#include "drv_i2c_bus.h"

#include <string.h>

#include "cfg_board.h"
#include "cfg_defaults.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "proto_cmd.h"
#include "util_log.h"

static i2c_master_bus_handle_t s_bus = NULL;
static i2c_master_dev_handle_t s_tca = NULL;
static i2c_master_dev_handle_t s_aw = NULL;
static bus_info_t s_info;
static bool s_ready = false;

/* ------------------------------------------------------------------ */

uint8_t bus_err_to_proto(esp_err_t err)
{
    switch (err) {
    case ESP_OK:                    return ERR_OK;
    case ESP_ERR_TIMEOUT:           return ERR_I2C_TIMEOUT;
    case ESP_ERR_INVALID_ARG:       return ERR_BAD_PARAM;
    case ESP_ERR_INVALID_STATE:     return ERR_BUS_BUSY;
    case ESP_ERR_NOT_FOUND:         return ERR_NOT_PRESENT;
    default:                        return ERR_I2C_NACK;
    }
}

static void init_tca_reset_gpio(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOARD_TCA_RST_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    /* 低有效：平时保持高（外部还有 R26 10K 上拉） */
    gpio_set_level(BOARD_TCA_RST_GPIO, 1);
}

void bus_tca_reset_pulse(uint16_t hold_us)
{
    if (hold_us < BOARD_TCA_RST_HOLD_US) {
        hold_us = BOARD_TCA_RST_HOLD_US;
    }
    gpio_set_level(BOARD_TCA_RST_GPIO, 0);
    esp_rom_delay_us(hold_us);
    gpio_set_level(BOARD_TCA_RST_GPIO, 1);
    esp_rom_delay_us(100);
}

/* ------------------------------------------------------------------ */
/* 总线生命周期                                                        */
/* ------------------------------------------------------------------ */

static void bus_destroy(void)
{
    if (s_tca) {
        i2c_master_bus_rm_device(s_tca);
        s_tca = NULL;
    }
    if (s_aw) {
        i2c_master_bus_rm_device(s_aw);
        s_aw = NULL;
    }
    if (s_bus) {
        i2c_del_master_bus(s_bus);
        s_bus = NULL;
    }
}

esp_err_t bus_recreate(uint8_t sda_gpio, uint8_t scl_gpio, uint32_t speed_hz,
                       bool internal_pullup)
{
    bus_destroy();

    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = -1,                     /* 自动选端口 */
        .sda_io_num = (gpio_num_t)sda_gpio,
        .scl_io_num = (gpio_num_t)scl_gpio,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .intr_priority = 0,                 /* 0 = 驱动自选 */
        .trans_queue_depth = 0,             /* 只用同步事务 */
        .flags.enable_internal_pullup = internal_pullup ? 1u : 0u,
    };
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &s_bus);
    if (err != ESP_OK) {
        LOGE("创建 I2C 总线失败：%s", esp_err_to_name(err));
        return err;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_TCA9548A_ADDR,
        .scl_speed_hz = speed_hz,
    };
    err = i2c_master_bus_add_device(s_bus, &dev_cfg, &s_tca);
    if (err != ESP_OK) {
        LOGE("挂载 TCA 设备失败：%s", esp_err_to_name(err));
        bus_destroy();
        return err;
    }

    dev_cfg.device_address = BOARD_AW8623X_ADDR;
    err = i2c_master_bus_add_device(s_bus, &dev_cfg, &s_aw);
    if (err != ESP_OK) {
        LOGE("挂载 AW 设备失败：%s", esp_err_to_name(err));
        bus_destroy();
        return err;
    }

    s_info.sda_gpio = sda_gpio;
    s_info.scl_gpio = scl_gpio;
    s_info.speed_hz = speed_hz;
    s_info.internal_pullup = internal_pullup ? 1u : 0u;
    return ESP_OK;
}

/* ------------------------------------------------------------------ */
/* 裸传输                                                              */
/* ------------------------------------------------------------------ */

esp_err_t bus_tca_write_byte(uint8_t value, uint32_t timeout_ms)
{
    if (!s_tca) {
        return ESP_ERR_INVALID_STATE;
    }
    return i2c_master_transmit(s_tca, &value, 1, (int)timeout_ms);
}

esp_err_t bus_tca_read_byte(uint8_t *out, uint32_t timeout_ms)
{
    if (!s_tca) {
        return ESP_ERR_INVALID_STATE;
    }
    /* 单寄存器器件：直接读一个字节就是控制寄存器（协议 §9.2） */
    return i2c_master_receive(s_tca, out, 1, (int)timeout_ms);
}

esp_err_t bus_aw_tx(const uint8_t *wr, size_t wr_len, uint32_t timeout_ms)
{
    if (!s_aw) {
        return ESP_ERR_INVALID_STATE;
    }
    return i2c_master_transmit(s_aw, wr, wr_len, (int)timeout_ms);
}

esp_err_t bus_aw_txrx(const uint8_t *wr, size_t wr_len,
                      uint8_t *rd, size_t rd_len, uint32_t timeout_ms)
{
    if (!s_aw) {
        return ESP_ERR_INVALID_STATE;
    }
    /* 写地址 + 重复起始 + 读，**一次事务**（不要拆成两次） */
    return i2c_master_transmit_receive(s_aw, wr, wr_len, rd, rd_len, (int)timeout_ms);
}

esp_err_t bus_probe_dev(uint8_t addr7, uint32_t timeout_ms)
{
    if (!s_bus) {
        return ESP_ERR_INVALID_STATE;
    }
    return i2c_master_probe(s_bus, addr7, (int)timeout_ms);
}

/** 读 TCA 控制寄存器（回读确认器件真的在）。 */
static bool tca_ping(uint8_t *ctrl_out)
{
    esp_err_t err = bus_tca_write_byte(0x00, BOARD_I2C_XFER_TIMEOUT_MS);
    if (err != ESP_OK) {
        return false;
    }
    uint8_t ctrl = 0xFF;
    err = bus_tca_read_byte(&ctrl, BOARD_I2C_XFER_TIMEOUT_MS);
    if (err != ESP_OK) {
        return false;
    }
    if (ctrl_out) {
        *ctrl_out = ctrl;
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* 探测                                                                */
/* ------------------------------------------------------------------ */

static esp_err_t try_candidate(uint8_t sda, uint8_t scl, uint8_t sel, bool pullup,
                               bus_info_t *out)
{
    esp_err_t err = bus_recreate(sda, scl, BOARD_I2C_PROBE_SPEED, pullup);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t ctrl = 0;
    if (!tca_ping(&ctrl)) {
        LOGD("引脚组合 sel=%u（SDA=IO%u SCL=IO%u, pullup=%d）探测失败", sel, sda, scl, pullup);
        return ESP_ERR_NOT_FOUND;
    }

    /* 先 100 kHz 通；再升 400 kHz 确认（上拉不足时这一步会失败，是重要诊断信息） */
    uint8_t probe_speed_ok = 0;
    err = bus_set_speed(BOARD_I2C_SPEED_FAST, NULL);
    if (err == ESP_OK) {
        uint8_t ctrl2 = 0;
        if (tca_ping(&ctrl2)) {
            probe_speed_ok = 1;
            ctrl = ctrl2;
        } else {
            LOGW("400 kHz 下 TCA 无应答，回落到 100 kHz（疑似上游上拉不足，见硬件问题 V-1）");
            bus_set_speed(BOARD_I2C_SPEED_SAFE, NULL);
        }
    }

    out->sel_used = sel;
    out->sda_gpio = sda;
    out->scl_gpio = scl;
    out->speed_hz = (probe_speed_ok ? BOARD_I2C_SPEED_FAST : BOARD_I2C_SPEED_SAFE);
    out->internal_pullup = pullup ? 1u : 0u;
    out->tca_present = 1;
    out->tca_addr = BOARD_TCA9548A_ADDR;
    out->ctrl_readback = ctrl;
    out->probe_speed_ok = probe_speed_ok;
    return ESP_OK;
}

esp_err_t bus_probe_auto(uint8_t bus_sel, bus_info_t *out)
{
    init_tca_reset_gpio();

    s_ready = false;
    memset(&s_info, 0, sizeof(s_info));
    s_info.tca_addr = BOARD_TCA9548A_ADDR;

    /* 候选列表（auto：先 LP，再 HP） */
    uint8_t cands[2][3];
    int n = 0;
    if (bus_sel == BUS_SEL_LP) {
        cands[n][0] = BOARD_I2C_LP_SDA; cands[n][1] = BOARD_I2C_LP_SCL; cands[n][2] = BUS_SEL_LP; n++;
    } else if (bus_sel == BUS_SEL_HP) {
        cands[n][0] = BOARD_I2C_HP_SDA; cands[n][1] = BOARD_I2C_HP_SCL; cands[n][2] = BUS_SEL_HP; n++;
    } else {
        cands[n][0] = BOARD_I2C_LP_SDA; cands[n][1] = BOARD_I2C_LP_SCL; cands[n][2] = BUS_SEL_LP; n++;
        cands[n][0] = BOARD_I2C_HP_SDA; cands[n][1] = BOARD_I2C_HP_SCL; cands[n][2] = BUS_SEL_HP; n++;
    }

    for (int i = 0; i < n; i++) {
        /* 先不使能内部上拉 */
        if (try_candidate(cands[i][0], cands[i][1], cands[i][2], false, out) == ESP_OK) {
            s_info = *out;
            s_ready = true;
            LOGI("I2C 引脚探测成功：%s（SDA=IO%u SCL=IO%u）@%u Hz，TCA ctrl=0x%02X",
                 (out->sel_used == BUS_SEL_LP ? "LP" : "HP"), out->sda_gpio,
                 out->scl_gpio, (unsigned)out->speed_hz, out->ctrl_readback);
            return ESP_OK;
        }
    }
    /* 都不通：使能内部上拉再试一遍（降级运行，对应硬件问题 V-1） */
    for (int i = 0; i < n; i++) {
        if (try_candidate(cands[i][0], cands[i][1], cands[i][2], true, out) == ESP_OK) {
            s_info = *out;
            if (out->speed_hz > BOARD_I2C_SPEED_SAFE) {
                bus_set_speed(BOARD_I2C_SPEED_SAFE, NULL);
                s_info.speed_hz = BOARD_I2C_SPEED_SAFE;
                out->speed_hz = BOARD_I2C_SPEED_SAFE;
            }
            s_ready = true;
            LOGW("TCA 需借助**内部弱上拉**才能通信（%s，@%u Hz）——上游 4.7 kΩ 上拉可能缺失，"
                 "建议补焊到 3.3 V（硬件问题 V-1）",
                 (out->sel_used == BUS_SEL_LP ? "LP" : "HP"), (unsigned)out->speed_hz);
            return ESP_OK;
        }
    }

    s_ready = false;
    LOGE("I2C 引脚探测失败：LP 与 HP 组合在 0x70 都无应答。"
         "检查 J1 跳线（推荐 3-5 + 4-6）、TCA 供电、上游上拉（V-1）");
    return ESP_ERR_NOT_FOUND;
}

esp_err_t bus_set_speed(uint32_t hz, uint32_t *actual_hz)
{
    if (hz != BOARD_I2C_SPEED_SAFE && hz != BOARD_I2C_SPEED_FAST) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_tca || !s_aw) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = i2c_master_bus_rm_device(s_tca);
    if (err != ESP_OK) {
        return err;
    }
    err = i2c_master_bus_rm_device(s_aw);
    if (err != ESP_OK) {
        return err;
    }
    s_tca = NULL;
    s_aw = NULL;

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_TCA9548A_ADDR,
        .scl_speed_hz = hz,
    };
    err = i2c_master_bus_add_device(s_bus, &dev_cfg, &s_tca);
    if (err != ESP_OK) {
        return err;
    }
    dev_cfg.device_address = BOARD_AW8623X_ADDR;
    err = i2c_master_bus_add_device(s_bus, &dev_cfg, &s_aw);
    if (err != ESP_OK) {
        return err;
    }
    s_info.speed_hz = hz;
    if (actual_hz) {
        *actual_hz = hz;
    }
    return ESP_OK;
}

esp_err_t bus_set_pullup(bool enable)
{
    if (!s_bus) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t sda = s_info.sda_gpio;
    uint8_t scl = s_info.scl_gpio;
    uint32_t spd = s_info.speed_hz ? s_info.speed_hz : BOARD_I2C_SPEED_SAFE;
    if (enable && spd > BOARD_I2C_SPEED_SAFE) {
        spd = BOARD_I2C_SPEED_SAFE;    /* 内部弱上拉只在 100 kHz 下勉强可用 */
    }
    esp_err_t err = bus_recreate(sda, scl, spd, enable);
    if (err == ESP_OK) {
        s_info.internal_pullup = enable ? 1u : 0u;
    }
    return err;
}

bool bus_is_ready(void)
{
    return s_ready;
}

const bus_info_t *bus_info(void)
{
    return &s_info;
}

/* ------------------------------------------------------------------ */
/* 总线恢复                                                            */
/* ------------------------------------------------------------------ */

/** 第 1 级：把 SDA 释放成输入，敲 9 个 SCL 时钟，再发一个 STOP。 */
static void bitbang_recover(uint8_t sda, uint8_t scl)
{
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << sda) | (1ULL << scl),
        .mode = GPIO_MODE_INPUT_OUTPUT_OD,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    gpio_set_level(sda, 1);
    gpio_set_level(scl, 1);
    esp_rom_delay_us(10);

    for (int i = 0; i < 9; i++) {
        gpio_set_level(scl, 0);
        esp_rom_delay_us(5);
        gpio_set_level(scl, 1);
        esp_rom_delay_us(5);
    }
    /* STOP：SCL 高时 SDA 由低变高 */
    gpio_set_level(sda, 0);
    esp_rom_delay_us(5);
    gpio_set_level(scl, 1);
    esp_rom_delay_us(5);
    gpio_set_level(sda, 1);
    esp_rom_delay_us(5);
}

uint8_t bus_recover(void)
{
    uint8_t sda = s_info.sda_gpio ? s_info.sda_gpio : BOARD_I2C_LP_SDA;
    uint8_t scl = s_info.scl_gpio ? s_info.scl_gpio : BOARD_I2C_LP_SCL;
    uint32_t spd = s_info.speed_hz ? s_info.speed_hz : BOARD_I2C_SPEED_SAFE;

    /* --- 1) 9 clock + STOP（必须先释放总线，引脚才能被 GPIO 接管） --- */
    bus_destroy();
    bitbang_recover(sda, scl);
    if (bus_recreate(sda, scl, spd, s_info.internal_pullup != 0) == ESP_OK) {
        uint8_t ctrl = 0;
        if (tca_ping(&ctrl)) {
            s_info.ctrl_readback = ctrl;
            s_ready = true;
            LOGI("总线恢复成功：9 clock + STOP");
            return 1;
        }
    }

    /* --- 2) GPIO23 硬复位 TCA（i2c_master_bus_reset 复位不了它的通道状态） --- */
    bus_tca_reset_pulse(BOARD_TCA_RST_HOLD_US);
    if (!s_bus) {
        bus_recreate(sda, scl, spd, s_info.internal_pullup != 0);
    }
    {
        uint8_t ctrl = 0;
        if (tca_ping(&ctrl)) {
            s_info.ctrl_readback = ctrl;
            s_ready = true;
            LOGI("总线恢复成功：TCA RESET 脉冲");
            return 2;
        }
    }

    /* --- 3) 重建总线 --- */
    if (s_bus) {
        i2c_master_bus_reset(s_bus);
    }
    if (bus_recreate(sda, scl, spd, s_info.internal_pullup != 0) == ESP_OK) {
        uint8_t ctrl = 0;
        if (tca_ping(&ctrl)) {
            s_info.ctrl_readback = ctrl;
            s_ready = true;
            LOGI("总线恢复成功：总线重建");
            return 3;
        }
    }

    s_ready = false;
    LOGE("总线恢复失败（三级都试过）");
    return 0;
}
