#include <Wire.h>
#include "mag_calibration.h"

// ICM-20948 + internal AK09916 nine-axis data stream.
//
// Serial format (no CSV header):
// micros,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,mx_cal,my_cal,mz_cal
//
// Configuration:
//   Accelerometer: +/-4 g, 8192 LSB/g, DLPF 50.4 Hz
//   Gyroscope:     +/-2000 dps, 16.4 LSB/(degree/s), DLPF 51.2 Hz
//   Output rate:   approximately 100 Hz
//   Magnetometer:  AK09916 continuous 100 Hz, 0.15 uT/LSB nominal

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t ICM_ADDR_LOW = 0x68;
constexpr uint8_t ICM_ADDR_HIGH = 0x69;
constexpr uint8_t AK09916_ADDR = 0x0C;

constexpr uint8_t ICM_BANK_0 = 0;
constexpr uint8_t ICM_BANK_2 = 2;
constexpr uint8_t ICM_REG_BANK_SEL = 0x7F;

// User bank 0.
constexpr uint8_t ICM_WHO_AM_I = 0x00;
constexpr uint8_t ICM_USER_CTRL = 0x03;
constexpr uint8_t ICM_PWR_MGMT_1 = 0x06;
constexpr uint8_t ICM_PWR_MGMT_2 = 0x07;
constexpr uint8_t ICM_INT_PIN_CFG = 0x0F;
constexpr uint8_t ICM_ACCEL_XOUT_H = 0x2D;
constexpr uint8_t ICM_WHO_AM_I_VALUE = 0xEA;

// User bank 2.
constexpr uint8_t ICM_GYRO_SMPLRT_DIV = 0x00;
constexpr uint8_t ICM_GYRO_CONFIG_1 = 0x01;
constexpr uint8_t ICM_ACCEL_SMPLRT_DIV_1 = 0x10;
constexpr uint8_t ICM_ACCEL_SMPLRT_DIV_2 = 0x11;
constexpr uint8_t ICM_ACCEL_CONFIG = 0x14;

// AK09916 registers.
constexpr uint8_t AK09916_WIA2 = 0x01;
constexpr uint8_t AK09916_ST1 = 0x10;
constexpr uint8_t AK09916_HXL = 0x11;
constexpr uint8_t AK09916_CNTL2 = 0x31;
constexpr uint8_t AK09916_CNTL3 = 0x32;
constexpr uint8_t AK09916_WIA2_VALUE = 0x09;
constexpr uint8_t AK09916_MODE_CONTINUOUS_100_HZ = 0x08;

uint8_t icmAddress = ICM_ADDR_LOW;

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t *buffer,
                   uint8_t count) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;

  const uint8_t received = Wire.requestFrom(address, count);
  if (received != count) {
    while (Wire.available()) Wire.read();
    return false;
  }

  for (uint8_t i = 0; i < count; ++i) buffer[i] = Wire.read();
  return true;
}

bool readRegister(uint8_t address, uint8_t reg, uint8_t &value) {
  return readRegisters(address, reg, &value, 1);
}

bool selectIcmBank(uint8_t bank) {
  return writeRegister(icmAddress, ICM_REG_BANK_SEL,
                       static_cast<uint8_t>((bank & 0x03) << 4));
}

bool writeIcmRegister(uint8_t bank, uint8_t reg, uint8_t value) {
  return selectIcmBank(bank) && writeRegister(icmAddress, reg, value);
}

bool readIcmRegisters(uint8_t bank, uint8_t reg, uint8_t *buffer,
                      uint8_t count) {
  return selectIcmBank(bank) && readRegisters(icmAddress, reg, buffer, count);
}

bool readIcmRegister(uint8_t bank, uint8_t reg, uint8_t &value) {
  return readIcmRegisters(bank, reg, &value, 1);
}

bool findIcm20948() {
  const uint8_t candidates[] = {ICM_ADDR_LOW, ICM_ADDR_HIGH};
  for (uint8_t candidate : candidates) {
    icmAddress = candidate;
    uint8_t whoAmI = 0;
    if (!readIcmRegister(ICM_BANK_0, ICM_WHO_AM_I, whoAmI)) continue;

    Serial.print(F("# ICM-20948 WHO_AM_I at 0x"));
    Serial.print(candidate, HEX);
    Serial.print(F(" = 0x"));
    Serial.println(whoAmI, HEX);
    if (whoAmI == ICM_WHO_AM_I_VALUE) return true;
  }
  return false;
}

bool initializeIcmSensors() {
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_1, 0x80)) return false;
  delay(100);
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_1, 0x01)) return false;
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_2, 0x00)) return false;
  delay(10);

  // 1125 / (1 + 10) = 102.27 Hz. This closely matches the AK09916's
  // 100 Hz continuous mode while retaining the requested sensor ranges.
  if (!writeIcmRegister(ICM_BANK_2, ICM_GYRO_SMPLRT_DIV, 10)) return false;

  // DLPFCFG=3, FS_SEL=3 (+/-2000 dps), FCHOICE=1 (DLPF enabled).
  if (!writeIcmRegister(ICM_BANK_2, ICM_GYRO_CONFIG_1, 0x1F)) return false;

  // ACCEL_SMPLRT_DIV is 12-bit: high nibble then low byte.
  if (!writeIcmRegister(ICM_BANK_2, ICM_ACCEL_SMPLRT_DIV_1, 0x00)) return false;
  if (!writeIcmRegister(ICM_BANK_2, ICM_ACCEL_SMPLRT_DIV_2, 10)) return false;

  // DLPFCFG=3, FS_SEL=1 (+/-4 g), FCHOICE=1 (DLPF enabled).
  if (!writeIcmRegister(ICM_BANK_2, ICM_ACCEL_CONFIG, 0x1B)) return false;

  // Disable the internal I2C master and expose the AK09916 at address 0x0C.
  if (!writeIcmRegister(ICM_BANK_0, ICM_USER_CTRL, 0x00)) return false;
  if (!writeIcmRegister(ICM_BANK_0, ICM_INT_PIN_CFG, 0x02)) return false;
  delay(10);
  return true;
}

bool initializeAk09916() {
  uint8_t whoAmI = 0;
  if (!readRegister(AK09916_ADDR, AK09916_WIA2, whoAmI)) return false;
  Serial.print(F("# AK09916 WIA2 = 0x"));
  Serial.println(whoAmI, HEX);
  if (whoAmI != AK09916_WIA2_VALUE) return false;

  if (!writeRegister(AK09916_ADDR, AK09916_CNTL3, 0x01)) return false;
  delay(100);
  if (!writeRegister(AK09916_ADDR, AK09916_CNTL2, 0x00)) return false;
  delay(10);
  if (!writeRegister(AK09916_ADDR, AK09916_CNTL2,
                     AK09916_MODE_CONTINUOUS_100_HZ)) {
    return false;
  }
  delay(10);
  return true;
}

int16_t decodeBigEndianInt16(const uint8_t *bytes) {
  return static_cast<int16_t>((static_cast<uint16_t>(bytes[0]) << 8) |
                              bytes[1]);
}

bool readAccelGyro(int16_t &ax, int16_t &ay, int16_t &az,
                   int16_t &gx, int16_t &gy, int16_t &gz) {
  // Unlike MPU6500, ICM-20948 stores the six gyro bytes immediately after
  // the six accelerometer bytes. Temperature follows them.
  uint8_t data[12];
  if (!readIcmRegisters(ICM_BANK_0, ICM_ACCEL_XOUT_H, data, 12)) return false;

  ax = decodeBigEndianInt16(&data[0]);
  ay = decodeBigEndianInt16(&data[2]);
  az = decodeBigEndianInt16(&data[4]);
  gx = decodeBigEndianInt16(&data[6]);
  gy = decodeBigEndianInt16(&data[8]);
  gz = decodeBigEndianInt16(&data[10]);
  return true;
}

bool readCalibratedMag(float &mx, float &my, float &mz) {
  uint8_t st1 = 0;
  if (!readRegister(AK09916_ADDR, AK09916_ST1, st1)) return false;
  if ((st1 & 0x01) == 0) return false;

  // HXL..HZH, one reserved byte, then ST2. Reading ST2 is required.
  uint8_t data[8];
  if (!readRegisters(AK09916_ADDR, AK09916_HXL, data, 8)) return false;
  if (data[7] & 0x08) return false;

  const int16_t rawX =
      static_cast<int16_t>((static_cast<uint16_t>(data[1]) << 8) | data[0]);
  const int16_t rawY =
      static_cast<int16_t>((static_cast<uint16_t>(data[3]) << 8) | data[2]);
  const int16_t rawZ =
      static_cast<int16_t>((static_cast<uint16_t>(data[5]) << 8) | data[4]);

  applyMagCalibration(rawX, rawY, rawZ, mx, my, mz);
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  Wire.setClock(100000UL);
  delay(200);

  Serial.println(F("# ICM-20948 + AK09916 nine-axis stream"));
  if (!findIcm20948()) {
    Serial.println(F("# ERROR: ICM-20948 not found (expected WHO_AM_I 0xEA)"));
    Serial.println(F("# Check power, SDA/SCL, AD0, nCS, and logic voltage"));
    while (true) delay(1000);
  }
  if (!initializeIcmSensors()) {
    Serial.println(F("# ERROR: ICM-20948 sensor initialization failed"));
    while (true) delay(1000);
  }
  if (!initializeAk09916()) {
    Serial.println(F("# ERROR: AK09916 not found (expected WIA2 0x09)"));
    Serial.println(F("# The module must expose the internal auxiliary bus in bypass mode"));
    while (true) delay(1000);
  }

#ifdef MAG_CALIBRATION_IS_PLACEHOLDER
  Serial.println(F("# WARNING: identity mag calibration; replace header with a PASS result"));
#endif
  Serial.println(F("# accel +/-4g; gyro +/-2000dps; AK09916 100Hz"));
  Serial.println(F("# columns: us,ax,ay,az,gx,gy,gz,mx,my,mz"));
  Serial.println(F("# READY"));
}

void loop() {
  float mx, my, mz;
  if (!readCalibratedMag(mx, my, mz)) return;

  int16_t ax, ay, az, gx, gy, gz;
  if (!readAccelGyro(ax, ay, az, gx, gy, gz)) return;

  Serial.print(micros());
  Serial.print(',');
  Serial.print(ax);
  Serial.print(',');
  Serial.print(ay);
  Serial.print(',');
  Serial.print(az);
  Serial.print(',');
  Serial.print(gx);
  Serial.print(',');
  Serial.print(gy);
  Serial.print(',');
  Serial.print(gz);
  Serial.print(',');
  Serial.print(mx, 3);
  Serial.print(',');
  Serial.print(my, 3);
  Serial.print(',');
  Serial.println(mz, 3);
}
