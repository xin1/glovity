/**
 * @file  svc_selftest.h
 * @brief 上电自检 / SELFTEST 命令：I2C 引脚探测 → TCA 探测 → 逐通道 CHIPID →
 *        逐通道软复位 + 关键位回读 → 马达通路粗判（DET_READ seq=3）。
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 启动一次异步自检（立即返回，结果经 EVT_LOG + EVT_SELFTEST_RESULT 上报）。 */
void svc_selftest_start(void);

/** 由监视任务周期性调用，推进自检状态机。 */
void svc_selftest_poll(uint32_t now_ms);

bool svc_selftest_active(void);

/** 最近一次自检的汇总（供 GET_STATUS / ASCII status 使用）。 */
typedef struct {
    uint8_t overall;        /* 0=通过 1=有问题 2=失败 */
    uint8_t present_mask;
    uint8_t bus_sel;
    uint8_t tca;
    uint8_t vreg_mask;
    uint8_t issue_count;
} selftest_summary_t;

void svc_selftest_summary(selftest_summary_t *out);

#ifdef __cplusplus
}
#endif
