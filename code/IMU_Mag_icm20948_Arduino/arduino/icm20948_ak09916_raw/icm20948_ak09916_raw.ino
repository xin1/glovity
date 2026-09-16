#include <Wire.h>

// ICM-20948 + internal AK09916 magnetometer logger.
//
// Serial format (no CSV header):
// mx_raw,my_raw,mz_raw
//
// The values are uncalibrated AK09916 counts. Feed them directly to
// mag_calibration/capture_magnetometer.py. Do not apply an old AK8963 or
// AK09916 calibration before collecting a new calibration data set.

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t ICM_ADDR_LOW = 0x68;
constexpr uint8_t ICM_ADDR_HIGH = 0x69;
constexpr uint8_t AK09916_ADDR = 0x0C;

constexpr uint8_t ICM_BANK_0 = 0;
constexpr uint8_t ICM_REG_BANK_SEL = 0x7F;
constexpr uint8_t ICM_WHO_AM_I = 0x00;
constexpr uint8_t ICM_USER_CTRL = 0x03;
constexpr uint8_t ICM_PWR_MGMT_1 = 0x06;
constexpr uint8_t ICM_PWR_MGMT_2 = 0x07;
constexpr uint8_t ICM_INT_PIN_CFG = 0x0F;
constexpr uint8_t ICM_WHO_AM_I_VALUE = 0xEA;

constexpr uint8_t AK09916_WIA2 = 0x01;
constexpr uint8_t AK09916_ST1 = 0x10;
constexpr uint8_t AK09916_HXL = 0x11;
constexpr uint8_t AK09916_ST2 = 0x18;
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

bool readIcmRegister(uint8_t bank, uint8_t reg, uint8_t &value) {
  return selectIcmBank(bank) && readRegister(icmAddress, reg, value);
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

bool initializeIcmBypass() {
  // Reset, wake the chip, select its best available clock, and enable all
  // accelerometer/gyroscope axes. The latter are not streamed by this sketch,
  // but keeping the device fully awake makes the later stream sketch behave
  // the same way.
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_1, 0x80)) return false;
  delay(100);
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_1, 0x01)) return false;
  if (!writeIcmRegister(ICM_BANK_0, ICM_PWR_MGMT_2, 0x00)) return false;
  delay(10);

  // Disable the ICM internal I2C master, then expose its auxiliary bus. This
  // makes the internal AK09916 available to Arduino Wire at address 0x0C.
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

  // Software reset, power-down, then continuous measurement mode 4 (100 Hz).
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

bool readMagnetometer(int16_t &mx, int16_t &my, int16_t &mz) {
  uint8_t st1 = 0;
  if (!readRegister(AK09916_ADDR, AK09916_ST1, st1)) return false;
  if ((st1 & 0x01) == 0) return false;  // No new sample.

  // HXL..HZH, one reserved byte, then ST2. Reading through ST2 releases the
  // magnetic data registers. ST2 bit 3 reports magnetic sensor overflow.
  uint8_t data[8];
  if (!readRegisters(AK09916_ADDR, AK09916_HXL, data, 8)) return false;
  if (data[7] & 0x08) return false;

  mx = static_cast<int16_t>((static_cast<uint16_t>(data[1]) << 8) | data[0]);
  my = static_cast<int16_t>((static_cast<uint16_t>(data[3]) << 8) | data[2]);
  mz = static_cast<int16_t>((static_cast<uint16_t>(data[5]) << 8) | data[4]);
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  // A conservative clock is reliable with jumper wires and inexpensive
  // breakout boards. It is still fast enough for the AK09916's 100 Hz ODR.
  Wire.setClock(100000UL);
  delay(200);

  Serial.println(F("# ICM-20948 + AK09916 raw magnetometer logger"));
  if (!findIcm20948()) {
    Serial.println(F("# ERROR: ICM-20948 not found (expected WHO_AM_I 0xEA)"));
    Serial.println(F("# Check power, SDA/SCL, AD0, nCS, and logic voltage"));
    while (true) delay(1000);
  }
  if (!initializeIcmBypass()) {
    Serial.println(F("# ERROR: ICM-20948 bypass initialization failed"));
    while (true) delay(1000);
  }
  if (!initializeAk09916()) {
    Serial.println(F("# ERROR: AK09916 not found (expected WIA2 0x09)"));
    Serial.println(F("# The module must expose the internal auxiliary bus in bypass mode"));
    while (true) delay(1000);
  }

  Serial.println(F("# AK09916 mode: raw counts, continuous 100 Hz"));
  Serial.println(F("# columns: mx,my,mz"));
  Serial.println(F("# READY"));
}

void loop() {
  int16_t mx, my, mz;
  if (!readMagnetometer(mx, my, mz)) return;

  Serial.print(mx);
  Serial.print(',');
  Serial.print(my);
  Serial.print(',');
  Serial.println(mz);
}
