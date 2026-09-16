const MIN_AMP = 25;
const MAX_AMP = 47;
const MAX_FORCE_N = 10;
const MIN_LONG_DURATION_MS = 3000;
const SAW_REFERENCE_AMPLITUDES = [25, 30, 35, 40, 47];
const SAW_FREQUENCY_HZ = 170;
const FRICTION_MIN_FREQUENCY_HZ = 50;
const FRICTION_MAX_FREQUENCY_HZ = 170;
const BAUD_RATE = 115200;
const HEARTBEAT_MS = 300;
const LIVE_UPDATE_MIN_MS = 90;
const MODE_DEFAULTS = {
  SHORT: { frequency: 150, amplitude: 38 },
  LONG: { frequency: 130, amplitude: 25 },
  SAW: { frequency: SAW_FREQUENCY_HZ, amplitude: 42 },
  FRICTION: { frequency: 50, amplitude: 35 },
  COLLISION: { frequency: 170, amplitude: 33 }
};
const HORIZONTAL_MODES = new Set(["SHORT", "LONG", "COLLISION"]);
const MODE_NAMES = {
  SHORT: "短振",
  LONG: "长振",
  SAW: "锯齿",
  FRICTION: "摩擦",
  COLLISION: "碰撞"
};
const MODE_HINTS = {
  SHORT: "短振：长按后横向移动，纵向位置保持不变",
  LONG: "长振：长按后横向移动，最短 3 秒，停留越久振动越久",
  SAW: "锯齿：固定 170 Hz，位置控制峰值振幅；自动播放两轮渐强并快速断开",
  FRICTION: "摩擦：最下 50 Hz，最上 170 Hz；只由上下位置决定",
  COLLISION: "碰撞：在 6 N 以上按下后横向移动，纵向位置保持不变"
};

const connectButton = document.querySelector("#connectButton");
const connectLabel = document.querySelector("#connectLabel");
const controlPad = document.querySelector("#controlPad");
const canvas = document.querySelector("#traceCanvas");
const context = canvas.getContext("2d");
const holdBadge = document.querySelector("#holdBadge");
const controlModeTitle = document.querySelector("#controlModeTitle");
const gestureHintText = document.querySelector("#gestureHintText");
const forceValue = document.querySelector("#forceValue");
const pressTimeValue = document.querySelector("#pressTimeValue");
const ampValue = document.querySelector("#ampValue");
const vppValue = document.querySelector("#vppValue");
const frequencyValue = document.querySelector("#frequencyValue");
const rangeFill = document.querySelector("#rangeFill");
const deviceOrb = document.querySelector("#deviceOrb");
const deviceState = document.querySelector("#deviceState");
const deviceDetail = document.querySelector("#deviceDetail");
const serialLog = document.querySelector("#serialLog");
const shortButton = document.querySelector("#shortButton");
const longButton = document.querySelector("#longButton");
const sawButton = document.querySelector("#sawButton");
const frictionButton = document.querySelector("#frictionButton");
const collisionButton = document.querySelector("#collisionButton");
const stopButton = document.querySelector("#stopButton");
const clearLogButton = document.querySelector("#clearLogButton");
const frequencyRange = document.querySelector("#frequencyRange");
const frequencyNumber = document.querySelector("#frequencyNumber");
const amplitudeRange = document.querySelector("#amplitudeRange");
const amplitudeNumber = document.querySelector("#amplitudeNumber");
const frequencyHelp = document.querySelector("#frequencyHelp");
const amplitudeVoltageHint = document.querySelector("#amplitudeVoltageHint");
const detectF0Button = document.querySelector("#detectF0Button");
const f0Result = document.querySelector("#f0Result");
const applyParametersButton = document.querySelector("#applyParametersButton");
const shortParameters = document.querySelector("#shortParameters");
const longParameters = document.querySelector("#longParameters");
const sawParameters = document.querySelector("#sawParameters");
const frictionParameters = document.querySelector("#frictionParameters");
const collisionParameters = document.querySelector("#collisionParameters");
const toast = document.querySelector("#toast");

let port = null;
let reader = null;
let writer = null;
let keepReading = false;
let connected = false;
let deviceReady = false;
let modePreparationPending = false;
let f0DetectionActive = false;
let f0DetectionTimer = null;
let holding = false;
let currentAmp = 36;
let currentPercent = 50;
let currentForce = 5;
let currentNormalized = 0.5;
let selectedMode = "FRICTION";
let selectedFrequency = 50;
let selectedMaxAmp = 35;
let liveFrequency = 100;
let heartbeatTimer = null;
let heartbeatWritePending = false;
let lastLiveUpdateSentAt = 0;
let lastLiveCommand = "";
let pressClockTimer = null;
let pressStartedAt = 0;
let playingEffect = null;
let toastTimer = null;
let writeQueue = Promise.resolve();
let receiveBuffer = "";
let traceHistory = [];
let hoverInside = false;
const effectButtons = [shortButton, longButton, sawButton, frictionButton, collisionButton];

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function ampToPercent(amp) {
  return Math.round(((amp - MIN_AMP) / (MAX_AMP - MIN_AMP)) * 100);
}

function ampToVpp(amp) {
  return 0.0651 * amp + 0.2038;
}

function clampNumber(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

function scaleAmplitude(referenceAmplitude, peakAmplitude) {
  if (peakAmplitude <= MIN_AMP) return MIN_AMP;
  const ratio = (referenceAmplitude - MIN_AMP) / (MAX_AMP - MIN_AMP);
  return Math.round(MIN_AMP + ratio * (peakAmplitude - MIN_AMP));
}

function updateEffectParameterLabels() {
  const short = selectedMode === "SHORT" ? { frequency: selectedFrequency, amplitude: selectedMaxAmp } : MODE_DEFAULTS.SHORT;
  const long = selectedMode === "LONG" ? { frequency: selectedFrequency, amplitude: selectedMaxAmp } : MODE_DEFAULTS.LONG;
  const saw = selectedMode === "SAW" ? { frequency: selectedFrequency, amplitude: selectedMaxAmp } : MODE_DEFAULTS.SAW;
  const friction = selectedMode === "FRICTION" ? { frequency: selectedFrequency, amplitude: selectedMaxAmp } : MODE_DEFAULTS.FRICTION;
  const collision = selectedMode === "COLLISION" ? { frequency: selectedFrequency, amplitude: selectedMaxAmp } : MODE_DEFAULTS.COLLISION;
  const sawAmps = SAW_REFERENCE_AMPLITUDES.map(value => scaleAmplitude(value, saw.amplitude)).join("/");

  shortParameters.textContent = `${short.frequency} Hz · AMP ${short.amplitude} · 60 ms · 横向`;
  longParameters.textContent = `${long.frequency} Hz · AMP ${long.amplitude} · 最短 3 s · 横向`;
  sawParameters.textContent = `${SAW_FREQUENCY_HZ} Hz · AMP ${sawAmps} · 两轮渐强`;
  frictionParameters.textContent = `${FRICTION_MIN_FREQUENCY_HZ}—${FRICTION_MAX_FREQUENCY_HZ} Hz · 最大 AMP ${friction.amplitude} · 位置调频`;
  collisionParameters.textContent = `≥6 N · ${collision.frequency} Hz · AMP ${collision.amplitude} · 28 ms · 横向`;
}

function updateSelectedModeText() {
  const amplitudeLabel = selectedMode === "SAW" ? "峰值 AMP" : "AMP";
  controlModeTitle.textContent = selectedMode === "FRICTION" ?
    `摩擦 · ${FRICTION_MIN_FREQUENCY_HZ}—${FRICTION_MAX_FREQUENCY_HZ} Hz / 最大 AMP ${selectedMaxAmp}` :
    selectedMode === "SAW" ?
      `锯齿 · ${SAW_FREQUENCY_HZ} Hz / ${amplitudeLabel} ${selectedMaxAmp}` :
      `${MODE_NAMES[selectedMode]} · ${selectedFrequency} Hz / ${amplitudeLabel} ${selectedMaxAmp}`;
  gestureHintText.textContent = MODE_HINTS[selectedMode];
  controlPad.classList.toggle("horizontal-mode", HORIZONTAL_MODES.has(selectedMode));

  if (HORIZONTAL_MODES.has(selectedMode)) {
    frequencyHelp.textContent = "当前模式使用固定频率；长按时锁定纵向位置，只识别横向移动";
    amplitudeVoltageHint.textContent = `当前模式使用固定振幅；输出约 ${ampToVpp(selectedMaxAmp).toFixed(2)} Vpp`;
  } else if (selectedMode === "FRICTION") {
    frequencyHelp.textContent = `最底部 ${FRICTION_MIN_FREQUENCY_HZ} Hz，最顶部 ${FRICTION_MAX_FREQUENCY_HZ} Hz；只由垂直位置决定`;
    amplitudeVoltageHint.textContent = `力度越大振幅越大；安全输出上限约 ${ampToVpp(selectedMaxAmp).toFixed(2)} Vpp`;
  } else if (selectedMode === "SAW") {
    frequencyHelp.textContent = `锯齿固定在实测 F0 ${SAW_FREQUENCY_HZ} Hz；五级渐强后断开 40 ms，共两轮`;
    amplitudeVoltageHint.textContent = `位置越高，锯齿峰值振幅越大；当前上限约 ${ampToVpp(selectedMaxAmp).toFixed(2)} Vpp`;
  } else {
    frequencyHelp.textContent = "当前模式使用设定频率；模块范围 20—500 Hz";
    amplitudeVoltageHint.textContent = `位置越高振幅越大；安全输出上限约 ${ampToVpp(selectedMaxAmp).toFixed(2)} Vpp`;
  }
}

function updateParameterPreview() {
  selectedFrequency = selectedMode === "FRICTION" ? FRICTION_MIN_FREQUENCY_HZ :
    selectedMode === "SAW" ? SAW_FREQUENCY_HZ :
    Math.round(clampNumber(frequencyNumber.value, 20, 500));
  selectedMaxAmp = Math.round(clampNumber(amplitudeNumber.value, MIN_AMP, MAX_AMP));
  frequencyRange.value = String(selectedFrequency);
  frequencyNumber.value = String(selectedFrequency);
  amplitudeRange.value = String(selectedMaxAmp);
  amplitudeNumber.value = String(selectedMaxAmp);
  frequencyRange.disabled = selectedMode === "FRICTION" || selectedMode === "SAW";
  frequencyNumber.disabled = selectedMode === "FRICTION" || selectedMode === "SAW";
  amplitudeRange.disabled = false;
  amplitudeNumber.disabled = false;
  applyParametersButton.disabled = f0DetectionActive;
  detectF0Button.disabled = f0DetectionActive;
  if (!holding) liveFrequency = selectedFrequency;
  frequencyValue.textContent = String(liveFrequency);
  updateSelectedModeText();
  updateEffectParameterLabels();
  updateOutputFromNormalized(currentNormalized);
}

function updateLiveFrequency() {
  if (selectedMode === "SAW") {
    liveFrequency = SAW_FREQUENCY_HZ;
  } else if (HORIZONTAL_MODES.has(selectedMode)) {
    liveFrequency = selectedFrequency;
  } else if (selectedMode === "FRICTION") {
    liveFrequency = Math.round(FRICTION_MIN_FREQUENCY_HZ + currentNormalized *
      (FRICTION_MAX_FREQUENCY_HZ - FRICTION_MIN_FREQUENCY_HZ));
  } else liveFrequency = selectedFrequency;
  frequencyValue.textContent = String(liveFrequency);
}

function updateOutputFromNormalized(normalized) {
  currentNormalized = Math.max(0, Math.min(1, normalized));
  currentAmp = HORIZONTAL_MODES.has(selectedMode) ? selectedMaxAmp :
    Math.round(MIN_AMP + currentNormalized * (selectedMaxAmp - MIN_AMP));
  currentPercent = Math.round(currentNormalized * 100);
  currentForce = currentNormalized * MAX_FORCE_N;

  document.documentElement.style.setProperty("--level", `${100 - currentPercent}%`);
  controlPad.setAttribute("aria-valuenow", String(currentPercent));
  forceValue.textContent = currentForce.toFixed(1);
  ampValue.textContent = String(currentAmp);
  vppValue.textContent = ampToVpp(currentAmp).toFixed(2);
  rangeFill.style.width = `${ampToPercent(currentAmp)}%`;
  updateLiveFrequency();
  refreshEffectLights();
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 1900);
}

function appendLog(message) {
  if (!message.trim()) return;
  const existing = serialLog.textContent === "等待连接…" ? "" : serialLog.textContent;
  const lines = `${existing}${existing ? "\n" : ""}${message}`.split("\n");
  serialLog.textContent = lines.slice(-80).join("\n");
  serialLog.scrollTop = serialLog.scrollHeight;
}

function updateConnectionUI() {
  connectButton.classList.toggle("connected", connected);
  deviceOrb.classList.toggle("ready", deviceReady);

  if (!connected) {
    connectLabel.textContent = "连接设备";
    deviceState.textContent = "未连接";
    deviceDetail.textContent = "请选择设备串口";
  } else if (!deviceReady) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = "正在初始化";
    deviceDetail.textContent = "等待 LRA 参数配置完成";
  } else if (f0DetectionActive) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = "正在检测 F0";
    deviceDetail.textContent = "请保持马达固定，不要触碰";
  } else if (modePreparationPending && !holding) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = "正在准备模式";
    deviceDetail.textContent = `${MODE_NAMES[selectedMode]}参数正在预加载`;
  } else {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = holding ? "持续振动中" : "设备就绪";
    deviceDetail.textContent = holding ? `AMP ${currentAmp}` : "按住线条开始控制";
  }
}

function setHoldingUI(active) {
  holding = active;
  controlPad.classList.toggle("pressed", active);
  holdBadge.classList.toggle("active", active);
  holdBadge.textContent = active ? "输出中" : "待机";
  if (!active) {
    liveFrequency = selectedFrequency;
    frequencyValue.textContent = String(liveFrequency);
  }
  updateConnectionUI();
  refreshEffectLights();
}

function refreshEffectLights() {
  const selected = playingEffect || selectedMode;
  effectButtons.forEach(button => {
    button.classList.toggle("active-effect", button.dataset.effect === selected);
  });
}

function updatePressClock() {
  if (!holding) return;
  pressTimeValue.textContent = String(Math.round(performance.now() - pressStartedAt));
  refreshEffectLights();
}

function startPressClock() {
  clearInterval(pressClockTimer);
  pressStartedAt = performance.now();
  pressTimeValue.textContent = "0";
  pressClockTimer = setInterval(updatePressClock, 40);
}

function stopPressClock() {
  clearInterval(pressClockTimer);
  pressClockTimer = null;
}

function setIntensityFromY(clientY) {
  const rect = controlPad.getBoundingClientRect();
  const normalized = Math.max(0, Math.min(1, 1 - (clientY - rect.top) / rect.height));
  updateOutputFromNormalized(normalized);
}

function queueLine(line) {
  if (!writer) return Promise.reject(new Error("Serial port is not connected"));

  writeQueue = writeQueue
    .catch(() => undefined)
    .then(() => writer.write(encoder.encode(`${line}\n`)));

  return writeQueue;
}

function prepareSelectedMode() {
  if (!connected || !deviceReady || f0DetectionActive) return;

  modePreparationPending = true;
  updateConnectionUI();
  const frequency = selectedFrequency;
  const amplitude = selectedMaxAmp;
  queueLine(`PREP ${frequency} ${amplitude}`).catch(error => {
    modePreparationPending = false;
    updateConnectionUI();
    showToast(error.message);
  });
}

function finishF0Detection(frequency, errorMessage = "") {
  clearTimeout(f0DetectionTimer);
  f0DetectionTimer = null;
  f0DetectionActive = false;
  detectF0Button.disabled = false;
  applyParametersButton.disabled = false;

  if (frequency) {
    f0Result.textContent = `${frequency} Hz`;
    showToast(`F0 检测完成：${frequency} Hz`);
  } else {
    f0Result.textContent = "检测失败";
    showToast(errorMessage || "F0 检测失败，请查看串口日志");
  }
  updateConnectionUI();
}

function detectF0() {
  if (!connected || !deviceReady) {
    showToast("请先连接并等待设备就绪");
    return;
  }
  if (f0DetectionActive) return;

  stopHeartbeat();
  stopPressClock();
  if (holding || playingEffect) {
    setHoldingUI(false);
    playingEffect = null;
    queueLine("STOP").catch(() => undefined);
  }

  f0DetectionActive = true;
  modePreparationPending = false;
  detectF0Button.disabled = true;
  applyParametersButton.disabled = true;
  f0Result.textContent = "检测中…";
  updateConnectionUI();

  queueLine("DETECT_F0").catch(error => finishF0Detection(0, error.message));
  f0DetectionTimer = setTimeout(() => {
    if (f0DetectionActive) finishF0Detection(0, "F0 检测超时");
  }, 33000);
}

function processDeviceLine(line) {
  appendLog(line);

  const f0Event = line.match(/EVENT F0 (\d+)/);
  const rawF0Event = line.match(/LRA_F0:\s*(\d+)\s*Hz/i);
  if (f0Event || rawF0Event) {
    const detectedFrequency = Number((f0Event || rawF0Event)[1]);
    if (f0DetectionActive || f0Result.textContent !== `${detectedFrequency} Hz`) {
      finishF0Detection(detectedFrequency);
    }
  }

  if (line.includes("EVENT READY")) {
    deviceReady = true;
    updateConnectionUI();
    prepareSelectedMode();
    showToast("LRA 已连接，正在预加载当前模式");
  }

  if (line.includes("EVENT STOP")) {
    stopHeartbeat();
    stopPressClock();
    setHoldingUI(false);
    playingEffect = null;
    setEffectButtonsBusy(false);
    refreshEffectLights();
  }

  if (line.includes("EVENT ERROR")) {
    if (f0DetectionActive && /EVENT ERROR F0_/.test(line)) {
      finishF0Detection(0);
    }
    modePreparationPending = false;
    setEffectButtonsBusy(false);
    applyParametersButton.disabled = false;
    updateConnectionUI();
    showToast("设备返回错误，请查看日志");
  }

  const preparationEvent = line.match(/EVENT PREP (\d+) (\d+)/);
  if (preparationEvent) {
    const preparedFrequency = Number(preparationEvent[1]);
    const preparedAmplitude = Number(preparationEvent[2]);
    if (preparedFrequency === selectedFrequency && preparedAmplitude === selectedMaxAmp) {
      modePreparationPending = false;
      updateConnectionUI();
    }
  }

  const outputEvent = line.match(/EVENT OUTPUT (\d+) (\d+)/);
  if (outputEvent && Number(outputEvent[1]) === liveFrequency &&
      Number(outputEvent[2]) === currentAmp) {
    modePreparationPending = false;
    updateConnectionUI();
  }

  const parameterEvent = line.match(/EVENT PARAM (\d+) (\d+)/);
  if (parameterEvent) {
    selectedFrequency = Number(parameterEvent[1]);
    selectedMaxAmp = Number(parameterEvent[2]);
    frequencyNumber.value = String(selectedFrequency);
    amplitudeNumber.value = String(selectedMaxAmp);
    modePreparationPending = false;
    applyParametersButton.disabled = false;
    updateParameterPreview();
    updateConnectionUI();
    showToast("频率和振幅已写入设备");
  }

  const effectEvent = line.match(/EVENT EFFECT (SHORT|LONG|SAW|FRICTION|COLLISION) (START|DONE|CANCELLED)/);
  if (effectEvent) {
    if (effectEvent[2] === "START") {
      modePreparationPending = false;
      playingEffect = effectEvent[1];
      setEffectButtonsBusy(true);
    } else {
      playingEffect = null;
      setEffectButtonsBusy(false);
      if (effectEvent[2] === "DONE") showToast("振动效果播放完成");
    }
    refreshEffectLights();
  }
}

function setEffectButtonsBusy(busy) {
  effectButtons.forEach(button => button.setAttribute("aria-busy", String(busy)));
}

function selectMode(mode) {
  if (f0DetectionActive) {
    showToast("请等待 F0 检测完成");
    return;
  }
  if (holding || playingEffect) {
    stopHeartbeat();
    stopPressClock();
    setHoldingUI(false);
    if (connected) queueLine("STOP").catch(() => undefined);
  }

  selectedMode = mode;
  const defaults = MODE_DEFAULTS[mode];
  selectedFrequency = defaults.frequency;
  selectedMaxAmp = defaults.amplitude;
  frequencyNumber.value = String(selectedFrequency);
  amplitudeNumber.value = String(selectedMaxAmp);
  pressTimeValue.textContent = "0";
  playingEffect = null;
  updateParameterPreview();
  refreshEffectLights();
  prepareSelectedMode();
  showToast(`已选择${effectButtons.find(button => button.dataset.effect === mode)?.querySelector("strong")?.textContent || "振动"}模式`);
}

function applyParameters() {
  if (f0DetectionActive) {
    showToast("请等待 F0 检测完成");
    return;
  }
  updateParameterPreview();

  if (!connected || !deviceReady) {
    showToast("请先连接并等待设备就绪");
    return;
  }

  applyParametersButton.disabled = true;
  modePreparationPending = true;
  updateConnectionUI();
  queueLine(`PARAM ${selectedFrequency} ${selectedMaxAmp}`).catch(error => {
    modePreparationPending = false;
    applyParametersButton.disabled = false;
    updateConnectionUI();
    showToast(error.message);
  });
}

async function readSerialLoop() {
  keepReading = true;

  try {
    while (port?.readable && keepReading) {
      reader = port.readable.getReader();

      try {
        while (keepReading) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!value) continue;

          receiveBuffer += decoder.decode(value, { stream: true });
          const lines = receiveBuffer.split(/\r?\n/);
          receiveBuffer = lines.pop() ?? "";
          lines.forEach(processDeviceLine);
        }
      } finally {
        reader.releaseLock();
        reader = null;
      }
    }
  } catch (error) {
    if (keepReading) appendLog(`SERIAL ERROR: ${error.message}`);
  }
}

async function connectSerial() {
  if (!("serial" in navigator)) {
    showToast("请使用最新版 Chrome 或 Edge");
    return;
  }

  try {
    port = await navigator.serial.requestPort();
    await port.open({ baudRate: BAUD_RATE });
    writer = port.writable.getWriter();
    connected = true;
    deviceReady = false;
    serialLog.textContent = "串口已连接，设备正在初始化…";
    updateConnectionUI();
    readSerialLoop();
  } catch (error) {
    if (error.name !== "NotFoundError") {
      showToast(`连接失败：${error.message}`);
    }
  }
}

async function disconnectSerial() {
  stopHeartbeat();
  setHoldingUI(false);

  try {
    if (writer) {
      await queueLine("STOP").catch(() => undefined);
      writer.releaseLock();
      writer = null;
    }

    keepReading = false;
    if (reader) await reader.cancel().catch(() => undefined);
    if (port) await port.close();
  } finally {
    port = null;
    connected = false;
    deviceReady = false;
    modePreparationPending = false;
    f0DetectionActive = false;
    clearTimeout(f0DetectionTimer);
    f0DetectionTimer = null;
    detectF0Button.disabled = false;
    updateConnectionUI();
  }
}

function sendLiveUpdate(forceKeepAlive = false) {
  if (!holding || !connected || heartbeatWritePending ||
      (selectedMode !== "LONG" && selectedMode !== "FRICTION")) return;

  const now = performance.now();
  const command = selectedMode === "LONG" ?
    `LONG_HOLD ${liveFrequency} ${currentAmp}` :
    `FRICTION_HOLD ${Math.round(currentForce * 10)} ${FRICTION_MIN_FREQUENCY_HZ} ${FRICTION_MAX_FREQUENCY_HZ} ${selectedMaxAmp}`;
  if (!forceKeepAlive &&
      (now - lastLiveUpdateSentAt < LIVE_UPDATE_MIN_MS || command === lastLiveCommand)) return;

  lastLiveUpdateSentAt = now;
  lastLiveCommand = command;
  heartbeatWritePending = true;
  queueLine(command)
    .catch(() => undefined)
    .finally(() => { heartbeatWritePending = false; });
}

function startHeartbeat() {
  stopHeartbeat();
  lastLiveUpdateSentAt = 0;
  lastLiveCommand = "";
  heartbeatTimer = setInterval(() => sendLiveUpdate(true), HEARTBEAT_MS);
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  heartbeatWritePending = false;
  lastLiveCommand = "";
}

async function beginHold(event) {
  setIntensityFromY(event.clientY);

  if (f0DetectionActive) {
    showToast("请等待 F0 检测完成");
    return;
  }

  if (!connected) {
    showToast("请先连接设备");
    return;
  }

  if (!deviceReady) {
    showToast("LRA 正在初始化，请稍候");
    return;
  }

  if (selectedMode === "COLLISION" && currentForce < 6) {
    showToast("碰撞需要至少 6 N 才能触发");
    return;
  }

  controlPad.setPointerCapture(event.pointerId);
  startPressClock();
  setHoldingUI(true);
  updateLiveFrequency();

  let command;
  switch (selectedMode) {
    case "SHORT":
      command = `PULSE ${liveFrequency} ${currentAmp} 60`;
      break;
    case "LONG":
      command = `LONG_START ${liveFrequency} ${currentAmp}`;
      break;
    case "SAW":
      command = `SAW_PLAY ${SAW_FREQUENCY_HZ} ${currentAmp}`;
      break;
    case "COLLISION":
      command = `COLLISION ${Math.round(currentForce * 10)} ${liveFrequency} ${currentAmp}`;
      break;
    case "FRICTION":
      command = `FRICTION_START ${Math.round(currentForce * 10)} ${FRICTION_MIN_FREQUENCY_HZ} ${FRICTION_MAX_FREQUENCY_HZ} ${selectedMaxAmp}`;
      break;
    default:
      return;
  }

  await queueLine(command).catch(error => showToast(error.message));
  if (selectedMode === "LONG" || selectedMode === "FRICTION") startHeartbeat();
}

function moveHold(event) {
  hoverInside = true;
  if (holding && HORIZONTAL_MODES.has(selectedMode)) return;
  setIntensityFromY(event.clientY);
  if (holding && selectedMode === "FRICTION") sendLiveUpdate();
}

function endHold(event) {
  if (!holding) return;
  if (controlPad.hasPointerCapture(event.pointerId)) {
    controlPad.releasePointerCapture(event.pointerId);
  }
  stopHeartbeat();
  updatePressClock();
  stopPressClock();
  setHoldingUI(false);

  if (selectedMode === "LONG") {
    queueLine("LONG_RELEASE").catch(() => undefined);
  } else if (selectedMode === "FRICTION") {
    queueLine("STOP").catch(() => undefined);
  }
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rect.width * scale);
  canvas.height = Math.round(rect.height * scale);
  context.setTransform(scale, 0, 0, scale, 0, 0);
  traceHistory = Array(Math.max(80, Math.floor(rect.width / 5))).fill(0);
}

function drawTrace() {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const target = holding ? currentPercent / 100 : 0;
  const previous = traceHistory.at(-1) ?? 0;
  const smoothed = previous + (target - previous) * 0.24;
  traceHistory.push(smoothed);
  traceHistory.shift();

  context.clearRect(0, 0, width, height);

  context.strokeStyle = "rgba(255,255,255,.055)";
  context.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (height / 4) * i;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }

  const gradient = context.createLinearGradient(0, 0, width, 0);
  gradient.addColorStop(0, "rgba(101,119,255,0)");
  gradient.addColorStop(.22, "rgba(101,119,255,.72)");
  gradient.addColorStop(1, "rgba(77,226,255,.95)");

  context.beginPath();
  traceHistory.forEach((value, index) => {
    const x = (index / (traceHistory.length - 1)) * width;
    const y = height - 22 - value * (height - 44);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = gradient;
  context.lineWidth = holding ? 2.2 : 1.4;
  context.shadowBlur = holding ? 16 : 7;
  context.shadowColor = "rgba(77,226,255,.8)";
  context.stroke();
  context.shadowBlur = 0;

  requestAnimationFrame(drawTrace);
}

connectButton.addEventListener("click", () => {
  if (connected) disconnectSerial();
  else connectSerial();
});

controlPad.addEventListener("pointerdown", beginHold);
controlPad.addEventListener("pointermove", moveHold);
controlPad.addEventListener("pointerup", endHold);
controlPad.addEventListener("pointercancel", endHold);
controlPad.addEventListener("pointerenter", () => { hoverInside = true; });
controlPad.addEventListener("pointerleave", () => { hoverInside = false; });

shortButton.addEventListener("click", () => {
  selectMode("SHORT");
});

longButton.addEventListener("click", () => {
  selectMode("LONG");
});

sawButton.addEventListener("click", () => {
  selectMode("SAW");
});

frictionButton.addEventListener("click", () => {
  selectMode("FRICTION");
});

collisionButton.addEventListener("click", () => {
  selectMode("COLLISION");
});

frequencyRange.addEventListener("input", () => {
  frequencyNumber.value = frequencyRange.value;
  updateParameterPreview();
});

frequencyNumber.addEventListener("change", updateParameterPreview);

amplitudeRange.addEventListener("input", () => {
  amplitudeNumber.value = amplitudeRange.value;
  updateParameterPreview();
});

amplitudeNumber.addEventListener("change", updateParameterPreview);
applyParametersButton.addEventListener("click", applyParameters);
detectF0Button.addEventListener("click", detectF0);

stopButton.addEventListener("click", () => {
  stopHeartbeat();
  stopPressClock();
  setHoldingUI(false);
  playingEffect = null;
  setEffectButtonsBusy(false);
  refreshEffectLights();
  if (connected) queueLine("STOP").catch(error => showToast(error.message));
});

clearLogButton.addEventListener("click", () => {
  serialLog.textContent = "";
});

navigator.serial?.addEventListener("disconnect", event => {
  if (event.target === port) disconnectSerial();
});

window.addEventListener("resize", resizeCanvas);
window.addEventListener("blur", () => {
  if (holding) {
    stopHeartbeat();
    stopPressClock();
    setHoldingUI(false);
    if (connected) queueLine("STOP").catch(() => undefined);
  }
});

updateParameterPreview();
setIntensityFromY(controlPad.getBoundingClientRect().top + controlPad.clientHeight / 2);
resizeCanvas();
drawTrace();
updateConnectionUI();
