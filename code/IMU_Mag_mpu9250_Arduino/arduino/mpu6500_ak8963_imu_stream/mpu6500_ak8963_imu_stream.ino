#include <Wire.h>
#include "mag_calibration.h"

// MPU-6500 + AK8963 / MPU-9250 nine-axis data stream.
//
// Serial format (no CSV header):
// micros,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,mx_cal,my_cal,mz_cal
//
// MPU-6500 configuration:
//   Accelerometer: +/-4 g, 8192 LSB/g
//   Gyroscope:     +/-2000 dps, 16.4 LSB/(degree/s)
//   Output rate:   approximately 100 Hz
//   Hardware DLPF: approximately 41 Hz
//
// AK8963 output has both its factory sensitivity adjustment and the measured
// hard/soft-iron calibration applied. Magnetic output is still in adjusted
// counts; multiply by 0.15 to obtain nominal microtesla in 16-bit mode.

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t MPU_ADDR_LOW = 0x68;
constexpr uint8_t MPU_ADDR_HIGH = 0x69;
constexpr uint8_t AK8963_ADDR = 0x0C;

constexpr uint8_t MPU_SMPLRT_DIV = 0x19;
constexpr uint8_t MPU_CONFIG = 0x1A;
constexpr uint8_t MPU_GYRO_CONFIG = 0x1B;
constexpr uint8_t MPU_ACCEL_CONFIG = 0x1C;
constexpr uint8_t MPU_ACCEL_CONFIG2 = 0x1D;
constexpr uint8_t MPU_INT_PIN_CFG = 0x37;
constexpr uint8_t MPU_ACCEL_XOUT_H = 0x3B;
constexpr uint8_t MPU_USER_CTRL = 0x6A;
constexpr uint8_t MPU_PWR_MGMT_1 = 0x6B;
constexpr uint8_t MPU_PWR_MGMT_2 = 0x6C;
constexpr uint8_t MPU_WHO_AM_I = 0x75;

constexpr uint8_t AK8963_WIA = 0x00;
constexpr uint8_t AK8963_ST1 = 0x02;
constexpr uint8_t AK8963_HXL = 0x03;
constexpr uint8_t AK8963_CNTL1 = 0x0A;
constexpr uint8_t AK8963_ASAX = 0x10;

uint8_t mpuAddress = MPU_ADDR_LOW;
float factoryAdjustment[3] = {1.0f, 1.0f, 1.0f};

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
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  const uint8_t received = Wire.requestFrom(address, count);
  if (received != count) {
    while (Wire.available()) Wire.read();
    return false;
  }

  for (uint8_t i = 0; i < count; ++i) {
    buffer[i] = Wire.read();
  }
  return true;
}

bool readRegister(uint8_t address, uint8_t reg, uint8_t &value) {
  return readRegisters(address, reg, &value, 1);
}

bool findMpu() {
  const uint8_t candidates[] = {MPU_ADDR_LOW, MPU_ADDR_HIGH};
  for (uint8_t candidate : candidates) {
    uint8_t whoAmI = 0;
    if (!readRegister(candidate, MPU_WHO_AM_I, whoAmI)) continue;

    Serial.print(F("# MPU WHO_AM_I at 0x"));
    Serial.print(candidate, HEX);
    Serial.print(F(" = 0x"));
    Serial.println(whoAmI, HEX);

    if (whoAmI == 0x70 || whoAmI == 0x71 || whoAmI == 0x73) {
      mpuAddress = candidate;
      return true;
    }
  }
  return false;
}

bool initializeMpuSensors() {
  // Device reset, then select the PLL clock and enable all accel/gyro axes.
  if (!writeRegister(mpuAddress, MPU_PWR_MGMT_1, 0x80)) return false;
  delay(100);
  if (!writeRegister(mpuAddress, MPU_PWR_MGMT_1, 0x01)) return false;
  if (!writeRegister(mpuAddress, MPU_PWR_MGMT_2, 0x00)) return false;
  delay(10);

  // With the gyro DLPF enabled, sample rate is 1 kHz / (1 + divider).
  if (!writeRegister(mpuAddress, MPU_SMPLRT_DIV, 9)) return false;  // 100 Hz
  if (!writeRegister(mpuAddress, MPU_CONFIG, 0x03)) return false;  // ~41 Hz
  if (!writeRegister(mpuAddress, MPU_GYRO_CONFIG, 0x18)) return false;  // +/-2000 dps
  if (!writeRegister(mpuAddress, MPU_ACCEL_CONFIG, 0x08)) return false;  // +/-4 g
  if (!writeRegister(mpuAddress, MPU_ACCEL_CONFIG2, 0x03)) return false;  // ~41 Hz
  return true;
}

bool initializeAk8963() {
  // Disable the MPU internal I2C master and expose the AK8963 through bypass.
  if (!writeRegister(mpuAddress, MPU_USER_CTRL, 0x00)) return false;
  if (!writeRegister(mpuAddress, MPU_INT_PIN_CFG, 0x02)) return false;
  delay(10);

  uint8_t whoAmI = 0;
  if (!readRegister(AK8963_ADDR, AK8963_WIA, whoAmI)) return false;
  Serial.print(F("# AK8963 WIA = 0x"));
  Serial.println(whoAmI, HEX);
  if (whoAmI != 0x48) return false;

  // Power-down -> Fuse ROM access -> read ASA -> power-down -> 16-bit 100 Hz.
  if (!writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x00)) return false;
  delay(10);
  if (!writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x0F)) return false;
  delay(10);

  uint8_t asa[3];
  if (!readRegisters(AK8963_ADDR, AK8963_ASAX, asa, 3)) return false;
  for (uint8_t i = 0; i < 3; ++i) {
    factoryAdjustment[i] =
        ((static_cast<float>(asa[i]) - 128.0f) / 256.0f) + 1.0f;
  }

  if (!writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x00)) return false;
  delay(10);
  if (!writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x16)) return false;
  delay(10);
  return true;
}

int16_t decodeBigEndianInt16(const uint8_t *bytes) {
  const uint16_t value =
      (static_cast<uint16_t>(bytes[0]) << 8) | bytes[1];
  return static_cast<int16_t>(value);
}

bool readAccelGyro(int16_t &ax, int16_t &ay, int16_t &az,
                   int16_t &gx, int16_t &gy, int16_t &gz) {
  uint8_t data[14];
  if (!readRegisters(mpuAddress, MPU_ACCEL_XOUT_H, data, 14)) return false;

  ax = decodeBigEndianInt16(&data[0]);
  ay = decodeBigEndianInt16(&data[2]);
  az = decodeBigEndianInt16(&data[4]);
  // data[6:7] is temperature.
  gx = decodeBigEndianInt16(&data[8]);
  gy = decodeBigEndianInt16(&data[10]);
  gz = decodeBigEndianInt16(&data[12]);
  return true;
}

bool readCalibratedMag(float &mx, float &my, float &mz) {
  uint8_t st1 = 0;
  if (!readRegister(AK8963_ADDR, AK8963_ST1, st1)) return false;
  if ((st1 & 0x01) == 0) return false;

  // HXL..HZH plus ST2. Reading ST2 releases the data registers.
  uint8_t data[7];
  if (!readRegisters(AK8963_ADDR, AK8963_HXL, data, 7)) return false;
  if (data[6] & 0x08) return false;  // Magnetic overflow.

  const int16_t rawX =
      static_cast<int16_t>((static_cast<uint16_t>(data[1]) << 8) | data[0]);
  const int16_t rawY =
      static_cast<int16_t>((static_cast<uint16_t>(data[3]) << 8) | data[2]);
  const int16_t rawZ =
      static_cast<int16_t>((static_cast<uint16_t>(data[5]) << 8) | data[4]);

  const float adjustedX = rawX * factoryAdjustment[0];
  const float adjustedY = rawY * factoryAdjustment[1];
  const float adjustedZ = rawZ * factoryAdjustment[2];

  // Full 3x3 hard/soft-iron correction generated from the calibration data.
  applyMagCalibration(adjustedX, adjustedY, adjustedZ, mx, my, mz);
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  Wire.setClock(100000UL);
  delay(200);

  Serial.println(F("# MPU6500/MPU9250 + AK8963 nine-axis stream"));
  if (!findMpu()) {
    Serial.println(F("# ERROR: MPU not found (WHO_AM_I 0x70/0x71/0x73)"));
    while (true) delay(1000);
  }
  if (!initializeMpuSensors()) {
    Serial.println(F("# ERROR: MPU accel/gyro initialization failed"));
    while (true) delay(1000);
  }
  if (!initializeAk8963()) {
    Serial.println(F("# ERROR: AK8963 initialization failed (WIA 0x48)"));
    while (true) delay(1000);
  }

  Serial.println(F("# accel +/-4g; gyro +/-2000dps; rate ~100Hz"));
  Serial.println(F("# columns: us,ax,ay,az,gx,gy,gz,mx,my,mz"));
  Serial.println(F("# READY"));
}

void loop() {
  float mx, my, mz;
  if (!readCalibratedMag(mx, my, mz)) return;

  int16_t ax, ay, az, gx, gy, gz;
  if (!readAccelGyro(ax, ay, az, gx, gy, gz)) return;

  const uint32_t timestampUs = micros();
  Serial.print(timestampUs);
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
