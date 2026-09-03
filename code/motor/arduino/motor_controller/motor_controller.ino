// Arduino UNO vibration motor controller
// Module wiring: IN -> D9, VCC -> 5V, GND -> GND

const byte MOTOR_PIN = 9;  // PWM-capable pin on Arduino UNO
const unsigned long SERIAL_BAUD = 115200;
const unsigned long COMMAND_TIMEOUT_MS = 3000;

char inputBuffer[32];
byte inputLength = 0;
unsigned long lastCommandAt = 0;
byte currentPwm = 0;

void setMotor(byte pwm) {
  currentPwm = pwm;
  analogWrite(MOTOR_PIN, currentPwm);
}

void replyPwm() {
  Serial.print(F("OK:"));
  Serial.println(currentPwm);
}

void handleCommand(char *command) {
  // The desktop UI sends SET:<0-255>, STOP, or PING.
  if (strncmp(command, "SET:", 4) == 0) {
    char *endPointer;
    long value = strtol(command + 4, &endPointer, 10);

    if (*endPointer == '\0' && value >= 0 && value <= 255) {
      setMotor((byte)value);
      lastCommandAt = millis();
      replyPwm();
    } else {
      Serial.println(F("ERR:RANGE"));
    }
    return;
  }

  if (strcmp(command, "STOP") == 0) {
    setMotor(0);
    lastCommandAt = millis();
    replyPwm();
    return;
  }

  if (strcmp(command, "PING") == 0) {
    lastCommandAt = millis();
    Serial.println(F("PONG"));
    return;
  }

  Serial.println(F("ERR:COMMAND"));
}

void setup() {
  pinMode(MOTOR_PIN, OUTPUT);
  setMotor(0);
  Serial.begin(SERIAL_BAUD);
  lastCommandAt = millis();
  Serial.println(F("READY:VIBRATION_MOTOR"));
}

void loop() {
  while (Serial.available() > 0) {
    char incoming = (char)Serial.read();

    if (incoming == '\n' || incoming == '\r') {
      if (inputLength > 0) {
        inputBuffer[inputLength] = '\0';
        handleCommand(inputBuffer);
        inputLength = 0;
      }
    } else if (inputLength < sizeof(inputBuffer) - 1) {
      inputBuffer[inputLength++] = incoming;
    } else {
      inputLength = 0;
      Serial.println(F("ERR:TOO_LONG"));
    }
  }

  // Fail safe: stop the motor if the UI disappears or the USB cable is lost.
  if (currentPwm > 0 && millis() - lastCommandAt > COMMAND_TIMEOUT_MS) {
    setMotor(0);
    Serial.println(F("TIMEOUT:STOPPED"));
  }
}
