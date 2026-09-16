#include <Wire.h>
#include <math.h>
#include <REG.h>
#include <wit_c_sdk.h>

/*
 * JY901P new-firmware acceptance test (one sensor, I2C).
 *
 * FORCE_SENSOR_ADDRESS:
 *   0x00 = scan and select the first JY901P-like I2C device.
 *   0x50/0x55/... = use a fixed 7-bit I2C address.
 */
#define FORCE_SENSOR_ADDRESS  0x00
#define SAMPLE_INTERVAL_MS    10UL
#define LIVE_INTERVAL_MS      200UL
#define CAL_STATUS_INTERVAL_MS 500UL
#define STATIC_TEST_MS         10000UL
#define POSE_TEST_MS           3000UL
#define MAG_SWEEP_TEST_MS      30000UL
#define MAG_PROGRESS_REG       0x8F
#define MAG_QUALITY_REG        0xA7

#define ACC_UPDATE    0x01
#define GYRO_UPDATE   0x02
#define ANGLE_UPDATE  0x04
#define MAG_UPDATE    0x08
#define READ_UPDATE   0x80

typedef struct
{
  float acc[3];
  float gyro[3];
  float angle[3];
  int16_t mag[3];
  uint8_t valid;
} SensorSample;

typedef struct
{
  uint32_t count;
  float mean;
  float m2;
  float minValue;
  float maxValue;
} RunningStat;

typedef struct
{
  uint8_t active;
  uint32_t startMs;
  uint32_t count;
  int16_t minValue[3];
  int16_t maxValue[3];
} MagSweep;

enum TestMode
{
  TEST_NONE,
  TEST_STATIC,
  TEST_ROLL_PITCH,
  TEST_YAW
};

static uint8_t s_ucSensorAddr = 0;
static char s_cDataUpdate = 0;
static char s_cCommand = 0xff;
static SensorSample s_tSample;
static unsigned long s_ulLastSampleMs = 0;
static unsigned long s_ulLastLiveMs = 0;
static unsigned long s_ulLastCalStatusMs = 0;

static TestMode s_eTestMode = TEST_NONE;
static unsigned long s_ulTestStartMs = 0;
static float s_fExpectedRoll = 0.0f;
static float s_fExpectedPitch = 0.0f;
static float s_fExpectedYawDelta = 0.0f;
static uint8_t s_ucCheckRoll = 1;
static float s_fYawReference = 0.0f;
static uint8_t s_ucYawReferenceValid = 0;
static RunningStat s_tRollStat, s_tPitchStat, s_tYawStat;
static RunningStat s_tAccNormStat, s_tGyroStat[3];
static float s_fLastYawUnwrapped = 0.0f;
static uint8_t s_ucYawUnwrapValid = 0;

static uint8_t s_ucMagCalibrating = 0;
static uint16_t s_usCalMode = 0;
static uint16_t s_usCalProgressRaw = 0;
static uint16_t s_usCalQualityRaw = 0;
static uint8_t s_ucCalModeValid = 0;
static uint8_t s_ucCalProgressValid = 0;
static uint8_t s_ucCalQualityValid = 0;
static MagSweep s_tCalibrationSweep;
static MagSweep s_tValidationSweep;

static void ShowHelp(void);
static void ProcessCommand(void);
static uint8_t FindSensor(void);
static uint8_t ProbeJY901P(uint8_t ucAddress);
static void Apply100Hz(void);
static void ReadSensor(void);
static void PrintLive(void);
static void StartStaticTest(void);
static void StartPoseTest(float fRoll, float fPitch);
static void StartYawTest(float fExpectedDelta);
static void UpdateActiveTest(void);
static void FinishActiveTest(void);
static void ResetAllStats(void);
static void ResetStat(RunningStat *pStat);
static void AddStat(RunningStat *pStat, float fValue);
static float StatStdDev(const RunningStat *pStat);
static float AngleDelta(float fAngle, float fReference);
static void StartMagCalibration(void);
static void StopMagCalibration(void);
static void ReadCalibrationStatus(void);
static uint8_t CalibrationProgressPercent(void);
static void ResetMagSweep(MagSweep *pSweep);
static void AddMagSweepSample(MagSweep *pSweep);
static void PrintMagSweepResult(const __FlashStringHelper *pName,
                                const MagSweep *pSweep);
static uint8_t ReadRegister16(uint8_t ucAddress, uint8_t ucRegister,
                              uint16_t *pusValue);
static uint8_t WireAddress(uint8_t ucSdkAddress);
static int32_t IICreadBytes(uint8_t dev, uint8_t reg,
                            uint8_t *data, uint32_t length);
static int32_t IICwriteBytes(uint8_t dev, uint8_t reg,
                             uint8_t *data, uint32_t length);
static void CopeSensorData(uint32_t uiReg, uint32_t uiRegNum);
static void Delayms(uint16_t ucMs);

void setup()
{
  Wire.begin();
  Wire.setClock(100000);
  Serial.begin(115200);

  Serial.println(F("\r\n******** JY901P NEW FIRMWARE VALIDATION ********"));

  WitInit(WIT_PROTOCOL_I2C, 0x50);
  WitI2cFuncRegister(IICwriteBytes, IICreadBytes);
  WitRegisterCallBack(CopeSensorData);
  WitDelayMsRegister(Delayms);

  delay(500);
  if(!FindSensor())
  {
    Serial.println(F("ERROR: no JY901P-like I2C device found."));
    Serial.println(F("Check VCC/GND/SDA/SCL and reset the board."));
    return;
  }

  Apply100Hz();
  ShowHelp();
}

void loop()
{
  while(Serial.available())
  {
    const char c = (char)Serial.read();
    switch(c)
    {
      case 'q': case '0': case '1': case '2': case '3': case '4':
      case 'z': case '5': case '6': case '7': case '8':
      case 'm': case 'e': case 'k': case 'r': case 'h': case 'x':
        s_cCommand = c;
        break;
      default:
        break;
    }
  }
  ProcessCommand();

  if(s_ucSensorAddr == 0) return;

  const unsigned long ulNow = millis();
  if(ulNow - s_ulLastSampleMs >= SAMPLE_INTERVAL_MS)
  {
    s_ulLastSampleMs = ulNow;
    ReadSensor();
    if(s_tSample.valid)
    {
      UpdateActiveTest();
      if(s_ucMagCalibrating) AddMagSweepSample(&s_tCalibrationSweep);
      if(s_tValidationSweep.active)
      {
        AddMagSweepSample(&s_tValidationSweep);
        if(ulNow - s_tValidationSweep.startMs >= MAG_SWEEP_TEST_MS)
        {
          s_tValidationSweep.active = 0;
          PrintMagSweepResult(F("POST_CAL"), &s_tValidationSweep);
        }
      }
    }
  }

  if(s_ucMagCalibrating &&
     ulNow - s_ulLastCalStatusMs >= CAL_STATUS_INTERVAL_MS)
  {
    s_ulLastCalStatusMs = ulNow;
    ReadCalibrationStatus();
  }

  if(ulNow - s_ulLastLiveMs >= LIVE_INTERVAL_MS)
  {
    s_ulLastLiveMs = ulNow;
    PrintLive();
  }
}

static uint8_t FindSensor(void)
{
#if FORCE_SENSOR_ADDRESS != 0x00
  if(!ProbeJY901P(FORCE_SENSOR_ADDRESS))
  {
    Serial.print(F("Configured address 0x"));
    Serial.print(FORCE_SENSOR_ADDRESS, HEX);
    Serial.println(F(" did not respond."));
    return 0;
  }
  s_ucSensorAddr = FORCE_SENSOR_ADDRESS;
#else
  Serial.println(F("Scanning I2C bus..."));
  for(uint8_t ucAddress = 0x08; ucAddress <= 0x77; ucAddress++)
  {
    if(ProbeJY901P(ucAddress))
    {
      Serial.print(F("  candidate found at 0x"));
      Serial.println(ucAddress, HEX);
      if(s_ucSensorAddr == 0) s_ucSensorAddr = ucAddress;
    }
  }
  if(s_ucSensorAddr == 0) return 0;
#endif

  Serial.print(F("Selected sensor address: 0x"));
  Serial.println(s_ucSensorAddr, HEX);
  return 1;
}

static uint8_t ProbeJY901P(uint8_t ucAddress)
{
  uint8_t ucData[24];
  return IICreadBytes(ucAddress, AX, ucData, sizeof(ucData)) == 1;
}

static void Apply100Hz(void)
{
  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr);
  const int32_t iResult = WitSetOutputRate(RRATE_100HZ);
  delay(5);

  uint16_t usRate = 0;
  const uint8_t ucReadOk = ReadRegister16(s_ucSensorAddr, RRATE, &usRate);
  Serial.print(F("100 Hz configuration: "));
  Serial.println((iResult == WIT_HAL_OK && ucReadOk &&
                  usRate == RRATE_100HZ)
                     ? F("VERIFIED")
                     : F("FAILED OR READBACK MISMATCH"));
}

static void ReadSensor(void)
{
  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr);
  s_cDataUpdate = 0;
  WitReadReg(AX, 12);

  const char cRequired = ACC_UPDATE | GYRO_UPDATE |
                         MAG_UPDATE | ANGLE_UPDATE;
  if((s_cDataUpdate & cRequired) != cRequired)
  {
    s_tSample.valid = 0;
    return;
  }

  for(uint8_t i = 0; i < 3; i++)
  {
    s_tSample.acc[i] = (int16_t)sReg[AX + i] / 32768.0f * 16.0f;
    s_tSample.gyro[i] = (int16_t)sReg[GX + i] / 32768.0f * 2000.0f;
    s_tSample.mag[i] = (int16_t)sReg[HX + i];
    s_tSample.angle[i] = (int16_t)sReg[Roll + i] / 32768.0f * 180.0f;
  }
  s_tSample.valid = 1;
}

static void PrintLive(void)
{
  Serial.print(F("LIVE,ms="));
  Serial.print(millis());
  Serial.print(F(",address=0x"));
  Serial.print(s_ucSensorAddr, HEX);

  if(!s_tSample.valid)
  {
    Serial.println(F(",read=FAILED"));
    return;
  }

  Serial.print(F(",rpy_deg="));
  Serial.print(s_tSample.angle[0], 2); Serial.print(',');
  Serial.print(s_tSample.angle[1], 2); Serial.print(',');
  Serial.print(s_tSample.angle[2], 2);
  Serial.print(F(",gyro_dps="));
  Serial.print(s_tSample.gyro[0], 2); Serial.print(',');
  Serial.print(s_tSample.gyro[1], 2); Serial.print(',');
  Serial.print(s_tSample.gyro[2], 2);
  Serial.print(F(",mag_raw="));
  Serial.print(s_tSample.mag[0]); Serial.print(',');
  Serial.print(s_tSample.mag[1]); Serial.print(',');
  Serial.print(s_tSample.mag[2]);

  if(s_ucMagCalibrating)
  {
    Serial.print(F(",cal_mode="));
    if(s_ucCalModeValid) Serial.print(s_usCalMode, HEX);
    else Serial.print(F("READ_FAILED"));

    Serial.print(F(",cal_progress="));
    if(!s_ucCalProgressValid || (s_usCalProgressRaw & 0x0F) == 0)
    {
      Serial.print(F("WAITING_OR_UNSUPPORTED"));
    }
    else
    {
      Serial.print(CalibrationProgressPercent());
      Serial.print('%');
    }
    Serial.print(F(",cal_progress_raw=0x"));
    Serial.print(s_usCalProgressRaw, HEX);

    Serial.print(F(",cal_quality="));
    if(s_ucCalQualityValid)
    {
      Serial.print(s_usCalQualityRaw / 100.0f, 2);
      Serial.print('%');
    }
    else Serial.print(F("WAITING_OR_UNSUPPORTED"));
  }
  Serial.println();
}

static void StartStaticTest(void)
{
  ResetAllStats();
  s_eTestMode = TEST_STATIC;
  s_ulTestStartMs = millis();
  Serial.println(F("TEST,STATIC,START,duration_s=10"));
}

static void StartPoseTest(float fRoll, float fPitch)
{
  ResetAllStats();
  s_fExpectedRoll = fRoll;
  s_fExpectedPitch = fPitch;
  /* Roll is not a useful Euler-angle criterion at pitch = +/-90 degrees. */
  s_ucCheckRoll = (fabsf(fPitch) < 89.0f);
  s_eTestMode = TEST_ROLL_PITCH;
  s_ulTestStartMs = millis();
  Serial.print(F("TEST,POSE,START,expected_roll="));
  Serial.print(fRoll, 1);
  Serial.print(F(",expected_pitch="));
  Serial.println(fPitch, 1);
}

static void StartYawTest(float fExpectedDelta)
{
  if(!s_ucYawReferenceValid)
  {
    Serial.println(F("ERROR: send z at the zero-heading mark first."));
    return;
  }
  ResetAllStats();
  s_fExpectedYawDelta = fExpectedDelta;
  s_eTestMode = TEST_YAW;
  s_ulTestStartMs = millis();
  Serial.print(F("TEST,YAW,START,expected_delta="));
  Serial.println(fExpectedDelta, 1);
}

static void UpdateActiveTest(void)
{
  if(s_eTestMode == TEST_NONE) return;

  if(s_eTestMode == TEST_STATIC)
  {
    AddStat(&s_tRollStat, s_tSample.angle[0]);
    AddStat(&s_tPitchStat, s_tSample.angle[1]);

    if(!s_ucYawUnwrapValid)
    {
      s_fLastYawUnwrapped = s_tSample.angle[2];
      s_ucYawUnwrapValid = 1;
    }
    else
    {
      s_fLastYawUnwrapped += AngleDelta(
          s_tSample.angle[2], s_fLastYawUnwrapped);
    }
    AddStat(&s_tYawStat, s_fLastYawUnwrapped);

    const float fAccNorm = sqrtf(s_tSample.acc[0] * s_tSample.acc[0] +
                                 s_tSample.acc[1] * s_tSample.acc[1] +
                                 s_tSample.acc[2] * s_tSample.acc[2]);
    AddStat(&s_tAccNormStat, fAccNorm);
    for(uint8_t i = 0; i < 3; i++) AddStat(&s_tGyroStat[i], s_tSample.gyro[i]);
  }
  else if(s_eTestMode == TEST_ROLL_PITCH)
  {
    AddStat(&s_tRollStat, s_tSample.angle[0]);
    AddStat(&s_tPitchStat, s_tSample.angle[1]);
  }
  else if(s_eTestMode == TEST_YAW)
  {
    const float fMeasuredDelta = AngleDelta(s_tSample.angle[2],
                                             s_fYawReference);
    const float fUnwrappedNearExpected = s_fExpectedYawDelta +
        AngleDelta(fMeasuredDelta, s_fExpectedYawDelta);
    AddStat(&s_tYawStat, fUnwrappedNearExpected);
  }

  const unsigned long ulDuration =
      (s_eTestMode == TEST_STATIC) ? STATIC_TEST_MS : POSE_TEST_MS;
  if(millis() - s_ulTestStartMs >= ulDuration) FinishActiveTest();
}

static void FinishActiveTest(void)
{
  if(s_eTestMode == TEST_STATIC)
  {
    Serial.print(F("RESULT,STATIC,samples=")); Serial.print(s_tRollStat.count);
    Serial.print(F(",roll_mean=")); Serial.print(s_tRollStat.mean, 3);
    Serial.print(F(",roll_std=")); Serial.print(StatStdDev(&s_tRollStat), 3);
    Serial.print(F(",pitch_mean=")); Serial.print(s_tPitchStat.mean, 3);
    Serial.print(F(",pitch_std=")); Serial.print(StatStdDev(&s_tPitchStat), 3);
    Serial.print(F(",yaw_std=")); Serial.print(StatStdDev(&s_tYawStat), 3);
    Serial.print(F(",yaw_p2p="));
    Serial.print(s_tYawStat.maxValue - s_tYawStat.minValue, 3);
    Serial.print(F(",acc_norm_mean=")); Serial.print(s_tAccNormStat.mean, 4);
    for(uint8_t i = 0; i < 3; i++)
    {
      Serial.print(F(",gyro")); Serial.print(i);
      Serial.print(F("_mean=")); Serial.print(s_tGyroStat[i].mean, 3);
      Serial.print(F(",gyro")); Serial.print(i);
      Serial.print(F("_std=")); Serial.print(StatStdDev(&s_tGyroStat[i]), 3);
    }
    Serial.println();
  }
  else if(s_eTestMode == TEST_ROLL_PITCH)
  {
    Serial.print(F("RESULT,POSE,samples=")); Serial.print(s_tRollStat.count);
    Serial.print(F(",expected_roll=")); Serial.print(s_fExpectedRoll, 2);
    Serial.print(F(",roll_mean=")); Serial.print(s_tRollStat.mean, 3);
    Serial.print(F(",roll_error="));
    if(s_ucCheckRoll)
      Serial.print(AngleDelta(s_tRollStat.mean, s_fExpectedRoll), 3);
    else
      Serial.print(F("NA_GIMBAL_LOCK"));
    Serial.print(F(",expected_pitch=")); Serial.print(s_fExpectedPitch, 2);
    Serial.print(F(",pitch_mean=")); Serial.print(s_tPitchStat.mean, 3);
    Serial.print(F(",pitch_error="));
    Serial.print(AngleDelta(s_tPitchStat.mean, s_fExpectedPitch), 3);
    Serial.print(F(",roll_std=")); Serial.print(StatStdDev(&s_tRollStat), 3);
    Serial.print(F(",pitch_std=")); Serial.println(StatStdDev(&s_tPitchStat), 3);
  }
  else if(s_eTestMode == TEST_YAW)
  {
    Serial.print(F("RESULT,YAW,samples=")); Serial.print(s_tYawStat.count);
    Serial.print(F(",expected_delta=")); Serial.print(s_fExpectedYawDelta, 2);
    Serial.print(F(",measured_delta=")); Serial.print(s_tYawStat.mean, 3);
    Serial.print(F(",error="));
    Serial.print(AngleDelta(s_tYawStat.mean, s_fExpectedYawDelta), 3);
    Serial.print(F(",std=")); Serial.println(StatStdDev(&s_tYawStat), 3);
  }
  s_eTestMode = TEST_NONE;
}

static void ResetAllStats(void)
{
  ResetStat(&s_tRollStat);
  ResetStat(&s_tPitchStat);
  ResetStat(&s_tYawStat);
  ResetStat(&s_tAccNormStat);
  for(uint8_t i = 0; i < 3; i++) ResetStat(&s_tGyroStat[i]);
  s_ucYawUnwrapValid = 0;
}

static void ResetStat(RunningStat *pStat)
{
  pStat->count = 0;
  pStat->mean = 0.0f;
  pStat->m2 = 0.0f;
  pStat->minValue = 0.0f;
  pStat->maxValue = 0.0f;
}

static void AddStat(RunningStat *pStat, float fValue)
{
  pStat->count++;
  if(pStat->count == 1)
  {
    pStat->mean = fValue;
    pStat->m2 = 0.0f;
    pStat->minValue = fValue;
    pStat->maxValue = fValue;
    return;
  }
  const float fDelta = fValue - pStat->mean;
  pStat->mean += fDelta / pStat->count;
  pStat->m2 += fDelta * (fValue - pStat->mean);
  if(fValue < pStat->minValue) pStat->minValue = fValue;
  if(fValue > pStat->maxValue) pStat->maxValue = fValue;
}

static float StatStdDev(const RunningStat *pStat)
{
  return (pStat->count > 1)
             ? sqrtf(pStat->m2 / (pStat->count - 1))
             : 0.0f;
}

static float AngleDelta(float fAngle, float fReference)
{
  float fDelta = fAngle - fReference;
  while(fDelta > 180.0f) fDelta -= 360.0f;
  while(fDelta < -180.0f) fDelta += 360.0f;
  return fDelta;
}

static void StartMagCalibration(void)
{
  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr);
  const int32_t iResult = WitStartMagCali();
  delay(5);

  s_ucMagCalibrating = 1;
  s_ucCalQualityValid = 0;
  s_usCalProgressRaw = 0;
  s_usCalQualityRaw = 0;
  ResetMagSweep(&s_tCalibrationSweep);
  s_tCalibrationSweep.active = 1;
  s_tCalibrationSweep.startMs = millis();
  ReadCalibrationStatus();

  Serial.print(F("CAL,START,command="));
  Serial.print(iResult == WIT_HAL_OK ? F("OK") : F("FAILED"));
  Serial.print(F(",CALSW="));
  if(s_ucCalModeValid) Serial.print(s_usCalMode, HEX);
  else Serial.print(F("READ_FAILED"));
  Serial.println();
  Serial.println(F("Rotate slowly around all three axes until coverage is complete."));
}

static void StopMagCalibration(void)
{
  if(!s_ucMagCalibrating)
  {
    Serial.println(F("CAL,STOP,ERROR=not_calibrating"));
    return;
  }
  ReadCalibrationStatus();
  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr);
  const int32_t iResult = WitStopMagCali();
  s_ucMagCalibrating = 0;
  s_tCalibrationSweep.active = 0;

  Serial.print(F("CAL,STOP,command="));
  Serial.print(iResult == WIT_HAL_OK ? F("OK") : F("FAILED"));
  Serial.print(F(",progress_raw=0x")); Serial.print(s_usCalProgressRaw, HEX);
  Serial.print(F(",quality="));
  if(s_ucCalQualityValid)
  {
    Serial.print(s_usCalQualityRaw / 100.0f, 2);
    Serial.print('%');
  }
  else Serial.print(F("UNAVAILABLE"));
  Serial.println();
  PrintMagSweepResult(F("CALIBRATION"), &s_tCalibrationSweep);
}

static void ReadCalibrationStatus(void)
{
  s_ucCalModeValid = ReadRegister16(s_ucSensorAddr, CALSW, &s_usCalMode);
  s_ucCalProgressValid = ReadRegister16(
      s_ucSensorAddr, MAG_PROGRESS_REG, &s_usCalProgressRaw);

  if(s_ucCalProgressValid && (s_usCalProgressRaw & 0x0F) == 0x0F)
  {
    uint16_t usQuality = 0;
    if(ReadRegister16(s_ucSensorAddr, MAG_QUALITY_REG, &usQuality) &&
       usQuality > 0)
    {
      s_usCalQualityRaw = usQuality;
      s_ucCalQualityValid = 1;
    }
  }
}

static uint8_t CalibrationProgressPercent(void)
{
  if(s_ucCalQualityValid) return 100;
  switch(s_usCalProgressRaw & 0x0F)
  {
    case 0x01: return 25;
    case 0x03: return 50;
    case 0x07: return 75;
    case 0x0F: return 90;
    default:   return 0;
  }
}

static void ResetMagSweep(MagSweep *pSweep)
{
  pSweep->active = 0;
  pSweep->startMs = 0;
  pSweep->count = 0;
  for(uint8_t i = 0; i < 3; i++)
  {
    pSweep->minValue[i] = 32767;
    pSweep->maxValue[i] = -32768;
  }
}

static void AddMagSweepSample(MagSweep *pSweep)
{
  pSweep->count++;
  for(uint8_t i = 0; i < 3; i++)
  {
    if(s_tSample.mag[i] < pSweep->minValue[i])
      pSweep->minValue[i] = s_tSample.mag[i];
    if(s_tSample.mag[i] > pSweep->maxValue[i])
      pSweep->maxValue[i] = s_tSample.mag[i];
  }
}

static void PrintMagSweepResult(const __FlashStringHelper *pName,
                                const MagSweep *pSweep)
{
  if(pSweep->count == 0)
  {
    Serial.println(F("RESULT,MAG_SWEEP,ERROR=no_samples"));
    return;
  }

  float fSpan[3];
  float fMinSpan = 65535.0f;
  float fMaxSpan = 0.0f;
  Serial.print(F("RESULT,MAG_SWEEP,name=")); Serial.print(pName);
  Serial.print(F(",samples=")); Serial.print(pSweep->count);
  for(uint8_t i = 0; i < 3; i++)
  {
    fSpan[i] = (float)pSweep->maxValue[i] - pSweep->minValue[i];
    if(fSpan[i] < fMinSpan) fMinSpan = fSpan[i];
    if(fSpan[i] > fMaxSpan) fMaxSpan = fSpan[i];
    Serial.print(F(",axis")); Serial.print(i);
    Serial.print(F("_min=")); Serial.print(pSweep->minValue[i]);
    Serial.print(F(",axis")); Serial.print(i);
    Serial.print(F("_max=")); Serial.print(pSweep->maxValue[i]);
    Serial.print(F(",axis")); Serial.print(i);
    Serial.print(F("_center="));
    Serial.print(((float)pSweep->maxValue[i] +
                  (float)pSweep->minValue[i]) / 2.0f, 1);
    Serial.print(F(",axis")); Serial.print(i);
    Serial.print(F("_span=")); Serial.print(fSpan[i], 1);
  }
  Serial.print(F(",span_balance="));
  Serial.print(fMaxSpan > 0.0f ? fMinSpan / fMaxSpan * 100.0f : 0.0f, 1);
  Serial.println('%');
}

static void ProcessCommand(void)
{
  if(s_cCommand == (char)0xff) return;
  const char c = s_cCommand;
  s_cCommand = (char)0xff;

  switch(c)
  {
    case 'q': StartStaticTest(); break;
    case '0': StartPoseTest(0.0f, 0.0f); break;
    case '1': StartPoseTest(90.0f, 0.0f); break;
    case '2': StartPoseTest(-90.0f, 0.0f); break;
    case '3': StartPoseTest(0.0f, 90.0f); break;
    case '4': StartPoseTest(0.0f, -90.0f); break;
    case 'z':
      if(s_tSample.valid)
      {
        s_fYawReference = s_tSample.angle[2];
        s_ucYawReferenceValid = 1;
        Serial.print(F("YAW_REFERENCE,set_deg="));
        Serial.println(s_fYawReference, 3);
      }
      else Serial.println(F("ERROR: no valid sample for yaw reference."));
      break;
    case '5': StartYawTest(90.0f); break;
    case '6': StartYawTest(180.0f); break;
    case '7': StartYawTest(-90.0f); break;
    case '8': StartYawTest(0.0f); break;
    case 'm': StartMagCalibration(); break;
    case 'e': StopMagCalibration(); break;
    case 'k':
      ResetMagSweep(&s_tValidationSweep);
      s_tValidationSweep.active = 1;
      s_tValidationSweep.startMs = millis();
      Serial.println(F("TEST,MAG_SWEEP,START,duration_s=30"));
      break;
    case 'r': Apply100Hz(); break;
    case 'h': ShowHelp(); break;
    case 'x':
      s_eTestMode = TEST_NONE;
      s_tValidationSweep.active = 0;
      Serial.println(F("TEST,CANCELLED"));
      break;
    default: break;
  }
}

static void ShowHelp(void)
{
  Serial.println(F("\r\nCommands (single character):"));
  Serial.println(F(" q : 10 s stationary noise/drift test"));
  Serial.println(F(" 0 : capture level pose, expected roll=0 pitch=0"));
  Serial.println(F(" 1 : capture pose, expected roll=+90 pitch=0"));
  Serial.println(F(" 2 : capture pose, expected roll=-90 pitch=0"));
  Serial.println(F(" 3 : capture pose, expected roll=0 pitch=+90"));
  Serial.println(F(" 4 : capture pose, expected roll=0 pitch=-90"));
  Serial.println(F(" z : set current yaw as 0-degree reference"));
  Serial.println(F(" 5/6/7/8 : test yaw delta +90/+180/-90/return-to-0"));
  Serial.println(F(" m/e : start/stop official WitMotion mag calibration"));
  Serial.println(F(" k : 30 s post-calibration 3-axis magnetic sweep"));
  Serial.println(F(" r : set and verify 100 Hz again"));
  Serial.println(F(" x : cancel current measurement test"));
  Serial.println(F(" h : show help"));
  Serial.println(F("Serial: 115200 baud; any line-ending setting."));
}

static uint8_t ReadRegister16(uint8_t ucAddress, uint8_t ucRegister,
                              uint16_t *pusValue)
{
  uint8_t ucData[2];
  if(IICreadBytes(ucAddress, ucRegister, ucData, sizeof(ucData)) != 1)
    return 0;
  *pusValue = (uint16_t)ucData[0] | ((uint16_t)ucData[1] << 8);
  return 1;
}

static uint8_t WireAddress(uint8_t ucSdkAddress)
{
  return (ucSdkAddress > 0x7F) ? (ucSdkAddress >> 1) : ucSdkAddress;
}

static int32_t IICreadBytes(uint8_t dev, uint8_t reg,
                            uint8_t *data, uint32_t length)
{
  dev = WireAddress(dev);
  Wire.beginTransmission(dev);
  Wire.write(reg);
  if(Wire.endTransmission(false) != 0) return 0;
  const uint32_t uiReceived = Wire.requestFrom(dev, (uint8_t)length);
  if(uiReceived != length) return 0;
  for(uint32_t i = 0; i < length; i++) data[i] = Wire.read();
  return 1;
}

static int32_t IICwriteBytes(uint8_t dev, uint8_t reg,
                             uint8_t *data, uint32_t length)
{
  dev = WireAddress(dev);
  Wire.beginTransmission(dev);
  Wire.write(reg);
  Wire.write(data, length);
  return Wire.endTransmission() == 0 ? 1 : 0;
}

static void CopeSensorData(uint32_t uiReg, uint32_t uiRegNum)
{
  for(uint32_t i = 0; i < uiRegNum; i++, uiReg++)
  {
    switch(uiReg)
    {
      case AZ:  s_cDataUpdate |= ACC_UPDATE; break;
      case GZ:  s_cDataUpdate |= GYRO_UPDATE; break;
      case HZ:  s_cDataUpdate |= MAG_UPDATE; break;
      case Yaw: s_cDataUpdate |= ANGLE_UPDATE; break;
      default:  s_cDataUpdate |= READ_UPDATE; break;
    }
  }
}

static void Delayms(uint16_t ucMs)
{
  delay(ucMs);
}
