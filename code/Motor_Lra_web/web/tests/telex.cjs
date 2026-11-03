const assert = require('node:assert/strict');
const fs = require('node:fs');

const path = require('node:path');
const app = fs.readFileSync(path.resolve(__dirname, '..', 'app.js'), 'utf8');
const firmware = fs.readFileSync(path.resolve(__dirname, '..', '..', 'legacy', 'arduino-firmware', 'telex_core_left_v2', 'telex_core_left_v2.ino'), 'utf8');

assert.match(app, /thumb: 0, index: 1, middle: 2, ring: 3, little: 4/);
assert.match(app, /queueLine\(`CHANNEL \$\{channel\}`\)/);
assert.match(app, /TELEX_CORE_LEFT_V2\|AW86234_C6_8CH/);
assert.match(app, /AW86234_C6_8CH/);
assert.match(app, /TELEX_SHORT_DURATION_MS = 18/);
assert.match(app, /return Math\.round\(18 .* 22/s);

assert.match(firmware, /PIN_HP_SDA = 6/);
assert.match(firmware, /PIN_HP_SCL = 7/);
assert.match(firmware, /PIN_TCA_RESET = 21/);
assert.match(firmware, /TCA_ADDRESS = 0x70/);
assert.match(firmware, /AW_ADDRESS = 0x5A/);
assert.match(firmware, /high == 0x23 && low == 0x40/);
assert.match(firmware, /REG_PLAYCFG3, 0x0A/);
assert.match(firmware, /REG_DRV_WIDTH, 80/);
assert.match(firmware, /return map\(clampAmp\(amp\), 25, 47, 18, 40\)/);
assert.match(firmware, /updateRegister\(REG_DRV1_LVL, 0x7F, level\)/);
assert.match(firmware, /writeRegister\(REG_BRK_TIME, 4\)/);
assert.match(firmware, /delay\(16\)/);
assert.match(firmware, /DEFAULT_OUTPUT_MASK = 0xF0/);
assert.match(firmware, /PIN_TCA_RESET = 23/);
assert.match(firmware, /EVENT BOARD AW86234_C6_8CH 8/);
assert.match(firmware, /line\.startsWith\("FOUR "\)/);
assert.match(firmware, /const uint8_t channel = i \+ 4/);
assert.match(firmware, /digitalWrite\(PIN_TCA_RESET, HIGH\);[\s\S]*beginHapticBus\(\)/);
assert.match(app, /queueLine\(`FOUR \$\{signature\}`\)/);
assert.match(app, /\['index', 'middle', 'ring', 'little'\]/);

console.log('PASS: TELEX five-channel routing, schematic pin map, device discovery and CONT-mode setup.');
