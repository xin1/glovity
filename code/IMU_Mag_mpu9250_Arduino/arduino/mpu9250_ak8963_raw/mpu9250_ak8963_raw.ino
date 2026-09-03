#include <Wire.h>
#include "mag_calibration.h"

// MPU-6500 + AK8963 / MPU-9250 magnetometer logger.
// Serial data format: X,Y,Z (no header), in factory-adjusted LSB.

constexpr uint8_t MPU_ADDR_LOW = 0x68;
constexpr uint8_t MPU_ADDR_HIGH = 0x69;
constexpr uint8_t AK8963_ADDR = 0x0C;

constexpr uint8_t MPU_WHO_AM_I = 0x75;
constexpr uint8_t MPU_PWR_MGMT_1 = 0x6B;
constexpr uint8_t MPU_USER_CTRL = 0x6A;
constexpr uint8_t MPU_INT_PIN_CFG = 0x37;

constexpr uint8_t AK8963_WIA = 0x00;
constexpr uint8_t AK8963_ST1 = 0x02;
constexpr uint8_t AK8963_HXL = 0x03;
constexpr uint8_t AK8963_ST2 = 0x09;
constexpr uint8_t AK8963_CNTL1 = 0x0A;
constexpr uint8_t AK8963_ASAX = 0x10;

uint8_t mpuAddress = MPU_ADDR_LOW;
float factoryAdjustment[3] = {1.0f, 1.0f, 1.0f};

// true: output calibrated data for normal use.
// false: output only factory-adjusted raw data for a future recalibration.
constexpr bool APPLY_MAG_CALIBRATION = false;

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t *buffer, uint8_t count) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(address, count);
  if (received != count) {
    while (Wire.available()) {
      Wire.read();
    }
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
  uint8_t whoAmI = 0;
  const uint8_t candidates[] = {MPU_ADDR_LOW, MPU_ADDR_HIGH};

  for (uint8_t candidate : candidates) {
    if (readRegister(candidate, MPU_WHO_AM_I, whoAmI)) {
      Serial.print(F("# MPU WHO_AM_I at 0x"));
      Serial.print(candidate, HEX);
      Serial.print(F(" = 0x"));
      Serial.println(whoAmI, HEX);

      // 0x70: MPU-6500, 0x71: MPU-9250, 0x73: MPU-9255.
      if (whoAmI == 0x70 || whoAmI == 0x71 || whoAmI == 0x73) {
        mpuAddress = candidate;
        return true;
      }
    }
  }
  return false;
}

bool initializeAk8963() {
  // Wake MPU-9250, disable its internal I2C master, and expose AK8963 to the
  // Arduino's I2C bus using bypass mode.
  if (!writeRegister(mpuAddress, MPU_PWR_MGMT_1, 0x00)) return false;
  delay(100);
  if (!writeRegister(mpuAddress, MPU_USER_CTRL, 0x00)) return false;
  if (!writeRegister(mpuAddress, MPU_INT_PIN_CFG, 0x02)) return false;
  delay(10);

  uint8_t whoAmI = 0;
  if (!readRegister(AK8963_ADDR, AK8963_WIA, whoAmI)) return false;
  Serial.print(F("# AK8963 WIA = 0x"));
  Serial.println(whoAmI, HEX);
  if (whoAmI != 0x48) return false;

  // Read the factory sensitivity values from Fuse ROM access mode.
  writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x00);
  delay(10);
  writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x0F);
  delay(10);

  uint8_t asa[3];
  if (!readRegisters(AK8963_ADDR, AK8963_ASAX, asa, 3)) return false;
  for (uint8_t i = 0; i < 3; ++i) {
    factoryAdjustment[i] = ((static_cast<float>(asa[i]) - 128.0f) / 256.0f) + 1.0f;
  }

  writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x00);
  delay(10);
  // 0x16: 16-bit output, continuous measurement mode 2 (100 Hz).
  if (!writeRegister(AK8963_ADDR, AK8963_CNTL1, 0x16)) return false;
  delay(10);

  Serial.print(F("# Factory adjustment = "));
  Serial.print(factoryAdjustment[0], 6);
  Serial.print(',');
  Serial.print(factoryAdjustment[1], 6);
  Serial.print(',');
  Serial.println(factoryAdjustment[2], 6);
  return true;
}

bool readMagnetometer(float &x, float &y, float &z) {
  uint8_t st1 = 0;
  if (!readRegister(AK8963_ADDR, AK8963_ST1, st1)) return false;
  if ((st1 & 0x01) == 0) return false;  // No new sample.

  // Reading through ST2 is required to release the AK8963 data registers.
  uint8_t data[7];
  if (!readRegisters(AK8963_ADDR, AK8963_HXL, data, 7)) return false;
  if (data[6] & 0x08) return false;  // Magnetic sensor overflow.

  const int16_t rawX = static_cast<int16_t>((data[1] << 8) | data[0]);
  const int16_t rawY = static_cast<int16_t>((data[3] << 8) | data[2]);
  const int16_t rawZ = static_cast<int16_t>((data[5] << 8) | data[4]);

  x = rawX * factoryAdjustment[0];
  y = rawY * factoryAdjustment[1];
  z = rawZ * factoryAdjustment[2];
  return true;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  // Start conservatively; 100 kHz is more tolerant of jumper wires and
  // modules without strong I2C pull-ups.
  Wire.setClock(100000UL);
  delay(200);

  Serial.println(F("# MPU6500/MPU9250 + AK8963 magnetometer logger"));
  if (!findMpu()) {
    Serial.println(F("# ERROR: MPU not found (expected WHO_AM_I 0x70/0x71/0x73)."));
    while (true) delay(1000);
  }
  if (!initializeAk8963()) {
    Serial.println(F("# ERROR: AK8963 not found (expected WIA 0x48)."));
    Serial.println(F("# Check AK8963 power, address 0x0C, and I2C/bypass wiring."));
    while (true) delay(1000);
  }
  if (APPLY_MAG_CALIBRATION) {
    Serial.println(F("# Output mode: calibrated, factory-adjusted LSB"));
  } else {
    Serial.println(F("# Output mode: raw for calibration; save rows to magnetometer_data.csv"));
  }
}

void loop() {
  float rawX, rawY, rawZ;
  if (readMagnetometer(rawX, rawY, rawZ)) {
    float magX = rawX;
    float magY = rawY;
    float magZ = rawZ;

    if (APPLY_MAG_CALIBRATION) {
      applyMagCalibration(rawX, rawY, rawZ, magX, magY, magZ);
    }

    Serial.print(magX, 3);
    Serial.print(',');
    Serial.print(magY, 3);
    Serial.print(',');
    Serial.println(magZ, 3);
  }
}
