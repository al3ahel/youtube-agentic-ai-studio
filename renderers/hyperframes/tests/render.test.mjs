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
      opacity: style.opacity, visibility: style.visibility, transform: style.transform,
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
