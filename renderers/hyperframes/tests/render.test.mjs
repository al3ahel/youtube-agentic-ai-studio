import assert from "node:assert/strict";
import {after, before, test} from "node:test";
import {createServer} from "node:http";
import {readFile, stat, mkdir, writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {createHash} from "node:crypto";
import puppeteer from "puppeteer-core";

const fixtureRoot = path.resolve(fileURLToPath(new URL("../.smoke/", import.meta.url)));
const browserPath = process.env.YAAS_TEST_BROWSER || process.env.HYPERFRAMES_BROWSER_PATH;
const skipped = browserPath ? false : "Set YAAS_TEST_BROWSER to an installed Chrome/Chromium for browser tests";
let browser, server, origin;
const errors = [];
const mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2", ".wav": "audio/wav", ".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png"};

before(async () => {
  if (skipped) return;
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const filename = path.resolve(fixtureRoot, `.${pathname}`);
      if (!filename.startsWith(`${fixtureRoot}${path.sep}`)) throw new Error("Outside fixture directory");
      await stat(filename);
      response.writeHead(200, {"Content-Type": mime[path.extname(filename)] || "application/octet-stream", "Access-Control-Allow-Origin": "*"});
      response.end(await readFile(filename));
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  const args = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  browser = await puppeteer.launch({executablePath: browserPath, headless: true, args, timeout: 20000});
});

after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

function hash(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
async function domState(page) {
  return page.evaluate(() => [...document.querySelectorAll("#design-canvas *")].map((node, index) => {
    const style = getComputedStyle(node), rect = node.getBoundingClientRect();
    return {index, class: node.getAttribute("class"), text: node.children.length ? null : node.textContent,
      opacity: style.opacity, visibility: style.visibility, transform: style.transform, fill: style.fill, stroke: style.stroke,
      strokeDashoffset: style.strokeDashoffset,
      x: rect.x, y: rect.y, width: rect.width, height: rect.height};
  }));
}

async function pixelDifference(page, before, after) {
  return page.evaluate(async ({before, after}) => {
    async function pixels(encoded) {
      const image = new Image();
      image.src = `data:image/png;base64,${encoded}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext("2d", {willReadFrequently: true});
      context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, image.width, image.height).data;
    }
    const [left, right] = await Promise.all([pixels(before), pixels(after)]);
    let changed = 0, maxDelta = 0;
    for (let index = 0; index < left.length; index += 4) {
      let different = false;
      for (let channel = 0; channel < 3; channel++) {
        const delta = Math.abs(left[index + channel] - right[index + channel]);
        if (delta) different = true;
        maxDelta = Math.max(maxDelta, delta);
      }
      if (different) changed++;
    }
    return {changed, maxDelta, total: left.length / 4};
  }, {before: Buffer.from(before).toString("base64"), after: Buffer.from(after).toString("base64")});
}

async function seekForCapture(page, time) {
  await page.evaluate(async time => {
    await window.YAASMotion.seek(time);
    // The timeline is paused: these frames only flush the browser compositor.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, time);
}

async function openFixture(orientation, preview = false, viewport, transformPlan) {
  const page = await browser.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewport(viewport || (orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360}));
  if (transformPlan) {
    const source = await readFile(path.join(fixtureRoot, orientation, "motion", "index.html"), "utf8");
    const modified = source.replace(/(<script type="application\/json" id="scene-plan">)([\s\S]*?)(<\/script>)/,
      (match, start, data, end) => `${start}${JSON.stringify(transformPlan(JSON.parse(data)))}${end}`);
    await page.setRequestInterception(true);
    page.on("request", request => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
        return request.respond({status: 200, contentType: "text/html", body: modified});
      }
      return request.continue();
    });
  }
  await page.goto(`${origin}/${orientation}/motion/index.html${preview ? "?preview=1" : ""}`, {waitUntil: "domcontentloaded", timeout: 20000});
  await page.evaluate(() => window.YAASMotionReady);
  return page;
}

for (const orientation of ["landscape", "portrait"]) {
  test(`${orientation}: actual v2 director plan shows rise, flotation and power transfer on the saved clock`, {skip: skipped}, async () => {
    const page = await browser.newPage();
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
    await page.goto(`${origin}/directed-${orientation}/motion/index.html`, {waitUntil: "domcontentloaded"});
    const plan = await page.evaluate(async () => (await window.YAASMotionReady).plan);
    assert.deepEqual(plan.scenes.map(scene => [scene.graphics_version, scene.staging.world, scene.staging.action]),
      [[2, "flood", "rise"], [2, "flood", "float"], [2, "generic", "connect"]]);
    assert.equal(await page.$$eval(".node-card,.arranged-card,.orbit-card,.illustrated-item", nodes => nodes.length), 0);
    const snapshots = new Map();
    await mkdir(path.join(fixtureRoot, "screenshots"), {recursive: true});
    for (const time of [.5, 3.25, 4.5, 8.75, 10.5, 13.5]) {
      await seekForCapture(page, time);
      const state = await domState(page);
      const frame = await page.screenshot({path: path.join(fixtureRoot, "screenshots", `directed-${orientation}-${time}.png`)});
      const bounds = await page.evaluate(() => {
        const scene = [...document.querySelectorAll(".world-scene")].find(node => Number(getComputedStyle(node).opacity) > .99);
        const stage = scene.querySelector(".world-stage").getBoundingClientRect();
        const labels = [...scene.querySelectorAll(".world-label")].map(label => {
          const range = document.createRange(); range.selectNodeContents(label);
          const r = range.getBoundingClientRect();
          return {text: label.textContent, fits: r.left >= stage.left - 1 && r.right <= stage.right + 1 && r.top >= stage.top - 1 && r.bottom <= stage.bottom + 1};
        });
        const heading = scene.querySelector(".scene-title");
        const actor = scene.querySelector('.world-actor[data-role="subject"]')?.getBoundingClientRect();
        return {labels, titleLines: heading.offsetHeight / parseFloat(getComputedStyle(heading).lineHeight),
          subjectWidth: actor?.width / stage.width,
          water: scene.querySelector('[data-action-part="water-level"]')?.getAttribute("transform"),
          home: scene.querySelector('[data-action-part="floating-home"]')?.getAttribute("transform"),
          battery: scene.querySelector('[data-symbol="battery"] .world-energy')?.getAttribute("transform")};
      });
      assert.ok(bounds.labels.length && bounds.labels.every(label => label.fits), `${time}: ${JSON.stringify(bounds)}`);
      assert.ok(bounds.titleLines <= 2.1, `${time}: ${JSON.stringify(bounds)}`);
      assert.ok(bounds.subjectWidth >= .15, `Subject is too small: ${JSON.stringify(bounds)}`);
      snapshots.set(time, {state, frame, bounds});
    }
    assert.notEqual(snapshots.get(.5).bounds.water, snapshots.get(3.25).bounds.water, "Floodwater must rise through the shared world");
    assert.notEqual(snapshots.get(4.5).bounds.home, snapshots.get(8.75).bounds.home, "A source-explicit floating home must rise with the water");
    assert.notEqual(snapshots.get(10.5).bounds.battery, snapshots.get(13.5).bounds.battery, "Energy transfer must leave a charged storage result");
    await seekForCapture(page, 13.5);
    assert.ok(await page.$eval('#scene-2 [data-action-part="powered-windows"]', node => Number(getComputedStyle(node).opacity) <= .151),
      "The home must stay unpowered while its incoming transfer is still travelling");
    await seekForCapture(page, 14 - 1 / 12);
    assert.ok(await page.$eval('#scene-2 [data-action-part="powered-windows"]', node => Number(getComputedStyle(node).opacity) > .8),
      "The power transfer must arrive and leave a visible result before the final encoded frame");
    for (const time of [13.5, .5, 8.75, 3.25, 10.5, 4.5, .5]) {
      await seekForCapture(page, time);
      assert.deepEqual(await domState(page), snapshots.get(time).state, `Reverse seek at ${time}`);
      const difference = await pixelDifference(page, snapshots.get(time).frame, await page.screenshot());
      // Going through the fully lit final SVG frame can rerasterize a few
      // battery/roof/caption edge pixels on Chromium's opacity layers. Exact
      // geometry, transforms, opacity, fill and stroke are asserted above.
      // Inspected fringes: at most 371/230400 pixels and 46/255 delta; this
      // bounded tolerance cannot replace the actor/arrival/state assertions.
      assert.ok(difference.maxDelta <= 48 && difference.changed <= difference.total * .002, `${time}: ${JSON.stringify(difference)}`);
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 14);
    await page.close();
  });

  test(`${orientation}: composed worlds retain readable actors, roles and labels across bounded actions and cameras`, {skip: skipped}, async () => {
    const cases = [
      {world: "flood", action: "rise", camera: "wide", symbols: ["water", "building"]},
      {world: "flood", action: "adapt", camera: "detail", symbols: ["people", "circuit", "water"]},
      {world: "flood", action: "float", camera: "follow", symbols: ["building", "water", "people"]},
      {world: "census", action: "record", camera: "reveal", symbols: ["census", "document"]},
      {world: "census", action: "gap", camera: "detail", symbols: ["census-gap", "document"]},
      {world: "mining", action: "extract", camera: "follow", symbols: ["mining", "trade"]},
      {world: "mining", action: "transfer", camera: "wide", symbols: ["mining", "trade", "people"]},
      {world: "generic", action: "reveal", camera: "locked", symbols: ["plant"]},
      {world: "generic", action: "assemble", camera: "reveal", symbols: ["circuit", "network", "document"]},
      {world: "generic", action: "connect", camera: "follow", symbols: ["solar", "battery", "building", "network"]},
      {world: "generic", action: "compare", camera: "detail", symbols: ["plant", "water"]},
    ];
    const page = await openFixture(orientation, false, undefined, plan => {
      plan.scenes = cases.map((row, index) => ({id: `world-${index}`, section_id: String(index), template: "explain", graphics_version: 2,
        staging: {version: 1, world: row.world, action: row.action, camera: row.camera}, start: index * 4, duration: 4,
        title: "A shared world with a visible mechanism", visual_goal: "Qualitative mechanism, not numerical data",
        items: row.symbols.map((symbol, i) => ({symbol, role: i === 0 ? "subject" : symbol === "water" ? "context" : "support", label: symbol.replaceAll("-", " ")})),
        beats: row.symbols.map((_, i) => ({time: .2 + i * .6, action: "reveal", target: `item-${i}`})),
      }));
      plan.duration = cases.length * 4; plan.captions = []; plan.words = [];
      return plan;
    });
    for (let index = 0; index < cases.length; index++) {
      for (const phase of [.3, 2.7, 3.8]) {
        await seekForCapture(page, index * 4 + phase);
        const check = await page.evaluate(index => {
          const scene = document.getElementById(`scene-${index}`), stage = scene.querySelector(".world-stage").getBoundingClientRect();
          return [...scene.querySelectorAll(".world-label")].map(label => {
            const range = document.createRange(); range.selectNodeContents(label); const r = range.getBoundingClientRect();
            return {text: label.textContent, fits: r.left >= stage.left - 1 && r.right <= stage.right + 1 && r.top >= stage.top - 1 && r.bottom <= stage.bottom + 1};
          });
        }, index);
        assert.ok(check.length && check.every(label => label.fits), `${JSON.stringify(cases[index])} @${phase}: ${JSON.stringify(check)}`);
      }
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), cases.length * 4);
    await page.close();
  });

  test(`${orientation}: legacy v1 census and mining actors retain deterministic seeks`, {skip: skipped}, async () => {
    const page = await browser.newPage();
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
    // This is the actual generated fixture and its copied runtime, not an API
    // interception or a manually injected plan.
    await page.goto(`${origin}/objects-${orientation}/motion/index.html`, {waitUntil: "domcontentloaded"});
    await page.evaluate(() => window.YAASMotionReady);
    assert.equal(await page.$$eval(".node-card,.arranged-card,.orbit-card,.comparison-card", nodes => nodes.length), 0);
    assert.equal(await page.$$eval(".object-scene", nodes => nodes.length), 2);
    const snapshots = new Map();
    await mkdir(path.join(fixtureRoot, "screenshots"), {recursive: true});
    for (const time of [.5, 2.5, 4.5, 7.5, 9.5, 12.5]) {
      await seekForCapture(page, time);
      const frame = await page.screenshot({path: path.join(fixtureRoot, "screenshots", `objects-${orientation}-${time}.png`)});
      snapshots.set(time, {state: await domState(page), frame});
      const layout = await page.evaluate(() => {
        const scene = [...document.querySelectorAll(".scene")].find(node => Number(getComputedStyle(node).opacity) > .99);
        const subject = [...scene.querySelectorAll(".illustrated-item")].filter(node => Number(getComputedStyle(node).opacity) > .99);
        const title = scene.querySelector(".scene-title"), titleStyle = getComputedStyle(title);
        const stage = scene.querySelector(".object-stage").getBoundingClientRect();
        const labels = subject.map(node => {
          const label = node.querySelector(".object-label").getBoundingClientRect();
          return label.left >= stage.left - 1 && label.right <= stage.right + 1 && label.top >= stage.top - 1 && label.bottom <= stage.bottom + 1;
        });
        return {subjects: subject.map(node => node.dataset.symbol), labels,
          titleLines: title.offsetHeight / parseFloat(titleStyle.lineHeight),
          actor: subject[0]?.querySelector('[data-actor="drill-into-rock"],[data-actor="census-scan"]')?.getAttribute("transform")};
      });
      assert.equal(layout.subjects.length, 1, JSON.stringify(layout));
      assert.ok(layout.labels.every(Boolean), JSON.stringify(layout));
      assert.ok(layout.titleLines <= 2.1, JSON.stringify(layout));
      assert.ok(layout.subjects[0].startsWith(time < 7 ? "census" : "mining"));
      snapshots.get(time).actor = layout.actor;
    }
    assert.notEqual(snapshots.get(.5).actor, snapshots.get(2.5).actor, "The census scanner must actually traverse records");
    assert.notEqual(snapshots.get(7.5).actor, snapshots.get(9.5).actor, "The drill must move into the rock");
    for (const time of [12.5, .5, 9.5, 4.5, 2.5, 7.5, .5]) {
      await seekForCapture(page, time);
      assert.deepEqual(await domState(page), snapshots.get(time).state);
      const repeated = await page.screenshot();
      const difference = await pixelDifference(page, snapshots.get(time).frame, repeated);
      // Chromium can rerasterize the rounded caption's 1px border after a
      // reverse seek. Allow that tiny fringe; all object/actor DOM states above
      // must still match exactly and a moved subject changes far more pixels.
      assert.ok(difference.maxDelta <= 24 && difference.changed <= difference.total * .001, `${time}: ${JSON.stringify(difference)}`);
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 14);
    await page.close();
  });

  test(`${orientation}: bounded object vocabulary renders real subjects safely and keeps labels in frame`, {skip: skipped}, async () => {
    const symbols = ["concept", "census", "census-gap", "people", "mining", "solar", "battery", "circuit", "water", "plant", "network", "trade", "conflict", "document", "orbit", "building"];
    const page = await openFixture(orientation, false, undefined, plan => {
      plan.visual_direction = {version: 1, look: "studio", rhythm: "measured", rationale: "Bounded vocabulary coverage"};
      plan.scenes = symbols.map((symbol, index) => ({
        id: `subject-${symbol}`, section_id: String(index), template: "explain", graphics_version: 1,
        start: index * 3, duration: 3, title: `Illustrating ${symbol.replaceAll("-", " ")}`, composition: "split",
        items: [{label: `A qualitative ${symbol.replaceAll("-", " ")} explanation`, symbol}, {label: "Continue the explanation", symbol}],
        beats: [{time: 0, action: "reveal", target: "item-0"}, {time: 2.9, action: "reveal", target: "item-1"}],
      }));
      plan.duration = symbols.length * 3; plan.captions = []; plan.words = [];
      return plan;
    });
    for (let index = 0; index < symbols.length; index++) {
      await seekForCapture(page, index * 3 + 1.5);
      const bounds = await page.evaluate(index => {
        const scene = document.getElementById(`scene-${index}`);
        const item = scene.querySelector(".illustrated-item");
        const label = item.querySelector(".object-label");
        const range = document.createRange(); range.selectNodeContents(label);
        const r = range.getBoundingClientRect(), container = scene.getBoundingClientRect();
        return {inside: r.left >= container.left && r.right <= container.right && r.top >= container.top && r.bottom <= container.bottom,
          actors: item.querySelectorAll("[data-actor]").length};
      }, index);
      assert.ok(bounds.inside && bounds.actors > 0, `${symbols[index]}: ${JSON.stringify(bounds)}`);
      if (["solar", "plant", "network"].includes(symbols[index])) {
        await page.screenshot({path: path.join(fixtureRoot, "screenshots", `object-${symbols[index]}-${orientation}.png`)});
      }
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), symbols.length * 3);
    await page.close();
  });

  test(`${orientation}: object flows, comparisons and timelines fit two to six subjects`, {skip: skipped}, async () => {
    const cases = ["flow", "comparison", "timeline"].flatMap(template => [2, 3, 6].map(count => ({template, count})));
    const page = await openFixture(orientation, false, undefined, plan => {
      plan.visual_direction = {version: 1, look: "blueprint", rhythm: "measured", rationale: "Diagram bounds"};
      plan.scenes = cases.map(({template, count}, index) => ({
        id: `diagram-${index}`, section_id: String(index), template, graphics_version: 1,
        start: index * 3, duration: 3, title: "Trace the subjects through the explanation",
        items: Array.from({length: count}, (_, item) => ({label: ["Sunlight reaches the solar panel", "Electrons move through the circuit", "The battery stores energy for later use", "Rain reaches the ground", "Water travels through plant roots", "Records travel through the network"][item], symbol: ["solar", "circuit", "battery", "water", "plant", "network"][item]})),
        beats: Array.from({length: count}, (_, item) => ({time: item * .3, action: "reveal", target: `item-${item}`})),
      }));
      plan.duration = cases.length * 3; plan.captions = []; plan.words = [];
      return plan;
    });
    const states = new Map();
    for (let index = 0; index < cases.length; index++) {
      await seekForCapture(page, index * 3 + 2.7);
      const result = await page.evaluate(index => {
        const scene = document.getElementById(`scene-${index}`);
        const stage = scene.querySelector(".object-stage").getBoundingClientRect();
        return [...scene.querySelectorAll(".object-label")].map(node => {
          const range = document.createRange(); range.selectNodeContents(node);
          const text = range.getBoundingClientRect();
          return {text: node.textContent, fits: text.left >= stage.left - 1 && text.right <= stage.right + 1 && text.top >= stage.top - 1 && text.bottom <= stage.bottom + 1};
        });
      }, index);
      assert.ok(result.every(row => row.fits), `${JSON.stringify(cases[index])}: ${JSON.stringify(result)}`);
      states.set(index, await domState(page));
    }
    for (const index of [8, 0, 4, 2, 6, 1]) {
      await seekForCapture(page, index * 3 + 2.7);
      assert.deepEqual(await domState(page), states.get(index));
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), cases.length * 3);
    await page.close();
  });

  test(`${orientation}: five art directions visibly differ and scene overrides survive reverse seeks`, {skip: skipped}, async () => {
    const looks = ["editorial", "blueprint", "studio", "botanical", "poster"];
    const page = await openFixture(orientation, false, undefined, plan => {
      plan.visual_direction = {version: 1, look: "editorial", rhythm: "measured", rationale: "Fixture"};
      plan.scenes = looks.map((look, index) => ({
        id: `look-${look}`, section_id: String(index), template: "explain", look, composition: "split",
        start: index * 2, duration: 2, title: "A clear idea, a different point of view",
        items: [{label: "Start with the evidence"}, {label: "Connect the important details"},
          {label: "Make the result understandable"}, {label: "Leave room for the next question"}],
        beats: [{time: .1, target: "all", action: "reveal"}],
      }));
      plan.duration = 10; plan.captions = []; plan.words = [];
      return plan;
    });
    const snapshots = new Map(), backgrounds = new Set();
    await mkdir(path.join(fixtureRoot, "screenshots"), {recursive: true});
    for (const [index, look] of looks.entries()) {
      await seekForCapture(page, index * 2 + 1.25);
      assert.equal(await page.$eval("#composition", node => node.dataset.look), look);
      backgrounds.add(await page.$eval("#composition", node => getComputedStyle(node).backgroundColor));
      snapshots.set(look, {pixels: await page.screenshot({path: path.join(fixtureRoot, "screenshots", `art-${look}-${orientation}.png`)}), state: await domState(page)});
      if (index) {
        const difference = await pixelDifference(page, snapshots.get(looks[index - 1]).pixels, snapshots.get(look).pixels);
        assert.ok(difference.changed > difference.total * .5 && difference.maxDelta > 40, `${look} must visibly change the frame, not just a label`);
      }
    }
    assert.equal(backgrounds.size, 5);
    for (const index of [3, 0, 4, 1, 2, 0]) {
      await seekForCapture(page, index * 2 + 1.25);
      const look = looks[index];
      assert.equal(await page.$eval("#composition", node => node.dataset.look), look);
      assert.deepEqual(await domState(page), snapshots.get(look).state);
      const difference = await pixelDifference(page, snapshots.get(look).pixels, await page.screenshot());
      assert.ok(difference.maxDelta <= 8 && difference.changed <= difference.total * .005, JSON.stringify(difference));
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 10);
    await page.close();
  });

  test(`${orientation}: split, stack and focus layouts keep dense text in bounds`, {skip: skipped}, async () => {
    const page = await openFixture(orientation, false, undefined, plan => {
      const templates = ["flow", "explain", "text"], compositions = ["split", "stack", "focus"];
      plan.visual_direction = {version: 1, look: "studio", rhythm: "calm", rationale: "Fixture"};
      plan.scenes = templates.flatMap(template => compositions.map(composition => ({
        id: `${template}-${composition}`, section_id: "one", template, composition,
        start: 0, duration: 2, title: "Ideas need a clear structure, a useful explanation, and enough room to read the supporting evidence",
        items: Array.from({length: 6}, (_, index) => ({label: `${index + 1}. Keep the supporting evidence readable and connected to the main idea, including the detail that explains why this particular step matters.`})),
        beats: [{time: .1, target: "all", action: "reveal"}],
      })));
      plan.scenes.forEach((scene, index) => { scene.start = index * 2; });
      plan.duration = 18; plan.captions = []; plan.words = [];
      return plan;
    });
    const bounds = [], geometries = new Map();
    for (let index = 0; index < 9; index++) {
      await seekForCapture(page, index * 2 + 1.25);
      const result = await page.evaluate(index => {
        const scene = document.getElementById(`scene-${index}`), stage = scene.querySelector(".scene-visual"), stageBox = stage.getBoundingClientRect();
        const failures = [];
        for (const node of scene.querySelectorAll(".arranged-card .item-label,.kinetic-title,.keyword")) {
          const rect = node.getBoundingClientRect();
          const range = document.createRange(); range.selectNodeContents(node);
          const textBox = range.getBoundingClientRect();
          const container = node.closest(".arranged-card") || stage;
          const box = container.getBoundingClientRect();
          if (rect.left < box.left - .5 || rect.top < box.top - .5 || rect.right > box.right + .5 || rect.bottom > box.bottom + .5
              || textBox.left < box.left - .5 || textBox.top < box.top - .5 || textBox.right > box.right + .5 || textBox.bottom > box.bottom + .5) {
            failures.push({scene: scene.dataset.sceneId, node: node.className, text: node.textContent.slice(0, 25),
              rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}, box: {x: box.x, y: box.y, width: box.width, height: box.height}});
          }
        }
        const captionTop = (window.YAASMotion.duration && document.getElementById("caption-mount").getBoundingClientRect().top);
        if (stageBox.bottom > captionTop + .5) failures.push({scene: scene.dataset.sceneId, stageBottom: stageBox.bottom, captionTop});
        return {failures, id: scene.dataset.sceneId, geometry: [...scene.querySelectorAll(".arranged-card,.kinetic-title,.keyword")].map(node => {
          const rect = node.getBoundingClientRect(); return [rect.x, rect.y, rect.width, rect.height];
        })};
      }, index);
      bounds.push(...result.failures); geometries.set(result.id, result.geometry);
    }
    assert.deepEqual(bounds, []);
    for (const template of ["flow", "explain", "text"]) {
      assert.notDeepEqual(geometries.get(`${template}-split`), geometries.get(`${template}-stack`));
      assert.notDeepEqual(geometries.get(`${template}-stack`), geometries.get(`${template}-focus`));
    }
    await page.close();
  });
}

for (const timing of ["late beats", "short scenes without beats"]) {
  test(`${timing}: visual effects cannot extend past their scene or narration`, {skip: skipped}, async () => {
    const page = await openFixture("landscape", false, undefined, plan => {
      for (const [index, scene] of plan.scenes.entries()) {
        scene.rhythm = ["calm", "measured", "brisk"][index % 3];
        if (timing === "late beats") {
          scene.beats = scene.beats.map(beat => ({...beat, time: scene.duration - .01}));
          scene.beats.push(...["highlight", "draw", "count"].map(action => ({
            time: scene.duration - .01, target: "scene", action,
          })));
        } else {
          scene.start = index * .1;
          scene.duration = .1;
          scene.beats = [];
        }
      }
      plan.duration = plan.scenes.at(-1).start + plan.scenes.at(-1).duration;
      plan.captions = [];
      return plan;
    });
    const result = await page.evaluate(() => {
      const timeline = window.__timelines["yaas-motion"];
      const overruns = [];
      for (const tween of timeline.getChildren(false, true, false)) {
        for (const target of tween.targets()) {
          if (!(target instanceof Element)) continue;
          const scene = target.closest(".scene");
          if (!scene) continue;
          const boundary = Number(scene.dataset.visualStart) + Number(scene.dataset.visualDuration);
          if (tween.endTime() > boundary + .000001) {
            overruns.push({scene: scene.dataset.sceneId, target: target.className.baseVal || target.className,
              end: tween.endTime(), boundary});
          }
        }
      }
      return {overruns, duration: timeline.duration(), narration: window.YAASMotion.duration};
    });
    assert.deepEqual(result.overruns, []);
    assert.ok(Math.abs(result.duration - result.narration) < .000001);
    await page.close();
  });
}

for (const orientation of ["landscape", "portrait"]) {
  test(`${orientation}: frame seeks preserve scene state and pixels in arbitrary order`, {skip: skipped}, async t => {
    const page = await openFixture(orientation);
    const frames = [1.25, 3.25, 5.25, 7.25, 9.25, 11.25];
    const expected = new Map();
    const states = new Map();
    await mkdir(path.join(fixtureRoot, "screenshots"), {recursive: true});
    for (const time of frames) {
      await seekForCapture(page, time);
      const visible = await page.evaluate(() => [...document.querySelectorAll(".scene")].filter(node => getComputedStyle(node).visibility === "visible" && Number(getComputedStyle(node).opacity) > .9).map(node => node.dataset.sceneId));
      assert.deepEqual(visible, [`scene-${Math.floor(time / 2)}`]);
      const image = await page.screenshot({path: path.join(fixtureRoot, "screenshots", `${orientation}-${Math.floor(time / 2)}.png`)});
      expected.set(time, image);
      states.set(time, await domState(page));
    }
    let largestDifference = {changed: 0, maxDelta: 0};
    for (const time of [9.25, 1.25, 11.25, 3.25, 7.25, 5.25, 1.25]) {
      await seekForCapture(page, time);
      const actual = await page.screenshot();
      assert.deepEqual(await domState(page), states.get(time), `Scene geometry/content changed at ${time}s`);
      if (hash(actual) !== hash(expected.get(time))) {
        const difference = await pixelDifference(page, expected.get(time), actual);
        largestDifference = {changed: Math.max(largestDifference.changed, difference.changed), maxDelta: Math.max(largestDifference.maxDelta, difference.maxDelta)};
        // Chrome can round a few antialiased edge pixels differently after a
        // repaint even with byte-identical computed scene state. This tolerance
        // is bounded to 8/255 on <=0.5% of pixels; geometry and text are exact.
        // Fractional portrait scaling produces the larger edge-pixel differences.
        if (difference.maxDelta > 8 || difference.changed > difference.total * .005) {
          await writeFile(path.join(fixtureRoot, "screenshots", `${orientation}-seek-mismatch-${time}.png`), actual);
        }
        assert.ok(difference.maxDelta <= 8 && difference.changed <= difference.total * .005,
          `Frame ${time} changed beyond antialias tolerance: ${JSON.stringify(difference)}`);
      }
    }
    t.diagnostic(`Largest repaint difference: ${largestDifference.changed} pixels, ${largestDifference.maxDelta}/255 per channel; exact scene state`);
    assert.deepEqual(errors, []);
    await page.close();
  });

  test(`${orientation}: readable template bounds and local assets`, {skip: skipped}, async () => {
    const page = await openFixture(orientation);
    const result = await page.evaluate(() => {
      const canvas = document.getElementById("design-canvas");
      const canvasBox = canvas.getBoundingClientRect();
      const bounds = [];
      for (const scene of document.querySelectorAll(".scene")) {
        const elements = [...scene.querySelectorAll(".scene-title,.item-label,.kinetic-title,.bar-value")];
        for (const node of elements) {
          if (getComputedStyle(node).display === "none") continue;
          const box = node.getBoundingClientRect();
          if (box.width && (box.left < canvasBox.left - 1 || box.right > canvasBox.right + 1 || box.top < canvasBox.top - 1 || box.bottom > canvasBox.bottom + 1)) bounds.push({text: node.textContent, box: {left: box.left, top: box.top, right: box.right, bottom: box.bottom}});
        }
      }
      return {
        bounds,
        external: [...document.querySelectorAll("script[src],link[href],audio[src],video[src],img[src]")].map(node => node.src || node.href).filter(url => new URL(url).origin !== location.origin),
        pictures: [...document.querySelectorAll(".scene-photo img")].map(image => ({loaded: image.complete && image.naturalWidth > 0, local: image.getAttribute("src").startsWith("assets/picture-")})),
        photoOverlaps: [...document.querySelectorAll(".image-scene:not(.photo-hero)")].filter(scene => {
          const photo = scene.querySelector(".scene-photo").getBoundingClientRect();
          const diagram = scene.querySelector(".scene-visual").getBoundingClientRect();
          return Math.min(photo.right, diagram.right) - Math.max(photo.left, diagram.left) > 1 &&
            Math.min(photo.bottom, diagram.bottom) - Math.max(photo.top, diagram.top) > 1;
        }).map(scene => scene.dataset.sceneId),
        duration: window.__timelines["yaas-motion"].duration(),
      };
    });
    assert.deepEqual(result.bounds, []);
    assert.deepEqual(result.external, []);
    assert.equal(result.pictures.length, 5);
    assert.ok(result.pictures.every(image => image.loaded && image.local));
    assert.deepEqual(result.photoOverlaps, []);
    assert.equal(result.duration, 14);
    await page.close();
  });
}

test("picture camera motion follows deterministic seeks without playing narration", {skip: skipped}, async () => {
  const page = await openFixture("landscape");
  const pictureState = () => page.evaluate(() => ({
    transform: getComputedStyle(document.querySelector("#scene-1 .scene-photo img")).transform,
    paused: document.getElementById("narration").paused,
  }));
  await seekForCapture(page, 2.2);
  const first = await pictureState();
  await seekForCapture(page, 3.8);
  const later = await pictureState();
  assert.notEqual(later.transform, first.transform);
  assert.equal(later.paused, true);
  await seekForCapture(page, 9.1);
  await seekForCapture(page, 2.2);
  assert.deepEqual(await pictureState(), first);
  await page.close();
});

for (const orientation of ["landscape", "portrait"]) {
  test(`${orientation}: only measured caption words highlight, with exact boundaries and deterministic seeks`, {skip: skipped}, async () => {
    const page = await openFixture(orientation, false, undefined, plan => {
      plan.caption_style = "highlight"; plan.timing_source = "measured_sections";
      plan.sections.forEach(section => section.caption_timing = "word");
      plan.sections[2].caption_timing = "estimated";
      plan.captions = [
        {start: .25, end: 1.7, text: "Hello <world>"},
        {start: 2.25, end: 3.7, text: "مرحبا بالعالم"},
        {start: 4.25, end: 5.7, text: "Estimated words"},
        {start: 6.25, end: 7.7, text: "Missing measured word"},
      ];
      plan.words = [
        {word: "Hello", start: .3, end: .6, section_id: plan.sections[0].id},
        {word: "<world>", start: .8, end: 1.2, section_id: plan.sections[0].id},
        {word: "مرحبا", start: 2.3, end: 2.6, section_id: plan.sections[1].id},
        {word: "بالعالم", start: 2.8, end: 3.2, section_id: plan.sections[1].id},
        {word: "Estimated", start: 4.3, end: 4.6, section_id: plan.sections[2].id},
        {word: "words", start: 4.8, end: 5.2, section_id: plan.sections[2].id},
        {word: "Missing", start: 6.3, end: 6.6, section_id: plan.sections[3].id},
        {word: "word", start: 6.8, end: 7.2, section_id: plan.sections[3].id},
      ];
      return plan;
    });
    assert.equal(await page.$$eval(".caption-word", nodes => nodes.length), 4);
    assert.equal(await page.$("#caption-mount world"), null, "Caption text is never parsed as HTML");
    assert.deepEqual(await page.$$eval(".caption-cue", nodes => nodes.map(node => node.textContent)), ["Hello <world>", "مرحبا بالعالم", "Estimated words", "Missing measured word"]);
    const active = async time => {
      await seekForCapture(page, time);
      return page.$$eval(".caption-word", nodes => nodes.filter(node => getComputedStyle(node).color !== "rgb(255, 255, 255)" && Number(getComputedStyle(node.parentElement).opacity) > .99).map(node => node.textContent));
    };
    for (const [time, expected] of [[.29, []], [.3, ["Hello"]], [.6, []], [.8, ["<world>"]], [1.2, []], [2.3, ["مرحبا"]], [3.2, []], [6.4, []], [.8, ["<world>"]], [.3, ["Hello"]]]) {
      assert.deepEqual(await active(time), expected, `Active words at ${time}s`);
    }
    await seekForCapture(page, 2.4);
    assert.equal(await page.$$eval(".caption-cue", nodes => getComputedStyle(nodes[1]).direction), "rtl");
    const before = await page.$$eval(".caption-word", nodes => nodes.map(node => node.getAttribute("style")));
    await seekForCapture(page, 13.5); await seekForCapture(page, .1); await seekForCapture(page, 2.4);
    assert.deepEqual(await page.$$eval(".caption-word", nodes => nodes.map(node => node.getAttribute("style"))), before);
    await mkdir(path.join(fixtureRoot, "screenshots"), {recursive: true});
    await page.screenshot({path: path.join(fixtureRoot, "screenshots", `measured-captions-${orientation}.png`)});
    await page.close();
  });
}

test("caption style keeps plain phrases or hides captions without changing the scene", {skip: skipped}, async () => {
  for (const style of ["plain", "off"]) {
    const page = await openFixture("landscape", false, undefined, plan => {
      plan.caption_style = style;
      return plan;
    });
    await seekForCapture(page, .8);
    assert.equal(await page.$$eval(".caption-word", nodes => nodes.length), 0);
    assert.equal(await page.$$eval(".caption-cue", nodes => nodes.length > 0), style === "plain");
    assert.ok(await page.$$eval("#scene-mount .scene", nodes => nodes.length > 0));
    await page.close();
  }
});

test("preview stays paused, fits a narrow iframe, and exposes seek controls", {skip: skipped}, async () => {
  const page = await openFixture("portrait", true, {width: 320, height: 600});
  await page.waitForFunction(() => Boolean(window.YAASMotionPreview));
  const first = await page.evaluate(() => ({time: window.YAASMotion.currentTime, paused: document.getElementById("narration").paused}));
  await new Promise(resolve => setTimeout(resolve, 150));
  const second = await page.evaluate(() => ({time: window.YAASMotion.currentTime, paused: document.getElementById("narration").paused}));
  assert.deepEqual(second, first);
  assert.equal(first.paused, true);
  await page.evaluate(() => window.YAASMotion.seek(9.25));
  const seek = await page.evaluate(() => ({time: window.YAASMotion.currentTime, audio: document.getElementById("narration").currentTime, scrollWidth: document.body.scrollWidth, width: innerWidth}));
  assert.equal(seek.time, 9.25);
  assert.equal(seek.audio, 9.25);
  assert.ok(seek.scrollWidth <= seek.width);
  await page.screenshot({path: path.join(fixtureRoot, "screenshots", "portrait-preview-320.png")});
  await page.close();
});
