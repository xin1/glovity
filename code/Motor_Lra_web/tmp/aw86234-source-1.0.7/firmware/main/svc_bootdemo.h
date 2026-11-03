/**
 * @file  svc_bootdemo.h
 * @brief BOOT 按键（GPIO9 / SW2）→ 对全部通道发一次 TRIG 硬触发脉冲。
 *
 * 现场手动体验"硬件触发"链路的演示功能，不需要上位机参与。
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 启动演示任务（由 app_main 调用）。
 * `BOARD_BOOTDEMO_ENABLE = 0` 时为空操作。
 *
 * 行为：
 *   1) 上电后等后台自检跑完，随后给每个**在线**通道装载一段演示波形，
 *      并把该通道切到 TRIG 模式（TRIG 播放取的是芯片 SRAM 里的波形库）；
 *   2) 之后轮询 BOOT 键，**每按一次**就对全部 TRIG 线发一个脉冲 →
 *      所有接好马达的通道同时振动一下。
 */
void svc_bootdemo_start(void);

#ifdef __cplusplus
}
#endif
