#pragma once
typedef int *SemaphoreHandle_t;
static int fake_mutex;
static inline SemaphoreHandle_t xSemaphoreCreateRecursiveMutex(void) { return &fake_mutex; }
static inline int xSemaphoreTakeRecursive(SemaphoreHandle_t m, unsigned int ticks) { (void)ticks; ++*m; return 1; }
static inline int xSemaphoreGiveRecursive(SemaphoreHandle_t m) { --*m; return 1; }
