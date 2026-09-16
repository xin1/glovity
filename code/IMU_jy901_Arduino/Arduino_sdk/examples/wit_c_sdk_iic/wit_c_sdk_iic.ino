#include <Wire.h>
#include <REG.h>
#include <wit_c_sdk.h>

/*
 * WitMotion SDK example for two JY901P sensors at 0x55 and 0x56.
 *
 * - Sensor output rate: 100 Hz, set with WitSetOutputRate().
 * - Arduino polling rate: 100 Hz.
 * - Serial printing rate: 5 Hz, so printing does not block sampling.
 * - Magnetometer calibration: WitStartMagCali()/WitStopMagCali().
 */

#define ACC_UPDATE        0x01
#define GYRO_UPDATE       0x02
#define ANGLE_UPDATE      0x04
#define MAG_UPDATE        0x08
#define READ_UPDATE       0x80
#define SENSOR_NUM        2
#define SAMPLE_INTERVAL_MS 10UL
#define PRINT_INTERVAL_MS  200UL
#define MAG_PROGRESS_REG   0x8F
#define MAG_QUALITY_REG    0xA7

static const uint8_t s_ucSensorAddr[SENSOR_NUM] = {0x55, 0x56};
static char s_cDataUpdate = 0;
static char s_cCmd = 0xff;
static uint8_t s_ucMagCalibrating = 0;

typedef struct
{
  float acc[3];
  float gyro[3];
  float angle[3];
  int16_t mag[3];
  uint8_t valid;
} SensorSnapshot;

static SensorSnapshot s_tSensorData[SENSOR_NUM];
static unsigned long s_ulLastSampleMs = 0;
static unsigned long s_ulLastPrintMs = 0;
static uint16_t s_usMagProgressRaw[SENSOR_NUM] = {0};
static uint16_t s_usMagQualityRaw[SENSOR_NUM] = {0};
static uint8_t s_ucMagProgressValid[SENSOR_NUM] = {0};
static uint8_t s_ucMagQualityValid[SENSOR_NUM] = {0};

static void CopeCmdData(unsigned char ucData);
static void CmdProcess(void);
static void ShowHelp(void);
static void Apply100Hz(void);
static void ReadSensor(uint8_t ucSensorIndex);
static void PrintSensor(uint8_t ucSensorIndex);
static void ReadMagCalibrationStatus(uint8_t ucSensorIndex);
static uint8_t MagProgressPercent(uint16_t usRawProgress,
                                  uint8_t ucQualityValid);
static uint8_t ReadPrivateRegister(uint8_t ucSensorAddr,
                                   uint8_t ucRegister,
                                   uint16_t *pusValue);
static void CopeSensorData(uint32_t uiReg, uint32_t uiRegNum);
static uint8_t WireAddress(uint8_t ucSdkAddr);
static int32_t IICreadBytes(uint8_t dev, uint8_t reg,
                            uint8_t *data, uint32_t length);
static int32_t IICwriteBytes(uint8_t dev, uint8_t reg,
                             uint8_t *data, uint32_t length);
static void Delayms(uint16_t ucMs);

void setup()
{
  Wire.begin();
  Wire.setClock(100000);
  Serial.begin(115200);

  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr[0]);
  WitI2cFuncRegister(IICwriteBytes, IICreadBytes);
  WitRegisterCallBack(CopeSensorData);
  WitDelayMsRegister(Delayms);

  Serial.print(F("\r\n******** WitMotion SDK: dual JY901P 100 Hz ********\r\n"));
  Apply100Hz();
  ShowHelp();
}

void loop()
{
  while(Serial.available())
  {
    CopeCmdData(Serial.read());
  }
  CmdProcess();

  const unsigned long ulNow = millis();

  if(ulNow - s_ulLastSampleMs >= SAMPLE_INTERVAL_MS)
  {
    s_ulLastSampleMs = ulNow;
    for(uint8_t i = 0; i < SENSOR_NUM; i++)
    {
      ReadSensor(i);
    }
  }

  if(ulNow - s_ulLastPrintMs >= PRINT_INTERVAL_MS)
  {
    s_ulLastPrintMs = ulNow;
    for(uint8_t i = 0; i < SENSOR_NUM; i++)
    {
      if(s_ucMagCalibrating)
      {
        ReadMagCalibrationStatus(i);
      }
      PrintSensor(i);
    }
  }
}

static void ReadSensor(uint8_t ucSensorIndex)
{
  WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr[ucSensorIndex]);
  s_cDataUpdate = 0;
  WitReadReg(AX, 12);

  const char cRequired = ACC_UPDATE | GYRO_UPDATE |
                         ANGLE_UPDATE | MAG_UPDATE;
  if((s_cDataUpdate & cRequired) != cRequired)
  {
    s_tSensorData[ucSensorIndex].valid = 0;
    return;
  }

  for(uint8_t i = 0; i < 3; i++)
  {
    s_tSensorData[ucSensorIndex].acc[i] =
        (int16_t)sReg[AX + i] / 32768.0f * 16.0f;
    s_tSensorData[ucSensorIndex].gyro[i] =
        (int16_t)sReg[GX + i] / 32768.0f * 2000.0f;
    s_tSensorData[ucSensorIndex].mag[i] = (int16_t)sReg[HX + i];
    s_tSensorData[ucSensorIndex].angle[i] =
        (int16_t)sReg[Roll + i] / 32768.0f * 180.0f;
  }
  s_tSensorData[ucSensorIndex].valid = 1;
}

static void PrintSensor(uint8_t ucSensorIndex)
{
  SensorSnapshot *pData = &s_tSensorData[ucSensorIndex];

  Serial.print(F("DATA,address=0x"));
  Serial.print(s_ucSensorAddr[ucSensorIndex], HEX);
  Serial.print(F(",ms="));
  Serial.print(millis());
  Serial.print(F(",cal="));
  Serial.print(s_ucMagCalibrating);

  if(s_ucMagCalibrating)
  {
    Serial.print(F(",mag_cal_progress="));
    if(s_ucMagProgressValid[ucSensorIndex])
    {
      if((s_usMagProgressRaw[ucSensorIndex] & 0x0F) == 0)
      {
        Serial.print(F("WAITING_OR_UNSUPPORTED"));
      }
      else
      {
        Serial.print(MagProgressPercent(
            s_usMagProgressRaw[ucSensorIndex],
            s_ucMagQualityValid[ucSensorIndex]));
        Serial.print('%');
      }
      Serial.print(F(",mag_cal_progress_raw=0x"));
      Serial.print(s_usMagProgressRaw[ucSensorIndex], HEX);
    }
    else
    {
      Serial.print(F("READ_FAILED"));
    }

    Serial.print(F(",mag_cal_quality="));
    if(s_ucMagQualityValid[ucSensorIndex])
    {
      Serial.print(s_usMagQualityRaw[ucSensorIndex] / 100.0f, 2);
      Serial.print('%');
    }
    else
    {
      Serial.print(F("WAITING"));
    }
  }

  if(!pData->valid)
  {
    Serial.println(F(",read=FAILED"));
    return;
  }

  Serial.print(F(",acc_g="));
  Serial.print(pData->acc[0], 3); Serial.print(',');
  Serial.print(pData->acc[1], 3); Serial.print(',');
  Serial.print(pData->acc[2], 3);

  Serial.print(F(",gyro_dps="));
  Serial.print(pData->gyro[0], 2); Serial.print(',');
  Serial.print(pData->gyro[1], 2); Serial.print(',');
  Serial.print(pData->gyro[2], 2);

  Serial.print(F(",mag_raw="));
  Serial.print(pData->mag[0]); Serial.print(',');
  Serial.print(pData->mag[1]); Serial.print(',');
  Serial.print(pData->mag[2]);

  Serial.print(F(",angle_deg="));
  Serial.print(pData->angle[0], 2); Serial.print(',');
  Serial.print(pData->angle[1], 2); Serial.print(',');
  Serial.println(pData->angle[2], 2);
}

static void Apply100Hz(void)
{
  Serial.println(F("Setting both sensors to 100 Hz:"));

  for(uint8_t i = 0; i < SENSOR_NUM; i++)
  {
    WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr[i]);
    int32_t iResult = WitSetOutputRate(RRATE_100HZ);

    s_cDataUpdate = 0;
    WitReadReg(RRATE, 1);
    const uint8_t ucVerified =
        ((s_cDataUpdate & READ_UPDATE) != 0) &&
        ((uint16_t)sReg[RRATE] == RRATE_100HZ);

    Serial.print(F("  0x"));
    Serial.print(s_ucSensorAddr[i], HEX);
    Serial.println((iResult == WIT_HAL_OK && ucVerified)
                       ? F(": 100 Hz verified")
                       : F(": FAILED or readback mismatch"));
  }

  Serial.println(F("I2C polling: 100 Hz; serial printing: 5 Hz."));
}

static void ReadMagCalibrationStatus(uint8_t ucSensorIndex)
{
  uint16_t usValue = 0;

  s_ucMagProgressValid[ucSensorIndex] = ReadPrivateRegister(
      s_ucSensorAddr[ucSensorIndex], MAG_PROGRESS_REG, &usValue);
  if(!s_ucMagProgressValid[ucSensorIndex])
  {
    return;
  }
  s_usMagProgressRaw[ucSensorIndex] = usValue;

  /* Quality calculation starts after the progress bit mask reaches 0x0F. */
  if((usValue & 0x0F) == 0x0F)
  {
    if(ReadPrivateRegister(s_ucSensorAddr[ucSensorIndex],
                           MAG_QUALITY_REG, &usValue) &&
       (usValue > 0))
    {
      s_usMagQualityRaw[ucSensorIndex] = usValue;
      s_ucMagQualityValid[ucSensorIndex] = 1;
    }
  }
}

static uint8_t MagProgressPercent(uint16_t usRawProgress,
                                  uint8_t ucQualityValid)
{
  if(ucQualityValid) return 100;

  switch(usRawProgress & 0x0F)
  {
    case 0x01: return 25;
    case 0x03: return 50;
    case 0x07: return 75;
    case 0x0F: return 90;
    default:   return 0;
  }
}

static uint8_t ReadPrivateRegister(uint8_t ucSensorAddr,
                                   uint8_t ucRegister,
                                   uint16_t *pusValue)
{
  uint8_t ucData[2];
  if(IICreadBytes(ucSensorAddr, ucRegister, ucData, sizeof(ucData)) != 1)
  {
    return 0;
  }

  *pusValue = (uint16_t)ucData[0] | ((uint16_t)ucData[1] << 8);
  return 1;
}

static void CopeCmdData(unsigned char ucData)
{
  /* Accept a command immediately; CR/LF and spaces are simply ignored. */
  switch(ucData)
  {
    case 'm':
    case 'e':
    case 'r':
    case 'h':
      s_cCmd = (char)ucData;
      break;
    default:
      break;
  }
}

static void CmdProcess(void)
{
  if(s_cCmd == 'm')
  {
    memset(s_usMagProgressRaw, 0, sizeof(s_usMagProgressRaw));
    memset(s_usMagQualityRaw, 0, sizeof(s_usMagQualityRaw));
    memset(s_ucMagProgressValid, 0, sizeof(s_ucMagProgressValid));
    memset(s_ucMagQualityValid, 0, sizeof(s_ucMagQualityValid));
    Serial.println(F("Start WitMotion magnetic calibration:"));
    for(uint8_t i = 0; i < SENSOR_NUM; i++)
    {
      WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr[i]);
      int32_t iResult = WitStartMagCali();
      delay(5);
      uint16_t usCalMode = 0;
      const uint8_t ucModeRead = ReadPrivateRegister(
          s_ucSensorAddr[i], CALSW, &usCalMode);
      Serial.print(F("  0x"));
      Serial.print(s_ucSensorAddr[i], HEX);
      if(iResult != WIT_HAL_OK)
      {
        Serial.println(F(": command FAILED"));
      }
      else if(!ucModeRead)
      {
        Serial.println(F(": command sent; CALSW read FAILED"));
      }
      else
      {
        Serial.print(F(": CALSW=0x"));
        Serial.print(usCalMode, HEX);
        Serial.println(((usCalMode & 0xFF) == CALMAGMM)
                           ? F(" (calibration active)")
                           : F(" (calibration NOT active)"));
      }
    }
    s_ucMagCalibrating = 1;
    Serial.println(F("Rotate both sensors together around all three axes."));
    Serial.println(F("Send e + CRLF after calibration."));
  }
  else if(s_cCmd == 'e')
  {
    Serial.println(F("Stop WitMotion magnetic calibration:"));
    for(uint8_t i = 0; i < SENSOR_NUM; i++)
    {
      WitInit(WIT_PROTOCOL_I2C, s_ucSensorAddr[i]);
      int32_t iResult = WitStopMagCali();
      Serial.print(F("  0x"));
      Serial.print(s_ucSensorAddr[i], HEX);
      Serial.println(iResult == WIT_HAL_OK ? F(": OK") : F(": FAILED"));
    }
    s_ucMagCalibrating = 0;
  }
  else if(s_cCmd == 'r')
  {
    Apply100Hz();
  }
  else if(s_cCmd == 'h')
  {
    ShowHelp();
  }
  else
  {
    return;
  }

  s_cCmd = 0xff;
}

static void ShowHelp(void)
{
  Serial.print(F("\r\n**************** WIT SDK HELP ****************\r\n"));
  Serial.print(F("Send m: start official mag calibration on both sensors.\r\n"));
  Serial.print(F("Send e: stop official mag calibration on both sensors.\r\n"));
  Serial.print(F("During calibration, progress and quality are read automatically.\r\n"));
  Serial.print(F("Send r: set and verify both sensors at 100 Hz.\r\n"));
  Serial.print(F("Send h: show help.\r\n"));
  Serial.print(F("Serial Monitor: 115200 baud; any line-ending setting.\r\n"));
  Serial.print(F("************************************************\r\n"));
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

static uint8_t WireAddress(uint8_t ucSdkAddr)
{
  /* Accept the original SDK's 8-bit callback address and a 7-bit address. */
  return (ucSdkAddr > 0x7F) ? (ucSdkAddr >> 1) : ucSdkAddr;
}

static int32_t IICreadBytes(uint8_t dev, uint8_t reg,
                            uint8_t *data, uint32_t length)
{
  dev = WireAddress(dev);
  Wire.beginTransmission(dev);
  Wire.write(reg);
  if(Wire.endTransmission(false) != 0) return 0;

  uint32_t uiReceived = Wire.requestFrom(dev, (uint8_t)length);
  if(uiReceived != length) return 0;

  for(uint32_t i = 0; i < length; i++)
  {
    data[i] = Wire.read();
  }
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

static void Delayms(uint16_t ucMs)
{
  delay(ucMs);
}
