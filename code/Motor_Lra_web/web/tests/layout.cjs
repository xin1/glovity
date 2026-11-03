const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { chromium } = require(require.resolve('playwright', {
  paths: [process.cwd(), path.join(process.env.USERPROFILE || '', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node')]
}));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.resolve(__dirname, '..', 'index.html')).href);
    for (const width of [1440, 1100, 900, 650, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.waitForTimeout(150);
      const dimensions = await page.evaluate(() => {
        const rect = selector => {
          const { x, y, width, height, bottom } = document.querySelector(selector).getBoundingClientRect();
          return { x, y, width, height, bottom };
        };
        return { overflow: document.documentElement.scrollWidth > innerWidth,
          sample: rect('#tactilePad'), heat: rect('.heat-frame'),
          left: rect('.control-card'), center: rect('#tactileColumn'),
          side: rect('.side-panel'), parameters: rect('.parameter-editor') };
      });
      assert.equal(dimensions.overflow, false, `page overflows at ${width}px`);
      for (const box of [dimensions.sample, dimensions.heat]) {
        assert.ok(Math.abs(box.width / box.height - .5) < .02, `canvas ratio at ${width}px`);
      }
      if (width > 1000) {
        assert.ok(dimensions.left.x < dimensions.center.x && dimensions.center.x < dimensions.side.x);
        assert.ok(Math.abs(dimensions.left.bottom - dimensions.center.bottom) < 2);
        assert.ok(Math.abs(dimensions.parameters.bottom - dimensions.side.bottom) < 2);
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.waitForTimeout(100);
    const traceHeightBefore = await page.locator('#traceCanvas').evaluate(element => element.getBoundingClientRect().height);
    await page.setViewportSize({ width: 900, height: 1000 });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.waitForTimeout(150);
    const traceHeightAfter = await page.locator('#traceCanvas').evaluate(element => element.getBoundingClientRect().height);
    assert.ok(traceHeightAfter <= 590, 'left force chart must have a bounded height');
    assert.ok(Math.abs(traceHeightAfter - traceHeightBefore) < 2, 'left force chart must not grow after window resize');
    const logFill = await page.evaluate(() => {
      const card = document.querySelector('.log-card').getBoundingClientRect();
      const log = document.querySelector('#serialLog').getBoundingClientRect();
      return { cardBottom: card.bottom, logBottom: log.bottom, logHeight: log.height };
    });
    assert.ok(logFill.logHeight >= 100 && logFill.cardBottom - logFill.logBottom < 20,
      'serial log should fill the lower portion of its card');
    assert.equal(await page.locator('#robotAllFingers').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length), 3);
    assert.deepEqual(await page.locator('[data-joint]').evaluateAll(controls => controls.map(control => Number(control.querySelector('input[type="range"]').value))),
      Array.from({ length: 25 }, (_, index) => index >= 6 && index <= 9 ? 120 : index === 20 ? 225 : 255));
    assert.equal(await page.locator('[data-force-chart]').count(), 5);
    assert.equal(await page.locator('[data-matrix-canvas]').count(), 5);
    assert.equal(await page.locator('#robotFingerCards').count(), 0);
    await page.locator('#robotFingerTabs [data-finger="middle"]').click();
    assert.equal(await page.evaluate(() => robotFinger), 'middle');
    await page.locator('#tactileSourceToggle').click();
    await page.locator('#expandTactile').click();
    assert.equal(await page.locator('#tactileDialog').evaluate(el => el.open), true);
    const expanded = await page.locator('#tactilePad').boundingBox();
    assert.ok(Math.abs(expanded.width / expanded.height - .5) < .02);
    const pad = page.locator('#tactilePad');
    await pad.click({ position: { x: 50, y: 50 } });
    await page.locator('#tactileStopButton').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#tactileDialog').evaluate(el => el.open), false);
    assert.match(await page.locator('#tactileGridLabel').innerText(), /12 × 6/);
    await page.waitForTimeout(2100);
    fs.mkdirSync('.qa', { recursive: true });
    await page.screenshot({ path: '.qa/console-layout.png', fullPage: true });
    const sampleBox = await pad.boundingBox();
    await page.mouse.move(sampleBox.x + sampleBox.width * .53, sampleBox.y + sampleBox.height * .48);
    await page.mouse.down();
    await page.keyboard.press('+');
    assert.equal(await page.locator('#forceValue').innerText(), '5.0');
    await page.keyboard.press('-');
    await page.waitForTimeout(2100);
    await page.screenshot({ path: '.qa/console-pressure.png', fullPage: true });
    await page.mouse.up();
    assert.equal(await page.locator('#forceValue').innerText(), '0.0');
    for (const [button, mode] of [['defaultButton', 'DEFAULT'], ['shortButton', 'SHORT'], ['longButton', 'LONG'], ['sawButton', 'SAW'], ['collisionButton', 'COLLISION'], ['frictionButton', 'FRICTION']]) {
      await page.locator(`#${button}`).click();
      const box = await pad.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      assert.equal(await page.evaluate(() => selectedMode), mode);
      if (mode === 'SHORT') assert.equal(await page.locator('#ampValue').innerText(), '28');
      await page.mouse.up();
      assert.equal(await page.locator(`#${button}`).evaluate(el => el.classList.contains('active-effect')), true);
    }
    await page.locator('#handPresetName').fill('侧摆测试');
    await page.locator('#saveHandPreset').click();
    const customPresetButton = page.locator('#handBuiltinPresets [data-custom-preset="侧摆测试"]');
    const samplingPresetButton = page.locator('#samplingPresetButtons [data-sampling-preset="侧摆测试"]');
    assert.equal(await customPresetButton.count(), 1);
    assert.equal(await samplingPresetButton.count(), 1);
    assert.equal(await customPresetButton.innerText(), '侧摆测试');
    await samplingPresetButton.click({ force: true });
    assert.equal(await page.locator('#handPresetSelect').inputValue(), '侧摆测试');
    assert.match(await page.locator('#handControlStatus').innerText(), /已载入“侧摆测试”/);
    assert.equal(await samplingPresetButton.getAttribute('aria-pressed'), 'true');
    assert.equal(await samplingPresetButton.evaluate(el => el.classList.contains('selected')), true);
    await page.locator('#deleteHandPreset').click({ force: true });
    assert.equal(await customPresetButton.count(), 0);
    assert.equal(await samplingPresetButton.count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: five viewport widths, canvas proportions, alignment, modal, stop, density, modes, custom preset shortcut and browser errors.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
