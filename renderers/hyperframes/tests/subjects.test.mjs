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

async function sceneState(page) {
  return page.evaluate(() => [...document.querySelectorAll("#design-canvas, #design-canvas *")].map(node => {
    const style = getComputedStyle(node), box = node.getBoundingClientRect(), matrix = node.getScreenCTM?.();
    return {tag: node.tagName, attributes: [...node.attributes].map(attribute => [attribute.name, attribute.value]),
      text: node.children.length ? null : node.textContent,
      transform: style.transform, origin: style.transformOrigin, opacity: style.opacity, visibility: style.visibility,
      fill: style.fill, stroke: style.stroke, dash: style.strokeDashoffset,
      matrix: matrix ? [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f] : null,
      box: [box.x, box.y, box.width, box.height]};
  }));
}

async function seekForCapture(page, time) {
  await page.evaluate(async time => {
    window.__timelines["yaas-motion"].pause().seek(time, true);
    // Flush the paused scene's compositor, using no separate animation clock.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, time);
}

async function assertStableFrame(page, before, after, expectedState, t) {
  assert.deepEqual(await sceneState(page), expectedState, "Reverse seeks must preserve exact geometry, SVG matrices, paint state and text");
  if (hash(after) === hash(before)) return;
  const difference = await page.evaluate(async ({before, after}) => {
    const decode = async encoded => {
      const image = new Image(); image.src = `data:image/png;base64,${encoded}`; await image.decode();
      const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext("2d", {willReadFrequently: true}); context.drawImage(image, 0, 0);
      return {width: image.width, pixels: context.getImageData(0, 0, image.width, image.height).data};
    };
    const [left, right] = await Promise.all([decode(before), decode(after)]);
    // Subject rendering owns the visible world window (or the legacy visual
    // stage). Heading and caption state is still checked exactly above; their
    // compositor edges belong to the separate full-frame renderer tests.
    const stages = [...document.querySelectorAll(".scene")]
      .filter(node => getComputedStyle(node).visibility === "visible" && Number(getComputedStyle(node).opacity) > .99)
      .map(node => (node.querySelector(".shot-world-window") || node.querySelector(".scene-visual"))?.getBoundingClientRect())
      .filter(Boolean);
    const textBounds = [...document.querySelectorAll(".world-label,.object-label")]
      .filter(node => getComputedStyle(node).visibility !== "hidden")
      .map(node => node.getBoundingClientRect());
    const captionOverlays = [...document.querySelectorAll(".caption-cue")]
      .filter(node => getComputedStyle(node).visibility !== "hidden" && Number(getComputedStyle(node).opacity) > .99)
      .map(node => node.getBoundingClientRect());
    let changed = 0, maxDelta = 0, outsideText = 0, total = 0;
    for (let index = 0; index < left.pixels.length; index += 4) {
      const x = index / 4 % left.width, y = Math.floor(index / 4 / left.width);
      if (!stages.some(box => x >= Math.ceil(box.left) && x < Math.floor(box.right)
        && y >= Math.ceil(box.top) && y < Math.floor(box.bottom))) continue;
      // The portrait caption overlays the last few stage rows. Its rounded
      // border has a one-pixel antialias footprint outside the CSS bounds.
      // These are caption pixels, not the subject SVG's rasterization.
      if (captionOverlays.some(box => x >= Math.floor(box.left) - 1 && x <= Math.ceil(box.right) + 1
        && y >= Math.floor(box.top) - 1 && y <= Math.ceil(box.bottom) + 1)) continue;
      total++;
      const delta = Math.max(...[0, 1, 2, 3].map(channel => Math.abs(left.pixels[index + channel] - right.pixels[index + channel])));
      maxDelta = Math.max(maxDelta, delta);
      if (!delta) continue;
      changed++;
      if (!textBounds.some(box => x >= Math.floor(box.left) && x <= Math.ceil(box.right)
        && y >= Math.floor(box.top) && y <= Math.ceil(box.bottom))) outsideText++;
    }
    return {changed, maxDelta, outsideText, total};
  }, {before: Buffer.from(before).toString("base64"), after: Buffer.from(after).toString("base64")});
  // With identical DOM and SVG matrices, Chromium can round text-edge
  // coverage by one 8-bit value after repaint. Geometry outside text must
  // remain pixel-identical; no displaced glyph or broader fade is allowed.
  assert.ok(difference.total > 0 && difference.maxDelta <= 1 && difference.changed <= difference.total * .005 && difference.outsideText === 0,
    `Reverse seek changed more than text-edge rounding: ${JSON.stringify(difference)}`);
  t.diagnostic(`Exact scene state; ${difference.changed} subject-label pixels differ by at most ${difference.maxDelta}/255`);
}

for (const orientation of ["portrait", "landscape"]) {
  test(`${orientation}: paired comparisons give both source objects readable bounds without invented interactions`, {skip, timeout: 45000}, async t => {
    const page = await browser.newPage();
    t.after(() => page.close().catch(() => {}));
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
    const source = await readFile(path.join(root, `subjects-${orientation}`, "motion", "index.html"), "utf8");
    const html = source.replace(/(<script type="application\/json" id="scene-plan">)([\s\S]*?)(<\/script>)/,
      (match, start, data, end) => {
        const plan = JSON.parse(data), scene = plan.scenes[1];
        scene.template = "comparison";
        scene.staging.action = "compare";
        scene.staging.camera = "locked";
        Object.assign(scene.shot, {framing: "medium", camera: "locked"});
        return `${start}${JSON.stringify(plan)}${end}`;
      });
    await page.setRequestInterception(true);
    page.on("request", request => request.isNavigationRequest() && request.frame() === page.mainFrame()
      ? request.respond({status: 200, contentType: "text/html", body: html}) : request.continue());
    await page.goto(`${origin}/subjects-${orientation}/motion/index.html`, {waitUntil: "domcontentloaded"});
    await ready(page);
    await seekForCapture(page, 7.3);
    const state = await page.evaluate(() => {
      const scene = document.getElementById("scene-1"), viewport = scene.querySelector(".shot-world-window").getBoundingClientRect();
      const bounds = selector => [...scene.querySelectorAll(selector)].map(node => {
        const r = node.getBoundingClientRect();
        return {text: node.textContent, width: r.width, height: r.height,
          fits: r.left >= viewport.left - 1 && r.right <= viewport.right + 1 && r.top >= viewport.top - 1 && r.bottom <= viewport.bottom + 1};
      });
      return {actors: bounds(".world-actor"), labels: bounds(".world-label"), width: viewport.width, height: viewport.height,
        channels: scene.querySelectorAll(".world-channel,.world-channel-under").length};
    });
    assert.equal(state.actors.length, 2);
    assert.ok(state.actors.every(actor => actor.fits && actor.width > state.width * .12), JSON.stringify(state));
    assert.ok(state.actors[1].width > state.width * (orientation === "portrait" ? .6 : .3), "The elongated comparison subject must occupy its available width");
    assert.ok(state.labels.every(label => label.fits));
    assert.equal(state.channels, 0, "Comparison cannot imply a firing or energy-transfer event");
    const finalState = await sceneState(page);
    const final = await page.screenshot({path: path.join(root, "screenshots", `paired-comparison-${orientation}.png`)});
    await seekForCapture(page, 4.05);
    assert.notEqual(hash(await page.screenshot()), hash(final));
    await seekForCapture(page, 7.3);
    const reversed = await page.screenshot({path: path.join(root, "screenshots", `paired-comparison-${orientation}-reverse.png`)});
    await assertStableFrame(page, final, reversed, finalState, t);
    assert.deepEqual(errors, []);
  });

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
      await seekForCapture(page, time);
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
      saved.set(time, {frame, state: await sceneState(page)});
    }
    await seekForCapture(page, 4.2);
    assert.notEqual(hash(await page.screenshot()), hash(saved.get(7.3).frame), "The equipment must enter/reframe on the saved shot clock");
    for (const time of [3.3, 7.3, 3.3]) {
      await seekForCapture(page, time);
      await assertStableFrame(page, saved.get(time).frame, await page.screenshot(), saved.get(time).state, t);
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
    await seekForCapture(page, 7.3);
    assert.deepEqual(await page.$$eval("#scene-1 .subject-glyph", nodes => nodes.map(node => node.dataset.subject)), ["armour", "firearm"]);
    assert.equal(await page.$$eval('#scene-1 .illustrated-item[data-symbol="concept"]', nodes => nodes.length), 0);
    const frame = await page.screenshot();
    const finalState = await sceneState(page);
    await seekForCapture(page, 1);
    await seekForCapture(page, 7.3);
    await assertStableFrame(page, frame, await page.screenshot(), finalState, t);
    await page.close();
  });
}
