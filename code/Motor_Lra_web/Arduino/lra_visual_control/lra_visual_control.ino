#include <SoftwareSerial.h>

/*
 * LRA serial wiring:
 *   Arduino D10 <- LRA TX
 *   Arduino D11 -> LRA RX (use a 5 V to 3.3 V divider/level shifter)
 *
 * Continuous trigger wiring:
 *   Arduino D6  -> LRA IO1
 *
 * D6 is only driven LOW or left as a high-impedance input. This emulates an
 * open-drain output and avoids applying Arduino's 5 V HIGH level to IO1.
 */
SoftwareSerial lraSerial(10, 11);

const uint8_t LRA_IO1_PIN = 6;
const uint8_t MIN_AMP = 25;  // About 1.83 Vpp
const uint8_t MAX_AMP = 47;  // About 3.26 Vpp
const unsigned long HOLD_TIMEOUT_MS = 800;
const unsigned long LRA_REPLY_IDLE_MS = 8;
const unsigned long ACTION_RETRY_DELAY_MS = 25;
const unsigned long F0_DETECTION_TIMEOUT_MS = 30000;

enum EffectType : uint8_t
{
    EFFECT_NONE,
    EFFECT_SHORT,
    EFFECT_LONG,
    EFFECT_SAW,
    EFFECT_FRICTION,
    EFFECT_COLLISION
};

const uint16_t SAW_FREQUENCY_HZ = 170;
// With a peak of 42 these references become 25, 29, 33, 37, 42.
const uint8_t SAW_REFERENCE_AMPLITUDES[] = {25, 30, 35, 40, 47};
const uint8_t SAW_RISE_STEP_COUNT = sizeof(SAW_REFERENCE_AMPLITUDES) / sizeof(SAW_REFERENCE_AMPLITUDES[0]);
const uint8_t SAW_REPEAT_COUNT = 2;
const uint8_t SAW_PHASES_PER_CYCLE = SAW_RISE_STEP_COUNT + 1; // Five levels plus the off gap.
const uint8_t SAW_TOTAL_PHASE_COUNT = SAW_PHASES_PER_CYCLE * SAW_REPEAT_COUNT;
const unsigned long SAW_STEP_MS = 100;
const unsigned long SAW_PEAK_MS = 120;
const unsigned long SAW_GAP_MS = 40;

bool continuousActive = false;
uint8_t currentAmp = MAX_AMP;
uint8_t userMaxAmp = MAX_AMP;
uint16_t currentFrequency = 170;
uint16_t userBaseFrequency = 170;
unsigned long lastHostActivityMs = 0;
EffectType activeEffect = EFFECT_NONE;
uint8_t effectStep = 0;
bool effectOutputOn = false;
unsigned long effectDeadlineMs = 0;
unsigned long effectStartMs = 0;
uint8_t effectPeakAmp = MAX_AMP;
bool longEffectReleased = false;
bool cancelRequested = false;

char hostLine[40];
uint8_t hostLineLength = 0;
char pendingHostCommand[40];
char pendingLiveCommand[40];
bool hasPendingHostCommand = false;
bool hasPendingLiveCommand = false;
bool f0DetectionActive = false;
unsigned long f0DetectionStartMs = 0;
char lraAsyncLine[48];
uint8_t lraAsyncLineLength = 0;

void clearLraReceiveBuffer()
{
    while (lraSerial.available())
        lraSerial.read();
}

void stopContinuousVibration()
{
    // INPUT means high impedance. The LRA module's pull-up releases IO1.
    pinMode(LRA_IO1_PIN, INPUT);
    continuousActive = false;
}

void startContinuousVibration()
{
    digitalWrite(LRA_IO1_PIN, LOW);
    pinMode(LRA_IO1_PIN, OUTPUT);
    continuousActive = true;
    lastHostActivityMs = millis();
}

void checkHoldWatchdog()
{
    if (activeEffect == EFFECT_NONE && continuousActive &&
        millis() - lastHostActivityMs > HOLD_TIMEOUT_MS)
    {
        stopContinuousVibration();
        Serial.println(F("EVENT STOP WATCHDOG"));
    }
}

const char *effectName(EffectType effect)
{
    switch (effect)
    {
    case EFFECT_SHORT: return "SHORT";
    case EFFECT_LONG: return "LONG";
    case EFFECT_SAW: return "SAW";
    case EFFECT_FRICTION: return "FRICTION";
    case EFFECT_COLLISION: return "COLLISION";
    default: return "NONE";
    }
}

void stopEverything(bool reportStop)
{
    EffectType stoppedEffect = activeEffect;
    stopContinuousVibration();
    activeEffect = EFFECT_NONE;
    effectOutputOn = false;
    cancelRequested = true;

    if (stoppedEffect != EFFECT_NONE)
    {
        Serial.print(F("EVENT EFFECT "));
        Serial.print(effectName(stoppedEffect));
        Serial.println(F(" CANCELLED"));
    }
    if (reportStop)
        Serial.println(F("EVENT STOP"));
}

bool isLiveHostCommand(const char *line)
{
    return strncmp(line, "HOLD ", 5) == 0 ||
           strncmp(line, "LONG_HOLD ", 10) == 0 ||
           strncmp(line, "FRICTION_HOLD ", 14) == 0;
}

void copyPendingCommand(char *destination, const char *source)
{
    strncpy(destination, source, sizeof(pendingHostCommand) - 1);
    destination[sizeof(pendingHostCommand) - 1] = '\0';
}

/*
 * LRA commands wait for a reply. Continue reading USB serial during that wait:
 * STOP is executed immediately, the latest control command is retained, and
 * rapidly changing HOLD values are coalesced to the newest one.
 */
void readEmergencyStopDuringLraWait()
{
    while (Serial.available())
    {
        char c = (char)Serial.read();

        if (c == '\r' || c == '\n')
        {
            if (hostLineLength > 0)
            {
                hostLine[hostLineLength] = '\0';
                if (strcmp(hostLine, "STOP") == 0)
                {
                    hasPendingHostCommand = false;
                    hasPendingLiveCommand = false;
                    stopEverything(true);
                }
                else if (isLiveHostCommand(hostLine))
                {
                    copyPendingCommand(pendingLiveCommand, hostLine);
                    hasPendingLiveCommand = true;
                }
                else
                {
                    copyPendingCommand(pendingHostCommand, hostLine);
                    hasPendingHostCommand = true;
                    hasPendingLiveCommand = false;
                }
                hostLineLength = 0;
            }
        }
        else if (hostLineLength < sizeof(hostLine) - 1)
        {
            hostLine[hostLineLength++] = c;
        }
        else
        {
            hostLineLength = 0;
        }
    }
}

bool waitForOK(unsigned long timeoutMs)
{
    char responseTail[8] = {0};
    uint8_t responseLength = 0;
    unsigned long startTime = millis();
    unsigned long lastReceiveTime = startTime;
    int8_t responseResult = 0;
    bool waitingForStatusStart = false;
    bool statusStartedWithO = false;
    bool statusStartedWithE = false;
    bool sawOkSuffix = false;
    char previousChar = '\0';

    while (millis() - startTime < timeoutMs)
    {
        readEmergencyStopDuringLraWait();
        if (cancelRequested)
            return false;

        checkHoldWatchdog();

        while (lraSerial.available())
        {
            char c = (char)lraSerial.read();
            Serial.write(c);
            lastReceiveTime = millis();

            if (c == ',')
            {
                waitingForStatusStart = true;
            }
            else if (waitingForStatusStart && c != ' ' && c != '\r' && c != '\n')
            {
                statusStartedWithO = c == 'O';
                statusStartedWithE = c == 'E';
                waitingForStatusStart = false;
            }

            if (previousChar == 'K' && c == '!')
                sawOkSuffix = true;
            previousChar = c;

            if (responseLength < sizeof(responseTail) - 1)
                responseTail[responseLength++] = c;
            else
            {
                memmove(responseTail, responseTail + 1, sizeof(responseTail) - 2);
                responseTail[sizeof(responseTail) - 2] = c;
                responseLength = sizeof(responseTail) - 1;
            }
            responseTail[responseLength] = '\0';

            if (strstr(responseTail, "ERROR!") != NULL)
                responseResult = -1;
            else if (strstr(responseTail, "OK!") != NULL)
                responseResult = 1;
        }

        // Do not transmit the next command as soon as the three OK characters
        // arrive. Drain the complete reply and wait for a short idle gap so the
        // module firmware has left its transmit routine and can receive again.
        if (responseResult != 0 &&
            millis() - lastReceiveTime >= LRA_REPLY_IDLE_MS)
            return responseResult > 0;

        if (millis() - lastReceiveTime >= LRA_REPLY_IDLE_MS)
        {
            if (statusStartedWithE)
                return false;
            if (statusStartedWithO || sawOkSuffix)
            {
                Serial.println(F("\nEVENT WARN RECOVERED_DAMAGED_OK"));
                return true;
            }
        }
    }

    if (responseResult != 0)
        return responseResult > 0;
    if (statusStartedWithE)
        return false;
    if (statusStartedWithO || sawOkSuffix)
    {
        Serial.println(F("\nEVENT WARN RECOVERED_DAMAGED_OK"));
        return true;
    }

    Serial.println(F("\nTimeout waiting for LRA response"));
    return false;
}

bool sendCommand(const char *command, unsigned long timeoutMs = 1000)
{
    Serial.print(F("\nSend: "));
    Serial.println(command);

    lraSerial.print(command);
    lraSerial.print("\r\n");

    return waitForOK(timeoutMs);
}

bool sendAction(unsigned long timeoutMs = 1000)
{
    for (uint8_t attempt = 0; attempt < 2; ++attempt)
    {
        if (sendCommand("ACTION", timeoutMs))
            return true;
        if (cancelRequested)
            return false;

        if (attempt == 0)
        {
            Serial.println(F("EVENT RETRY ACTION"));
            delay(ACTION_RETRY_DELAY_MS);
            clearLraReceiveBuffer();
        }
    }
    return false;
}

bool connectToLRA()
{
    lraSerial.listen();
    clearLraReceiveBuffer();

    for (uint8_t attempt = 1; attempt <= 3; ++attempt)
    {
        Serial.print(F("LRA connection attempt "));
        Serial.print(attempt);
        Serial.println(F("/3"));

        if (sendCommand("MCU_MODE", 1500))
            return true;

        // WAKE_UP is harmless when the module is already awake. Its reply is
        // intentionally ignored here; the following MCU_MODE is the probe.
        lraSerial.print("WAKE_UP\r\n");
        delay(300);
        clearLraReceiveBuffer();
    }

    return false;
}

bool setParameter(const char *command, const char *value,
                  unsigned long timeoutMs = 1000)
{
    for (uint8_t attempt = 0; attempt < 3; ++attempt)
    {
        if (sendCommand(command, timeoutMs) &&
            sendCommand(value, timeoutMs))
            return true;

        if (cancelRequested)
            return false;

        if (attempt < 2)
        {
            Serial.print(F("EVENT RETRY PARAM "));
            Serial.println(command);
            delay(ACTION_RETRY_DELAY_MS);
            clearLraReceiveBuffer();
        }
    }
    return false;
}

uint8_t clampAmp(int value)
{
    if (value < MIN_AMP)
        return MIN_AMP;
    if (value > MAX_AMP)
        return MAX_AMP;
    return (uint8_t)value;
}

uint16_t clampFrequency(int value)
{
    if (value < 20)
        return 20;
    if (value > 500)
        return 500;
    return (uint16_t)value;
}

void processLraAsyncLine(char *line)
{
    char *f0Marker = strstr(line, "LRA_F0:");
    if (f0Marker == NULL)
        return;

    uint16_t detectedFrequency = clampFrequency(atoi(f0Marker + 7));
    currentFrequency = detectedFrequency;
    userBaseFrequency = detectedFrequency;
    f0DetectionActive = false;

    Serial.print(F("EVENT F0 "));
    Serial.println(detectedFrequency);
}

void readLraAsyncOutput()
{
    while (lraSerial.available())
    {
        char c = (char)lraSerial.read();
        Serial.write(c);

        if (c == '\r' || c == '\n')
        {
            if (lraAsyncLineLength > 0)
            {
                lraAsyncLine[lraAsyncLineLength] = '\0';
                processLraAsyncLine(lraAsyncLine);
                lraAsyncLineLength = 0;
            }
        }
        else if (lraAsyncLineLength < sizeof(lraAsyncLine) - 1)
        {
            lraAsyncLine[lraAsyncLineLength++] = c;
        }
        else
        {
            lraAsyncLineLength = 0;
        }
    }
}

void checkF0DetectionTimeout()
{
    if (f0DetectionActive &&
        millis() - f0DetectionStartMs > F0_DETECTION_TIMEOUT_MS)
    {
        f0DetectionActive = false;
        Serial.println(F("EVENT ERROR F0_TIMEOUT"));
    }
}

uint8_t scalePresetAmplitude(uint8_t referenceAmplitude)
{
    if (referenceAmplitude <= MIN_AMP || userMaxAmp <= MIN_AMP)
        return MIN_AMP;

    unsigned int numerator = (unsigned int)(referenceAmplitude - MIN_AMP) *
                             (unsigned int)(userMaxAmp - MIN_AMP);
    return MIN_AMP + (uint8_t)((numerator + (MAX_AMP - MIN_AMP) / 2) /
                               (MAX_AMP - MIN_AMP));
}

uint8_t scaleAmplitudeToPeak(uint8_t referenceAmplitude, uint8_t peakAmplitude)
{
    peakAmplitude = clampAmp(peakAmplitude);
    if (referenceAmplitude <= MIN_AMP || peakAmplitude <= MIN_AMP)
        return MIN_AMP;

    unsigned int numerator = (unsigned int)(referenceAmplitude - MIN_AMP) *
                             (unsigned int)(peakAmplitude - MIN_AMP);
    return MIN_AMP + (uint8_t)((numerator + (MAX_AMP - MIN_AMP) / 2) /
                               (MAX_AMP - MIN_AMP));
}

bool setRuntimeOutput(uint16_t frequency, uint8_t amplitude)
{
    frequency = clampFrequency(frequency);
    amplitude = clampAmp(amplitude);

    bool frequencyChanged = frequency != currentFrequency;
    bool amplitudeChanged = amplitude != currentAmp;
    if (!frequencyChanged && !amplitudeChanged)
        return true;

    // Some LRA module firmware only applies SET_FREQ/SET_AMP between IO1
    // triggers. Release IO1 while changing parameters, then restore the
    // continuous trigger so live friction updates are physically audible.
    bool restoreContinuousTrigger = continuousActive;
    if (restoreContinuousTrigger)
        stopContinuousVibration();

    char frequencyValue[6];
    char amplitudeValue[4];
    itoa(frequency, frequencyValue, 10);
    itoa(amplitude, amplitudeValue, 10);

    if (frequencyChanged && !setParameter("SET_FREQ", frequencyValue, 400))
    {
        stopContinuousVibration();
        if (!cancelRequested)
            Serial.println(F("EVENT ERROR FREQ"));
        return false;
    }

    if (amplitudeChanged && !setParameter("SET_AMP1", amplitudeValue, 400))
    {
        stopContinuousVibration();
        if (!cancelRequested)
            Serial.println(F("EVENT ERROR AMP"));
        return false;
    }

    if (!sendAction(400))
    {
        stopContinuousVibration();
        if (!cancelRequested)
            Serial.println(F("EVENT ERROR ACTION"));
        return false;
    }

    currentFrequency = frequency;
    currentAmp = amplitude;
    if (restoreContinuousTrigger)
        startContinuousVibration();
    Serial.print(F("EVENT OUTPUT "));
    Serial.print(currentFrequency);
    Serial.print(' ');
    Serial.println(currentAmp);
    return true;
}

bool setRuntimeAmplitude(uint8_t amplitude)
{
    return setRuntimeOutput(currentFrequency, amplitude);
}

bool applyUserParameters(uint16_t frequency, uint8_t maximumAmplitude,
                         bool isPreload = false)
{
    frequency = clampFrequency(frequency);
    maximumAmplitude = clampAmp(maximumAmplitude);

    stopEverything(false);
    cancelRequested = false;

    // Reuse the hardware cache so selecting an already prepared mode returns
    // immediately instead of rewriting every module parameter.
    if (!setRuntimeOutput(frequency, maximumAmplitude))
        return false;

    userBaseFrequency = frequency;
    userMaxAmp = maximumAmplitude;

    Serial.print(isPreload ? F("EVENT PREP ") : F("EVENT PARAM "));
    Serial.print(currentFrequency);
    Serial.print(' ');
    Serial.println(userMaxAmp);
    return true;
}

bool configureLRA()
{
    stopEverything(false);
    cancelRequested = false;

    if (!connectToLRA())
        return false;
    if (!setParameter("SET_FREQ", "170"))
        return false;
    if (!setParameter("SET_AMP1", "47"))
        return false;
    if (!setParameter("SET_AMP2", "36"))
        return false;
    if (!setParameter("SET_CYC", "21"))
        return false;
    if (!setParameter("SET_ON_TIME", "50"))
        return false;
    if (!sendAction())
        return false;

    currentAmp = MAX_AMP;
    userMaxAmp = MAX_AMP;
    currentFrequency = 170;
    userBaseFrequency = 170;
    Serial.println(F("EVENT READY"));
    return true;
}

void beginEffectAt(EffectType effect, uint16_t frequency, uint8_t amplitude,
                   unsigned long firstPhaseMs)
{
    stopEverything(false);
    cancelRequested = false;

    if (!setRuntimeOutput(frequency, amplitude) || cancelRequested)
        return;

    activeEffect = effect;
    effectStep = 0;
    effectPeakAmp = clampAmp(amplitude);
    effectOutputOn = true;
    longEffectReleased = false;
    startContinuousVibration();
    effectStartMs = millis();
    effectDeadlineMs = effectStartMs + firstPhaseMs;

    Serial.print(F("EVENT EFFECT "));
    Serial.print(effectName(effect));
    Serial.println(F(" START"));
}

void beginEffect(EffectType effect, uint8_t amplitude, unsigned long firstPhaseMs)
{
    beginEffectAt(effect, userBaseFrequency, amplitude, firstPhaseMs);
}

void beginSawtoothEffect(uint8_t peakAmplitude)
{
    stopEverything(false);
    cancelRequested = false;

    peakAmplitude = clampAmp(peakAmplitude);
    uint8_t firstAmplitude = scaleAmplitudeToPeak(SAW_REFERENCE_AMPLITUDES[0], peakAmplitude);
    if (!setRuntimeOutput(SAW_FREQUENCY_HZ, firstAmplitude) || cancelRequested)
        return;

    activeEffect = EFFECT_SAW;
    effectStep = 0;
    effectPeakAmp = peakAmplitude;
    effectOutputOn = true;
    longEffectReleased = false;
    startContinuousVibration();
    effectStartMs = millis();
    effectDeadlineMs = effectStartMs + SAW_STEP_MS;

    Serial.println(F("EVENT EFFECT SAW START"));
}

void finishEffect()
{
    EffectType finishedEffect = activeEffect;
    stopContinuousVibration();
    activeEffect = EFFECT_NONE;
    effectOutputOn = false;

    if (finishedEffect != EFFECT_NONE)
    {
        Serial.print(F("EVENT EFFECT "));
        Serial.print(effectName(finishedEffect));
        Serial.println(F(" DONE"));
    }
}

void playShortVibration()     { beginEffectAt(EFFECT_SHORT, 150, scalePresetAmplitude(30), 60); }
void playLongVibration()      { beginEffectAt(EFFECT_LONG, 130, scalePresetAmplitude(36), 500); }
void playSawtoothVibration()  { beginSawtoothEffect(userMaxAmp); }
void playFrictionVibration()  { beginEffect(EFFECT_FRICTION, scalePresetAmplitude(42), 700); }
void playCollisionVibration() { beginEffectAt(EFFECT_COLLISION, 170, 33, 28); }

void updateEffect()
{
    if (activeEffect == EFFECT_NONE)
        return;
    if ((long)(millis() - effectDeadlineMs) < 0)
        return;

    switch (activeEffect)
    {
    case EFFECT_SHORT:
    case EFFECT_LONG:
    case EFFECT_FRICTION:
    case EFFECT_COLLISION:
        finishEffect();
        break;

    case EFFECT_SAW:
        ++effectStep;
        if (effectStep >= SAW_TOTAL_PHASE_COUNT)
        {
            finishEffect();
        }
        else
        {
            uint8_t phaseInCycle = effectStep % SAW_PHASES_PER_CYCLE;

            // Each rising ramp ends with a sharp 40 ms release before the
            // next ramp. The second release is also the tail of the effect.
            if (phaseInCycle == SAW_RISE_STEP_COUNT)
            {
                stopContinuousVibration();
                effectOutputOn = false;
                effectDeadlineMs = millis() + SAW_GAP_MS;
                break;
            }

            // Release IO1 while applying the next level. The longer 100/120
            // ms phases give the module time to finish its UART transaction
            // and let the LRA settle mechanically before the following step.
            stopContinuousVibration();
            if (!setRuntimeAmplitude(scaleAmplitudeToPeak(
                    SAW_REFERENCE_AMPLITUDES[phaseInCycle], effectPeakAmp)))
            {
                if (!cancelRequested)
                    Serial.println(F("EVENT ERROR SAW_PATTERN"));
                finishEffect();
            }
            else
            {
                startContinuousVibration();
                effectOutputOn = true;
                effectDeadlineMs = millis() +
                    (phaseInCycle == SAW_RISE_STEP_COUNT - 1 ? SAW_PEAK_MS : SAW_STEP_MS);
            }
        }
        break;

    default:
        finishEffect();
        break;
    }
}

void processHostCommand(char *line)
{
    if (line[0] == '\0')
        return;

    if (strcmp(line, "STOP") == 0)
    {
        stopEverything(true);
        return;
    }

    if (strcmp(line, "DETECT_F0") == 0 || strcmp(line, "F0_DET") == 0)
    {
        stopEverything(false);
        cancelRequested = false;
        lraAsyncLineLength = 0;
        f0DetectionActive = true;
        f0DetectionStartMs = millis();

        if (sendCommand("F0_DET", 2000))
            Serial.println(F("EVENT F0 SCANNING"));
        else
        {
            f0DetectionActive = false;
            if (!cancelRequested)
                Serial.println(F("EVENT ERROR F0_START"));
        }
        return;
    }

    if (f0DetectionActive)
    {
        Serial.println(F("EVENT ERROR F0_BUSY"));
        return;
    }

    if (strncmp(line, "PULSE ", 6) == 0)
    {
        char *frequencyText = strtok(line + 6, " ");
        char *amplitudeText = strtok(NULL, " ");
        char *durationText = strtok(NULL, " ");
        if (frequencyText == NULL || amplitudeText == NULL || durationText == NULL)
        {
            Serial.println(F("EVENT ERROR PULSE_FORMAT"));
            return;
        }

        unsigned long durationMs = strtoul(durationText, NULL, 10);
        if (durationMs < 20) durationMs = 20;
        if (durationMs > 5000) durationMs = 5000;
        beginEffectAt(EFFECT_SHORT, clampFrequency(atoi(frequencyText)),
                      clampAmp(atoi(amplitudeText)), durationMs);
        return;
    }

    if (strncmp(line, "SAW_PLAY ", 9) == 0)
    {
        char *frequencyText = strtok(line + 9, " ");
        char *amplitudeText = strtok(NULL, " ");
        if (frequencyText == NULL || amplitudeText == NULL)
        {
            Serial.println(F("EVENT ERROR SAW_FORMAT"));
            return;
        }

        // SAW is intentionally locked to the measured 170 Hz resonance.
        // Keep accepting FREQ in the protocol for compatibility with the UI.
        (void)frequencyText;
        beginSawtoothEffect(clampAmp(atoi(amplitudeText)));
        return;
    }

    if (strncmp(line, "LONG_START ", 11) == 0)
    {
        char *frequencyText = strtok(line + 11, " ");
        char *amplitudeText = strtok(NULL, " ");
        if (frequencyText == NULL || amplitudeText == NULL)
        {
            Serial.println(F("EVENT ERROR LONG_FORMAT"));
            return;
        }

        beginEffectAt(EFFECT_LONG, clampFrequency(atoi(frequencyText)),
                      clampAmp(atoi(amplitudeText)), 3000);
        return;
    }

    if (strncmp(line, "LONG_HOLD ", 10) == 0)
    {
        if (activeEffect != EFFECT_LONG)
            return;

        char *frequencyText = strtok(line + 10, " ");
        char *amplitudeText = strtok(NULL, " ");
        if (frequencyText == NULL || amplitudeText == NULL)
            return;

        if (setRuntimeOutput(clampFrequency(atoi(frequencyText)),
                             clampAmp(atoi(amplitudeText))))
        {
            unsigned long minimumEnd = effectStartMs + 3000;
            unsigned long keepAliveEnd = millis() + 500;
            effectDeadlineMs = ((long)(keepAliveEnd - minimumEnd) > 0) ?
                               keepAliveEnd : minimumEnd;
        }
        return;
    }

    if (strcmp(line, "LONG_RELEASE") == 0)
    {
        if (activeEffect == EFFECT_LONG)
        {
            longEffectReleased = true;
            if (millis() - effectStartMs >= 3000)
                finishEffect();
            else
                effectDeadlineMs = effectStartMs + 3000;
        }
        return;
    }

    bool frictionStart = strncmp(line, "FRICTION_START ", 15) == 0;
    bool frictionHold = strncmp(line, "FRICTION_HOLD ", 14) == 0;
    if (frictionStart || frictionHold)
    {
        if (frictionHold && activeEffect != EFFECT_NONE)
            return;

        char *arguments = line + (frictionStart ? 15 : 14);
        char *forceText = strtok(arguments, " ");
        char *minimumFrequencyText = strtok(NULL, " ");
        char *maximumFrequencyText = strtok(NULL, " ");
        char *maximumAmplitudeText = strtok(NULL, " ");
        if (forceText == NULL || minimumFrequencyText == NULL ||
            maximumFrequencyText == NULL || maximumAmplitudeText == NULL)
        {
            Serial.println(F("EVENT ERROR FRICTION_FORMAT"));
            return;
        }

        int forceTenths = atoi(forceText);
        if (forceTenths < 0) forceTenths = 0;
        if (forceTenths > 100) forceTenths = 100;

        uint16_t minimumFrequency = clampFrequency(atoi(minimumFrequencyText));
        uint16_t maximumFrequency = clampFrequency(atoi(maximumFrequencyText));
        if (maximumFrequency < minimumFrequency)
            maximumFrequency = minimumFrequency;
        uint8_t maximumAmplitude = clampAmp(atoi(maximumAmplitudeText));

        uint16_t requestedFrequency = minimumFrequency +
            ((unsigned long)(maximumFrequency - minimumFrequency) * forceTenths + 50) / 100;
        uint8_t requestedAmplitude = MIN_AMP +
            ((unsigned int)(maximumAmplitude - MIN_AMP) * forceTenths + 50) / 100;

        if (frictionStart)
        {
            stopEverything(false);
            cancelRequested = false;
        }

        lastHostActivityMs = millis();
        if (setRuntimeOutput(requestedFrequency, requestedAmplitude))
        {
            if (!continuousActive)
                startContinuousVibration();

            if (frictionStart)
            {
                Serial.print(F("EVENT START "));
                Serial.print(currentFrequency);
                Serial.print(' ');
                Serial.println(currentAmp);
            }
        }
        return;
    }

    if (strcmp(line, "G") == 0 || strcmp(line, "g") == 0 ||
        strcmp(line, "SHORT") == 0)
    {
        playShortVibration();
        return;
    }

    if (strcmp(line, "L") == 0 || strcmp(line, "l") == 0 ||
        strcmp(line, "LONG") == 0)
    {
        playLongVibration();
        return;
    }

    if (strcmp(line, "SAW") == 0)
    {
        playSawtoothVibration();
        return;
    }

    if (strcmp(line, "FRICTION") == 0)
    {
        playFrictionVibration();
        return;
    }

    if (strncmp(line, "COLLISION ", 10) == 0)
    {
        char *forceText = strtok(line + 10, " ");
        char *frequencyText = strtok(NULL, " ");
        char *amplitudeText = strtok(NULL, " ");
        if (forceText == NULL)
        {
            Serial.println(F("EVENT ERROR COLLISION_FORMAT"));
            return;
        }

        int forceTenths = atoi(forceText);
        if (forceTenths >= 60)
        {
            uint16_t frequency = frequencyText == NULL ? 170 :
                                 clampFrequency(atoi(frequencyText));
            uint8_t amplitude = amplitudeText == NULL ? 33 :
                                clampAmp(atoi(amplitudeText));
            beginEffectAt(EFFECT_COLLISION, frequency, amplitude, 28);
        }
        else
            Serial.println(F("EVENT COLLISION BLOCKED BELOW_6N"));
        return;
    }

    if (strncmp(line, "PARAM ", 6) == 0)
    {
        char *separator = strchr(line + 6, ' ');
        if (separator == NULL)
        {
            Serial.println(F("EVENT ERROR PARAM_FORMAT"));
            return;
        }

        *separator = '\0';
        uint16_t requestedFrequency = clampFrequency(atoi(line + 6));
        uint8_t requestedMaximumAmp = clampAmp(atoi(separator + 1));

        if (!applyUserParameters(requestedFrequency, requestedMaximumAmp) &&
            !cancelRequested)
            Serial.println(F("EVENT ERROR PARAM_APPLY"));
        return;
    }

    if (strncmp(line, "PREP ", 5) == 0)
    {
        char *separator = strchr(line + 5, ' ');
        if (separator == NULL)
        {
            Serial.println(F("EVENT ERROR PREP_FORMAT"));
            return;
        }

        *separator = '\0';
        uint16_t requestedFrequency = clampFrequency(atoi(line + 5));
        uint8_t requestedMaximumAmp = clampAmp(atoi(separator + 1));

        if (!applyUserParameters(requestedFrequency, requestedMaximumAmp, true) &&
            !cancelRequested)
            Serial.println(F("EVENT ERROR PREP_APPLY"));
        return;
    }

    if (strcmp(line, "C") == 0 || strcmp(line, "c") == 0 ||
        strcmp(line, "CONFIG") == 0)
    {
        configureLRA();
        return;
    }

    if (strncmp(line, "START ", 6) == 0)
    {
        stopEverything(false);
        cancelRequested = false;
        char *value = line + 6;
        char *separator = strchr(value, ' ');
        uint16_t requestedFrequency = currentFrequency;
        if (separator != NULL)
        {
            *separator = '\0';
            requestedFrequency = clampFrequency(atoi(separator + 1));
        }
        uint8_t requestedAmp = clampAmp(atoi(value));
        lastHostActivityMs = millis();

        if (setRuntimeOutput(requestedFrequency, requestedAmp))
        {
            startContinuousVibration();
            Serial.print(F("EVENT START "));
            Serial.print(currentFrequency);
            Serial.print(' ');
            Serial.println(currentAmp);
        }
        return;
    }

    if (strncmp(line, "AMP ", 4) == 0 ||
        strncmp(line, "HOLD ", 5) == 0)
    {
        // A queued mouse heartbeat must not overwrite an effect pattern.
        if (activeEffect != EFFECT_NONE)
            return;

        char *value = line[0] == 'A' ? line + 4 : line + 5;
        char *separator = strchr(value, ' ');
        uint16_t requestedFrequency = currentFrequency;
        if (separator != NULL)
        {
            *separator = '\0';
            requestedFrequency = clampFrequency(atoi(separator + 1));
        }
        uint8_t requestedAmp = clampAmp(atoi(value));
        lastHostActivityMs = millis();
        setRuntimeOutput(requestedFrequency, requestedAmp);
        return;
    }

    Serial.print(F("EVENT ERROR UNKNOWN_COMMAND "));
    Serial.println(line);
}

void readHostCommands()
{
    if (hasPendingHostCommand)
    {
        char commandToProcess[sizeof(hostLine)];
        copyPendingCommand(commandToProcess, pendingHostCommand);
        hasPendingHostCommand = false;
        processHostCommand(commandToProcess);
        return;
    }

    if (hasPendingLiveCommand)
    {
        char commandToProcess[sizeof(hostLine)];
        copyPendingCommand(commandToProcess, pendingLiveCommand);
        hasPendingLiveCommand = false;
        processHostCommand(commandToProcess);
        return;
    }

    while (Serial.available())
    {
        char c = (char)Serial.read();

        if (c == '\r' || c == '\n')
        {
            if (hostLineLength > 0)
            {
                hostLine[hostLineLength] = '\0';
                char commandToProcess[sizeof(hostLine)];
                copyPendingCommand(commandToProcess, hostLine);
                hostLineLength = 0;
                processHostCommand(commandToProcess);
            }
        }
        else if (hostLineLength < sizeof(hostLine) - 1)
        {
            hostLine[hostLineLength++] = c;
        }
        else
        {
            hostLineLength = 0;
            Serial.println(F("EVENT ERROR COMMAND_TOO_LONG"));
        }
    }
}

void setup()
{
    stopContinuousVibration();

    Serial.begin(115200);
    lraSerial.begin(9600);

    Serial.println(F("LRA controller started"));
    Serial.println(F("Waiting for the LRA module to start..."));

    // Opening a USB serial port resets many Uno/Nano boards. Give both the
    // Arduino bootloader and the independently powered LRA module time to settle.
    delay(2500);

    if (configureLRA())
    {
        Serial.println(F("\nLRA configured successfully"));
        Serial.println(F("Commands: START AMP FREQ, HOLD AMP FREQ, STOP"));
        Serial.println(F("Editable parameters: PARAM 20..500 25..47"));
        Serial.println(F("Effects: SHORT, LONG, SAW, FRICTION, COLLISION"));
        Serial.println(F("Also available: G, L, C"));
    }
    else
    {
        Serial.println(F("\nLRA configuration failed; check TX/RX and common GND"));
        Serial.println(F("Required: LRA TX -> D10, LRA RX <- D11, GND -> GND"));
        Serial.println(F("Do not use Arduino D0/D1; they are reserved for USB serial"));
    }
}

void loop()
{
    checkHoldWatchdog();
    checkF0DetectionTimeout();
    readHostCommands();
    updateEffect();
    readLraAsyncOutput();
}
