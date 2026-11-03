#include <stdio.h>
#include <string.h>
#include "svc_play.h"
#include "svc_channel.h"
#include "app_protocol.h"
#include "drv_aw8623x.h"
#include "proto_cmd.h"

static ch_ctx_t chips[8];
static uint8_t irq[8];
static bool physical_output[8];
static uint8_t brake_time[8], brake_enable[8];
static int brake_fail;
static int stops[8], done_events, failures, stop_fail, clear_fail, last_reason, trips;
static uint32_t now_ms;
static int delay_action, pulse_edges, irq_after_go;
static int go_calls, rtp_fail, fifo_count[8], fifo_af[8], fifo_ae[8];
static int consume_per_xfer, rtp_calls, rtp_bytes, max_rtp_len;
static int hw_amp[8], peak_energy, amp_fail_ch = -1;

#define CHECK(cond, text) do { if (!(cond)) { printf("FAIL: %s\n", text); failures++; } else printf("PASS: %s\n", text); } while (0)
ch_ctx_t *ch_ctx(uint8_t ch) { return ch < 8 ? &chips[ch] : NULL; }
bool ch_is_busy(uint8_t ch) { return false; }
void ch_arm_safety(uint8_t ch, uint16_t ms) { chips[ch].safety_armed = true; chips[ch].safety_deadline_ms = now_ms + ms; }
int32_t ch_safety_remaining_ms(uint8_t ch) { return (int32_t)(chips[ch].safety_deadline_ms - now_ms); }
void ch_disarm_safety(uint8_t ch) { chips[ch].safety_armed = false; }
void ch_mark_play_started(uint8_t ch) { chips[ch].playing = true; chips[ch].play_started_ms = now_ms; }
void ch_mark_play_stopped(uint8_t ch) { chips[ch].playing = false; }
void app_session_get_safety(app_safety_cfg_t *out) { *out = (app_safety_cfg_t){2000, 2, 5000, 0}; }
bool aw_profile_is_full_amplitude(const aw_profile_t *p, uint8_t mode) { return false; }
esp_err_t aw_get_glb_state(uint8_t ch, uint8_t *out) { *out = 3; /* Real board GLB is stale even while playing. */ return ESP_OK; }
esp_err_t aw_get_sysint(uint8_t ch, uint8_t *out) { *out = irq[ch]; irq[ch] = 0; return ESP_OK; }
esp_err_t aw_clear_sysint(uint8_t ch) { if (clear_fail) return ESP_FAIL; irq[ch] = 0; return ESP_OK; }
esp_err_t aw_play_go(uint8_t ch) { go_calls++; physical_output[ch] = true; if (irq_after_go) irq[ch] = BIT_DONEI; return ESP_OK; }
esp_err_t aw_stop(uint8_t ch, uint8_t immediate) { stops[ch]++; if (stop_fail) return ESP_FAIL; physical_output[ch] = false; fifo_count[ch] = 0; return ESP_OK; }
esp_err_t aw_wave_lib_read(uint8_t ch, uint16_t addr, uint8_t *data, size_t n) { uint8_t h[] = {0, 100, 0, 200}; memcpy(data, h, n); return ESP_OK; }
esp_err_t aw_ensure_trig_safety(uint8_t ch) { return ESP_OK; }
esp_err_t aw_verify_trap_bits(uint8_t ch, uint8_t mode) { return ESP_OK; }
esp_err_t aw_apply_mode(uint8_t ch, uint8_t mode, const aw_profile_t *p) { return ESP_OK; }
esp_err_t aw_update(uint8_t ch, uint8_t reg, uint8_t mask, uint8_t value, uint8_t *old, uint8_t *newv) {
    if (reg == 0x20) { if (brake_fail) { brake_fail=0; return ESP_FAIL; } brake_time[ch]=value; }
    if (reg == 0x08 && mask == 4) { brake_enable[ch]=value; }
    if (reg == 0x1C && mask == 0x7F) {
        if (ch == amp_fail_ch) { amp_fail_ch = -1; return ESP_FAIL; }
        hw_amp[ch] = value;
        int energy = 0; for (int i=0;i<8;i++) energy += hw_amp[i]*hw_amp[i];
        if (energy > peak_energy) peak_energy = energy;
    }
    return ESP_OK;
}
esp_err_t aw_apply_profile(uint8_t ch, const aw_profile_t *p) { fifo_af[ch] = p->fifo_af; fifo_ae[ch] = p->fifo_ae; return ESP_OK; }
esp_err_t aw_get_sysst(uint8_t ch, uint8_t *out) {
    fifo_count[ch] = fifo_count[ch] > consume_per_xfer ? fifo_count[ch] - consume_per_xfer : 0;
    *out = fifo_count[ch] >= fifo_af[ch] ? 8 : (fifo_count[ch] <= fifo_ae[ch] ? 16 : 0);
    return ESP_OK;
}
esp_err_t aw_write(uint8_t ch, uint8_t reg, const uint8_t *data, size_t len) { if (reg == 0x2e) { fifo_af[ch] = ((data[0] & 15) << 8) | data[2]; fifo_ae[ch] = ((data[0] >> 4) << 8) | data[1]; } return ESP_OK; }
esp_err_t aw_rtp_write(uint8_t ch, const uint8_t *data, size_t len) { if (rtp_fail || len > 63) return ESP_FAIL;
    rtp_calls++; rtp_bytes += len; if ((int)len > max_rtp_len) max_rtp_len = (int)len;
    fifo_count[ch] += len;
    fifo_count[ch] = fifo_count[ch] > consume_per_xfer ? fifo_count[ch] - consume_per_xfer : 0;
    return ESP_OK; }
void evt_play_started(uint8_t ch, uint8_t mode, uint16_t ms) {}
void evt_play_done(uint8_t ch, uint8_t reason, uint16_t ms) { done_events++; last_reason = reason; }
void evt_safety_trip(uint8_t reason, uint8_t mask, uint8_t detail) { trips++; }
void app_session_note_error(uint8_t err) {}
void log_emit(uint8_t level, const char *fmt, ...) {}
esp_err_t gpio_config(const gpio_config_t *cfg) { return ESP_OK; }
esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level) {
    for (uint8_t ch = 0; ch < 8; ch++)
        if (board_trig_gpio(ch) == pin && level && chips[ch].present) {
            physical_output[ch] = true;
            pulse_edges++;
        }
    return ESP_OK;
}
void vTaskDelay(unsigned int ms) {
    now_ms += ms;
    if (delay_action == 1) {
        delay_action = 0; stop_fail = 1; now_ms += 3000;
        svc_play_tick(now_ms);
    } else if (delay_action == 2) {
        delay_action = 0; svc_play_stop(4, 1);
    } else if (delay_action == 3) {
        irq[4] = BIT_DONEI; svc_play_poll(now_ms);
    }
}
void esp_rom_delay_us(unsigned int us) {}
int64_t esp_timer_get_time(void) { return (int64_t)now_ms * 1000; }

static void setup(uint8_t mode) {
    memset(chips, 0, sizeof chips); memset(irq, 0, sizeof irq);
    memset(hw_amp,0,sizeof hw_amp); peak_energy=0; amp_fail_ch=-1;
    memset(stops, 0, sizeof stops); memset(physical_output, 0, sizeof physical_output);
    chips[4].present = 1; chips[4].mode = mode; chips[4].profile.mode = mode;
    chips[4].profile.trig1_pos_en = 1; chips[4].profile.trig1_seq_p = 1;
    now_ms += 1000; done_events = 0; stop_fail = clear_fail = trips = 0;
    delay_action = pulse_edges = irq_after_go = 0;
    go_calls = rtp_fail = consume_per_xfer = rtp_calls = rtp_bytes = max_rtp_len = 0; memset(fifo_count, 0, sizeof fifo_count); memset(fifo_af, 0, sizeof fifo_af);
    chips[4].profile.base_addr = mode == AW_MODE_TRIG ? 0 : 2048;
}
int main(void) {
    svc_play_init();
    uint8_t mask = 0, playing = 0, glb = 0; uint16_t skew = 0, elapsed = 0;
    setup(AW_MODE_RAM); irq[4] = BIT_DONEI; /* Latched completion from previous run. */
    CHECK(svc_play_play_multi(0x10, 2000, 0, false, &mask, &skew) == ESP_OK, "RAM starts");
    svc_play_poll(now_ms + 100);
    CHECK(chips[4].playing && chips[4].safety_armed, "RAM stale DONE must not cancel the new run/watchdog");
    printf("  RAM physical=%d software=%d watchdog=%d STOPs=%d\n", physical_output[4], chips[4].playing, chips[4].safety_armed, stops[4]);

    setup(AW_MODE_TRIG); irq[4] = BIT_DONEI;
    CHECK(svc_play_trig_pulse(0x10, 20, 1, 100, 0, &mask) == ESP_OK, "TRIG starts");
    svc_play_query_state(4, &playing, &glb, &elapsed);
    CHECK(playing && chips[4].safety_armed, "TRIG immediate status read must not consume an old DONE as current completion");
    printf("  TRIG physical=%d software=%d watchdog=%d STOPs=%d\n", physical_output[4], chips[4].playing, chips[4].safety_armed, stops[4]);

    /* A completion event is injected while a residual drive remains. This
       checks cleanup policy, not whether real hardware has residual drive. */
    setup(AW_MODE_RAM);
    svc_play_play_multi(0x10, 2000, 0, false, &mask, &skew);
    irq[4] = BIT_DONEI;
    svc_play_poll(now_ms + 100);
    printf("  Natural-DONE policy: physical=%d software=%d watchdog=%d STOPs=%d\n", physical_output[4], chips[4].playing, chips[4].safety_armed, stops[4]);
    svc_play_stop_all(1, &mask);
    CHECK(!physical_output[4], "STOP_ALL removes residual output in the model");
    CHECK(stops[4] >= 2, "Natural DONE performs hardware STOP before STOP_ALL");

    setup(AW_MODE_TRIG);
    svc_play_trig_pulse(0x10, 20, 1, 0, 0, &mask);
    stop_fail = 1; irq[4] = BIT_DONEI;
    svc_play_query_state(4, &playing, &glb, &elapsed);
    CHECK(playing && chips[4].safety_armed, "Failed cleanup retains playing state and watchdog");
    CHECK(svc_play_trig_pulse(0x10, 20, 1, 0, 0, &mask) != ESP_OK, "New trigger cannot erase a failed STOP");
    stop_fail = 0;
    svc_play_query_state(4, &playing, &glb, &elapsed);
    CHECK(!playing && !chips[4].safety_armed && !physical_output[4], "Retry cleanup after read-clear DONE has vanished");

    setup(AW_MODE_CONT);
    CHECK(svc_play_timed(0x10, 2000, &mask) == ESP_OK, "Timed CONT starts");
    now_ms += 2000; svc_play_tick(now_ms);
    CHECK(last_reason == PLAY_DONE_DURATION && !trips && !physical_output[4], "Normal duration stops without safety-trip error");
    setup(AW_MODE_CONT); svc_play_timed(0x10, 2000, &mask);
    now_ms += 2600; svc_play_tick(now_ms);
    CHECK(last_reason == PLAY_DONE_SAFETY_TIMEOUT && trips == 1, "Missed deadline remains a real safety-trip");

    setup(AW_MODE_RAM); clear_fail = 1;
    svc_play_play_multi(0x10, 2000, 0, false, &mask, &skew);
    CHECK(!physical_output[4], "Failed old-event clear prevents GO");

    setup(AW_MODE_TRIG);
    CHECK(svc_play_boot_arm(0x10) == ESP_OK, "BOOT can arm uploaded TRIG");
    CHECK(svc_play_boot_fire(&mask) == ESP_OK && mask == 0x10, "BOOT starts only explicitly selected channel");
    irq[4] = BIT_DONEI; svc_play_query_state(4, &playing, &glb, &elapsed);
    CHECK(svc_play_boot_fire(&mask) == ESP_OK, "BOOT can retrigger after automatic cleanup");
    svc_play_stop_all(1, &mask);
    CHECK(svc_play_boot_fire(&mask) != ESP_OK && mask == 0, "Emergency STOP disarms BOOT");

    setup(AW_MODE_RAM); irq_after_go = 1;
    svc_play_start(4, 2000, false, &elapsed);
    svc_play_poll(now_ms + 100);
    CHECK(!physical_output[4] && !chips[4].safety_armed, "Very short real DONE after GO is preserved");

    setup(AW_MODE_CONT); svc_play_start(4, 2000, false, &elapsed);
    irq[4] = BIT_DONEI; svc_play_poll(now_ms + 100);
    CHECK(physical_output[4] && chips[4].safety_armed, "CONT segment DONE cannot stop the full run");
    setup(AW_MODE_RAM); chips[4].profile.mainloop = 15;
    svc_play_start(4, 2000, false, &elapsed); irq[4] = BIT_DONEI;
    svc_play_poll(now_ms + 100);
    CHECK(physical_output[4] && chips[4].safety_armed, "Infinite RAM retains protection despite DONE");

    setup(AW_MODE_TRIG); delay_action = 1;
    CHECK(svc_play_trig_pulse(0x10, 20, 2, 100, 0, &mask) != ESP_OK, "Timeout cancels multi-pulse even when STOP fails");
    CHECK(pulse_edges == 1 && chips[4].safety_armed, "No second edge or watchdog rearm after failed safety STOP");
    stop_fail = 0; svc_play_tick(now_ms);
    CHECK(!physical_output[4], "Safety STOP retries successfully");
    setup(AW_MODE_TRIG); delay_action = 2;
    svc_play_trig_pulse(0x10, 20, 2, 100, 0, &mask);
    CHECK(pulse_edges == 1 && !physical_output[4], "STOP can interrupt an inter-pulse wait");
    setup(AW_MODE_TRIG); delay_action = 3;
    CHECK(svc_play_trig_pulse(0x10, 20, 2, 100, 0, &mask) == ESP_OK, "Intermediate DONE does not terminate pulse train");
    svc_play_query_state(4, &playing, &glb, &elapsed);
    CHECK(playing && chips[4].safety_armed && pulse_edges == 2, "Final pulse owns completion after intermediate DONE cleared");

    setup(AW_MODE_TRIG); svc_play_boot_arm(0x10);
    svc_play_prepare_config(0x10);
    CHECK(svc_play_boot_fire(&mask) != ESP_OK, "Configuration change revokes BOOT authorization");
    live_config_t cfg = {.frequency_x10=1680, .amplitude=50, .envelope=0, .rate_x10=10, .depth=100, .tracking=0};
    setup(AW_MODE_RAM);
    CHECK(svc_play_live_control(0, 0x10, &cfg) == ESP_OK, "ADS start accepts and fills bounded FIFO");
    CHECK(fifo_count[4] <= 120 && fifo_count[4] >= 110, "ADS uses small real-watermark queue");
    CHECK(!chips[4].safety_armed && chips[4].playing, "Live owns separate lease without ordinary PLAY timeout");
    int old_go = go_calls; cfg.amplitude=20; cfg.frequency_x10=2000;
    CHECK(svc_play_live_control(1, 0x10, &cfg) == ESP_OK && go_calls == old_go, "Parameter UPDATE never GO or restart");
    CHECK(svc_play_live_control(1, 0x20, &cfg) != ESP_OK, "UPDATE rejects a changed mask");
    now_ms += 1900; CHECK(svc_play_live_keepalive(0x10)==ESP_OK, "Explicit keepalive renews lease");
    now_ms += 1900; svc_play_tick(now_ms);
    CHECK(chips[4].playing, "Renewed lease permits live beyond ordinary 2s deadline");
    now_ms += 101; svc_play_tick(now_ms);
    CHECK(!chips[4].playing && !svc_play_live_mask() && trips, "Expired lease stops output");
    CHECK(svc_play_live_keepalive(0x10)!=ESP_OK && svc_play_live_control(1,0x10,&cfg)!=ESP_OK && go_calls==old_go, "Late keepalive and UPDATE cannot restart expired session");
    setup(AW_MODE_RAM); cfg.frequency_x10=1680;
    svc_play_live_control(0,0x10,&cfg); old_go=go_calls;
    stop_fail=1; svc_play_stop(4,1);
    CHECK(!svc_play_live_mask() && physical_output[4], "STOP failure revokes lease before hardware retry");
    stop_fail=0; svc_play_tick(now_ms);
    CHECK(!physical_output[4] && go_calls==old_go, "Failed live STOP retries without new GO");
    setup(AW_MODE_RAM); chips[5]=chips[4];
    svc_play_live_control(0,0x30,&cfg); irq[4]=BIT_DONEI; svc_play_tick(now_ms);
    CHECK(!physical_output[4]&&!physical_output[5]&&!svc_play_live_mask(), "One ADS natural DONE cancels the whole session");
    setup(AW_MODE_RAM); svc_play_live_control(0,0x10,&cfg);
    fifo_count[4]=0; rtp_fail=1; svc_play_tick(now_ms);
    CHECK(!physical_output[4]&&!svc_play_live_mask(), "FIFO I2C error stops instead of blindly restarting");
    setup(AW_MODE_RAM); cfg.tracking=1; chips[4].profile.drv_width=255; chips[4].profile.track_margin=0;
    CHECK(svc_play_live_control(0,0x10,&cfg)==ESP_OK && chips[4].mode==AW_MODE_CONT && chips[4].profile.track_en, "Tracked engine uses CONT hardware tracking");
    CHECK(chips[4].profile.drv_width==106 && chips[4].profile.track_margin==17, "Tracked start restores validated width and loop margin");
    old_go=go_calls; cfg.envelope=1; svc_play_live_control(1,0x10,&cfg); svc_play_tick(now_ms+10);
    CHECK(go_calls==old_go, "Tracked envelope UPDATE has no GO");
    int old_done = done_events;
    cfg.tracking=0; CHECK(svc_play_live_control(1,0x10,&cfg)==ESP_OK && go_calls==old_go+1 && chips[4].mode==AW_MODE_RTP, "Explicit engine switch safely stops and starts ADS");
    CHECK(done_events==old_done, "Successful engine switch emits no DONE event");
    svc_play_prepare_config(0x10);
    CHECK(!svc_play_live_mask()&&!physical_output[4], "Configuration revokes live authorization");
    CHECK(svc_play_boot_fire(&mask)!=ESP_OK, "Live mode revokes prior BOOT authorization");
    setup(AW_MODE_RAM); cfg.tracking=0; cfg.frequency_x10=3000; consume_per_xfer=2;
    CHECK(svc_play_live_control(0,0x10,&cfg)==ESP_OK, "300Hz ADS starts while FIFO consumes during I2C transfers");
    CHECK(svc_play_live_mask()==0x10 && chips[4].playing, "Consuming FIFO need not reach AF to establish live session");
    CHECK(rtp_bytes<=200 && rtp_calls<=4 && max_rtp_len<=60, "Initial refill has strict AF-byte and <=60-byte transaction budgets");
    fifo_count[4]=110; rtp_bytes=rtp_calls=0; svc_play_tick(now_ms);
    CHECK(chips[4].playing && rtp_bytes<=90 && rtp_calls<=2, "Steady low-water refill tolerates consumption with strict AF-AE budget");
    CHECK(fifo_count[4]>110 && fifo_count[4]<=200, "Steady refill restores headroom without building a long queue");
    rtp_bytes=rtp_calls=0; svc_play_tick(now_ms);
    CHECK(rtp_bytes==0, "No refill while true FIFO level remains above AE");
    svc_play_stop(4,1);
    setup(AW_MODE_RAM); chips[5]=chips[4]; chips[6]=chips[4]; chips[7]=chips[4]; consume_per_xfer=2;
    CHECK(svc_play_live_control(0,0xF0,&cfg)==ESP_OK && svc_play_live_mask()==0xF0, "Four 300Hz channels start with per-transfer FIFO consumption");
    CHECK(rtp_bytes<=800 && rtp_calls<=16, "Four-channel initial work remains bounded");
    svc_play_stop_all(1,&mask);
    setup(AW_MODE_RAM); chips[5]=chips[4]; chips[6]=chips[4]; chips[7]=chips[4];
    uint8_t levels[8]={0};
    CHECK(svc_play_tactile_frame(0,0xF0,65535,levels)==ESP_OK, "Tactile silent start");
    old_go=go_calls; levels[4]=20; levels[5]=30; levels[6]=40; levels[7]=50;
    CHECK(svc_play_tactile_frame(1,0xF0,0,levels)==ESP_OK, "Tactile sequence wrap accepted");
    CHECK(chips[4].profile.drv1_lvl==20 && chips[5].profile.drv1_lvl==30 && chips[6].profile.drv1_lvl==40 && chips[7].profile.drv1_lvl==50, "Per-channel amplitudes applied");
    CHECK(go_calls==old_go+4, "Each nonzero tactile onset starts hardware");
    old_go=go_calls;
    CHECK(svc_play_tactile_frame(1,0xF0,1,levels)==ESP_OK && go_calls==old_go, "Sustained tactile amplitude does not restart");
    CHECK(svc_play_live_keepalive(0xF0)!=ESP_OK, "Live keepalive cannot renew tactile data");
    CHECK(svc_play_live_control(1,0xF0,&cfg)!=ESP_OK, "Ordinary live update cannot overwrite tactile session");
    now_ms+=200;
    CHECK(svc_play_tactile_frame(1,0xF0,1,levels)!=ESP_OK, "Duplicate tactile sequence rejected");
    CHECK(svc_play_tactile_frame(1,0xF0,65535,levels)!=ESP_OK, "Old tactile sequence rejected");
    levels[4]=61; CHECK(svc_play_tactile_frame(1,0xF0,1,levels)!=ESP_OK, "Tactile peak clamp enforced");
    levels[4]=levels[5]=levels[6]=levels[7]=60;
    CHECK(svc_play_tactile_frame(1,0xF0,1,levels)!=ESP_OK, "Tactile total output budget enforced");
    now_ms+=100; svc_play_tick(now_ms);
    CHECK(!svc_play_live_mask()&&!physical_output[4]&&!physical_output[7], "Tactile data lease stops entire vector after 300ms");
    levels[4]=levels[5]=levels[6]=levels[7]=20;
    CHECK(svc_play_tactile_frame(1,0xF0,2,levels)!=ESP_OK, "Late UPDATE cannot restart expired tactile session");
    CHECK(svc_play_tactile_frame(0,0xF0,2,levels)!=ESP_OK, "Nonzero START rejected");
    memset(levels,0,8); CHECK(svc_play_tactile_frame(0,0xF0,10,levels)==ESP_OK, "Explicit silent restart allowed");
    levels[0]=1; CHECK(svc_play_tactile_frame(1,0xF0,11,levels)!=ESP_OK, "Unselected output rejected");
    levels[0]=0; now_ms+=250; CHECK(svc_play_tactile_frame(1,0xF0,11,levels)==ESP_OK, "Fresh frame renews tactile lease");
    now_ms+=100; svc_play_tick(now_ms); CHECK(svc_play_live_mask()==0xF0, "Fresh zero-contact data sustains silent session");
    svc_play_stop(4,1);
    CHECK(!svc_play_live_mask() && svc_play_tactile_frame(1,0xF0,12,levels)!=ESP_OK, "STOP revokes entire tactile session");
    setup(AW_MODE_RAM); chips[5]=chips[4]; chips[6]=chips[4]; chips[7]=chips[4];
    memset(levels,0,8); svc_play_tactile_frame(0,0xF0,1,levels);
    levels[6]=levels[7]=60; svc_play_tactile_frame(1,0xF0,2,levels);
    levels[6]=levels[7]=0; levels[4]=levels[5]=60;
    CHECK(svc_play_tactile_frame(1,0xF0,3,levels)==ESP_OK && peak_energy<=7200, "Budget holds at every intermediate hardware write");
    amp_fail_ch=4; levels[4]=levels[5]=0; levels[6]=levels[7]=60;
    CHECK(svc_play_tactile_frame(1,0xF0,4,levels)!=ESP_OK && !svc_play_live_mask(), "Failed amplitude decrease cancels before any increase");
    CHECK(peak_energy<=7200 && !physical_output[4] && !physical_output[7], "Failed transition cannot exceed budget or continue driving");
    setup(AW_MODE_RAM);
    memset(levels,0,8);
    old_go=go_calls;
    CHECK(svc_play_tactile_frame(0,0x10,0,levels)==ESP_OK && go_calls==old_go && !physical_output[4], "Silent tactile START never sends zero-amplitude GO");
    CHECK(chips[4].profile.track_en==0 && chips[4].profile.drv_width==80, "Tactile uses tested fixed-width drive, no low-amplitude tracking");
    for (int cycle=0; cycle<20; ++cycle) {
        levels[4]=30;
        CHECK(svc_play_tactile_frame(1,0x10,cycle*2+1,levels)==ESP_OK && physical_output[4], "Repeated nonzero onset drives output");
        CHECK(brake_time[4]==0 && brake_enable[4]==0, "Sustained drive disables brake segments");
        levels[4]=0;
        CHECK(svc_play_tactile_frame(1,0x10,cycle*2+2,levels)==ESP_OK && !physical_output[4], "Zero tactile level physically stops output");
        CHECK(brake_time[4]==4 && brake_enable[4]==4, "Release enables bounded brake");
    }
    CHECK(go_calls==old_go+20, "Exactly one GO per pulse");
    levels[4]=30; svc_play_tactile_frame(1,0x10,41,levels);
    stop_fail=1; levels[4]=0;
    CHECK(svc_play_tactile_frame(1,0x10,42,levels)!=ESP_OK && !svc_play_live_mask(), "Failed zero-level STOP revokes tactile session");
    stop_fail=0; svc_play_tick(now_ms+20);
    setup(AW_MODE_RAM); memset(levels,0,8);
    svc_play_tactile_frame(0,0x10,0,levels); levels[4]=30;
    svc_play_tactile_frame(1,0x10,1,levels); levels[4]=0; brake_fail=1;
    CHECK(svc_play_tactile_frame(1,0x10,2,levels)!=ESP_OK && !svc_play_live_mask(), "Failed brake configuration revokes whole session");
    setup(AW_MODE_RAM); memset(levels,0,8);
    svc_play_tactile_frame(0,0x10,0,levels); levels[4]=35;
    svc_play_tactile_frame(1,0x10,1,levels); levels[4]=0;
    svc_play_tactile_frame(1,0x10,2,levels);
    old_go=go_calls;
    now_ms+=10; svc_play_tick(now_ms);
    CHECK(brake_enable[4]==4 && brake_time[4]==4, "Brake is preserved during intended short window");
    now_ms+=10; svc_play_tick(now_ms);
    CHECK(brake_enable[4]==0 && brake_time[4]==0 && !physical_output[4], "Idle tick finishes brake without any new input or rising edge");
    CHECK(go_calls==old_go && svc_play_live_mask()==0x10, "Quiet cleanup never GO and keeps valid silent session");
    int quiet_stops=stops[4];
    now_ms+=20; svc_play_tactile_frame(1,0x10,3,levels); svc_play_tick(now_ms);
    CHECK(stops[4]==quiet_stops && brake_enable[4]==0, "Repeated zero frames do not rearm brake or repeat cleanup");
    levels[4]=35; svc_play_tactile_frame(1,0x10,4,levels);
    CHECK(go_calls==old_go+1 && physical_output[4], "Next click still starts normally after quiet idle");
    levels[4]=0; svc_play_tactile_frame(1,0x10,5,levels);
    brake_fail=1; now_ms+=20; svc_play_tick(now_ms);
    CHECK(!svc_play_live_mask(), "Idle brake cleanup failure revokes tactile session");
    printf("%d failed regression checks\n", failures);
    return failures ? 1 : 0;
}
