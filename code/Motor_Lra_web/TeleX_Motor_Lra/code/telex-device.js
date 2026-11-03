// TELEX v1 USB transport. IMU packets are deliberately left untouched.
(function (scope) {
  "use strict";
  const MAX_PACKET = 250;
  const FINGERS = { little: 0, ring: 1, middle: 2, index: 3, thumb: 4 };
  function ampToMotorLevel(value) {
    const amp = Math.max(0, Math.min(100, Number(value) || 0));
    if (amp <= 25) return Math.round(amp * 18 / 25);
    if (amp <= 47) return Math.round(18 + (amp - 25));
    return 40;
  }
  const crc16 = bytes => {
    let crc = 0xffff;
    for (const value of bytes) {
      crc ^= value << 8;
      for (let bit = 0; bit < 8; bit++) crc = ((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0)) & 0xffff;
    }
    return crc;
  };
  function cobsEncode(bytes) {
    const out = new Uint8Array(bytes.length + 2);
    let codeAt = 0, next = 1, code = 1;
    for (const value of bytes) {
      if (value === 0) { out[codeAt] = code; codeAt = next++; code = 1; }
      else { out[next++] = value; code++; }
    }
    out[codeAt] = code;
    out[next++] = 0;
    return out.subarray(0, next);
  }
  function cobsDecode(bytes) {
    const out = [];
    for (let i = 0; i < bytes.length;) {
      const code = bytes[i++];
      if (!code || i + code - 1 > bytes.length) throw new Error("COBS 帧无效");
      for (let j = 1; j < code; j++) out.push(bytes[i++]);
      if (code !== 255 && i < bytes.length) out.push(0);
    }
    return Uint8Array.from(out);
  }
  const put64 = (view, offset, value) => view.setBigUint64(offset, BigInt(value), true);
  function encodePacket(packet) {
    const payload = packet.payload || new Uint8Array(0);
    if (payload.length > 224) throw new Error("TELEX payload 过长");
    const raw = new Uint8Array(26 + payload.length), view = new DataView(raw.buffer);
    raw[0] = 0x54; raw[1] = 0x58; raw[2] = 1; raw[3] = packet.kind;
    raw[5] = packet.source || 0; raw[6] = packet.target || 0;
    view.setUint32(8, packet.sessionId || 0, true);
    view.setUint32(12, packet.sequence || 0, true);
    view.setUint32(16, packet.timestampMs || 0, true);
    view.setUint16(20, payload.length, true);
    raw.set(payload, 24);
    view.setUint16(raw.length - 2, crc16(raw.subarray(0, raw.length - 2)), true);
    return cobsEncode(raw);
  }
  function decodePacket(encoded) {
    const raw = cobsDecode(encoded);
    if (raw.length < 26 || raw.length > MAX_PACKET || raw[0] !== 0x54 || raw[1] !== 0x58 || raw[2] !== 1 ||
        raw[4] || raw[7] || raw[22] || raw[23]) throw new Error("TELEX 包头无效");
    const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength), length = view.getUint16(20, true);
    if (length !== raw.length - 26 || view.getUint16(raw.length - 2, true) !== crc16(raw.subarray(0, raw.length - 2)))
      throw new Error("TELEX 长度或 CRC 错误");
    return { kind: raw[3], source: raw[5], target: raw[6], sessionId: view.getUint32(8, true),
      sequence: view.getUint32(12, true), timestampMs: view.getUint32(16, true), payload: raw.slice(24, -2) };
  }
  function scaleLevels(values) {
    const levels = values.map(value => Math.max(0, Math.min(40, Math.round(Number(value) || 0))));
    const energy = levels.reduce((sum, level) => sum + level * level, 0);
    return energy <= 7200 ? levels : levels.map(level => Math.floor(level * Math.sqrt(7200 / energy)));
  }
  class TelexDevice {
    constructor(write, notify) {
      this.write = write;
      this.notify = notify;
      this.buffer = [];
      this.pending = new Map();
      this.sequence = 1;
      this.identity = null;
      this.receiverIdentity = null;
      this.receiverMode = false;
      this.capabilities = null;
      this.status = null;
      this.config = null;
      this.clockReadyAt = 0;
      this.clockPending = null;
      this.sessionId = 0;
      this.sessionActive = false;
      this.lastUpdateAt = 0;
      this.stopGeneration = 0;
      this.selectedFinger = "index";
      this.lastLevels = [0, 0, 0, 0, 0];
      this.finiteTimer = null;
      this.syncTimer = null;
      this.keepaliveTimer = null;
      this.updatePending = false;
      this.startPending = null;
      this.closed = false;
    }
    nowUs() { return BigInt(Math.floor(performance.now() * 1000)); }
    async send(kind, payload = new Uint8Array(0), extra = {}) {
      if (this.closed) throw new Error("设备已断开");
      const sequence = this.sequence++ >>> 0;
      const packet = { kind, payload, source: 0, target: extra.target ?? this.identity?.role ?? 0,
        sessionId: extra.sessionId ?? 0, sequence, timestampMs: Math.floor(performance.now()) >>> 0 };
      await this.write(encodePacket(packet));
      return sequence;
    }
    request(kind, payload = new Uint8Array(0), timeout = 1200, extra = {}) {
      if (this.closed) return Promise.reject(new Error("设备已断开"));
      const sequence = this.sequence++ >>> 0;
      const packet = { kind, payload, source: 0, target: extra.target ?? this.identity?.role ?? 0,
        sessionId: extra.sessionId ?? 0, sequence, timestampMs: Math.floor(performance.now()) >>> 0 };
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { this.pending.delete(sequence); reject(new Error(`TELEX 0x${kind.toString(16)} 响应超时`)); }, timeout);
        this.pending.set(sequence, { kind, resolve, reject, timer });
        Promise.resolve(this.write(encodePacket(packet))).catch(error => {
          clearTimeout(timer); this.pending.delete(sequence); reject(error);
        });
      });
    }
    feed(chunk) {
      for (const byte of chunk) {
        if (byte !== 0) {
          if (this.buffer.length < 251) this.buffer.push(byte);
          else this.buffer.length = 0;
          continue;
        }
        if (!this.buffer.length) continue;
        const encoded = Uint8Array.from(this.buffer); this.buffer.length = 0;
        try { this.onPacket(decodePacket(encoded)); }
        catch (error) { this.notify("error", error.message); }
      }
    }
    onPacket(packet) {
      if (packet.kind === 0x10) return; // Existing IMU acquisition and payload remain unchanged.
      // A C6 emits its own periodic identity/status while also forwarding the
      // paired glove's packets. Once receiver mode is selected, keep those
      // local packets from replacing the glove that the UI controls.
      if (this.receiverMode && packet.source === 3 && [1, 2, 0x11].includes(packet.kind)) return;
      if (packet.kind === 1 || packet.kind === 2 || packet.kind === 0x11) {
        let value;
        try { value = JSON.parse(new TextDecoder().decode(packet.payload)); }
        catch (_) { this.notify("error", "设备 JSON 状态无效"); return; }
        if (packet.kind === 1) {
          const rebooted = this.identity && this.identity.boot !== value.boot;
          this.identity = value;
          if (rebooted) { this.sessionActive = false; this.clockReadyAt = 0; this.config = null; this.notify("reboot", value); }
          this.notify("identity", value);
        } else if (packet.kind === 2) { this.capabilities = value; this.notify("capabilities", value); }
        else { this.status = value; this.notify("status", value); }
      }
      const pending = this.pending.get(packet.sequence);
      if (!pending) return;
      if (packet.kind === 0x7e && packet.payload[0] === pending.kind) {
        clearTimeout(pending.timer); this.pending.delete(packet.sequence);
        const state = packet.payload[1], reason = packet.payload[2];
        if (state === 2 || state === 3) pending.reject(new Error(`设备拒绝命令 0x${pending.kind.toString(16)}：状态 ${state}，原因 ${reason}`));
        else pending.resolve(packet);
      } else if (packet.kind === pending.kind || packet.kind === 0x7f) {
        clearTimeout(pending.timer); this.pending.delete(packet.sequence);
        if (packet.kind === 0x7f) pending.reject(new Error("设备返回协议错误"));
        else pending.resolve(packet);
      }
    }
    async initialize() {
      let identity = await this.request(1).then(packet => JSON.parse(new TextDecoder().decode(packet.payload)));
      if (identity.chip === "esp32c6" && identity.role === 3) {
        this.receiverIdentity = identity;
        this.receiverMode = true;
        this.identity = null; // Switching targets is not a device reboot.
        // Address the paired left glove through the C6. The receiver forwards
        // the request over ESP-NOW and returns the glove's reply over USB.
        identity = await this.request(1, new Uint8Array(0), 1800, { target: 1 })
          .then(packet => JSON.parse(new TextDecoder().decode(packet.payload)));
      }
      if (identity.chip !== "esp32s3" || ![1, 2].includes(identity.role))
        throw new Error("未识别到左/右手 S3；请检查手套供电和 ESP-NOW 配对");
      this.identity = identity;
      this.capabilities = await this.request(2, new Uint8Array(0), 1800, { target: identity.role })
        .then(packet => JSON.parse(new TextDecoder().decode(packet.payload)));
      this.notify("ready", { identity, receiver: this.receiverIdentity, capabilities: this.capabilities });
      this.syncTimer = setInterval(() => this.syncClock().catch(error => this.notify("error", error.message)), 2500);
      await this.syncClock();
      return identity;
    }
    async syncClock() {
      if (this.clockPending) return this.clockPending;
      this.clockPending = (async () => {
        const timeout = this.receiverMode ? 1500 : 300;
        const start = new Uint8Array(8), startView = new DataView(start.buffer);
        put64(startView, 0, this.nowUs());
        const response = await this.request(0x40, start, timeout);
        if (response.payload.length !== 24) throw new Error("时钟响应长度错误");
        const values = new DataView(response.payload.buffer, response.payload.byteOffset, 24);
        if (values.getBigUint64(0, true) !== startView.getBigUint64(0, true)) throw new Error("时钟响应不匹配");
        const confirmation = new Uint8Array(32);
        confirmation.set(response.payload);
        put64(new DataView(confirmation.buffer), 24, this.nowUs());
        await this.request(0x40, confirmation, timeout);
        this.clockReadyAt = performance.now();
      })();
      try { return await this.clockPending; } finally { this.clockPending = null; }
    }
    async ensureClock() {
      if (!this.clockReadyAt || performance.now() - this.clockReadyAt > 3500) await this.syncClock();
    }
    nextSessionId() {
      const high = this.identity?.last_session || 0;
      this.sessionId = (Math.max(high, this.sessionId) + 1) >>> 0;
      if (!this.sessionId) this.sessionId = 1;
      return this.sessionId;
    }
    async start(testOnly = false) {
      if (this.sessionActive) return;
      if (this.startPending) return this.startPending;
      this.startPending = this.openSession(testOnly);
      try { return await this.startPending; } finally { this.startPending = null; }
    }
    async openSession(testOnly) {
      const generation = this.stopGeneration;
      if (!this.identity?.configured || !this.capabilities?.haptics || this.capabilities.mode !== 1)
        throw new Error("振动尚未启用：请点击“启用此板振动”完成配置");
      if (!this.capabilities.motors) throw new Error("未探测到马达，请检查供电和连接");
      await this.ensureClock();
      if (generation !== this.stopGeneration) return;
      const id = this.nextSessionId();
      const boot = BigInt(`0x${this.identity.boot}`);
      const payload = new Uint8Array(9);
      put64(new DataView(payload.buffer), 0, boot);
      if (this.capabilities.prewarm) {
        payload[8] = 2;
        await this.request(0x20, payload, 1200, { sessionId: id });
      }
      if (generation !== this.stopGeneration) return;
      payload[8] = testOnly ? 1 : 0;
      await this.request(0x20, payload, 1200, { sessionId: id });
      if (generation !== this.stopGeneration) return;
      this.sessionActive = true;
      this.lastUpdateAt = performance.now();
      this.notify("start", { testOnly });
    }
    async update(levels) {
      const scaled = scaleLevels(levels);
      if (!scaled.some(Boolean)) return this.stop();
      if (this.sessionActive && this.lastUpdateAt && performance.now() - this.lastUpdateAt > 260) await this.stop();
      if (!this.sessionActive) await this.start(false);
      if (!this.sessionActive) return;
      this.lastLevels = scaled;
      if (this.updatePending) return;
      this.updatePending = true;
      try { await this.request(0x21, Uint8Array.from(scaled), 450, { sessionId: this.sessionId }); }
      finally { this.updatePending = false; }
      this.lastUpdateAt = performance.now();
      if (!this.keepaliveTimer) this.keepaliveTimer = setInterval(() => {
        if (this.sessionActive && !this.updatePending && this.lastLevels.some(Boolean))
          this.update(this.lastLevels).catch(error => this.notify("error", error.message));
      }, 100);
      this.notify("output", { levels: scaled });
    }
    async single(level, finger = this.selectedFinger) {
      const levels = [0, 0, 0, 0, 0];
      levels[FINGERS[finger] ?? 3] = ampToMotorLevel(level);
      return this.update(levels);
    }
    async finite(level, durationMs, preset = 0, finger = this.selectedFinger) {
      await this.stop();
      await this.start(true);
      const channel = FINGERS[finger] ?? 3;
      const mask = 1 << channel;
      if (!(this.capabilities.motors & mask)) throw new Error(`${finger} 马达未在线`);
      const strength = Math.max(1, ampToMotorLevel(level));
      const duration = Math.max(1, Math.min(200, Math.round(Number(durationMs) || 60)));
      await this.request(0x23, Uint8Array.of(mask, preset, strength, duration & 255, duration >> 8), 1200, { sessionId: this.sessionId });
      this.notify("finite", { duration });
      clearTimeout(this.finiteTimer);
      this.finiteTimer = setTimeout(() => { this.sessionActive = false; this.notify("finiteDone", {}); }, duration + 80);
    }
    async stop() {
      this.stopGeneration++;
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
      clearTimeout(this.finiteTimer);
      this.finiteTimer = null;
      const id = this.sessionId;
      this.sessionActive = false;
      this.lastUpdateAt = 0;
      this.lastLevels = [0, 0, 0, 0, 0];
      if (!id || this.closed) return;
      await this.request(0x22, new Uint8Array(0), 400, { sessionId: id });
      this.notify("stop", {});
    }
    async readConfig() {
      const packet = await this.request(3, new Uint8Array(0), 1200);
      if (packet.payload.length !== 99 || packet.payload[0] !== 3 || packet.payload[1] !== 1)
        throw new Error("设备未返回有效配置");
      this.config = packet.payload.slice(3);
      return this.config;
    }
    async enableHaptics() {
      if (!this.identity || !this.capabilities) throw new Error("请先连接并识别设备");
      if (this.sessionActive) await this.stop();
      let record;
      if (this.identity.configured) record = (await this.readConfig()).slice();
      else {
        record = new Uint8Array(96);
        record.set([0x54, 0x58, 0x43, 0x46, 1], 0);
        const uid = this.identity.uid.match(/.{2}/g).map(value => parseInt(value, 16));
        record.set(uid, 20);
        record.set(new TextEncoder().encode("WEB_USB"), 26);
        // Preserve the IMU settings reported by the running firmware.
        record[13] = this.capabilities.imu;
        record[16] = this.capabilities.rate & 255;
        record[17] = this.capabilities.rate >> 8;
      }
      record[12] = 1; // New protocol.
      record[15] = 1; // Haptics enabled.
      record[94] = record[95] = 0;
      const crc = crc16(record.subarray(0, 94));
      record[94] = crc & 255; record[95] = crc >> 8;
      const response = await this.request(3, record, 2500);
      if (response.payload.length !== 99 || response.payload[0] !== 3 || response.payload[1] !== 1)
        throw new Error("配置写入未得到持久化确认");
      this.config = response.payload.slice(3);
      this.notify("configured", {});
    }
    close() {
      this.closed = true;
      clearInterval(this.syncTimer);
      clearInterval(this.keepaliveTimer);
      clearTimeout(this.finiteTimer);
      for (const entry of this.pending.values()) { clearTimeout(entry.timer); entry.reject(new Error("设备已断开")); }
      this.pending.clear();
    }
  }
  scope.TelexProtocol = { TelexDevice, crc16, cobsEncode, cobsDecode, encodePacket, decodePacket, scaleLevels, FINGERS, ampToMotorLevel };
})(typeof window !== "undefined" ? window : globalThis);
