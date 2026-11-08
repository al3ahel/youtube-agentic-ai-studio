import assert from "node:assert/strict";
import {before, after, test} from "node:test";
import {createServer} from "node:http";
import {readFile, mkdir} from "node:fs/promises";
import {createHash} from "node:crypto";
import {spawnSync} from "node:child_process";
import path from "node:path";
import {fileURLToPath} from "node:url";
import puppeteer from "puppeteer-core";

const root = path.resolve(fileURLToPath(new URL("../.smoke/", import.meta.url)));
const browserPath = process.env.YAAS_TEST_BROWSER || process.env.HYPERFRAMES_BROWSER_PATH;
const skip = browserPath ? false : "Set YAAS_TEST_BROWSER to an installed Chromium";
let browser, server, origin;
const errors = [];
const types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".wav": "audio/wav"};
const hash = value => createHash("sha256").update(value).digest("hex");

before(async () => {
  if (skip) return;
  await mkdir(path.join(root, "screenshots"), {recursive: true});
  server = createServer(async (request, response) => {
    try {
      const filename = path.resolve(root, `.${new URL(request.url, "http://localhost").pathname}`);
      if (!filename.startsWith(root + path.sep)) throw new Error("Outside fixture");
      response.setHeader("Content-Type", types[path.extname(filename)] || "application/octet-stream");
      const bytes = await readFile(filename);
      response.setHeader("Accept-Ranges", "bytes");
      const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range || "");
      if (range) {
        const start = Number(range[1]), end = Math.min(bytes.length - 1, range[2] ? Number(range[2]) : bytes.length - 1);
        response.writeHead(206, {"Content-Range": `bytes ${start}-${end}/${bytes.length}`, "Content-Length": end - start + 1});
        response.end(bytes.subarray(start, end + 1));
      } else {
        response.setHeader("Content-Length", bytes.length); response.end(bytes);
      }
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  const args = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  browser = await puppeteer.launch({executablePath: browserPath, headless: true, args, timeout: 30000});
});

after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

async function open(orientation, preview = false, transformPlan) {
  const page = await browser.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewport(orientation === "portrait" ? {width: 360, height: 640} : {width: 640, height: 360});
  if (transformPlan) {
    const source = await readFile(path.join(root, `shots-${orientation}`, "motion", "index.html"), "utf8");
    const html = source.replace(/(<script type="application\/json" id="scene-plan">)([\s\S]*?)(<\/script>)/,
      (match, start, data, end) => `${start}${JSON.stringify(transformPlan(JSON.parse(data)))}${end}`);
    await page.setRequestInterception(true);
    page.on("request", request => request.isNavigationRequest() && request.frame() === page.mainFrame()
      ? request.respond({status: 200, contentType: "text/html", body: html}) : request.continue());
  }
  await page.goto(`${origin}/shots-${orientation}/motion/index.html${preview ? "?preview=1" : ""}`, {waitUntil: "domcontentloaded", timeout: 30000});
  await page.evaluate(() => window.YAASMotionReady);
  if (preview) await page.waitForFunction(() => window.YAASMotionPreview);
  return page;
}

async function sample(page, scene, time, suppressEvents = false) {
  return page.evaluate(({scene, time, suppressEvents}) => {
    window.__timelines["yaas-motion"].pause().seek(time, suppressEvents);
    const canvas = document.querySelector(`#scene-${scene} canvas`);
    return {pixels: canvas.toDataURL(), phase: canvas.dataset.phase, contact: canvas.dataset.contact,
      projectile: canvas.dataset.projectileZ, projectileVisible: canvas.dataset.projectileVisible,
      resultView: canvas.dataset.resultView, meshes: Number(canvas.dataset.meshes || 0)};
  }, {scene, time, suppressEvents});
}

async function collageCaptionPixels(page) {
  const clips = await page.$$eval("#scene-6 .collage-label", labels => labels
    .filter(label => getComputedStyle(label).visibility === "visible" && Number(getComputedStyle(label.parentElement).opacity) > .05)
    .map(label => {
      const range = document.createRange(); range.selectNodeContents(label);
      const rect = range.getBoundingClientRect(), x = Math.max(0, Math.floor(rect.left) - 2), y = Math.max(0, Math.floor(rect.top) - 2);
      return {x, y, width: Math.min(innerWidth, Math.ceil(rect.right) + 2) - x, height: Math.min(innerHeight, Math.ceil(rect.bottom) + 2) - y};
    }));
  // Crop the captured full frame, not Chromium's viewport. Clipped CDP
  // screenshots can rerasterize rotated text with a different pixel origin.
  const pixels = spawnSync(process.env.YAAS_TEST_PYTHON || process.env.PYTHON || "python", ["-c",
    "import hashlib,io,json,sys; from PIL import Image; im=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGBA'); print(json.dumps([hashlib.sha256(im.crop((b['x'],b['y'],b['x']+b['width'],b['y']+b['height'])).tobytes()).hexdigest() for b in json.loads(sys.argv[1])]))",
    JSON.stringify(clips)], {input: await page.screenshot(), encoding: "utf8"});
  assert.equal(pixels.status, 0, pixels.stderr);
  return JSON.parse(pixels.stdout);
}

for (const orientation of ["portrait", "landscape"]) {
  test(`${orientation}: a settled result holds the real deformation without replaying contact in 2D or 3D`, {skip}, async () => {
    for (const technique of ["stage2d", "diagram3d"]) {
      const directed = resultView => plan => {
        // Test the app-derived renderer contract. Python source tests establish
        // when a result view is permitted; no model can author this flag.
        for (const scene of plan.scenes) if (scene.shot.technique === "diagram3d") scene.shot.technique = "stage2d";
        const scene = plan.scenes[1];
        Object.assign(scene.shot, {technique, camera: "locked"});
        scene.mechanism.action = "absorb";
        if (resultView) scene.mechanism_result_view = true;
        else delete scene.mechanism_result_view;
        return plan;
      };
      const page = await open(orientation, false, directed(true));
      const first = await sample(page, 1, 4.1), middle = await sample(page, 1, 6.0), last = await sample(page, 1, 7.8);
      for (const pose of [first, middle, last]) {
        assert.equal(pose.resultView, "true");
        assert.equal(pose.contact, "after");
        assert.equal(pose.projectileVisible, "false", "A consequence view must not introduce another projectile");
        assert.equal(hash(pose.pixels), hash(first.pixels), "A locked result must preserve its settled physical shape");
      }
      if (technique === "diagram3d") assert.ok(first.meshes > 0);
      await sample(page, 0, .8);
      assert.equal(hash((await sample(page, 1, 4.1, true)).pixels), hash(first.pixels), "Result pose drifted after reverse/suppressed seek");
      await page.screenshot({path: path.join(root, "screenshots", `settled-${technique}-${orientation}.png`)});
      await page.close();
      const control = await open(orientation, false, directed(false));
      const before = await sample(control, 1, 4.1), after = await sample(control, 1, 7.8);
      assert.equal(before.contact, "before"); assert.equal(after.contact, "after");
      assert.equal(before.resultView, "false"); assert.equal(after.projectileVisible, "true");
      assert.notEqual(hash(before.pixels), hash(first.pixels), "The settled view must change actual geometry/projectile pixels");
      assert.notEqual(hash(before.pixels), hash(after.pixels), "Ordinary contact scenes must retain their full action");
      await control.close();
    }
  });

  test(`${orientation}: collage frames identify their trusted pictures rather than repeating narration fragments`, {skip}, async () => {
    const page = await open(orientation, false, plan => {
      for (const scene of plan.scenes) if (scene.shot.technique === "diagram3d") scene.shot.technique = "stage2d";
      const scene = plan.scenes[6];
      scene.items = [{label: "Unrelated cut-off narration"}, {label: "Another incomplete sentence"}];
      Object.assign(scene.media_layers[0], {credit: "  Armour   object  · Museum · Public domain  ", source: "Museum"});
      Object.assign(scene.media_layers[1], {credit: "", source: "Verified photograph"});
      Object.assign(scene.media_layers[2], {credit: "Reinforcing plate · Photographer", source: "Museum"});
      return plan;
    });
    await page.evaluate(() => window.YAASMotion.seek(27.2));
    const labels = await page.$$eval("#scene-6 .collage-label", nodes => nodes.map(node => node.textContent));
    assert.deepEqual(labels, ["Armour object", "Verified photograph", "Reinforcing plate"]);
    assert.ok(!labels.some(label => /narration|sentence/.test(label)));
    await page.close();
  });

  test(`${orientation}: collage identity captions never leave occluded fragments and restore on reverse seeks`, {skip}, async t => {
    const identities = ["Archive object A", "Archive object B", "Public archive detail"];
    const page = await open(orientation, false, plan => {
      for (const scene of plan.scenes) if (scene.shot.technique === "diagram3d") scene.shot.technique = "stage2d";
      const scene = plan.scenes[6];
      scene.items = [{label: "Unrelated narration fragment"}, {label: "Another sentence"}];
      scene.media_layers.forEach((asset, i) => { asset.credit = identities[i] + " · Verified collection · Public domain"; });
      return plan;
    });
    t.after(() => page.close().catch(() => {}));
    const frames = new Map(); let settledFrame;
    for (const time of [24.15, 24.7, 25.1, 25.4, 25.8, 27.2]) {
      await page.evaluate(time => { window.__timelines["yaas-motion"].pause().seek(time, true); }, time);
      const labels = await page.evaluate(() => {
        const scene = document.getElementById("scene-6"), papers = [...scene.querySelectorAll(".collage-layer")];
        return papers.map(paper => {
          const label = paper.querySelector("figcaption"), visible = getComputedStyle(label).visibility === "visible";
          const occludedCharacters = [], range = document.createRange();
          if (visible && Number(getComputedStyle(paper).opacity) > .05) {
            for (let i = 0; i < label.textContent.length; i++) {
              if (!label.textContent[i].trim()) continue;
              range.setStart(label.firstChild, i); range.setEnd(label.firstChild, i + 1);
              const rect = range.getBoundingClientRect();
              // Browser paint/hit order is independent of the renderer's
              // overlap calculation; ignore transparent/inactive scenes.
              const topPaper = document.elementsFromPoint((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2)
                .map(node => node.closest(".collage-layer"))
                .find(node => papers.includes(node) && Number(getComputedStyle(node).opacity) > .001);
              if (topPaper !== paper) occludedCharacters.push(i);
            }
          }
          return {text: label.textContent, visible, opacity: Number(getComputedStyle(paper).opacity), occludedCharacters};
        });
      });
      assert.deepEqual(labels.map(label => label.text), identities, "Trusted identities must survive suppression intact");
      assert.ok(labels.every(label => !label.occludedCharacters.length), JSON.stringify({time, labels}));
      assert.ok(labels.some(label => label.visible && label.opacity > .05), "Keep unobstructed photo identities legible");
      if (time === 24.15) assert.equal(labels[0].visible, true, "The first photo is labelled before overlapping photos arrive");
      if (time === 27.2) {
        assert.ok(labels.some(label => !label.visible), "The actual overlapping layout needs whole-caption suppression");
        assert.equal(labels.at(-1).visible, true, "The foreground photo must retain its full identity");
        settledFrame = hash(await page.screenshot({path: path.join(root, "screenshots", `collage-caption-${orientation}.png`)}));
      }
      frames.set(time, await collageCaptionPixels(page));
    }
    for (const time of [24.7, 27.2, 24.15, 25.4, 27.2]) {
      await page.evaluate(time => { window.__timelines["yaas-motion"].pause().seek(time, true); }, time);
      assert.deepEqual(await collageCaptionPixels(page), frames.get(time), `Caption pixels drifted after reverse seek at ${time}`);
      if (time === 27.2) assert.equal(hash(await page.screenshot()), settledFrame, "The settled composition must remain deterministic");
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 40);
    await page.close();
  });

  test(`${orientation}: material action changes real 2D/3D pixels and survives reverse and suppressed-event seeks`, {skip}, async () => {
    const page = await open(orientation);
    assert.ok(await page.$eval("#scene-0", node => Number(getComputedStyle(node).opacity) > .99), "The first encoded frame needs a visible shot");
    await mkdir(path.join(root, "screenshots"), {recursive: true});
    const saved = new Map();
    for (const index of [0, 1, 2, 3, 4, 5]) {
      const early = await sample(page, index, index * 4 + .7);
      const late = await sample(page, index, index * 4 + 3.2);
      // Establishing views may hold a physical shape; the push still changes
      // their real canvas. Interaction shots must change an actual result.
      assert.notEqual(hash(early.pixels), hash(late.pixels), `No actual mechanism change in shot ${index}`);
      assert.equal(early.contact, "before"); assert.equal(late.contact, "after");
      assert.equal(late.phase, "0.800000");
      if (index >= 1 && index <= 4) assert.ok(late.meshes > 0, "3D must draw geometry, not an empty canvas");
      if ([1, 2, 3].includes(index)) assert.notEqual(early.projectile, late.projectile);
      saved.set(index, late);
      await page.screenshot({path: path.join(root, "screenshots", `shot-${orientation}-${index}.png`)});
    }
    for (const index of [5, 2, 0, 4, 1, 3, 0]) {
      const again = await sample(page, index, index * 4 + 3.2, true);
      assert.equal(hash(again.pixels), hash(saved.get(index).pixels), `Canvas drift after seek(t, true), shot ${index}`);
      assert.equal(again.phase, saved.get(index).phase);
    }
    assert.equal(await page.evaluate(() => window.__timelines["yaas-motion"].duration()), 40);
    assert.ok(await page.$$eval(".shot-surface canvas", nodes => nodes.filter(node => node.width > 1).length <= 2), "Hidden shots must release their full-frame bitmap buffers");
    await page.close();
  });

  test(`${orientation}: actual collage assets, footage and single saved pictures are composed without placeholder cards`, {skip}, async () => {
    const page = await open(orientation, true);
    await page.evaluate(() => window.YAASMotion.seek(26.8));
    const collage = await page.evaluate(() => {
      const scene = document.getElementById("scene-6");
      return {sources: [...scene.querySelectorAll(".collage-layer img")].map(node => node.currentSrc),
        opacity: [...scene.querySelectorAll(".collage-layer")].map(node => Number(getComputedStyle(node).opacity)),
        placeholders: scene.querySelectorAll(".node-card,.orbit-card,.keyword-row").length};
    });
    assert.equal(collage.sources.length, 3); assert.equal(new Set(collage.sources).size, 3);
    assert.ok(collage.opacity.every(value => value > .99)); assert.equal(collage.placeholders, 0);
    await page.waitForFunction(() => document.querySelector("#scene-7 video").readyState >= 2);
    await page.evaluate(() => window.YAASMotion.seek(30.8));
    await page.waitForFunction(() => Math.abs(document.querySelector("#scene-7 video").currentTime - 2.8) < .09 && !document.querySelector("#scene-7 video").seeking);
    const footage = await page.$eval("#scene-7 video", video => ({time: video.currentTime, parent: video.parentElement.className, muted: video.muted}));
    assert.ok(Math.abs(footage.time - 2.8) < .09, JSON.stringify(footage));
    assert.equal(footage.parent, "shot-footage"); assert.equal(footage.muted, true);
    assert.equal(await page.$$eval("#scene-7 .shot-footage-inset img", nodes => nodes.length), 1);
    await page.evaluate(() => window.YAASMotion.seek(34.8));
    assert.equal(await page.$$eval("#scene-8 .shot-footage > img", nodes => nodes.length), 1);
    assert.equal(await page.$eval(".film-header", node => getComputedStyle(node).display), "none");
    await page.close();
  });

  test(`${orientation}: separated-layer labels remain inside the frame and outside measured captions`, {skip}, async () => {
    const page = await open(orientation);
    await page.evaluate(() => window.YAASMotion.seek(19.2));
    const layout = await page.evaluate(() => {
      const frame = document.getElementById("composition").getBoundingClientRect();
      const captions = [...document.querySelectorAll(".caption-cue")].filter(node => Number(getComputedStyle(node).opacity) > .99).map(node => node.getBoundingClientRect());
      const labels = [...document.querySelectorAll("#scene-4 .shot-layer-label")].map(node => {
        const rect = node.getBoundingClientRect();
        return {text: node.textContent, x: rect.x, y: rect.y, width: rect.width, height: rect.height,
          fits: rect.left >= frame.left && rect.right <= frame.right && rect.top >= frame.top && rect.bottom <= frame.bottom,
          touchesCaption: captions.some(c => rect.left < c.right && rect.right > c.left && rect.top < c.bottom && rect.bottom > c.top)};
      });
      return labels;
    });
    assert.equal(layout.length, 3); assert.ok(layout.every(row => row.fits && !row.touchesCaption), JSON.stringify(layout));
    for (let i = 0; i < layout.length; i++) for (let j = i + 1; j < layout.length; j++) {
      const a = layout[i], b = layout[j];
      assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y,
        `Overlapping material labels: ${JSON.stringify(layout)}`);
    }
    await page.close();
  });

  test(`${orientation}: collage and saved-photo framing and cameras change the actual image independently`, {skip}, async () => {
    const indices = [6, 8], sizes = new Map(indices.map(index => [index, []]));
    const mediaPlan = (framing, camera) => plan => {
      // This risk is image-camera behavior; no 3D context is needed to verify
      // it. Keep the real copied pictures and production composition builders.
      for (const scene of plan.scenes) if (scene.shot.technique === "diagram3d") scene.shot.technique = "stage2d";
      for (const index of indices) Object.assign(plan.scenes[index].shot, {framing, camera});
      return plan;
    };
    async function pose(page, index, phase, suppress = false) {
      return page.evaluate(({index, phase, suppress}) => {
        window.__timelines["yaas-motion"].pause().seek(index * 4 + phase, suppress);
        const selector = index === 6 ? ".collage-layer img" : ".shot-footage > img";
        const image = document.querySelector(`#scene-${index} ${selector}`);
        const matrix = new DOMMatrix(getComputedStyle(image).transform);
        return {scale: Math.hypot(matrix.a, matrix.b), x: matrix.e, y: matrix.f,
          source: image.currentSrc, loaded: image.complete && image.naturalWidth > 0};
      }, {index, phase, suppress});
    }
    for (const framing of ["wide", "medium", "close", "macro"]) {
      const page = await open(orientation, false, mediaPlan(framing, "locked"));
      for (const index of indices) {
        const first = await pose(page, index, 1.1), last = await pose(page, index, 3.3);
        assert.ok(first.loaded && first.source, "The image camera must act on a decoded real picture");
        assert.deepEqual(last, first, `Locked ${framing} image drifts in scene ${index}`);
        assert.deepEqual(await pose(page, index, 1.1, true), first);
        sizes.get(index).push(first.scale);
      }
      await page.close();
    }
    for (const [index, scales] of sizes) {
      assert.ok(scales.every((value, i) => !i || value > scales[i - 1]), `Framing does not change actual image crop in scene ${index}: ${scales}`);
    }
    for (const camera of ["push", "track"]) {
      const page = await open(orientation, false, mediaPlan("medium", camera));
      for (const index of indices) {
        const first = await pose(page, index, 1.1), last = await pose(page, index, 3.3);
        if (camera === "push") {
          assert.ok(last.scale > first.scale, "A push must actually zoom the image");
          assert.equal(last.x, first.x, "A push must not add an unrequested pan");
        } else {
          assert.equal(last.scale, first.scale, "Tracking must preserve the selected zoom");
          assert.notEqual(last.x, first.x, "A track must actually pan the image");
        }
        assert.equal(last.y, first.y);
        assert.deepEqual(await pose(page, index, 1.1, true), first, `${camera} camera drifted after reverse/suppressed seek`);
      }
      await page.close();
    }
  });

  test(`${orientation}: 2D mechanism framing and cameras change pixels while locked reveals remain still`, {skip}, async () => {
    const indices = [0, 5], framed = new Map(indices.map(index => [index, []]));
    const mechanismPlan = (framing, camera) => plan => {
      for (const scene of plan.scenes) if (scene.shot.technique === "diagram3d") scene.shot.technique = "stage2d";
      for (const index of indices) {
        Object.assign(plan.scenes[index].shot, {framing, camera});
        plan.scenes[index].mechanism.action = "reveal";
      }
      return plan;
    };
    for (const framing of ["wide", "medium", "close", "macro"]) {
      const page = await open(orientation, false, mechanismPlan(framing, "locked"));
      for (const index of indices) {
        const first = await sample(page, index, index * 4 + 1.1), last = await sample(page, index, index * 4 + 3.3);
        assert.equal(hash(first.pixels), hash(last.pixels), `Locked reveal drifts for ${framing} mechanism ${index}`);
        framed.get(index).push(hash(last.pixels));
      }
      if (framing === "macro") await page.screenshot({path: path.join(root, "screenshots", `2d-framing-${orientation}.png`)});
      await page.close();
    }
    for (const [index, hashes] of framed) assert.equal(new Set(hashes).size, 4, `A framing edit did not reach actual pixels of mechanism ${index}`);
    const moved = new Map(indices.map(index => [index, []]));
    for (const camera of ["push", "track"]) {
      const page = await open(orientation, false, mechanismPlan("medium", camera));
      for (const index of indices) {
        const time = index * 4 + 1.1, first = await sample(page, index, time), last = await sample(page, index, index * 4 + 3.3);
        assert.notEqual(hash(first.pixels), hash(last.pixels), `${camera} does not move the actual 2D mechanism pixels`);
        assert.equal(hash((await sample(page, index, time, true)).pixels), hash(first.pixels), `${camera} changed after a suppressed reverse seek`);
        moved.get(index).push(hash(last.pixels));
      }
      await page.close();
    }
    for (const [index, hashes] of moved) assert.notEqual(hashes[0], hashes[1], `Push and track produce the same result for mechanism ${index}`);
  });
}

test("saved film palettes visibly change 2D, 3D and composed worlds without changing their actions", {skip}, async () => {
  const palettes = [
    {background: "#f2eadc", surface: "#fffaf0", ink: "#342c26", accent: "#87552e", secondary: "#326678"},
    {background: "#112a22", surface: "#204335", ink: "#edf2df", accent: "#aad08a", secondary: "#e7be79"},
  ];
  const frames = [];
  for (const palette of palettes) {
    const page = await open("portrait", false, plan => ({...plan, palette_colors: palette}));
    const current = [];
    for (const index of [0, 1, 9]) {
      await page.evaluate(time => window.YAASMotion.seek(time), index * 4 + 2.8);
      current.push(hash(await page.screenshot()));
      assert.equal(await page.$eval(`#scene-${index}`, node => getComputedStyle(node).getPropertyValue("--shot-ink").trim()), palette.ink);
    }
    frames.push(current);
    assert.equal(await page.$eval("#scene-9 .world-stage", node => node.style.getPropertyValue("--world-accent")), palette.accent);
    await page.close();
  }
  for (let i = 0; i < frames[0].length; i++) assert.notEqual(frames[0][i], frames[1][i], "Saved palette made no visible change");
});

test("requested 3D without WebGL fails explicitly instead of claiming a successful empty shot", {skip}, async () => {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
      if (kind === "webgl2" || kind === "webgl") return null;
      return original.call(this, kind, ...args);
    };
  });
  await page.goto(`${origin}/shots-portrait/motion/index.html`, {waitUntil: "domcontentloaded"});
  const failure = await page.evaluate(async () => {
    try { await window.YAASMotionReady; return "unexpected success"; }
    catch (error) { return error.message; }
  });
  assert.match(failure, /needs WebGL 2/);
  assert.match(await page.evaluate(() => document.body.dataset.motionError), /needs WebGL 2/);
  await page.close();
});

test("no shot fixture raises an unexpected browser runtime error", {skip}, () => assert.deepEqual(errors, []));
