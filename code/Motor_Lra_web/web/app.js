const MIN_AMP = 0;
const MAX_AMP = 75;
const MAX_FORCE_N = 10;
const SENSOR_FULL_SCALE_POINTS = 140;
const SENSOR_MAX_FORCE_N = 15;
const FORCE_CHART_LEVELS = 256;
const FORCE_CHART_HISTORY = 256;
const DEFAULT_FREQUENCY_HZ = 170;
const SHORT_DURATION_MS = 100;
const TELEX_SHORT_DURATION_MS = 18;
const SENSITIVITY_LEVELS = {
  1: { label: "最稳定", step: 0.24, interval: 550, threshold: 2.0 },
  2: { label: "稳定", step: 0.20, interval: 450, threshold: 1.6 },
  3: { label: "均衡", step: 0.16, interval: 350, threshold: 1.2 },
  4: { label: "灵敏", step: 0.12, interval: 280, threshold: 0.8 },
  5: { label: "高灵敏", step: 0.08, interval: 220, threshold: 0.05 }
};
const BAUD_RATE = 115200;
const HEARTBEAT_MS = 100;
const HORIZONTAL_MODES = new Set(["SHORT", "LONG", "COLLISION"]);
const MODE_NAMES = {
  DEFAULT: "默认",
  SHORT: "短振",
  LONG: "长振",
  FRICTION: "摩擦",
  COLLISION: "碰撞"
};
const MODE_HINTS = {
  DEFAULT: "默认：按住多久振动多久；按所选灵敏度响应压力变化",
  SHORT: "短振：长按后横向移动，纵向位置保持不变",
  LONG: "长振：按住多久振动多久，松开立即停止",
  FRICTION: "摩擦：按整体频率和整体振幅持续输出",
  COLLISION: "碰撞：在 6 N 以上按下后横向移动，纵向位置保持不变"
};

const connectButton = document.querySelector("#connectButton");
const deviceProtocol = document.querySelector("#deviceProtocol");
const enableTelexHaptics = document.querySelector("#enableTelexHaptics");
const themeToggle = document.querySelector("#themeToggle");
const connectLabel = document.querySelector("#connectLabel");
const controlPad = document.querySelector("#controlPad");
const canvas = document.querySelector("#traceCanvas");
const context = canvas.getContext("2d");
const holdBadge = document.querySelector("#holdBadge");
const controlSectionLabel = document.querySelector("#controlSectionLabel");
const controlModeTitle = document.querySelector("#controlModeTitle");
const gestureHintText = document.querySelector("#gestureHintText");
const forceValue = document.querySelector("#forceValue");
const pressTimeValue = document.querySelector("#pressTimeValue");
const ampValue = document.querySelector("#ampValue");
const vppValue = document.querySelector("#vppValue");
const outputUnit = document.querySelector("#outputUnit");
const frequencyValue = document.querySelector("#frequencyValue");
const rangeFill = document.querySelector("#rangeFill");
const deviceOrb = document.querySelector("#deviceOrb");
const deviceState = document.querySelector("#deviceState");
const deviceDetail = document.querySelector("#deviceDetail");
const serialLog = document.querySelector("#serialLog");
const shortButton = document.querySelector("#shortButton");
const shortLed = shortButton?.querySelector('.effect-led');
const defaultButton = document.querySelector("#defaultButton");
const longButton = document.querySelector("#longButton");
const longLed = longButton?.querySelector('.effect-led');
const frictionButton = document.querySelector("#frictionButton");
const frictionLed = frictionButton?.querySelector('.effect-led');
const collisionButton = document.querySelector("#collisionButton");
const collisionLed = collisionButton?.querySelector('.effect-led');
const stopButton = document.querySelector("#stopButton");
const clearLogButton = document.querySelector("#clearLogButton");
const frequencyRange = document.querySelector("#frequencyRange");
const frequencyNumber = document.querySelector("#frequencyNumber");
const amplitudeRange = document.querySelector("#amplitudeRange");
const amplitudeNumber = document.querySelector("#amplitudeNumber");
const frequencyHelp = document.querySelector("#frequencyHelp");
const amplitudeVoltageHint = document.querySelector("#amplitudeVoltageHint");
const sensitivityRange = document.querySelector("#sensitivityRange");
const sensitivityValue = document.querySelector("#sensitivityValue");
const sensitivityHelp = document.querySelector("#sensitivityHelp");
const detectF0Button = document.querySelector("#detectF0Button");
const f0Result = document.querySelector("#f0Result");
const applyParametersButton = document.querySelector("#applyParametersButton");
const shortParameters = document.querySelector("#shortParameters");
const defaultParameters = document.querySelector("#defaultParameters");
const longParameters = document.querySelector("#longParameters");
const frictionParameters = document.querySelector("#frictionParameters");
const collisionParameters = document.querySelector("#collisionParameters");
const toast = document.querySelector("#toast");

function applyTheme(theme) {
  const nextTheme = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = nextTheme;
  const switchLabel = nextTheme === 'light' ? '切换到夜间模式' : '切换到白天模式';
  themeToggle.textContent = nextTheme === 'light' ? '☀' : '☾';
  themeToggle.setAttribute('aria-label', switchLabel);
  themeToggle.title = switchLabel;
  themeToggle.setAttribute('aria-pressed', String(nextTheme === 'light'));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', nextTheme === 'light' ? '#eef2f7' : '#0b0d12');
  try { localStorage.setItem('lra-theme', nextTheme); } catch (_) {}
}

let initialTheme = 'dark';
try { initialTheme = localStorage.getItem('lra-theme') || 'dark'; } catch (_) {}
applyTheme(initialTheme);
themeToggle.addEventListener('click', () => {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  requestAnimationFrame(() => {
    drawTrace();
    drawTactile();
    drawPressureHeat();
    drawAllRobotVisuals();
  });
});

let port = null;
let reader = null;
let writer = null;
let keepReading = false;
let connected = false;
let deviceReady = false;
let deviceInitTimedOut = false;
let deviceHandshakeTimers = [];
let hapticBoard = "legacy";
let telexDevice = null;
let telexCommandQueue = Promise.resolve();
let telexMotionGeneration = 0;
let hapticChannelCount = 1;
let modePreparationPending = false;
let f0DetectionActive = false;
let f0DetectionTimer = null;
let f0DetectionFinger = null;
let holding = false;
let currentAmp = 36;
let currentPercent = 50;
let currentForce = 5;
let currentNormalized = 0.5;
let selectedMode = "DEFAULT";
let selectedFrequency = DEFAULT_FREQUENCY_HZ;
let selectedMaxAmp = 47;
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
let tactileSource = false;
let tactileActive = false;
let tactilePressure = 4;
let tactileMotorAllowed = false;
let tactileTriggeredMode = null;
let tactileEffectCompleted = false;
let pendingStopReplies = 0;
let tactileOutputConfirmed = false;
let motionGeneration = 0;
let confirmedMotorOutput = null;
let tactilePreloadPending = false;
let continuousTargetLevel = 0;
let continuousOutputLevel = 0;
let feedbackSensitivity = Number(sensitivityRange?.value || 5);
let robotState = null;
let liveTactileMatrix = [];
let liveTactilePressure = 0;
const effectButtons = [defaultButton, shortButton, longButton, frictionButton, collisionButton];

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function ampToPercent(amp) {
  return Math.round(((amp - MIN_AMP) / (MAX_AMP - MIN_AMP)) * 100);
}

function ampToVpp(amp) {
  if (amp <= 0) return 0;
  return 0.0651 * amp + 0.2038;
}

function ampToTelexLevel(amp) {
  const value = clampNumber(amp, MIN_AMP, MAX_AMP);
  if (value <= 25) return Math.round(value * 18 / 25);
  if (value <= 47) return Math.round(18 + (value - 25) * 22 / 22);
  return Math.round(40 + (value - 47) * 37 / 53);
}

function isAw86234Board() {
  return hapticBoard === "telex" || hapticBoard === "c6" || hapticBoard === "telex-v1";
}

function shortDurationForBoard() {
  return isAw86234Board() ? TELEX_SHORT_DURATION_MS : SHORT_DURATION_MS;
}

function clampNumber(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

function sensorPointsToNewtons(points) {
  const raw = Math.max(0, Number(points) || 0);
  // Provisional two-point calibration: 10 points ≈ 0.5 N; 140 points ≈ 15 N.
  return Math.min(SENSOR_MAX_FORCE_N, raw / 20 + 8 * raw * (raw - 10) / 18200);
}

function fingerPressureFromState(state, key) {
  const calibrated = Number(state?.matrix_stats?.[key]?.peak_n);
  if (Number.isFinite(calibrated) && calibrated >= 0) return Math.min(SENSOR_MAX_FORCE_N, calibrated);
  const cells = (state?.matrices?.[key] || []).flat().map(Number).filter(Number.isFinite);
  return sensorPointsToNewtons(Math.max(0, ...cells));
}

function amplitudeForPressure(level) {
  // Keep a barely perceptible floor, then use the full remaining range for force contrast.
  return Math.round(clampNumber(selectedMaxAmp * (0.05 + 0.95 * clampNumber(level, 0, 1)), 0, MAX_AMP));
}

function frequencyForPressure(level) {
  if (hapticBoard === "telex-v1") return selectedMaxAmp > 0 ? 168 : 0;
  if (!selectedFrequency) return 0;
  const maximum = clampNumber(selectedFrequency, 1, 400);
  const minimum = isAw86234Board() ? 94 : Math.max(1, maximum * 0.35);
  if (maximum <= minimum) return Math.round(minimum);
  return Math.round(minimum + clampNumber(level, 0, 1) * (maximum - minimum));
}

function sensitivityConfig() {
  return SENSITIVITY_LEVELS[feedbackSensitivity] || SENSITIVITY_LEVELS[5];
}

function quantizeFeedbackLevel(level) {
  const step = sensitivityConfig().step;
  return Math.max(0, Math.min(1, Math.round(clampNumber(level, 0, 1) / step) * step));
}

function acceptFeedbackLevel(rawLevel, previousLevel) {
  const step = sensitivityConfig().step;
  return Math.abs(rawLevel - previousLevel) >= step - 0.001 ? quantizeFeedbackLevel(rawLevel) : previousLevel;
}

function updateSensitivityUI() {
  const config = sensitivityConfig();
  if (sensitivityValue) sensitivityValue.textContent = `${feedbackSensitivity} · ${config.label}`;
  if (sensitivityHelp) sensitivityHelp.textContent = `压力超过 ${config.threshold.toFixed(1)} N 启振；最快每 ${config.interval} ms 响应一次`;
}

function updateEffectParameterLabels() {
  if (hapticBoard === "telex-v1") {
    defaultParameters.textContent = `固定 168 Hz · AMP ${selectedMaxAmp} · ${sensitivityConfig().label}`;
    shortParameters.textContent = `预设短振 · 最长 200 ms · AMP ${selectedMaxAmp}`;
    longParameters.textContent = `固定 168 Hz · AMP ${selectedMaxAmp} · 按多久振多久`;
    frictionParameters.textContent = `固定 168 Hz · AMP ${selectedMaxAmp} · 按压力度调节`;
    collisionParameters.textContent = `≥6 N · 预设碰撞 · AMP ${selectedMaxAmp}`;
    return;
  }
  defaultParameters.textContent = `${selectedFrequency} Hz · AMP ${selectedMaxAmp} · ${sensitivityConfig().label}`;
  shortParameters.textContent = `${selectedFrequency} Hz · AMP ${selectedMaxAmp} · ${shortDurationForBoard()} ms`;
  longParameters.textContent = `${selectedFrequency} Hz · AMP ${selectedMaxAmp} · 按多久振多久`;
  frictionParameters.textContent = `${selectedFrequency} Hz · AMP ${selectedMaxAmp} · 按多久振多久`;
  collisionParameters.textContent = `>10 N · ${selectedFrequency} Hz · AMP ${selectedMaxAmp} · 28 ms`;
}

function updateSelectedModeText() {
  controlModeTitle.textContent = `${MODE_NAMES[selectedMode]} · ${hapticBoard === "telex-v1" ? 168 : selectedFrequency} Hz / AMP ${selectedMaxAmp}`;
  gestureHintText.textContent = MODE_HINTS[selectedMode];
  controlPad.classList.toggle("horizontal-mode", HORIZONTAL_MODES.has(selectedMode));
  controlPad.classList.toggle("force-monitor", selectedMode === "DEFAULT");
  controlPad.setAttribute("aria-disabled", String(selectedMode === "DEFAULT"));
  controlSectionLabel.textContent = selectedMode === "DEFAULT" ? "触觉实时监视" : "连续强度控制";

  if (selectedMode === "DEFAULT") {
    controlModeTitle.textContent = "默认 · 触觉实时力曲线";
    gestureHintText.textContent = "只读显示所选手指矩阵峰值换算力 · 此区域不发送控制指令";
    frequencyHelp.textContent = `浅色建议区 50–300 Hz；按压力度从 USB 板最低约 94 Hz 升到设定值`;
    amplitudeVoltageHint.textContent = `浅色建议区 AMP 25–47；按压力度使振幅在设定值附近变化`;
  } else if (HORIZONTAL_MODES.has(selectedMode)) {
    frequencyHelp.textContent = `浅色建议区 50–300 Hz；按压力度从 USB 板最低约 94 Hz 升到设定值`;
    amplitudeVoltageHint.textContent = `浅色建议区 AMP 25–47；按压力度使振幅在设定值附近变化`;
  } else if (selectedMode === "FRICTION") {
    frequencyHelp.textContent = `浅色建议区 50–300 Hz；摩擦按压力度从约 94 Hz 升到设定值`;
    amplitudeVoltageHint.textContent = `浅色建议区 AMP 25–47；摩擦时按压力度调幅`;
  } else {
    frequencyHelp.textContent = `浅色建议区 50–300 Hz；按压力度从 USB 板最低约 94 Hz 升到设定值`;
    amplitudeVoltageHint.textContent = `浅色建议区 AMP 25–47；按压力度使振幅在设定值附近变化`;
  }
}

function updateParameterPreview() {
  const minimumFrequency = 0;
  const maximumFrequency = 400;
  selectedFrequency = Math.round(clampNumber(frequencyNumber.value, minimumFrequency, maximumFrequency));
  selectedMaxAmp = Math.round(clampNumber(amplitudeNumber.value, MIN_AMP, MAX_AMP));
  frequencyRange.min = frequencyNumber.min = String(minimumFrequency);
  frequencyRange.max = frequencyNumber.max = String(maximumFrequency);
  frequencyRange.value = String(selectedFrequency);
  frequencyNumber.value = String(selectedFrequency);
  amplitudeRange.value = String(selectedMaxAmp);
  amplitudeNumber.value = String(selectedMaxAmp);
  amplitudeRange.max = amplitudeNumber.max = String(MAX_AMP);
  frequencyRange.disabled = hapticBoard === "telex-v1";
  frequencyNumber.disabled = hapticBoard === "telex-v1";
  amplitudeRange.disabled = false;
  amplitudeNumber.disabled = false;
  applyParametersButton.disabled = f0DetectionActive;
  detectF0Button.disabled = f0DetectionActive || hapticBoard === "telex-v1";
  if (!holding) liveFrequency = selectedFrequency;
  frequencyValue.textContent = String(liveFrequency);
  updateSelectedModeText();
  if (hapticBoard === "telex-v1") {
    frequencyHelp.textContent = "新固件连续驱动固定 nominal 168 Hz，暂不支持网页改频或 F0 测量";
    f0Result.textContent = "此固件未提供 F0 测量命令";
  }
  updateEffectParameterLabels();
  updateOutputFromNormalized(currentNormalized);
}

function updateLiveFrequency() {
  liveFrequency = frequencyForPressure(currentNormalized);
  frequencyValue.textContent = String(liveFrequency);
}

function updateOutputFromNormalized(normalized) {
  currentNormalized = Math.max(0, Math.min(1, normalized));
  currentAmp = amplitudeForPressure(currentNormalized);
  currentPercent = Math.round(currentNormalized * 100);
  currentForce = tactileSource ? (tactileActive ? tactilePressure : 0) : currentNormalized * MAX_FORCE_N;

  document.documentElement.style.setProperty("--level", `${100 - currentPercent}%`);
  controlPad.setAttribute("aria-valuenow", String(currentPercent));
  forceValue.textContent = currentForce.toFixed(1);
  ampValue.textContent = String(currentAmp);
  vppValue.textContent = hapticBoard === "telex-v1" ? String(Math.round(currentAmp * 40 / 75)) :
    isAw86234Board() ? String(ampToTelexLevel(currentAmp)) : ampToVpp(currentAmp).toFixed(2);
  if (outputUnit) outputUnit.textContent = isAw86234Board() ? "级" : "Vpp";
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
  if (deviceProtocol) deviceProtocol.disabled = connected;
  if (enableTelexHaptics && !connected) enableTelexHaptics.hidden = true;
  connectButton.classList.toggle("connected", connected);
  deviceOrb.classList.toggle("ready", deviceReady);

  if (!connected) {
    connectLabel.textContent = "连接设备";
    deviceState.textContent = "未连接";
    deviceDetail.textContent = "请选择设备串口";
  } else if (!deviceReady) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = deviceInitTimedOut ? "固件未响应" : "正在初始化";
    deviceDetail.textContent = deviceInitTimedOut ? "请检查设备类型与 USB 串口" : "正在与振动板握手";
  } else if (f0DetectionActive) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = "正在检测 F0";
    deviceDetail.textContent = `${f0DetectionFinger || '当前指尖'} · 请勿触碰马达`;
  } else if (modePreparationPending && !holding) {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = "正在准备模式";
    deviceDetail.textContent = `${MODE_NAMES[selectedMode]}参数正在预加载`;
  } else {
    connectLabel.textContent = "断开连接";
    deviceState.textContent = holding ? "持续振动中" : "设备就绪";
    const board = hapticBoard === "telex-v1" ? `S3 Mini · ${robotFinger} · TX v1` :
      hapticBoard === "c6" ? "C6 · 四路 CH4–CH7" :
      hapticBoard === "telex" ? `TELEX · 通道 ${telexChannelForFinger(robotFinger)}` : "LRA";
    deviceDetail.textContent = holding ? `${board} · AMP ${currentAmp}` : `${board} · 按住线条开始控制`;
  }
}

enableTelexHaptics?.addEventListener("click", async () => {
  if (!telexDevice || !deviceReady) return;
  enableTelexHaptics.disabled = true;
  try {
    await telexDevice.enableHaptics();
    showToast("振动配置已保存，等待设备重启");
  } catch (error) {
    appendLog(`TELEX 配置失败：${error.message}`);
    showToast(`配置失败：${error.message}`);
  } finally { enableTelexHaptics.disabled = false; }
});

function telexChannelForFinger(key) {
  const order = { thumb: 0, index: 1, middle: 2, ring: 3, little: 4 };
  return order[key] ?? 0;
}

function selectTelexChannel() {
  if (hapticBoard === "telex-v1") {
    if (telexDevice) telexDevice.selectedFinger = robotFinger;
    return;
  }
  if (hapticBoard !== "telex" || !connected || !writer) return;
  const channel = Math.min(telexChannelForFinger(robotFinger), hapticChannelCount - 1);
  queueLine(`CHANNEL ${channel}`).catch(error => showToast(`切换振动通道失败：${error.message}`));
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
  shortLed?.classList.toggle('short-playing', playingEffect === 'SHORT' || tactileTriggeredMode === 'SHORT');
}

function updatePressClock() {
  if (!holding && !tactileActive) return;
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

function telexNotice(type, value) {
  if (type === "error") { appendLog(`TELEX ERROR ${value}`); return; }
  if (type === "ready") {
    const { identity, capabilities } = value;
    hapticBoard = "telex-v1";
    hapticChannelCount = 5;
    deviceReady = true;
    deviceInitTimedOut = false;
    telexDevice.selectedFinger = robotFinger;
    enableTelexHaptics.hidden = Boolean(identity.configured && capabilities.haptics && capabilities.mode === 1);
    appendLog(`TELEX ${identity.chip} ${identity.role === 1 ? "左手" : "右手"} · 马达在线掩码 0x${capabilities.motors.toString(16)}`);
    if (!enableTelexHaptics.hidden) appendLog("振动未启用；点击“启用此板振动”写入设备配置");
    updateParameterPreview();
    updateConnectionUI();
    showToast("ESP32-S3 Mini 已连接");
    return;
  }
  if (type === "reboot") {
    deviceReady = false;
    stopHeartbeat();
    setHoldingUI(false);
    appendLog("TELEX 设备已重启，正在重新识别");
    telexDevice.initialize().catch(error => appendLog(`TELEX ERROR ${error.message}`));
    return;
  }
  if (type === "status") {
    if (value.faults && value.faults !== telexNotice.lastFaults)
      appendLog(`TELEX 马达故障掩码 0x${value.faults.toString(16)}`);
    telexNotice.lastFaults = value.faults;
    return;
  }
  if (type === "output") {
    tactileOutputConfirmed = true;
    updateTactileMotorStatus();
  }
  if (type === "finiteDone") processDeviceLine(`EVENT EFFECT ${selectedMode} DONE`);
  if (type === "configured") appendLog("TELEX 配置已写入并校验；设备即将重启");
}

function queueTelexCommand(line) {
  if (!telexDevice) return Promise.reject(new Error("TELEX 设备未连接"));
  const parts = line.trim().split(/\s+/), name = parts[0];
  if (name === "STOP" || name === "LONG_RELEASE") {
    telexMotionGeneration++;
    return telexDevice.stop();
  }
  const generation = telexMotionGeneration;
  const motion = /^(START|HOLD|FRICTION_START|FRICTION_HOLD|LONG_START|LONG_HOLD|PULSE|COLLISION|FIVE)$/.test(name);
  telexCommandQueue = telexCommandQueue.catch(() => undefined).then(async () => {
    if (motion && generation !== telexMotionGeneration) return;
    if (name === "HELLO" || name === "PREP" || name === "PARAM") return;
    if (name === "CHANNEL") {
      telexDevice.selectedFinger = Object.keys(TelexProtocol.FINGERS).find(key => TelexProtocol.FINGERS[key] === Number(parts[1])) || "index";
      return;
    }
    if (name === "DETECT_F0") throw new Error("S3 固件未提供 F0 检测");
    if (name === "FIVE") {
      const levels = parts.slice(1, 6).map(Number);
      return telexDevice.update(levels);
    }
    if (name === "PULSE" || name === "COLLISION") {
      const amp = Number(parts[name === "PULSE" ? 2 : 3]);
      const duration = name === "PULSE" ? Number(parts[3]) : 60;
      const effect = name === "PULSE" ? "SHORT" : "COLLISION";
      await telexDevice.finite(amp, duration, name === "PULSE" ? 0 : 3);
      processDeviceLine(`EVENT EFFECT ${effect} START`);
      return;
    }
    const amp = Number(parts[name === "LONG_START" || name === "LONG_HOLD" ? 2 : 1]);
    if (!Number.isFinite(amp) || amp <= 0) return telexDevice.stop();
    await telexDevice.single(amp);
    if (name.endsWith("START") || name === "START") processDeviceLine(`EVENT START 168 ${amp}`);
  });
  return telexCommandQueue;
}

function queueLine(line) {
  if (telexDevice) return queueTelexCommand(line);
  if (!writer) return Promise.reject(new Error("Serial port is not connected"));

  if (line === 'STOP') {
    motionGeneration += 1;
    if (tactilePreloadPending) {
      tactilePreloadPending = false;
      confirmedMotorOutput = null;
    }
  }
  const generation = motionGeneration;
  const motionCommand = /^(START|HOLD|FRICTION_START|FRICTION_HOLD|LONG_START|LONG_HOLD|LONG_RELEASE|PULSE|COLLISION|PREP)(?: |$)/.test(line);
  writeQueue = writeQueue
    .catch(() => undefined)
    .then(() => {
      if (motionCommand && generation !== motionGeneration) return;
      if (!/^(FRICTION_HOLD|LONG_HOLD|HOLD) /.test(line)) appendLog(`TX ${line}`);
      if (line === 'STOP') pendingStopReplies += 1;
      return writer.write(encoder.encode(`${line}\n`)).catch(error => {
        if (line === 'STOP') pendingStopReplies = Math.max(0, pendingStopReplies - 1);
        throw error;
      });
    });

  return writeQueue;
}

function prepareSelectedMode() {
  if (!connected || !deviceReady || f0DetectionActive) return;
  if (hapticBoard === "telex-v1") {
    modePreparationPending = false;
    updateConnectionUI();
    return;
  }

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

function f0ChannelForFinger(key) {
  if (hapticBoard === 'c6') return ({ index: 4, middle: 5, ring: 6, little: 7 })[key] ?? null;
  return telexChannelForFinger(key);
}

function finishF0Detection(message, failed = false) {
  clearTimeout(f0DetectionTimer);
  f0DetectionTimer = null;
  f0DetectionActive = false;
  f0Result.textContent = message;
  detectF0Button.disabled = false;
  applyParametersButton.disabled = false;
  updateConnectionUI();
  updateTactileMotorStatus();
  if (failed) showToast(message);
}

function detectF0() {
  if (hapticBoard === "telex-v1") { showToast("S3 固件未提供 F0 测量命令"); return; }
  if (!connected || !deviceReady) { showToast('请先连接并等待设备就绪'); return; }
  if (f0DetectionActive) return;
  const channel = f0ChannelForFinger(robotFinger);
  const fingerName = ROBOT_FINGERS.find(finger => finger.key === robotFinger)?.name || '当前指尖';
  if (channel === null) { showToast(`${fingerName}没有可用的振动通道`); return; }
  if (holding || playingEffect || robotHapticActive) stopButton.click();
  f0DetectionActive = true;
  f0DetectionFinger = fingerName;
  detectF0Button.disabled = true;
  applyParametersButton.disabled = true;
  f0Result.textContent = `${fingerName}检测中…`;
  updateConnectionUI();
  queueLine(`DETECT_F0 ${channel}`).catch(error => finishF0Detection(error.message, true));
  f0DetectionTimer = setTimeout(() => {
    if (f0DetectionActive) finishF0Detection(`${fingerName} F0 检测超时`, true);
  }, 5000);
}

function processDeviceLine(line) {
  appendLog(line);
  const boardEvent = line.match(/^EVENT BOARD (TELEX_CORE_LEFT_V2|AW86234_C6_8CH) (\d+)/);
  if (boardEvent) {
    hapticBoard = boardEvent[1] === "AW86234_C6_8CH" ? "c6" : "telex";
    hapticChannelCount = Math.max(1, Number(boardEvent[2]) || 5);
    updateParameterPreview();
    updateConnectionUI();
  }
  const recoverableHostFragment = /EVENT (?:ERROR UNKNOWN_COMMAND|WARN DROPPED_INCOMPLETE_HOST_COMMAND) (?:START|HOLD|LONG_HOLD|FRICTION_HOLD)(?:\s|$)/.test(line);
  const confirmed = line.match(/^EVENT (?:OUTPUT|PREP|PARAM|START) (\d+) (\d+)/);
  if (confirmed) confirmedMotorOutput = { frequency: Number(confirmed[1]), amplitude: Number(confirmed[2]) };
  if (/^EVENT PREP /.test(line) && tactilePreloadPending) {
    tactilePreloadPending = false;
    modePreparationPending = false;
    updateConnectionUI();
  }
  if (!recoverableHostFragment && /EVENT ERROR/.test(line)) {
    confirmedMotorOutput = null;
    tactilePreloadPending = false;
  }
  const f0Event = line.match(/^EVENT F0 (\d+) (\d+)$/);
  if (f0Event && f0DetectionActive) {
    const hz = Number(f0Event[2]) / 10;
    finishF0Detection(`${f0DetectionFinger || '当前指尖'}：${hz.toFixed(1)} Hz`);
    showToast(`F0 检测完成：${hz.toFixed(1)} Hz`);
  }
  if (f0DetectionActive && /^EVENT ERROR F0_/.test(line)) {
    finishF0Detection(`${f0DetectionFinger || '当前指尖'} F0 检测失败`, true);
  }

  if (line.includes("EVENT READY")) {
    deviceReady = true;
    deviceInitTimedOut = false;
    deviceHandshakeTimers.forEach(clearTimeout);
    deviceHandshakeTimers = [];
    updateConnectionUI();
    selectTelexChannel();
    prepareSelectedMode();
    showToast(hapticBoard === "c6" ? "C6 四路振动板已连接 · 食/中/无名/小指独立联动" :
      hapticBoard === "telex" ? "TELEX 五路振动板已连接" : "LRA 已连接，正在预加载当前模式");
  }

  if (line.includes("EVENT STOP")) {
    const expectedReply = line.trim() === 'EVENT STOP' && pendingStopReplies > 0;
    if (expectedReply) pendingStopReplies -= 1;
    // A reply to the previous press may arrive after the new START was queued.
    // Keep the new press's heartbeat alive rather than timing out the motor.
    if (expectedReply && tactileMotorAllowed && tactileActive && tactilePressure > 0 && holding) return;
    if (tactileMotorAllowed && tactileActive) endTactile();
    stopHeartbeat();
    stopPressClock();
    setHoldingUI(false);
    playingEffect = null;
    setEffectButtonsBusy(false);
    refreshEffectLights();
  }

  if (line.includes("EVENT ERROR") && !recoverableHostFragment) {
    modePreparationPending = false;
    setEffectButtonsBusy(false);
    applyParametersButton.disabled = false;
    updateConnectionUI();
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

  const startEvent = line.match(/^EVENT START (\d+) (\d+)/);
  if (startEvent && tactileMotorAllowed && tactileActive && holding) {
    tactileOutputConfirmed = true;
    updateTactileMotorStatus();
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

  const effectEvent = line.match(/EVENT EFFECT (SHORT|LONG|FRICTION|COLLISION) (START|DONE|CANCELLED)/);
  if (effectEvent) {
    if (effectEvent[2] === "START") {
      modePreparationPending = false;
      playingEffect = effectEvent[1];
      if (effectEvent[1] === 'SHORT') shortLed?.classList.add('short-playing');
      setEffectButtonsBusy(true);
    } else {
      playingEffect = null;
      if (effectEvent[1] === 'SHORT') {
        setTimeout(() => shortLed?.classList.remove('short-playing'), 450);
      }
      if (effectEvent[1] === 'LONG') longLed?.classList.remove('long-recognized');
      setEffectButtonsBusy(false);
      if (effectEvent[2] === "DONE") showToast("振动效果播放完成");
    }
    if (tactileSource && tactileTriggeredMode === effectEvent[1]) {
      tactileOutputConfirmed = effectEvent[2] === 'START';
      if (effectEvent[2] !== 'START') {
        tactileEffectCompleted = true;
        stopHeartbeat();
        setHoldingUI(false);
      }
      updateTactileMotorStatus();
    }
    refreshEffectLights();
  }
}

function setEffectButtonsBusy(busy) {
  effectButtons.forEach(button => button.setAttribute("aria-busy", String(busy)));
}

function selectMode(mode) {
  if (tactileActive) endTactile();
  if (robotHapticActive) stopRobotHaptic();
  robotHapticTriggeredMode = null;
  if (holding || playingEffect) {
    stopHeartbeat();
    stopPressClock();
    setHoldingUI(false);
    if (connected) queueLine("STOP").catch(() => undefined);
  }

  selectedMode = mode;
  pressTimeValue.textContent = "0";
  playingEffect = null;
  shortLed?.classList.remove('short-playing');
  updateParameterPreview();
  refreshEffectLights();
  prepareSelectedMode();
  updateTactileMotorStatus();
  updateDefaultForceMonitor();
  showToast(`已选择${effectButtons.find(button => button.dataset.effect === mode)?.querySelector("strong")?.textContent || "振动"}模式`);
}

function applyParameters() {
  updateParameterPreview();

  if (hapticBoard === "telex-v1") {
    showToast("已应用网页强度上限；S3 固件频率固定 168 Hz");
    return;
  }

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

            if (telexDevice) { telexDevice.feed(value); continue; }
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
    deviceInitTimedOut = false;
    hapticBoard = "legacy";
    hapticChannelCount = 1;
    serialLog.textContent = "串口已连接，设备正在初始化…";
    updateConnectionUI();
    if (deviceProtocol?.value === "telex-v1") {
      if (!window.TelexProtocol) throw new Error("TELEX 协议脚本未加载");
      telexDevice = new TelexProtocol.TelexDevice(bytes => writer.write(bytes), telexNotice);
      readSerialLoop();
      telexDevice.initialize().catch(error => {
        deviceInitTimedOut = true;
        appendLog(`TELEX 握手失败：${error.message}`);
        updateConnectionUI();
      });
      return;
    }
    readSerialLoop();
    deviceHandshakeTimers.forEach(clearTimeout);
    deviceHandshakeTimers = [250, 800, 1600, 3000].map(delay => setTimeout(() => {
      if (connected && !deviceReady) queueLine("HELLO").catch(() => undefined);
    }, delay));
    deviceHandshakeTimers.push(setTimeout(() => {
      if (!connected || deviceReady) return;
      deviceInitTimedOut = true;
      appendLog("未收到 EVENT READY：请断开后改选另一块 Espressif USB 串口（COM49 / COM59）");
      updateConnectionUI();
    }, 5000));
  } catch (error) {
    if (error.name !== "NotFoundError") {
      showToast(`连接失败：${error.message}`);
    }
  }
}

async function disconnectSerial() {
  if (tactileActive) endTactile();
  stopHeartbeat();
  setHoldingUI(false);

  try {
    if (writer) {
      await queueLine("STOP").catch(() => undefined);
      telexDevice?.close();
      telexDevice = null;
      writer.releaseLock();
      writer = null;
    }

    keepReading = false;
    if (reader) await reader.cancel().catch(() => undefined);
    if (port) await port.close();
  } finally {
    port = null;
    telexDevice?.close();
    telexDevice = null;
    connected = false;
    deviceReady = false;
    deviceInitTimedOut = false;
    deviceHandshakeTimers.forEach(clearTimeout);
    deviceHandshakeTimers = [];
    pendingStopReplies = 0;
    confirmedMotorOutput = null;
    tactilePreloadPending = false;
    modePreparationPending = false;
    f0DetectionActive = false;
    f0DetectionFinger = null;
    clearTimeout(f0DetectionTimer);
    f0DetectionTimer = null;
    detectF0Button.disabled = false;
    applyParametersButton.disabled = false;
    updateConnectionUI();
  }
}

function sendLiveUpdate(forceKeepAlive = false) {
  if (!holding || !connected || heartbeatWritePending ||
      !["DEFAULT", "LONG", "FRICTION"].includes(selectedMode)) return;

  const now = performance.now();
  const rawLevel = tactileSource ? tactileFeedbackLevel() : currentNormalized;
  continuousTargetLevel = acceptFeedbackLevel(rawLevel, continuousTargetLevel);
  const outputChanged = Math.abs(continuousTargetLevel - continuousOutputLevel) > 0.001;
  if (outputChanged && now - lastLiveUpdateSentAt < sensitivityConfig().interval) {
    if (forceKeepAlive && lastLiveCommand) queueLine(lastLiveCommand).catch(() => undefined);
    return;
  }
  if (outputChanged) continuousOutputLevel = continuousTargetLevel;

  const smoothAmp = amplitudeForPressure(continuousOutputLevel);
  const smoothFrequency = frequencyForPressure(continuousOutputLevel);
  const command = selectedMode === "DEFAULT" ? `HOLD ${smoothAmp} ${smoothFrequency}` :
    selectedMode === "LONG" ? `LONG_HOLD ${smoothFrequency} ${smoothAmp}` :
    `HOLD ${smoothAmp} ${smoothFrequency}`;
  if (!forceKeepAlive && (!outputChanged || command === lastLiveCommand)) return;

  liveFrequency = smoothFrequency;
  frequencyValue.textContent = String(liveFrequency);

  lastLiveUpdateSentAt = now;
  lastLiveCommand = command;
  heartbeatWritePending = true;
  queueLine(command)
    .catch(() => undefined)
    .finally(() => { heartbeatWritePending = false; });
}

function startHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  heartbeatWritePending = false;
  lastLiveUpdateSentAt = 0;
  lastLiveCommand = "";
  heartbeatTimer = setInterval(() => sendLiveUpdate(true), HEARTBEAT_MS);
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  heartbeatWritePending = false;
  lastLiveCommand = "";
  continuousTargetLevel = 0;
  continuousOutputLevel = 0;
}

function updateDefaultForceMonitor() {
  if (selectedMode !== "DEFAULT") return;
  const force = Math.max(0, Number(liveTactilePressure) || 0);
  currentForce = force;
  currentPercent = Math.round(Math.min(1, force / 15) * 100);
  forceValue.textContent = force.toFixed(1);
  pressTimeValue.textContent = "—";
  ampValue.textContent = "—";
  vppValue.textContent = "—";
  frequencyValue.textContent = "—";
  rangeFill.style.width = `${currentPercent}%`;
  holdBadge.textContent = robotState?.connected ? "实时监视" : "等待数据";
  holdBadge.classList.toggle("active", Boolean(robotState?.connected));
}

async function beginHold(event) {
  if (selectedMode === "DEFAULT") {
    showToast("默认模式下左侧为实时触觉监视，不发送控制指令");
    return;
  }
  if (tactileActive) return;
  tactileSource = false;
  controlPad.classList.remove("tactile-preview");
  setIntensityFromY(event.clientY);

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
  if (!selectedFrequency || !selectedMaxAmp || !liveFrequency || !currentAmp) {
    showToast("频率或振幅为 0，振动已关闭");
    return;
  }

  controlPad.setPointerCapture(event.pointerId);
  startPressClock();
  setHoldingUI(true);
  updateLiveFrequency();

  let command;
  switch (selectedMode) {
    case "DEFAULT":
      continuousTargetLevel = quantizeFeedbackLevel(currentNormalized);
      continuousOutputLevel = continuousTargetLevel;
      liveFrequency = frequencyForPressure(continuousOutputLevel);
      command = `START ${amplitudeForPressure(continuousOutputLevel)} ${liveFrequency}`;
      break;
    case "SHORT":
      command = `PULSE ${liveFrequency} ${currentAmp} ${shortDurationForBoard()}`;
      break;
    case "LONG":
      command = `LONG_START ${liveFrequency} ${currentAmp}`;
      break;
    case "COLLISION":
      command = `COLLISION ${Math.round(currentForce * 10)} ${liveFrequency} ${currentAmp}`;
      break;
    case "FRICTION":
      command = `START ${currentAmp} ${liveFrequency}`;
      break;
    default:
      return;
  }

  await queueLine(command).catch(error => showToast(error.message));
  if (["DEFAULT", "LONG", "FRICTION"].includes(selectedMode)) startHeartbeat();
}

function moveHold(event) {
  if (selectedMode === "DEFAULT") return;
  if (tactileSource) return;
  hoverInside = true;
  if (holding && HORIZONTAL_MODES.has(selectedMode)) return;
  setIntensityFromY(event.clientY);
  if (holding && ["DEFAULT", "FRICTION"].includes(selectedMode)) sendLiveUpdate();
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

  if (selectedMode === "DEFAULT") {
    queueLine("STOP").catch(() => undefined);
  } else if (selectedMode === "LONG") {
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
  const monitoring = selectedMode === "DEFAULT";
  const plotLeft = monitoring ? 38 : 0;
  const plotRight = monitoring ? 8 : 0;
  const target = selectedMode === "DEFAULT"
    ? Math.max(0, Math.min(1, liveTactilePressure / 15))
    : tactileSource ? (tactileActive ? tactilePressure / 15 : 0) : (holding ? currentPercent / 100 : 0);
  const previous = traceHistory.at(-1) ?? 0;
  const smoothed = previous + (target - previous) * 0.24;
  traceHistory.push(smoothed);
  traceHistory.shift();

  context.clearRect(0, 0, width, height);

  context.strokeStyle = "rgba(255,255,255,.055)";
  context.lineWidth = 1;
  for (let i = 1; i < (monitoring ? 3 : 4); i += 1) {
    const y = monitoring ? 22 + (height - 44) * i / 3 : (height / 4) * i;
    context.beginPath();
    context.moveTo(plotLeft, y);
    context.lineTo(width - plotRight, y);
    context.stroke();
  }

  if (monitoring) {
    const top = 22, bottom = height - 22;
    context.strokeStyle = "rgba(255,255,255,.18)";
    context.beginPath();
    context.moveTo(plotLeft, top);
    context.lineTo(plotLeft, bottom);
    context.lineTo(width - plotRight, bottom);
    context.stroke();
    context.fillStyle = "rgba(184,197,216,.78)";
    context.font = "10px Segoe UI, sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    [15, 10, 5, 0].forEach(value => {
      const y = bottom - value / 15 * (bottom - top);
      context.fillText(String(value), plotLeft - 7, y);
    });
  }

  const gradient = context.createLinearGradient(0, 0, width, 0);
  gradient.addColorStop(0, "rgba(101,119,255,0)");
  gradient.addColorStop(.22, "rgba(101,119,255,.72)");
  gradient.addColorStop(1, "rgba(77,226,255,.95)");

  context.beginPath();
  traceHistory.forEach((value, index) => {
    const x = plotLeft + (index / (traceHistory.length - 1)) * (width - plotLeft - plotRight);
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

defaultButton.addEventListener("click", () => {
  selectMode("DEFAULT");
});

shortButton.addEventListener("click", () => {
  selectMode("SHORT");
});

longButton.addEventListener("click", () => {
  selectMode("LONG");
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
sensitivityRange?.addEventListener("input", () => {
  feedbackSensitivity = Math.round(clampNumber(sensitivityRange.value, 1, 5));
  updateSensitivityUI();
  updateSelectedModeText();
  updateEffectParameterLabels();
});
applyParametersButton.addEventListener("click", applyParameters);
detectF0Button.addEventListener("click", detectF0);

stopButton.addEventListener("click", () => {
  stopHeartbeat();
  stopPressClock();
  setHoldingUI(false);
  playingEffect = null;
  longHoldRecognized = false;
  pressureHeldSince = 0;
  longLed?.classList.remove('long-recognized');
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

updateSensitivityUI();
updateParameterPreview();
setIntensityFromY(controlPad.getBoundingClientRect().top + controlPad.clientHeight / 2);
updateDefaultForceMonitor();
resizeCanvas();
drawTrace();
updateConnectionUI();

const tactilePad = document.querySelector('#tactilePad');
const tactileCanvas = document.querySelector('#tactileCanvas');
const tactileContext = tactileCanvas.getContext('2d');
const heatCanvas = document.querySelector('#heatCanvas');
const heatContext = heatCanvas.getContext('2d');
const tactileForceLabel = document.querySelector('#tactileForce');
const tactileForceTitle = document.querySelector('#tactileForceTitle');
const tactileStatus = document.querySelector('#tactileStatus');
const tactilePoint = document.querySelector('#tactilePoint');
const tactileDirection = document.querySelector('#tactileDirection');
const tactileVibration = document.querySelector('#tactileVibration');
const singleForceValue = document.querySelector('#singleForceValue');
const singleAmpValue = document.querySelector('#singleAmpValue');
const singleFrequencyValue = document.querySelector('#singleFrequencyValue');
const singleFingerChartTitle = document.querySelector('#singleFingerChartTitle');
const singleFingerForceChart = document.querySelector('#singleFingerForceChart');
const totalDirectionArrow = document.querySelector('#totalDirectionArrow');
const totalDirectionText = document.querySelector('#totalDirectionText');
const tactileMotorStatus = document.querySelector('#tactileMotorStatus');
const robotHapticToggle = document.querySelector('#robotHapticToggle');
const heatLegendMax = document.querySelector('#heatLegendMax');
const heatLegendMiddle = document.querySelector('#heatLegendMiddle');
const heatLegendHigh = document.querySelector('#heatLegendHigh');
let tactileInput = 'mouse';
let manualTactilePressure = 4;
let rawPenPressure = null;
let tactilePointer = null;
let tactileCaptureTarget = null;
let tactileRows = 12;
let tactileColumns = 6;
let tactilePosition = { x: 2.5, y: 5.5 };
let tactileLiveMode = true;
let robotHapticEnabled = true;
let robotHapticActive = false;
let robotHapticLastSent = 0;
let robotHapticTargetLevel = 0;
let robotHapticOutputLevel = 0;
let robotHapticTriggeredMode = null;
let frictionMotionCenter = null;
let frictionLastMotionAt = 0;
let frictionRecognized = false;
let longHoldRecognized = false;
let pressureHeldSince = 0;
let autoContactSince = 0;
let autoCollisionTriggered = false;
let frictionDirection = null;
let frictionTravel = 0;
const simulationHistory = Array(FORCE_CHART_HISTORY).fill(0);
let simulationTargetPressure = 0;
let simulationInputTimer = null;

function recordSimulationPressure(pressure) {
  simulationHistory.push(clampNumber(pressure, 0, SENSOR_MAX_FORCE_N));
  if (simulationHistory.length > FORCE_CHART_HISTORY) simulationHistory.shift();
  drawRobotForceChart(robotFinger, singleFingerForceChart);
}

function stopSimulationInputLoop() {
  if (simulationInputTimer !== null) clearInterval(simulationInputTimer);
  simulationInputTimer = null;
}

function startSimulationInputLoop() {
  stopSimulationInputLoop();
  simulationInputTimer = setInterval(() => {
    if (tactileLiveMode || !tactileActive || tactilePointer === null) {
      stopSimulationInputLoop();
      return;
    }
    const difference = simulationTargetPressure - tactilePressure;
    tactilePressure = Math.abs(difference) < .02
      ? simulationTargetPressure
      : tactilePressure + difference * .24;
    recordSimulationPressure(tactilePressure);
    syncTactile();
    syncTactileMotor();
  }, 32);
}

function updateFrictionRecognition(matrix) {
  if (!frictionLed || !Array.isArray(matrix) || !matrix.length) return;
  let weight = 0, x = 0, y = 0;
  matrix.forEach((row, rowIndex) => (Array.isArray(row) ? row : []).forEach((value, columnIndex) => {
    const pressure = Math.max(0, Number(value) || 0);
    weight += pressure;
    x += columnIndex * pressure;
    y += rowIndex * pressure;
  }));
  if (weight < 3) {
    frictionMotionCenter = null;
    frictionRecognized = false;
    frictionLastMotionAt = 0;
    frictionDirection = null;
    frictionTravel = 0;
    frictionLed.classList.remove('friction-recognized');
    return;
  }
  if (frictionLastMotionAt && Date.now() - frictionLastMotionAt > 300 && !frictionRecognized) {
    frictionDirection = null;
    frictionTravel = 0;
  }
  const center = { x: x / weight, y: y / weight };
  if (frictionMotionCenter) {
    const dx = center.x - frictionMotionCenter.x;
    const dy = center.y - frictionMotionCenter.y;
    const shift = Math.hypot(dx, dy);
    if (shift >= 0.03) {
      const direction = { x: dx / shift, y: dy / shift };
      const aligned = frictionDirection && (direction.x * frictionDirection.x + direction.y * frictionDirection.y) > 0.55;
      frictionTravel = aligned ? frictionTravel + shift : shift;
      frictionDirection = direction;
      if (frictionTravel >= 0.18) {
        frictionRecognized = true;
        longHoldRecognized = false;
        longLed?.classList.remove('long-recognized');
      }
      frictionLastMotionAt = Date.now();
    }
  }
  frictionMotionCenter = center;
  if (frictionRecognized) {
    longHoldRecognized = false;
    longLed?.classList.remove('long-recognized');
  }
  frictionLed.classList.toggle('friction-recognized', frictionRecognized);
}

setInterval(() => {
  if (frictionLed && frictionRecognized && frictionLastMotionAt && Date.now() - frictionLastMotionAt >= 600) {
    frictionRecognized = false;
    frictionLed.classList.remove('friction-recognized');
    if (liveTactilePressure > 0.05) {
      longHoldRecognized = true;
      longLed?.classList.add('long-recognized');
    }
  }
}, 200);

// Pressure controls the activation threshold and scales up to the global amplitude limit.
function tactileFeedbackLevel(pressure = tactilePressure) {
  const deadZone = sensitivityConfig().threshold;
  const force = clampNumber(pressure, 0, SENSOR_MAX_FORCE_N);
  if (force <= deadZone) return 0;
  const lowForceBoundary = 5;
  const levelAtBoundary = 0.25;
  if (force < lowForceBoundary) {
    const lowRange = Math.max(0.001, lowForceBoundary - deadZone);
    const lowProgress = (force - deadZone) / lowRange;
    return levelAtBoundary * lowProgress * lowProgress;
  }
  const highProgress = (force - lowForceBoundary) / (SENSOR_MAX_FORCE_N - lowForceBoundary);
  return levelAtBoundary + highProgress * (1 - levelAtBoundary);
}

function pressureColor(force) {
  const lightTheme = document.documentElement.dataset.theme === 'light';
  const stops = lightTheme
    ? [
        [0, [227,232,238]], [1.8, [204,218,234]], [4.05, [142,175,210]],
        [6.45, [71,127,188]], [8.55, [32,88,148]], [10.5, [18,63,121]],
        [12.3, [11,53,109]], [15, [8,43,89]]
      ]
    : [
        [0, [23,68,204]], [.3, [48,85,235]], [.7, [25,132,246]],
        [1.2, [15,188,238]], [1.7, [22,215,211]], [2.2, [39,210,146]],
        [2.7, [114,218,84]], [3.3, [197,225,65]], [4, [249,211,75]],
        [6, [255,175,46]], [9, [255,126,35]], [12, [250,83,39]], [15, [240,68,56]]
      ];
  const pressure = Math.max(0, Math.min(15, force));
  const index = Math.max(0, stops.findIndex((stop, i) => i < stops.length - 1 && pressure <= stops[i + 1][0]));
  const [low, color] = stops[index];
  const [high, next] = stops[index + 1];
  return `rgb(${color.map((v, i) => Math.round(v + (next[i] - v) * (pressure - low) / (high - low))).join(',')})`;
}

function tactileVector(column, row) {
  if (!tactileActive || Math.abs(column - Math.floor(tactilePosition.x)) > 1 ||
      Math.abs(row - Math.floor(tactilePosition.y)) > 1) return null;
  return { x: tactilePosition.x - column - .5, y: tactilePosition.y - row - .5 };
}

function tactileSample(column, row) {
  if (!tactileActive) return 0;
  const centerColumn = Math.floor(tactilePosition.x);
  const centerRow = Math.floor(tactilePosition.y);
  if (Math.abs(column - centerColumn) > 1 || Math.abs(row - centerRow) > 1) return 0;
  const distance = (column + .5 - tactilePosition.x) ** 2 + (row + .5 - tactilePosition.y) ** 2;
  return tactilePressure * Math.exp(-distance / 1.25);
}

function liveMatrixSummary() {
  let sum = 0, peak = 0, peakRow = -1, peakColumn = -1, weightedX = 0, weightedY = 0;
  for (let row = 0; row < tactileRows; row++) for (let column = 0; column < tactileColumns; column++) {
    const raw = Math.max(0, Number(liveTactileMatrix[row]?.[column]) || 0);
    sum += raw;
    weightedX += (column + .5) * raw;
    weightedY += (row + .5) * raw;
    if (raw > peak) { peak = raw; peakRow = row; peakColumn = column; }
  }
  return { sum, peak, peakRow, peakColumn,
    centerX: sum ? weightedX / sum : tactileColumns / 2,
    centerY: sum ? weightedY / sum : tactileRows / 2 };
}

function displayedPressure(column, row) {
  return tactileLiveMode
    ? sensorPointsToNewtons(liveTactileMatrix[row]?.[column])
    : tactileSample(column, row);
}

function prepareTactileCanvas(target, ctx) {
  const width = target.clientWidth, height = target.clientHeight;
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  target.width = Math.round(width * scale);
  target.height = Math.round(height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  return { width, height };
}

function drawTactile() {
  const ctx = tactileContext;
  const { width, height } = prepareTactileCanvas(tactileCanvas, ctx);
  const cw = width / tactileColumns, ch = height / tactileRows;
  const lightTheme = document.documentElement.dataset.theme === 'light';
  ctx.fillStyle = lightTheme ? '#f4f7fa' : '#0c1420';
  ctx.fillRect(0, 0, width, height);
  const cellSize = Math.min(cw, ch);
  const dotRadius = Math.max(1, Math.min(1.8, cellSize * .055));
  const activeRadius = Math.min(12, cellSize * .30);
  const liveSummary = tactileLiveMode ? liveMatrixSummary() : null;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (let row = 0; row < tactileRows; row++) for (let col = 0; col < tactileColumns; col++) {
    const x = (col + .5) * cw, y = (row + .5) * ch;
    const localPressure = displayedPressure(col, row);
    const vector = tactileLiveMode
      ? (localPressure > 0 ? { x: liveSummary.centerX - col - .5, y: liveSummary.centerY - row - .5 } : null)
      : tactileVector(col, row);
    if (!vector) {
      // Quiet reference points keep dense grids readable at either size.
      ctx.fillStyle = lightTheme ? 'rgba(66, 88, 116, .38)' : 'rgba(148, 168, 192, .36)';
      ctx.beginPath(); ctx.arc(x, y, dotRadius, 0, Math.PI * 2); ctx.fill();
      continue;
    }
    ctx.fillStyle = lightTheme ? 'rgba(8, 127, 155, .07)' : 'rgba(112, 210, 226, .055)';
    ctx.strokeStyle = lightTheme ? 'rgba(8, 127, 155, .32)' : 'rgba(139, 207, 220, .24)';
    ctx.lineWidth = .8;
    ctx.beginPath(); ctx.arc(x, y, activeRadius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    if (Math.hypot(vector.x, vector.y) > .08) {
      ctx.save(); ctx.translate(x, y); ctx.rotate(Math.atan2(vector.y * ch, vector.x * cw));
      const referencePressure = tactileLiveMode ? Math.max(.001, liveTactilePressure) : tactilePressure;
      const localLevel = referencePressure > 0 ? Math.sqrt(Math.min(1, localPressure / referencePressure)) : 0;
      const tip = activeRadius * (.22 + .46 * localLevel);
      const tail = -activeRadius * (.18 + .34 * localLevel);
      const head = activeRadius * (.17 + .13 * localLevel);
      ctx.strokeStyle = lightTheme
        ? `rgba(8, 127, 155, ${(.35 + .6 * localLevel).toFixed(3)})`
        : `rgba(160, 225, 235, ${(.28 + .7 * localLevel).toFixed(3)})`;
      ctx.lineWidth = .7 + 1.1 * localLevel;
      ctx.beginPath(); ctx.moveTo(tail, 0); ctx.lineTo(tip, 0);
      ctx.lineTo(tip - head, -head); ctx.moveTo(tip, 0); ctx.lineTo(tip - head, head);
      ctx.stroke(); ctx.restore();
    }
  }
  const showCenter = tactileLiveMode ? liveSummary.peak > 0 : tactileActive;
  if (showCenter) {
    const centerX = tactileLiveMode ? liveSummary.centerX : tactilePosition.x;
    const centerY = tactileLiveMode ? liveSummary.centerY : tactilePosition.y;
    const x = centerX * cw, y = centerY * ch;
    ctx.strokeStyle = lightTheme ? 'rgba(8, 91, 112, .72)' : 'rgba(220, 244, 248, .55)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = lightTheme ? '#087f9b' : '#e0f4f7';
    ctx.beginPath(); ctx.arc(x, y, 1.5, 0, Math.PI * 2); ctx.fill();
  }
  drawPressureHeat();
}

function heatPressureAt(x, y) {
  if (!tactileActive) return 0;
  return tactilePressure * Math.exp(-((x - tactilePosition.x) ** 2 + (y - tactilePosition.y) ** 2) / 1.25);
}

function drawPressureHeat() {
  const ctx = heatContext;
  const { width, height } = prepareTactileCanvas(heatCanvas, ctx);
  const cw = width / tactileColumns, ch = height / tactileRows;
  const lightTheme = document.documentElement.dataset.theme === 'light';
  ctx.fillStyle = lightTheme ? '#cbd3dd' : '#0c1420';
  ctx.fillRect(0, 0, width, height);
  if (tactileLiveMode) {
    const gap = 2;
    for (let row = 0; row < tactileRows; row++) for (let col = 0; col < tactileColumns; col++) {
      const raw = Math.max(0, Number(liveTactileMatrix[row]?.[col]) || 0);
      ctx.fillStyle = lightTheme && raw <= 0
        ? '#e3e8ee'
        : robotHeatColor(Math.min(1, raw / SENSOR_FULL_SCALE_POINTS));
      ctx.fillRect(col * cw + gap / 2, row * ch + gap / 2, Math.max(1, cw - gap), Math.max(1, ch - gap));
    }
    return;
  }
  if (!tactileActive || tactilePressure === 0) return;
  ctx.save();
  ctx.translate(tactilePosition.x * cw, tactilePosition.y * ch);
  ctx.scale(cw, ch);
  // The radius spans the entire surface, without a contact-region clip.
  const radiusLimit = Math.hypot(tactileColumns, tactileRows);
  const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, radiusLimit);
  for (let i = 0; i <= 480; i++) {
    const radius = radiusLimit * i / 480;
    const pressure = heatPressureAt(tactilePosition.x + radius, tactilePosition.y);
    const alpha = Math.min(1, pressure / .3);
    gradient.addColorStop(i / 480, pressureColor(pressure).replace('rgb(', 'rgba(').replace(')', `,${alpha})`));
  }
  ctx.fillStyle = gradient;
  ctx.fillRect(-tactilePosition.x, -tactilePosition.y, tactileColumns, tactileRows);
  ctx.restore();
}

function totalTactileVector() {
  if (tactileLiveMode) {
    const summary = liveMatrixSummary();
    return { x: summary.centerX - tactileColumns / 2, y: summary.centerY - tactileRows / 2 };
  }
  let x = 0, y = 0, weight = 0;
  for (let row = 0; row < tactileRows; row++) for (let col = 0; col < tactileColumns; col++) {
    const vector = tactileVector(col, row);
    const pressure = tactileSample(col, row);
    if (!vector || pressure === 0) continue;
    x += vector.x * pressure;
    y += vector.y * pressure;
    weight += pressure;
  }
  return weight ? { x: x / weight, y: y / weight } : { x: 0, y: 0 };
}

function updateTotalDirection() {
  const vector = totalTactileVector();
  const balanced = Math.hypot(vector.x, vector.y) < .015;
  const active = tactileLiveMode ? liveTactilePressure > 0 : tactileActive;
  const pressure = tactileLiveMode ? liveTactilePressure : tactilePressure;
  totalDirectionArrow.hidden = !active || pressure === 0 || balanced;
  if (!active) totalDirectionText.textContent = tactileLiveMode ? '等待实机压力' : '未按压';
  else if (pressure === 0) totalDirectionText.textContent = '无压力';
  else if (balanced) totalDirectionText.textContent = '方向平衡';
  else {
    const rect = tactilePad.getBoundingClientRect();
    const angle = Math.atan2(vector.y * rect.height / tactileRows, vector.x * rect.width / tactileColumns) * 180 / Math.PI;
    const degrees = (angle + 360) % 360;
    const directions = ['右', '右下', '下', '左下', '左', '左上', '上', '右上'];
    totalDirectionArrow.style.transform = `rotate(${angle}deg)`;
    totalDirectionText.textContent = `${directions[Math.round(degrees / 45) % 8]} · ${Math.round(degrees) % 360}°`;
  }
}

function syncTactile() {
  const shownPressure = tactileLiveMode ? liveTactilePressure : tactilePressure;
  const mappedLevel = tactileFeedbackLevel(shownPressure);
  const mappedAmp = mappedLevel > 0 ? amplitudeForPressure(mappedLevel) : 0;
  const mappedFrequency = mappedLevel > 0 ? frequencyForPressure(mappedLevel) : 0;
  tactileForceLabel.textContent = shownPressure.toFixed(1);
  if (singleForceValue) singleForceValue.textContent = `${shownPressure.toFixed(1)} N`;
  if (singleAmpValue) singleAmpValue.textContent = String(mappedAmp);
  if (singleFrequencyValue) singleFrequencyValue.textContent = mappedFrequency ? `${mappedFrequency} Hz` : '— Hz';
  tactileForceTitle.textContent = tactileLiveMode ? '实机峰值' : '模拟力';
  heatLegendMax.textContent = tactileLiveMode ? '约 140 点 / 15 N' : '15 N';
  heatLegendMiddle.textContent = tactileLiveMode ? '10 点 ≈ 0.5 N · 暂定标定' : '4 N · 黄';
  heatLegendHigh.textContent = tactileLiveMode ? '高压 · 黄' : '高压 · 红';
  updateTactileMotorStatus();

  const liveSummary = tactileLiveMode ? liveMatrixSummary() : null;
  const displayActive = tactileLiveMode ? liveTactilePressure > 0 : tactileActive;
  tactileStatus.textContent = tactileLiveMode ? (displayActive ? '实机按压' : '实机待机') : (tactileActive ? '按压中' : '未按压');
  tactileStatus.classList.toggle('active', displayActive);
  tactilePoint.textContent = tactileLiveMode
    ? (liveSummary.peak > 0 ? `${liveSummary.peakRow + 1} 行 ${liveSummary.peakColumn + 1} 列` : '—')
    : (tactileActive ? `${Math.floor(tactilePosition.y) + 1} 行 ${Math.floor(tactilePosition.x) + 1} 列` : '—');
  tactileDirection.textContent = displayActive ? '压力加权中心' : '—';
  const feedbackLevel = displayActive ? tactileFeedbackLevel(shownPressure) : 0;
  const feedbackAmp = amplitudeForPressure(feedbackLevel);
  tactileVibration.textContent = displayActive && shownPressure > sensitivityConfig().threshold
    ? `AMP ${feedbackAmp} · ${Math.round(feedbackLevel * 100)}%`
    : displayActive ? '死区 · 不输出' : '—';
  updateTotalDirection();
  if (tactileSource) {
    updateOutputFromNormalized(tactileActive ? feedbackLevel : 0);
    holdBadge.textContent = tactileActive ? '压力模拟' : '待机';
    holdBadge.classList.toggle('active', tactileActive);
    gestureHintText.textContent = tactileInput === 'pen' ? '数位笔压感控制 · 模拟力 0–15 N · 抬笔归零' : '采集区按住拖动 · + / − 调节压力 · 满量程 15 N';
  }
  drawTactile();
}

function updateTactileMotorStatus() {
  if (tactileLiveMode) {
    tactileMotorStatus.textContent = !robotState?.connected ? '等待机械手触觉数据' :
      !robotHapticEnabled ? '实机触觉显示中 · 振动联动已关闭' :
      !connected ? '实机触觉显示中 · 请连接 LRA' :
      !deviceReady ? 'LRA 初始化中 · 暂不输出' :
      liveTactilePressure <= sensitivityConfig().threshold ? `实机联动已开启 · 当前低于 ${sensitivityConfig().threshold.toFixed(1)} N 启振阈值` :
      `实机联动 · 峰值 ${liveTactilePressure.toFixed(1)} N · ${robotHapticActive ? '正在振动' : '准备输出'}`;
    return;
  }
  tactileMotorStatus.textContent = !connected ? '马达未连接 · 当前仅预览' :
    !deviceReady ? '马达初始化中 · 等待就绪后重新按压' :
    !tactileActive ? (playingEffect ? `${MODE_NAMES[playingEffect]}播放中 · 可立即停止` : `${MODE_NAMES[selectedMode]}输出就绪 · 按住采集区`) :
    !tactileMotorAllowed ? '本次仅预览 · 请松开后重新按压' :
    tactilePressure <= 0 ? '压力为零 · 马达停止' :
    tactileEffectCompleted ? `${MODE_NAMES[selectedMode]}已完成 · 松开后再次按压` :
    selectedMode === 'COLLISION' && tactilePressure < 6 ? '碰撞需至少 6 N · 继续加压触发' :
    holding ? `${MODE_NAMES[selectedMode]} · ${tactileOutputConfirmed ? '设备已确认启动' : '已发送启动请求'} · ${liveFrequency} Hz / AMP ${currentAmp}` : `正在启动${MODE_NAMES[selectedMode]}输出`;
}

function prepareTactileAttack() {
  if (hapticBoard === "telex-v1") return;
  if (!connected || !deviceReady || holding || tactileActive ||
      playingEffect || selectedMode !== 'FRICTION' || modePreparationPending || tactilePreloadPending) return;
  if (confirmedMotorOutput) return;
  tactilePreloadPending = true;
  queueLine(`PREP ${selectedFrequency} ${selectedMaxAmp}`).catch(error => {
    tactilePreloadPending = false;
    confirmedMotorOutput = null;
    showToast(error.message);
  });
}

function syncTactileMotor() {
  if (!tactileMotorAllowed || !tactileActive) return;
  if (!connected || !deviceReady) {
    endTactile();
    return;
  }
  if (!selectedFrequency || !selectedMaxAmp) {
    if (holding) {
      stopHeartbeat();
      setHoldingUI(false);
      queueLine('STOP').catch(() => undefined);
    }
    return;
  }
  if (tactilePressure <= sensitivityConfig().threshold) {
    if (holding) {
      stopHeartbeat();
      setHoldingUI(false);
      queueLine('STOP').catch(() => undefined);
    }
    return;
  }
  const singleEffect = ['SHORT', 'COLLISION'].includes(selectedMode);
  if (singleEffect && tactileTriggeredMode === selectedMode) return;
  if (selectedMode === 'COLLISION' && tactilePressure < 6) return;
  if (holding) { sendLiveUpdate(); updateTactileMotorStatus(); return; }
  tactileOutputConfirmed = false;
  tactileEffectCompleted = false;
  const feedbackLevel = tactileFeedbackLevel();
  const targetFrequency = frequencyForPressure(feedbackLevel);
  const targetAmplitude = amplitudeForPressure(feedbackLevel);
  if (!targetFrequency || !targetAmplitude) return;
  tactileTriggeredMode = selectedMode;
  if (selectedMode === 'SHORT') shortLed?.classList.add('short-playing');
  if (selectedMode === 'LONG') longLed?.classList.add('long-recognized');
  setHoldingUI(true);
  updateLiveFrequency();
  let command;
  switch (selectedMode) {
    case 'DEFAULT':
      continuousTargetLevel = quantizeFeedbackLevel(tactileFeedbackLevel());
      continuousOutputLevel = continuousTargetLevel;
      liveFrequency = targetFrequency;
      command = `START ${targetAmplitude} ${liveFrequency}`;
      break;
    case 'SHORT': command = `PULSE ${liveFrequency} ${currentAmp} ${shortDurationForBoard()}`; break;
    case 'LONG': command = `LONG_START ${liveFrequency} ${currentAmp}`; break;
    case 'COLLISION': command = `COLLISION ${Math.round(tactilePressure * 10)} ${liveFrequency} ${currentAmp}`; break;
    case 'FRICTION':
      command = `START ${targetAmplitude} ${targetFrequency}`;
      break;
    default: return;
  }
  if (!['DEFAULT', 'FRICTION'].includes(selectedMode)) playingEffect = selectedMode;
  queueLine(command).catch(error => { endTactile(); showToast(error.message); });
  if (['DEFAULT', 'FRICTION', 'LONG'].includes(selectedMode)) startHeartbeat();
  updateTactileMotorStatus();
}

function moveTactile(event) {
  if (tactileInput === 'pen') {
    rawPenPressure = Number.isFinite(event.pressure) ? Math.max(0, Math.min(1, event.pressure)) : null;
    simulationTargetPressure = (rawPenPressure ?? 0) * 15;
  }
  const rect = tactilePad.getBoundingClientRect();
  const next = { x: Math.max(0, Math.min(tactileColumns - .001, (event.clientX - rect.left) / rect.width * tactileColumns)), y: Math.max(0, Math.min(tactileRows - .001, (event.clientY - rect.top) / rect.height * tactileRows)) };
  tactilePosition = next;
  syncTactile();
}

function moveTactileFromChart(event) {
  const rect = singleFingerForceChart.getBoundingClientRect();
  const level = 1 - Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height)));
  tactileInput = 'mouse';
  simulationTargetPressure = level * SENSOR_MAX_FORCE_N;
  manualTactilePressure = simulationTargetPressure;
  tactilePosition = {
    x: Math.max(0, Math.min(tactileColumns - .001, (event.clientX - rect.left) / Math.max(1, rect.width) * tactileColumns)),
    y: tactileRows / 2
  };
  syncTactile();
}

function beginTactileSimulation(event, captureTarget, moveHandler) {
  if (tactileLiveMode) { showToast('请先切换到“模拟输入”'); return; }
  if (event.button !== 0 || tactilePointer !== null) return;
  event.preventDefault();
  if (holding || playingEffect) stopButton.click();
  tactileTriggeredMode = null;
  tactileEffectCompleted = false;
  tactileMotorAllowed = connected && deviceReady;
  if (!tactileMotorAllowed) showToast('模拟输入预览：连接马达并等待就绪后，重新按压即可输出所选振动模式');
  appendLog(`SIM INPUT ${captureTarget === singleFingerForceChart ? 'curve' : 'matrix'} · ${tactileMotorAllowed ? MODE_NAMES[selectedMode] + '输出' : '仅预览'}`);
  tactileSource = true;
  tactileInput = event.pointerType || 'mouse';
  tactilePressure = 0;
  simulationTargetPressure = tactileInput === 'pen' ? 0 : manualTactilePressure;
  tactileActive = true;
  tactilePointer = event.pointerId;
  tactileCaptureTarget = captureTarget;
  captureTarget.setPointerCapture(event.pointerId);
  controlPad.classList.add('tactile-preview');
  startPressClock();
  moveHandler(event);
  startSimulationInputLoop();
}

function endTactile(normalRelease = false) {
  if (tactilePointer === null) {
    if (normalRelease !== true && tactileSource && playingEffect && connected) {
      playingEffect = null;
      queueLine('STOP').catch(() => undefined);
      refreshEffectLights();
    }
    return;
  }
  const pointer = tactilePointer;
  stopSimulationInputLoop();
  simulationTargetPressure = 0;
  const stopMotor = tactileMotorAllowed;
  tactileMotorAllowed = false;
  if (stopMotor) {
    stopHeartbeat();
    setHoldingUI(false);
    if (connected) {
      if (normalRelease !== true || selectedMode === 'DEFAULT' || selectedMode === 'FRICTION') {
        playingEffect = null;
        queueLine('STOP').catch(() => undefined);
      } else if (selectedMode === 'LONG' && tactileTriggeredMode === 'LONG') {
        queueLine('LONG_RELEASE').catch(() => undefined);
      }
      // A normal release lets firmware finish SHORT / COLLISION once.
    }
  }
  tactilePointer = null;
  tactileActive = false;
  if (tactileInput === 'pen') rawPenPressure = 0;
  if (!tactileLiveMode || tactileInput === 'pen') tactilePressure = 0;
  if (tactileCaptureTarget?.hasPointerCapture(pointer)) tactileCaptureTarget.releasePointerCapture(pointer);
  tactileCaptureTarget = null;
  if (!tactileLiveMode) recordSimulationPressure(0);
  if (!holding) stopPressClock();
  syncTactile();
}

tactilePad.addEventListener('pointerenter', prepareTactileAttack);
tactilePad.addEventListener('pointermove', event => { if (tactilePointer === null && event.buttons === 0) prepareTactileAttack(); });
tactilePad.addEventListener('pointerdown', event => {
  beginTactileSimulation(event, tactilePad, moveTactile);
});
tactilePad.addEventListener('pointermove', event => {
  if (event.pointerId !== tactilePointer) return;
  if (tactileInput === 'pen' && event.buttons === 0) { endTactile(true); return; }
  moveTactile(event);
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) tactilePad.addEventListener(type, event => { if (event.pointerId === tactilePointer) endTactile(type === 'pointerup'); });
singleFingerForceChart.addEventListener('pointerdown', event => beginTactileSimulation(event, singleFingerForceChart, moveTactileFromChart));
singleFingerForceChart.addEventListener('pointermove', event => {
  if (event.pointerId === tactilePointer && tactileCaptureTarget === singleFingerForceChart) moveTactileFromChart(event);
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  singleFingerForceChart.addEventListener(type, event => {
    if (event.pointerId === tactilePointer && tactileCaptureTarget === singleFingerForceChart) endTactile(type === 'pointerup');
  });
}
function changeTactilePressure(delta) {
  if (tactileActive && tactileInput === 'pen') return;
  tactileInput = 'mouse';
  manualTactilePressure = Math.max(0, Math.min(15, manualTactilePressure + delta));
  simulationTargetPressure = manualTactilePressure;
  if (!tactileActive) tactilePressure = manualTactilePressure;
  syncTactile();
  syncTactileMotor();
}
window.addEventListener('keydown', event => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  if (event.key === '+' || event.key === '=' || event.key === '-') {
    event.preventDefault(); changeTactilePressure(event.key === '-' ? -1 : 1);
  }
});
window.addEventListener('blur', endTactile);
document.addEventListener('visibilitychange', () => { if (document.hidden) endTactile(); });
stopButton.addEventListener('click', endTactile);
stopButton.addEventListener('click', () => {
  stopRobotHaptic();
});
robotHapticToggle.addEventListener('click', () => {
  const useLiveInput = !tactileLiveMode;
  endTactile();
  tactileLiveMode = useLiveInput;
  robotHapticEnabled = useLiveInput;
  tactileColumn.classList.toggle('live-source', useLiveInput);
  tactileColumn.classList.toggle('simulation-source', !useLiveInput);
  if (useLiveInput) {
    tactileLiveMode = true;
  } else stopRobotHaptic();
  robotHapticToggle.setAttribute('aria-pressed', String(useLiveInput));
  robotHapticToggle.textContent = useLiveInput ? '实机模式' : '模拟输入';
  syncTactile();
  syncRobotHaptic();
});
new ResizeObserver(drawTactile).observe(tactilePad);
new ResizeObserver(drawPressureHeat).observe(heatCanvas);
syncTactile();


const tactileColumn = document.querySelector('#tactileColumn');
tactileColumn.classList.add('live-source');
const expandTactile = document.querySelector('#expandTactile');
const robotHandTitle = document.querySelector('#robotHandTitle');
new ResizeObserver(resizeCanvas).observe(controlPad);

function setTactileGrid(rows, columns) {
  endTactile();
  tactilePosition = { x: tactilePosition.x / tactileColumns * columns, y: tactilePosition.y / tactileRows * rows };
  tactileRows = rows;
  tactileColumns = columns;
  tactileCanvas.setAttribute('aria-label', `${rows} 行 ${columns} 列圆形采集点，箭头朝向按压中心`);
  syncTactile();
}


expandTactile.addEventListener('click', () => {
  const singleFingerMode = tactileColumn.classList.toggle('single-finger-mode');
  expandTactile.textContent = singleFingerMode ? '切换五指总览' : '切换单个指尖';
  expandTactile.setAttribute('aria-pressed', String(singleFingerMode));
  if (robotHandTitle) robotHandTitle.textContent = singleFingerMode ? '单指矩阵触觉总览' : '五指矩阵触觉总览';
  drawTactile();
  drawAllRobotVisuals();
  syncRobotHaptic();
  requestAnimationFrame(drawAllRobotVisuals);
  expandTactile.focus({ preventScroll: true });
});


// Keep long presses on the test surfaces inside the pressure interaction.
// Attach to the elements so the rules also follow them into the enlarged dialog.
for (const surface of [tactilePad, controlPad, heatCanvas]) {
  for (const type of ['contextmenu', 'dragstart', 'selectstart']) {
    surface.addEventListener(type, event => event.preventDefault());
  }
}

function stopRobotHaptic() {
  if (robotHapticActive && connected) queueLine('STOP').catch(() => undefined);
  robotHapticActive = false;
  robotHapticLastSent = 0;
  robotHapticTargetLevel = 0;
  robotHapticOutputLevel = 0;
}

function c6FourFingerAmps() {
  const keys = ['index', 'middle', 'ring', 'little'];
  return keys.map(key => {
    const pressure = fingerPressureFromState(robotState, key);
    const level = tactileFeedbackLevel(pressure);
    return level <= 0 ? 0 : amplitudeForPressure(level);
  });
}

function syncRobotHaptic() {
  if (!robotHapticEnabled || !tactileLiveMode || !connected || !deviceReady || f0DetectionActive ||
      !selectedFrequency || !selectedMaxAmp) {
    stopRobotHaptic();
    updateTactileMotorStatus();
    return;
  }
  if (hapticBoard === 'telex-v1' && ['DEFAULT', 'FRICTION', 'LONG'].includes(selectedMode)) {
    const fingers = ['little', 'ring', 'middle', 'index', 'thumb'];
    const singleFingerMode = tactileColumn.classList.contains('single-finger-mode');
    // Five-finger overview drives every matching motor independently. In the
    // single-finger view, retain the same mapping but silence unselected
    // channels so that the chosen finger is isolated for inspection.
    const levels = fingers.map(key => {
      if (singleFingerMode && key !== robotFinger) return 0;
      const pressure = fingerPressureFromState(robotState, key);
      const level = tactileFeedbackLevel(pressure);
      if (pressure <= sensitivityConfig().threshold || level <= 0) return 0;
      return Math.min(40, ampToTelexLevel(amplitudeForPressure(level)));
    });
    const safeLevels = TelexProtocol.scaleLevels(levels);
    const anyActive = safeLevels.some(Boolean);
    if (!anyActive) { stopRobotHaptic(); updateTactileMotorStatus(); return; }
    const signature = safeLevels.join(' '), now = performance.now();
    if (signature === robotHapticTriggeredMode && now - robotHapticLastSent < 400) return;
    robotHapticTriggeredMode = signature;
    robotHapticLastSent = now;
    queueLine(`FIVE ${signature}`).then(() => {
      robotHapticActive = true;
      updateTactileMotorStatus();
    }).catch(error => { robotHapticActive = false; showToast(error.message); });
    return;
  }
  if (hapticBoard === 'c6') {
    const amps = c6FourFingerAmps();
    const anyActive = amps.some(value => value > 0);
    const now = performance.now();
    const maximumLevel = Math.max(0, ...['index', 'middle', 'ring', 'little'].map(key => {
      return tactileFeedbackLevel(fingerPressureFromState(robotState, key));
    }));
    const frequency = frequencyForPressure(maximumLevel);
    const signature = `${amps.join(' ')} ${frequency}`;
    if (signature === robotHapticTriggeredMode && now - robotHapticLastSent < 500) return;
    if (signature !== robotHapticTriggeredMode && now - robotHapticLastSent < sensitivityConfig().interval) return;
    robotHapticLastSent = now;
    robotHapticTriggeredMode = signature;
    queueLine(`FOUR ${signature}`)
      .then(() => { robotHapticActive = anyActive; updateTactileMotorStatus(); })
      .catch(error => { robotHapticActive = false; showToast(error.message); });
    return;
  }
  const force = Math.round(tactileFeedbackLevel(liveTactilePressure) * 100);
  if (liveTactilePressure <= sensitivityConfig().threshold || force <= 0) {
    stopRobotHaptic();
    robotHapticTriggeredMode = null;
    updateTactileMotorStatus();
    return;
  }
  if (selectedMode === 'COLLISION' && liveTactilePressure < 6) return;
  const oneShot = ['SHORT', 'COLLISION'].includes(selectedMode);
  if (oneShot && robotHapticTriggeredMode === selectedMode) return;
  const now = performance.now();
  const rawLevel = force / 100;
  if (!robotHapticActive) {
    robotHapticTargetLevel = quantizeFeedbackLevel(rawLevel);
    robotHapticOutputLevel = robotHapticTargetLevel;
  } else {
    robotHapticTargetLevel = acceptFeedbackLevel(rawLevel, robotHapticTargetLevel);
    const outputChanged = Math.abs(robotHapticTargetLevel - robotHapticOutputLevel) >= .001;
    if (!outputChanged && now - robotHapticLastSent < 500) return;
    if (outputChanged && now - robotHapticLastSent < sensitivityConfig().interval) return;
    if (outputChanged) robotHapticOutputLevel = robotHapticTargetLevel;
  }
  robotHapticLastSent = now;
  const smoothAmp = amplitudeForPressure(robotHapticOutputLevel);
  const smoothFrequency = frequencyForPressure(robotHapticOutputLevel);
  if (!smoothAmp || !smoothFrequency) {
    stopRobotHaptic();
    updateTactileMotorStatus();
    return;
  }
  let command;
  switch (selectedMode) {
    case 'DEFAULT':
      liveFrequency = smoothFrequency;
      frequencyValue.textContent = String(liveFrequency);
      command = `${robotHapticActive ? 'HOLD' : 'START'} ${smoothAmp} ${smoothFrequency}`;
      break;
    case 'LONG': command = `${robotHapticActive ? 'LONG_HOLD' : 'LONG_START'} ${smoothFrequency} ${smoothAmp}`; break;
    case 'FRICTION': command = `${robotHapticActive ? 'HOLD' : 'START'} ${smoothAmp} ${smoothFrequency}`; break;
    case 'SHORT': command = `PULSE ${smoothFrequency} ${smoothAmp} ${shortDurationForBoard()}`; break;
    case 'COLLISION': command = `COLLISION ${Math.round(liveTactilePressure * 10)} ${smoothFrequency} ${smoothAmp}`; break;
    default: return;
  }
  if (oneShot) robotHapticTriggeredMode = selectedMode;
  queueLine(command).then(() => { robotHapticActive = true; updateTactileMotorStatus(); })
    .catch(error => { robotHapticActive = false; showToast(error.message); });
}

// LinkerHand L25 ROS2 live data ------------------------------------------------
const ROBOT_FINGERS = [
  { key: 'thumb', name: '拇指' },
  { key: 'index', name: '食指' },
  { key: 'middle', name: '中指' },
  { key: 'ring', name: '无名指' },
  { key: 'little', name: '小指' }
];
const robotStreamState = document.querySelector('#robotStreamState');
const robotStreamStatus = document.querySelector('#robotStreamStatus');
const robotLastUpdate = document.querySelector('#robotLastUpdate');
const robotFingerTabs = document.querySelector('#robotFingerTabs');
const robotAllFingers = document.querySelector('#robotAllFingers');
const robotDataNote = document.querySelector('#robotDataNote');

let robotFinger = 'index';
let robotLastSequence = -1;
const robotHistory = Object.fromEntries(ROBOT_FINGERS.map(({ key }) => [key, []]));

function robotValue(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number.toFixed(digits) : '—';
}

function prepareRobotCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawRobotForceChart(key, targetCanvas = null) {
  const canvas = targetCanvas || robotAllFingers?.querySelector(`[data-force-chart="${key}"]`);
  if (!canvas) return;
  const { ctx, width, height } = prepareRobotCanvas(canvas);
  const history = targetCanvas === singleFingerForceChart && !tactileLiveMode
    ? simulationHistory
    : robotHistory[key];
  const pad = { left: 42, right: 12, top: 12, bottom: 25 };
  const chartWidth = Math.max(1, width - pad.left - pad.right);
  const chartHeight = Math.max(1, height - pad.top - pad.bottom);
  const maximum = SENSOR_MAX_FORCE_N;
  const lightTheme = document.documentElement.dataset.theme === 'light';

  ctx.clearRect(0, 0, width, height);
  ctx.font = '10px Segoe UI, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let row = 0; row <= 4; row += 1) {
    const y = pad.top + chartHeight * row / 4;
    ctx.strokeStyle = lightTheme ? 'rgba(35,52,72,.11)' : 'rgba(255,255,255,.065)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillStyle = lightTheme ? '#6b7789' : '#727d91';
    ctx.fillText((maximum * (1 - row / 4)).toFixed(0), pad.left - 7, y);
  }
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillStyle = lightTheme ? '#667387' : '#687387';
  ctx.fillText(`最近 ${FORCE_CHART_HISTORY} 帧 · 256 级 · 实时压力 N`, pad.left, height - 16);

  const drawSeries = (values, color) => {
    if (values.length < 2) return;
    const points = values.map((value, index) => ({
      valid: value >= 0,
      x: pad.left + chartWidth * index / Math.max(1, values.length - 1),
      y: pad.top + chartHeight * (1 - Math.min(Math.max(value, 0), maximum) / maximum)
    }));
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    let previous = null;
    points.forEach((point, index) => {
      if (!point.valid) { previous = null; return; }
      if (!previous) {
        ctx.moveTo(point.x, point.y);
      } else {
        const next = points[index + 1]?.valid ? points[index + 1] : point;
        const controlX = point.x;
        const controlY = point.y;
        const endX = (point.x + next.x) / 2;
        const endY = (point.y + next.y) / 2;
        ctx.quadraticCurveTo(controlX, controlY, endX, endY);
      }
      previous = point;
    });
    ctx.stroke();
  };
  drawSeries(history, lightTheme ? '#087f9b' : '#4de2ff');
}

function robotHeatColor(value) {
  const lightTheme = document.documentElement.dataset.theme === 'light';
  const stops = lightTheme
    ? [
        [0, 227, 232, 238], [.12, 204, 218, 234], [.27, 142, 175, 210],
        [.43, 71, 127, 188], [.57, 32, 88, 148], [.70, 18, 63, 121],
        [.82, 11, 53, 109], [1, 8, 43, 89]
      ]
    : [
        [0, 28, 38, 55], [.18, 30, 54, 190], [.28, 56, 72, 207],
        [.36, 103, 57, 189], [.46, 181, 47, 86], [.57, 233, 74, 56],
        [.66, 240, 106, 50], [.73, 255, 154, 53], [.80, 255, 200, 61],
        [.87, 255, 232, 63], [.93, 255, 255, 96], [1, 255, 255, 138]
      ];
  const t = Math.max(0, Math.min(1, value));
  let upper = stops.findIndex(stop => stop[0] >= t);
  if (upper <= 0) upper = 1;
  const low = stops[upper - 1];
  const high = stops[upper] || stops[stops.length - 1];
  const mix = (t - low[0]) / Math.max(.001, high[0] - low[0]);
  const channel = index => Math.round(low[index] + (high[index] - low[index]) * mix);
  return `rgb(${channel(1)}, ${channel(2)}, ${channel(3)})`;
}

function drawRobotMatrix(key) {
  const canvas = robotAllFingers?.querySelector(`[data-matrix-canvas="${key}"]`);
  const peakLabel = robotAllFingers?.querySelector(`[data-matrix-peak="${key}"]`);
  const scaleLabel = robotAllFingers?.querySelector(`[data-matrix-scale="${key}"]`);
  if (!canvas || !peakLabel || !scaleLabel) return;
  const { ctx, width, height } = prepareRobotCanvas(canvas);
  const matrix = robotState?.matrices?.[key] || [];
  const rows = matrix.length;
  const columns = Math.max(0, ...matrix.map(row => Array.isArray(row) ? row.length : 0));
  ctx.clearRect(0, 0, width, height);
  if (!rows || !columns) {
    ctx.fillStyle = '#778196';
    ctx.font = '12px Segoe UI, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('等待压力矩阵', width / 2, height / 2);
    peakLabel.textContent = '峰值 —';
    scaleLabel.textContent = '自动量程';
    return;
  }
  const values = matrix.flat().map(Number).filter(value => Number.isFinite(value) && value >= 0);
  const maximum = Math.max(1, ...values);
  const gap = 2;
  const cellWidth = (width - gap * (columns - 1)) / columns;
  const cellHeight = (height - gap * (rows - 1)) / rows;
  matrix.forEach((row, y) => {
    for (let x = 0; x < columns; x += 1) {
      const value = Number(row[x]);
      const valid = Number.isFinite(value) && value >= 0;
      const lightTheme = document.documentElement.dataset.theme === 'light';
      ctx.fillStyle = valid
        ? (lightTheme && value <= 0 ? '#e3e8ee' : robotHeatColor(value / maximum))
        : (lightTheme ? '#edf0f4' : 'rgba(255,255,255,.035)');
      ctx.fillRect(x * (cellWidth + gap), y * (cellHeight + gap), cellWidth, cellHeight);
    }
  });
  peakLabel.textContent = `峰值 ${robotValue(maximum)} · ${sensorPointsToNewtons(maximum).toFixed(1)} N`;
  scaleLabel.textContent = maximum.toFixed(maximum < 10 ? 1 : 0);
}

function selectRobotFinger(key) {
  if (!ROBOT_FINGERS.some(finger => finger.key === key)) return;
  robotFinger = key;
  const fingerName = ROBOT_FINGERS.find(finger => finger.key === key)?.name || '手指';
  if (singleFingerChartTitle) singleFingerChartTitle.textContent = `${fingerName}触觉实时力曲线`;
  if (singleFingerForceChart) {
    singleFingerForceChart.setAttribute('aria-label', `${fingerName}触觉实时力曲线，量程 0 到 15 牛，256 级显示精度`);
    drawRobotForceChart(key, singleFingerForceChart);
  }
  selectTelexChannel();
  robotFingerTabs.querySelectorAll('[data-finger]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.finger === key)));
  if (tactileLiveMode && robotState) {
    liveTactileMatrix = robotState.matrices?.[key] || [];
    liveTactilePressure = fingerPressureFromState(robotState, key);
    updateDefaultForceMonitor();
    syncTactile();
    syncRobotHaptic();
  }
}

function renderRobotFingerPanels() {
  if (!robotAllFingers || robotAllFingers.children.length) return;
  robotAllFingers.innerHTML = ROBOT_FINGERS.map(({ key, name }) => `
    <section class="robot-finger-panel" data-finger-panel="${key}">
      <header class="robot-finger-panel-title"><strong>${name}</strong><span>12 × 6 矩阵触觉</span></header>
      <div class="robot-finger-visuals">
        <article class="robot-chart-card">
          <div class="robot-view-title"><div><span>触觉实时监视</span><strong>${name} · 触觉实时力曲线</strong></div><div class="robot-chart-legend"><span class="normal">实时压力 / 15 N</span></div></div>
          <canvas data-force-chart="${key}" role="img" aria-label="${name}触觉实时力曲线，量程 0 到 15 牛"></canvas>
        </article>
        <article class="robot-matrix-card">
          <div class="robot-view-title"><div><span>力的大小矩阵</span><strong>${name}</strong></div><span data-matrix-peak="${key}">峰值 —</span></div>
          <div class="robot-matrix-wrap"><canvas data-matrix-canvas="${key}" role="img" aria-label="${name}十二行六列压力矩阵"></canvas></div>
          <div class="robot-matrix-legend"><span>0</span><i></i><span data-matrix-scale="${key}">自动量程</span></div>
        </article>
      </div>
    </section>`).join('');
}

function drawAllRobotVisuals() {
  ROBOT_FINGERS.forEach(({ key }) => {
    drawRobotForceChart(key);
    drawRobotMatrix(key);
  });
  drawRobotForceChart(robotFinger, singleFingerForceChart);
}

function consumeRobotState(state) {
  robotState = state;
  if (state.sequence !== robotLastSequence) {
    ROBOT_FINGERS.forEach(({ key }) => {
      const history = robotHistory[key];
      const matrix = state.matrices?.[key];
      const calibrated = Number(state.matrix_stats?.[key]?.peak_n);
      const hasSample = (Array.isArray(matrix) && matrix.length > 0) || (Number.isFinite(calibrated) && calibrated >= 0);
      const measuredForce = hasSample ? fingerPressureFromState(state, key) : -1;
      const quantizedForce = measuredForce < 0
        ? -1
        : Math.round(measuredForce / SENSOR_MAX_FORCE_N * (FORCE_CHART_LEVELS - 1)) * SENSOR_MAX_FORCE_N / (FORCE_CHART_LEVELS - 1);
      const previousForce = history.length && history[history.length - 1] >= 0 ? history[history.length - 1] : quantizedForce;
      history.push(quantizedForce < 0 ? -1 : previousForce + (quantizedForce - previousForce) * 0.42);
      if (history.length > FORCE_CHART_HISTORY) history.shift();
    });
    robotLastSequence = state.sequence;
  }
  robotStreamState.classList.toggle('online', Boolean(state.connected));
  robotStreamState.classList.toggle('error', !state.connected);
  const nativeBridge = state.hand?.transport === 'native-cpp';
  robotStreamStatus.textContent = state.connected
    ? (nativeBridge ? '机械手数据流正常' : 'ROS2 数据流正常')
    : (state.bridge_error || (nativeBridge ? '等待机械手数据' : '桥接已启动 · 等待 ROS2'));
  robotDataNote.textContent = nativeBridge
    ? '数据来自 LinkerHand 原生 PCAN 驱动。矩阵峰值暂按 10 点≈0.5 N、140 点≈15 N 换算，超过 15 N 封顶；实际力值需用砝码标定。'
    : '数据来自 ROS2：/cb_left_hand_matrix_touch。矩阵峰值暂按 10 点≈0.5 N、140 点≈15 N 换算，超过 15 N 封顶；实际力值需用砝码标定。';
  robotLastUpdate.textContent = state.updated_at
    ? `最后更新 ${new Date(state.updated_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`
    : '请先启动 LinkerHand 驱动';
  liveTactileMatrix = state.matrices?.[robotFinger] || [];
  liveTactilePressure = fingerPressureFromState(state, robotFinger);
  if (liveTactilePressure <= 0.05) {
    frictionMotionCenter = null;
    frictionRecognized = false;
    frictionLastMotionAt = 0;
    frictionDirection = null;
    frictionTravel = 0;
    frictionLed?.classList.remove('friction-recognized');
    longHoldRecognized = false;
    pressureHeldSince = 0;
    longLed?.classList.remove('long-recognized');
    autoContactSince = 0;
    autoCollisionTriggered = false;
    collisionLed?.classList.remove('collision-detected');
    shortButton?.classList.remove('suppressed-by-collision');
  } else {
    const now = Date.now();
    if (!autoContactSince) {
      autoContactSince = now;
      shortLed?.classList.add('short-playing');
      setTimeout(() => shortLed?.classList.remove('short-playing'), 500);
      if (liveTactilePressure > 10) {
        autoCollisionTriggered = true;
        collisionLed?.classList.add('collision-detected');
        shortButton?.classList.add('suppressed-by-collision');
        setTimeout(() => collisionLed?.classList.remove('collision-detected'), 500);
      }
    } else if (now - autoContactSince >= 500 && !frictionRecognized &&
      (!frictionLastMotionAt || now - frictionLastMotionAt >= 600)) {
      longHoldRecognized = true;
      longLed?.classList.add('long-recognized');
    }
    if (!pressureHeldSince) pressureHeldSince = Date.now();
    if (Date.now() - pressureHeldSince >= 1000) {
      longHoldRecognized = true;
      longLed?.classList.add('long-recognized');
    }
    updateFrictionRecognition(liveTactileMatrix);
  }
  updateDefaultForceMonitor();
  syncTactile();
  syncRobotHaptic();
  updateHandMeasuredState(state.joints);
  selectRobotFinger(robotFinger);
  drawAllRobotVisuals();
}

if (robotFingerTabs && typeof robotFingerTabs.querySelectorAll === 'function' && typeof EventSource !== 'undefined') {
  robotFingerTabs.addEventListener('click', event => {
    const button = event.target.closest('[data-finger]');
    if (button) selectRobotFinger(button.dataset.finger);
  });
  renderRobotFingerPanels();
  selectRobotFinger(robotFinger);
  const robotEvents = new EventSource('/api/linkerhand/events');
  robotEvents.addEventListener('tactile', event => {
    try { consumeRobotState(JSON.parse(event.data)); }
    catch (error) { console.error('Invalid LinkerHand data', error); }
  });
  robotEvents.onerror = () => {
    robotStreamState.classList.remove('online');
    robotStreamState.classList.add('error');
    robotStreamStatus.textContent = '未连接触觉桥';
    robotLastUpdate.textContent = '请用 web/start-linkerhand-web.bat 启动';
    robotDataNote.textContent = '当前页面没有连接到 ROS2 Web 桥。关闭旧的静态网页窗口，再运行 web/start-linkerhand-web.bat。';
  };
  new ResizeObserver(drawAllRobotVisuals).observe(robotAllFingers);
  if (singleFingerForceChart) new ResizeObserver(() => drawRobotForceChart(robotFinger, singleFingerForceChart)).observe(singleFingerForceChart);
}

// L25 25-joint controller ------------------------------------------------------
const L25_JOINT_NAMES = [
  '拇指根部', '食指根部', '中指根部', '无名指根部', '小指根部',
  '拇指侧摆', '食指侧摆', '中指侧摆', '无名指侧摆', '小指侧摆',
  '拇指横滚', '预留 11', '预留 12', '预留 13', '预留 14',
  '拇指中部', '食指中部', '中指中部', '无名指中部', '小指中部',
  '拇指指尖', '食指指尖', '中指指尖', '无名指指尖', '小指指尖'
];
const L25_GROUPS = [
  ['根部弯曲', 0, 5], ['侧摆', 5, 10], ['横滚 / 预留', 10, 15],
  ['中部弯曲', 15, 20], ['指尖弯曲', 20, 25]
];
const L25_RESERVED = new Set([11, 12, 13, 14]);
const L25_PRESETS = {
  '张开': Array.from({ length: 25 }, (_, index) => index >= 6 && index <= 9 ? 120 : index === 20 ? 225 : 255),
  '握拳': Array.from({ length: 25 }, (_, index) => L25_RESERVED.has(index) ? 255 : 0),
  'OK': [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,0,0,255,255,0,0,0,255,255]
};
const handJointGroups = document.querySelector('#handJointGroups');
const handControlStatus = document.querySelector('#handControlStatus');
const readHandPose = document.querySelector('#readHandPose');
const sendHandPose = document.querySelector('#sendHandPose');
const handBuiltinPresets = document.querySelector('#handBuiltinPresets');
const samplingPresetRow = document.querySelector('#samplingPresetRow');
const samplingPresetButtons = document.querySelector('#samplingPresetButtons');
const samplingSendHandPose = document.querySelector('#samplingSendHandPose');
const handPresetName = document.querySelector('#handPresetName');
const handPresetSelect = document.querySelector('#handPresetSelect');
const saveHandPreset = document.querySelector('#saveHandPreset');
const loadHandPreset = document.querySelector('#loadHandPreset');
const deleteHandPreset = document.querySelector('#deleteHandPreset');
const calibrateHandPose = document.querySelector('#calibrateHandPose');
const restoreCalibration = document.querySelector('#restoreCalibration');
const handSpeedRange = document.querySelector('#handSpeedRange');
const handSpeedValue = document.querySelector('#handSpeedValue');
let handTargetPose = [...L25_PRESETS['张开']];
let handMeasuredPose = null;
// Use the first valid ROS2 joint-state frame as the startup target baseline.
// This prevents the editor from sending the open-hand preset as an intermediate pose.
let handTargetsDirty = false;
let handPoseInitialized = false;
let handAutoSendTimer = null;
let handDirtyIndices = new Set();
let handPendingCommand = null;
const HAND_SPEEDS = [
  { label: '慢', value: 25 },
  { label: '正常', value: 50 },
  { label: '快', value: 90 }
];

function handMotionOptions() {
  const speed = HAND_SPEEDS[Math.max(0, Math.min(2, Number(handSpeedRange?.value) || 0))];
  return {
    speed: speed.value
  };
}

function updateHandMotionControls() {
  const options = handMotionOptions();
  if (handSpeedValue) handSpeedValue.textContent = `${HAND_SPEEDS[Number(handSpeedRange.value)].label} · ${options.speed}`;
}

function setHandControlStatus(message, type = '') {
  if (!handControlStatus) return;
  handControlStatus.textContent = message;
  handControlStatus.className = `hand-control-status ${type}`.trim();
}

function validL25Pose(values) {
  return Array.isArray(values) && values.length === 25 && values.every(value => Number.isFinite(Number(value)) && Number(value) >= 0);
}

function updateHandMeasuredState(joints) {
  const values = joints?.position?.map(Number);
  if (!validL25Pose(values) || values.some(value => value < 0)) return;
  handMeasuredPose = values.map(value => Math.round(Math.max(0, Math.min(255, value))));
  if (!handPoseInitialized && !handPendingCommand && !handTargetsDirty) {
    handPoseInitialized = true;
    applyHandTargetPose(handMeasuredPose, false);
  }
  if (handPendingCommand) {
    const reached = handPendingCommand.indices.every(index =>
      Math.abs(handMeasuredPose[index] - handPendingCommand.pose[index]) <= 3);
    if (reached) {
      handPendingCommand = null;
      handDirtyIndices.clear();
      handTargetsDirty = false;
      applyHandTargetPose(handMeasuredPose, false);
      setHandControlStatus('机械手已到达目标位置', 'ok');
      return;
    }
    if (Date.now() < handPendingCommand.deadline) {
      const waiting = handPendingCommand.indices.map(index => `${L25_JOINT_NAMES[index]} ${handMeasuredPose[index]}→${handPendingCommand.pose[index]}`).join('，');
      setHandControlStatus(`指令已发送，等待回读：${waiting}`);
      return;
    }
    handPendingCommand = null;
    handTargetsDirty = true;
    setHandControlStatus('指令已发送，但 5 秒内未收到目标位置回读；界面保留已发送目标', 'error');
    return;
  }
  if (!handTargetsDirty) applyHandTargetPose(handMeasuredPose, false);
  setHandControlStatus(`已读取实际位置 · ${new Date((joints.updated_at || Date.now() / 1000) * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`, 'ok');
}

function applyHandTargetPose(values, dirty = true) {
  if (!validL25Pose(values)) return;
  handTargetPose = values.map(value => Math.round(Math.max(0, Math.min(255, Number(value)))));
  handTargetsDirty = dirty;
  if (dirty) {
    handPendingCommand = null;
    handDirtyIndices = new Set(values.map((_, index) => index).filter(index => !L25_RESERVED.has(index)));
  }
  if (!handJointGroups || typeof handJointGroups.querySelectorAll !== 'function') return;
  handJointGroups.querySelectorAll('[data-joint]').forEach(control => {
    const index = Number(control.dataset.joint);
    const value = handTargetPose[index];
    control.querySelector('input[type="range"]').value = String(value);
    control.querySelector('input[type="number"]').value = String(value);
  });
}

function loadStoredPresets() {
  try { return JSON.parse(localStorage.getItem('linkerhand-l25-presets') || '{}'); }
  catch { return {}; }
}

function escapePresetName(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function refreshPresetSelect() {
  const presets = loadStoredPresets();
  const customNames = Object.keys(presets);
  handPresetSelect.innerHTML = '<option value="">自定义预设</option>' +
    customNames.map(name => `<option value="${escapePresetName(name)}">${escapePresetName(name)}</option>`).join('');
  handBuiltinPresets.innerHTML =
    Object.keys(L25_PRESETS).map(name => `<button type="button" data-builtin-preset="${escapePresetName(name)}">${escapePresetName(name)}</button>`).join('') +
    customNames.map(name => `<button type="button" data-custom-preset="${escapePresetName(name)}" title="自定义预设">${escapePresetName(name)}</button>`).join('');
  if (samplingPresetButtons && samplingPresetRow) {
    samplingPresetButtons.innerHTML = customNames.map(name =>
      `<button type="button" data-sampling-preset="${escapePresetName(name)}">${escapePresetName(name)}</button>`).join('');
    samplingPresetRow.hidden = customNames.length === 0;
  }
}

function loadCustomHandPreset(name) {
  const pose = loadStoredPresets()[name];
  if (!validL25Pose(pose)) return false;
  applyHandTargetPose(pose);
  handPresetSelect.value = name;
  samplingPresetButtons?.querySelectorAll('[data-sampling-preset]').forEach(button => {
    const selected = button.dataset.samplingPreset === name;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  setHandControlStatus(`已载入“${name}”目标 · 尚未发送`);
  return true;
}

async function publishHandPose(values = handTargetPose) {
  if (!robotState?.connected) { setHandControlStatus('ROS2 未连接，无法发送姿态', 'error'); return false; }
  sendHandPose.disabled = true;
  try {
    const options = handMotionOptions();
    const response = await fetch('/api/linkerhand/motion', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position: values, ...options })
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    const indices = handDirtyIndices.size ? [...handDirtyIndices] :
      values.map((_, index) => index).filter(index => !L25_RESERVED.has(index));
    handPendingCommand = {
      pose: [...values], indices, sentAt: Date.now(),
      deadline: Date.now() + 5000
    };
    handTargetsDirty = true;
    setHandControlStatus('目标姿态已发送，等待机械手反馈', 'ok');
    return true;
  } catch (error) {
    setHandControlStatus(`发送失败：${error.message}`, 'error');
    return false;
  } finally { sendHandPose.disabled = false; }
}

function scheduleHandPosePublish() {
  clearTimeout(handAutoSendTimer);
  handAutoSendTimer = setTimeout(() => {
    handAutoSendTimer = null;
    publishHandPose([...handTargetPose]);
  }, 140);
}

if (handJointGroups && typeof handJointGroups.querySelectorAll === 'function') {
  handJointGroups.innerHTML = L25_GROUPS.map(([title, start, end]) => `
    <section class="joint-group"><h3>${title}</h3>${Array.from({ length: end - start }, (_, offset) => {
      const index = start + offset, reserved = L25_RESERVED.has(index);
      return `<div class="joint-control ${reserved ? 'reserved' : ''}" data-joint="${index}">
        <div class="joint-control-head"><label>${index + 1}. ${L25_JOINT_NAMES[index]}</label><span class="joint-control-value"><input type="number" min="0" max="255" value="${handTargetPose[index]}" ${reserved ? 'disabled' : ''}></span></div>
        <input type="range" min="0" max="255" step="1" value="${handTargetPose[index]}" ${reserved ? 'disabled' : ''}><small>${reserved ? '该位置没有可控电机' : '0–255 · 拖动后自动发送'}</small>
      </div>`;
    }).join('')}</section>`).join('');
  refreshPresetSelect();
  updateHandMotionControls();

  handSpeedRange?.addEventListener('input', updateHandMotionControls);

  handJointGroups.addEventListener('input', event => {
    const control = event.target.closest('[data-joint]');
    if (!control) return;
    const index = Number(control.dataset.joint);
    const value = Math.round(Math.max(0, Math.min(255, Number(event.target.value) || 0)));
    handPendingCommand = null;
    handTargetPose[index] = value;
    handDirtyIndices.add(index);
    handTargetsDirty = true;
    control.querySelectorAll('input').forEach(input => { if (input !== event.target) input.value = String(value); });
    if (event.target.matches('input[type="range"]')) {
      setHandControlStatus('目标姿态已修改 · 正在自动发送');
      scheduleHandPosePublish();
    } else {
      setHandControlStatus('目标姿态已修改 · 按回车或点击“发送姿态”执行');
    }
  });
  handJointGroups.addEventListener('change', event => {
    if (event.target.matches('input[type="number"]') && event.target.closest('[data-joint]')) scheduleHandPosePublish();
  });
  handBuiltinPresets.addEventListener('click', event => {
    const button = event.target.closest('[data-builtin-preset], [data-custom-preset]');
    if (!button) return;
    const name = button.dataset.builtinPreset || button.dataset.customPreset;
    if (button.dataset.customPreset) {
      loadCustomHandPreset(name);
      return;
    }
    const pose = L25_PRESETS[name];
    if (validL25Pose(pose)) {
      applyHandTargetPose(pose);
      samplingPresetButtons?.querySelectorAll('[data-sampling-preset]').forEach(presetButton => {
        presetButton.classList.remove('selected');
        presetButton.setAttribute('aria-pressed', 'false');
      });
      setHandControlStatus(`已载入“${name}”目标 · 尚未发送`);
    }
  });
  samplingPresetButtons?.addEventListener('click', event => {
    const button = event.target.closest('[data-sampling-preset]');
    if (button) loadCustomHandPreset(button.dataset.samplingPreset);
  });
  samplingSendHandPose?.addEventListener('click', async () => {
    samplingSendHandPose.disabled = true;
    samplingSendHandPose.classList.remove('sent', 'failed');
    samplingSendHandPose.classList.add('sending');
    samplingSendHandPose.textContent = '发送中…';
    const sent = await publishHandPose([...handTargetPose]);
    samplingSendHandPose.classList.remove('sending');
    samplingSendHandPose.classList.add(sent ? 'sent' : 'failed');
    samplingSendHandPose.textContent = sent ? '已发送' : '发送失败';
    setTimeout(() => {
      samplingSendHandPose.classList.remove('sent', 'failed');
      samplingSendHandPose.textContent = '发送姿态';
      samplingSendHandPose.disabled = false;
    }, 1400);
  });
  readHandPose.addEventListener('click', () => {
    if (!handMeasuredPose) { setHandControlStatus('尚未收到 /cb_left_hand_state', 'error'); return; }
    handPendingCommand = null;
    handDirtyIndices.clear();
    applyHandTargetPose(handMeasuredPose, false);
    setHandControlStatus('已把机械手实际位置载入滑杆', 'ok');
  });
  sendHandPose.addEventListener('click', () => publishHandPose());
  saveHandPreset.addEventListener('click', () => {
    const name = handPresetName.value.trim();
    if (!name) { setHandControlStatus('请先填写预设名称', 'error'); return; }
    const presets = loadStoredPresets(); presets[name] = [...handTargetPose];
    localStorage.setItem('linkerhand-l25-presets', JSON.stringify(presets));
    handPresetName.value = ''; refreshPresetSelect(); handPresetSelect.value = name;
    setHandControlStatus(`预设“${name}”已保存在本机浏览器`, 'ok');
  });
  loadHandPreset.addEventListener('click', () => {
    loadCustomHandPreset(handPresetSelect.value);
  });
  deleteHandPreset.addEventListener('click', () => {
    const name = handPresetSelect.value; if (!name) return;
    const presets = loadStoredPresets(); delete presets[name];
    localStorage.setItem('linkerhand-l25-presets', JSON.stringify(presets)); refreshPresetSelect();
    setHandControlStatus(`已删除预设“${name}”`);
  });
  calibrateHandPose.addEventListener('click', () => {
    if (!handMeasuredPose) { setHandControlStatus('无法标定：尚未收到机械手实际位置', 'error'); return; }
    localStorage.setItem('linkerhand-l25-calibration', JSON.stringify(handMeasuredPose));
    applyHandTargetPose(handMeasuredPose, false);
    setHandControlStatus('软件基准已保存；驱动重启后可载入此姿态', 'ok');
  });
  restoreCalibration.addEventListener('click', () => {
    try {
      const pose = JSON.parse(localStorage.getItem('linkerhand-l25-calibration') || 'null');
      if (!validL25Pose(pose)) throw new Error('尚未保存标定基准');
      applyHandTargetPose(pose); setHandControlStatus('已载入软件标定基准 · 点击“发送姿态”执行', 'ok');
    } catch (error) { setHandControlStatus(error.message, 'error'); }
  });
}
