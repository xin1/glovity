/* 纯 C99 示例：证明不需要 C++ 工具链也能集成。
 *   ./minimal_poll_c /dev/ttyACM0      (Windows: minimal_poll_c.exe COM3)
 *   ./minimal_poll_c file://sample_capture.bin */
#include <telex_glove.h>

#include <stdio.h>
#include <string.h>

#if defined(_WIN32)
#  define DEFAULT_PORT "COM3"
#else
#  define DEFAULT_PORT "/dev/ttyACM0"
#endif

int main(int argc, char** argv) {
    const char* port = (argc > 1) ? argv[1] : DEFAULT_PORT;
    telex_glove_hub* hub = NULL;
    telex_glove_status rc;

    rc = telex_glove_hub_create(port, 0, &hub);
    if (rc != TELEX_GLOVE_OK) {
        fprintf(stderr, "create: %s (%s)\n",
                telex_glove_status_string(rc), telex_glove_last_error());
        return 1;
    }
    rc = telex_glove_hub_start(hub, 3.0);
    if (rc != TELEX_GLOVE_OK) {
        fprintf(stderr, "start: %s (%s)\n",
                telex_glove_status_string(rc), telex_glove_last_error());
        telex_glove_hub_destroy(hub);
        return 1;
    }

    for (int i = 0; i < 50; ++i) {
        telex_glove_snapshot snap;
        memset(&snap, 0, sizeof(snap));
        snap.struct_size = (uint32_t)sizeof(snap);

        rc = telex_glove_hub_wait_next(hub, TELEX_GLOVE_HAND_LEFT, 0.5, &snap);
        if (rc != TELEX_GLOVE_OK) continue;

        printf("protocol=%d frame_seq=%u timestamp_us=%llu recording=%d bat=%d%% fail=0x%03x ",
               snap.protocol, snap.frame_id,
               (unsigned long long)snap.device_timestamp_us,
               snap.recording, snap.battery_percent, snap.imu_fail_mask);
        if (snap.joints_valid)
            printf("tip=(%.4f, %.4f, %.4f)\n",
                   snap.joints[4].x, snap.joints[4].y, snap.joints[4].z);
        else
            printf("joints=N/A\n");
    }

    telex_glove_hub_destroy(hub);
    return 0;
}
