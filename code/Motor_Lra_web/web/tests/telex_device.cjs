const assert = require('node:assert/strict');
const fs = require('node:fs');
require('../telex-device.js');
const { TelexDevice, crc16, encodePacket, decodePacket, cobsDecode, scaleLevels } = globalThis.TelexProtocol;

assert.equal(crc16(new TextEncoder().encode('123456789')), 0x29b1);
for (const vector of JSON.parse(fs.readFileSync(require('node:path').resolve(__dirname, '..', '..', 'firmware/TELEX-Firmware-20261018/source/protocol/vectors.json'), 'utf8'))) {
  const raw = Buffer.from(vector.wire, 'hex');
  const encoded = encodePacket({ kind: vector.kind, source: 1, target: 3,
    sessionId: 1, sequence: 2, timestampMs: 1000, payload: Buffer.from(vector.payload, 'hex') });
  const packet = decodePacket(encoded.subarray(0, -1));
  assert.deepEqual(Buffer.from(packet.payload), Buffer.from(vector.payload, 'hex'));
  assert.deepEqual(Buffer.from(cobsDecode(encodePacket(packet).subarray(0, -1))), raw);
}
assert.ok(scaleLevels([40, 40, 40, 40, 40]).reduce((sum, x) => sum + x * x, 0) <= 7200);

const sent = [], notices = [];
let device;
const uid = 'aabbccddeeff';
const identity = { uid, role: 1, chip: 'esp32s3', boot: '1234567890abcdef', last_session: 4, configured: true };
const caps = { mode: 1, haptics: true, motors: 31, prewarm: true };
const reply = (request, kind, payload) => device.feed(encodePacket({ kind, source: 1, sequence: request.sequence,
  sessionId: request.sessionId, payload }));
device = new TelexDevice(async bytes => {
  const request = decodePacket(bytes.subarray(0, -1));
  sent.push(request);
  if (request.kind === 1 || request.kind === 2) {
    reply(request, request.kind, new TextEncoder().encode(JSON.stringify(request.kind === 1 ? identity : caps)));
  } else if (request.kind === 0x40 && request.payload.length === 8) {
    const response = new Uint8Array(24), view = new DataView(response.buffer);
    response.set(request.payload);
    view.setBigUint64(8, 5000000n, true);
    view.setBigUint64(16, 5000100n, true);
    reply(request, 0x40, response);
  } else {
    reply(request, 0x7e, Uint8Array.of(request.kind, 1, 0));
  }
}, (kind, value) => notices.push([kind, value]));

(async () => {
  await device.initialize();
  await device.single(47, 'thumb');
  assert.equal(sent.find(packet => packet.kind === 0x20 && packet.payload[8] === 2).sessionId, 5);
  assert.deepEqual([...sent.find(packet => packet.kind === 0x21).payload], [0, 0, 0, 0, 40]);
  assert.ok(notices.some(([kind]) => kind === 'output'));
  await device.stop();
  assert.ok(sent.some(packet => packet.kind === 0x22));
  await device.single(30, 'middle');
  assert.deepEqual([...sent.filter(packet => packet.kind === 0x21).at(-1).payload], [0, 0, 23, 0, 0]);
  await device.stop();
  device.close();
  console.log('PASS: TX v1 vectors, COBS/CRC, S3 discovery, clock, prewarm, exact middle-finger mapping, stop.');
})().catch(error => { console.error(error); process.exitCode = 1; device.close(); });
