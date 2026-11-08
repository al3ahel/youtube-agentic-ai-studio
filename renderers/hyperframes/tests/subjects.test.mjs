import assert from "node:assert/strict";
import {before, after, test} from "node:test";
import {createServer} from "node:http";
import {readFile, mkdir} from "node:fs/promises";
import {spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import path from "node:path";
import {fileURLToPath} from "node:url";
import puppeteer from "puppeteer-core";

const renderer = path.resolve(fileURLToPath(new URL("../", import.meta.url)));
const root = path.join(renderer, ".smoke");
const browserPath = process.env.YAAS_TEST_BROWSER || process.env.HYPERFRAMES_BROWSER_PATH;
const skip = browserPath ? false : "Set YAAS_TEST_BROWSER to an installed Chromium";
const hash = bytes => createHash("sha256").update(bytes).digest("hex");
const types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".wav": "audio/wav"};
let browser, server, origin;

before(async () => {
  if (skip) return;
  const python = process.env.YAAS_TEST_PYTHON || process.env.PYTHON || "python";
  for (const args of [[], ["--portrait"]]) {
    const prepared = spawnSync(python, [path.join(renderer, "subjects_demo.py"), ...args], {cwd: renderer, encoding: "utf8", timeout: 30000});
    assert.equal(prepared.status, 0, prepared.error?.message || prepared.stderr || prepared.stdout);
  }
  await mkdir(path.join(root, "screenshots"), {recursive: true});
  server = createServer(async (request, response) => {
    try {
      const filename = path.resolve(root, `.${new URL(request.url, "http://localhost").pathname}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("Outside fixture");
      response.setHeader("Content-Type", types[path.extname(filename)] || "application/octet-stream");
      response.end(await readFile(filename));
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  const args = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  browser = await puppeteer.launch({executablePath: browserPath, headless: true, args, timeout: 30000, protocolTimeout: 30000});
});

after(async () => {
  if (browser) await browser.close();
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});

async function ready(page) {
  try {
    await page.waitForFunction(() => Boolean(window.__timelines?.["yaas-motion"] || document.body.dataset.motionError), {timeout: 20000});
  } catch (error) {
    const state = await page.evaluate(() => ({url: location.href, ready: document.readyState, fonts: document.fonts.status,
      text: document.body.innerText.slice(0, 800), scripts: [...document.scripts].map(node => node.src),
      gsap: typeof gsap, motion: typeof window.YAASMotion, shots: typeof window.YAASShots,
      objects: typeof window.YAASObjects, worlds: typeof window.YAASWorlds}));
    throw new Error(`${error.message}: ${JSON.stringify(state)}`);
  }
  assert.equal(await page.evaluate(() => document.body.dataset.motionError), undefined);
}

for (const orientation of ["portrait", "landscape"]) {
  test(`${orientation}: normal source planning renders concrete equipment with readable labels and deterministic reverse seeks`, {skip, timeout: 45000}, async t => {
    const page = await browser.newPage();
    t.after(() => page.close().catch(() => {}));
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
    await page.goto(`${origin}/subjects-${orientation}/motion/index.html`, {waitUntil: "domcontentloaded"});
    await ready(page);
    const saved = new Map();
    for (const [scene, time] of [[0, 3.3], [1, 7.3]]) {
      await page.evaluate(time => { window.__timelines["yaas-motion"].pause().seek(time, true); }, time);
      const state = await page.evaluate(index => {
        const scene = document.getElementById(`scene-${index}`);
        const viewport = scene.querySelector(".shot-world-window").getBoundingClientRect();
        const captions = [...document.querySelectorAll(".caption-cue")].filter(node => Number(getComputedStyle(node).opacity) > .99).map(node => node.getBoundingClientRect());
        const labels = [...scene.querySelectorAll(".world-label")].map(node => {
          const r = node.getBoundingClientRect();
          return {text: node.textContent, fits: r.left >= viewport.left && r.right <= viewport.right && r.top >= viewport.top && r.bottom <= viewport.bottom,
            overlapsCaption: captions.some(c => r.left < c.right && r.right > c.left && r.top < c.bottom && r.bottom > c.top)};
        });
        return {subjects: [...scene.querySelectorAll(".world-actor")].map(node => node.dataset.symbol), labels,
          geometry: [...scene.querySelectorAll(".subject-glyph")].map(node => ({subject: node.dataset.subject, paths: node.querySelectorAll("path").length})),
          channels: scene.querySelectorAll(".world-channel,.world-channel-under").length};
      }, scene);
      assert.deepEqual(state.subjects, scene === 0 ? ["armour"] : ["armour", "firearm"]);
      assert.deepEqual(state.labels.map(label => label.text), scene === 0 ? ["Armour"] : ["Armour", "Firearms"]);
      assert.ok(state.labels.every(label => label.fits && !label.overlapsCaption), JSON.stringify(state));
      assert.ok(state.geometry.every(glyph => glyph.paths >= 5), "A typed subject must produce its actual original SVG geometry");
      assert.equal(state.channels, 0, "A reveal cannot invent a projectile/energy transfer between named equipment");
      const frame = await page.screenshot({path: path.join(root, "screenshots", `subjects-${orientation}-${scene}.png`)});
      saved.set(time, hash(frame));
    }
    await page.evaluate(() => { window.__timelines["yaas-motion"].pause().seek(4.2, true); });
    assert.notEqual(hash(await page.screenshot()), saved.get(7.3), "The equipment must enter/reframe on the saved shot clock");
    for (const time of [3.3, 7.3, 3.3]) {
      await page.evaluate(time => { window.__timelines["yaas-motion"].pause().seek(time, true); }, time);
      assert.equal(hash(await page.screenshot()), saved.get(time), `Reverse seek changed the composed equipment pixels at ${time}`);
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 8);
    assert.deepEqual(errors, []);
    await page.close();
  });

  test(`${orientation}: saved version-1 equipment actors retain actual geometry`, {skip, timeout: 45000}, async t => {
    const page = await browser.newPage();
    t.after(() => page.close().catch(() => {}));
    await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
    await page.goto(`${origin}/subjects-${orientation}-legacy/motion/index.html`, {waitUntil: "domcontentloaded"});
    await ready(page);
    await page.evaluate(() => { window.__timelines["yaas-motion"].pause().seek(7.3, true); });
    assert.deepEqual(await page.$$eval("#scene-1 .subject-glyph", nodes => nodes.map(node => node.dataset.subject)), ["armour", "firearm"]);
    assert.equal(await page.$$eval('#scene-1 .illustrated-item[data-symbol="concept"]', nodes => nodes.length), 0);
    const frame = await page.screenshot();
    await page.evaluate(() => { window.__timelines["yaas-motion"].pause().seek(1, true); });
    await page.evaluate(() => { window.__timelines["yaas-motion"].pause().seek(7.3, true); });
    assert.equal(hash(await page.screenshot()), hash(frame));
    await page.close();
  });
}
