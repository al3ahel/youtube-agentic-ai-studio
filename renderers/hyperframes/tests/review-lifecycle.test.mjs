import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, mkdir, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {test} from "node:test";
import puppeteer from "puppeteer-core";

const root = path.resolve(fileURLToPath(new URL("../../../", import.meta.url)));
const browserPath = process.env.YAAS_TEST_BROWSER || process.env.HYPERFRAMES_BROWSER_PATH;
const skipped = browserPath ? false : "Set YAAS_TEST_BROWSER to run studio browser checks";

// The completed video, timing, storyboard, output endpoint, recovery, and run
// route are real. Only external suggestions and the next provider job are stubbed.
const serverCode = String.raw`
from copy import deepcopy
import json, shutil, sys, time
from pathlib import Path
from flask import jsonify, request
from werkzeug.serving import make_server
import config
scratch, mode = Path(sys.argv[1]), sys.argv[2]
output = scratch / "output"
config.DATA_DIR = str(scratch / "data")
config.SETTINGS_PATH = str(scratch / "settings.json")
config.OUTPUT_DIR = str(output)
config.IMAGES_DIR = str(output / "images")
config.YOUTUBE_API_KEY = ""
config.YOUTUBE_CHANNEL_ID = ""
config.YOUTUBE_CLIENT_SECRET = str(scratch / "client_secret.json")
config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS = 640, 360, 12
config.VISUAL_AI_ENABLED = config.VISUAL_REVIEW_ENABLED = False
from studio import create_app, pipeline
from studio.state import fresh_project_state, log_queue, state
from studio.workspace import checkpoint_workspace

def dump(name, value):
    (output / name).write_text(json.dumps(value), encoding="utf-8")

if mode == "live":
    from renderers.hyperframes.smoke import fixture
    from agents.visual_director import validate_plan
    from video.renderers.html import prepare_motion_project
    from video.timeline import audio_signature, build_timeline, script_signature
    plan, audio = fixture(output, seconds=2)
    # The renderer's smoke schema accepts extra fixture provenance. Production
    # storyboards use the director's narrower item schema and allowlisted URLs.
    for scene in plan["scenes"]:
        for item in scene["items"]:
            item.pop("source", None)
        if scene.get("footage"):
            scene["footage"]["source_url"] = "https://www.pexels.com/video/offline-test-fixture-1/"
    script = {"title": plan["title"], "description": "Saved description from the real output API.",
              "tags": ["offline fixture", "restart"], "sections": [
                  {"id": section["id"], "title": scene["title"], "narration": section["narration"]}
                  for section, scene in zip(plan["sections"], plan["scenes"])]}
    # Older source records can omit their provider label; saved metadata must
    # still load without an eagerly evaluated description fallback crashing.
    research = {"topic": "Ideas in motion", "grounding": {"sources": [
        {"title": "Saved source without a provider label", "url": "https://example.test/source"}]}}
    dump("script.json", script)
    dump("research.json", research)
    dump("narration.timings.json", {"version": 1, "script_signature": script_signature(script),
        "audio_sha256": audio_signature(audio), "duration": 14, "sections": plan["sections"], "words": []})
    timeline = build_timeline(script, str(audio), str(output))
    plan.update(script_signature=timeline["script_signature"], timeline_signature=timeline["timeline_signature"],
                audio_duration=timeline["audio_duration"], timing_source=timeline["timing_source"])
    validate_plan(plan)
    dump("scene_plan.json", plan)
    dump("render_audio.json", {"path": "narration.wav", "audio_sha256": timeline["audio_sha256"]})
    prepare_motion_project(plan, str(audio), str(output))
    # This committed sample was rendered from the same 14s/12fps fixture.
    shutil.copyfile(Path.cwd() / "docs/samples/motion-landscape.mp4", output / "motion_preview.mp4")
    from PIL import Image
    Image.new("RGB", (320, 180), "#355849").save(output / "thumbnail.jpg")
    state.update(script=script, research=research, description=script["description"],
                 visual_plan=plan, timeline=timeline, audio_path=str(audio), render_audio_path=str(audio),
                 thumb_path=str(output / "thumbnail.jpg"),
                 preview_path=str(output / "motion_preview.mp4"), preview_only=True, visual_mode="hybrid",
                 profile_id="default", profile_name="My AI Channel")
    state["stages"].update({stage: "done" for stage in state["stages"]})
    assert checkpoint_workspace(output, rendered_kind="draft")

app = create_app()
completed = deepcopy(state)
if mode == "live":
    state.update(running=True, job_profile_id="default", job_profile_name="My AI Channel")
    state["stages"]["video"] = "running"
app.view_functions["dashboard.trends"] = lambda: jsonify({"sources": {}, "fetched_at": time.time()})
app.view_functions["review.keywords"] = lambda: jsonify({"suggestions": [], "related": []})
output_route = app.view_functions["review.get_output"]
fault = {"output": False}
def output_with_failure():
    if fault["output"]:
        return "<h1>Temporary fixture failure</h1>", 503, {"Content-Type": "text/html"}
    return output_route()
app.view_functions["review.get_output"] = output_with_failure

@app.post("/_fixture/complete")
def complete():
    state.update(running=False)
    state["stages"]["video"] = "done"
    log_queue.put(("done", ""))
    return jsonify(ok=True)

@app.post("/_fixture/output-failure")
def output_failure():
    fault["output"] = request.get_json()["enabled"]
    return jsonify(ok=True)

@app.post("/_fixture/clear")
def clear_project():
    state.clear()
    state.update(fresh_project_state())
    state["workspace_warning"] = "The previous draft is not available in the open workspace."
    return jsonify(ok=True)

@app.post("/_fixture/storyboard-only")
def storyboard_only():
    state.clear()
    state.update(deepcopy(completed), running=False, preview_path=None, video_path=None, workspace_restored=False)
    index = output / "motion" / "index.html"
    if index.is_file():
        index.rename(index.with_name("unavailable-preview.html"))
    return jsonify(ok=True)

jobs = []
def next_job(steps, topic, video_type, *args, **kwargs):
    jobs.append({"topic": topic, "steps": steps, "profile_id": kwargs.get("profile_id"),
                 "visual_mode": kwargs.get("visual_mode"), "preview_only": kwargs.get("preview_only"),
                 "use_project_photos": kwargs.get("use_project_photos")})
    state.update(running=False, error="External generation is disabled in this browser fixture.")
    state["stages"]["preparation"] = "error"
    log_queue.put(("error", state["error"]))
pipeline.run_pipeline_thread = next_job
@app.get("/_fixture/jobs")
def job_requests():
    return jsonify(jobs)

server = make_server("127.0.0.1", int(sys.argv[3]), app, threaded=True)
print("YAAS_REAL_REVIEW=http://127.0.0.1:" + str(server.server_port), flush=True)
server.serve_forever()
`;

async function startServer(directory, mode, port = 0) {
  let log = "";
  const serverProcess = spawn(process.env.YAAS_TEST_PYTHON || "python", ["-u", "-c", serverCode, directory, mode, String(port)], {
    cwd: root, env: {...process.env, STUDIO_NO_BROWSER: "1", STUDIO_SCHEDULER: "0"}, stdio: ["ignore", "pipe", "pipe"],
  });
  serverProcess.stderr.on("data", data => { log = (log + data).slice(-10000); });
  const origin = await new Promise((resolve, reject) => {
    let text = "";
    const timeout = setTimeout(() => { serverProcess.kill("SIGTERM"); reject(new Error(`Review fixture did not start: ${log}`)); }, 20000);
    serverProcess.stdout.on("data", data => { text += data; const found = text.match(/YAAS_REAL_REVIEW=(http:\/\/127\.0\.0\.1:\d+)/); if (found) { clearTimeout(timeout); resolve(found[1]); } });
    serverProcess.once("error", error => { clearTimeout(timeout); reject(error); });
    serverProcess.once("exit", code => { clearTimeout(timeout); reject(new Error(`Review fixture exited ${code}: ${log}`)); });
  });
  return {origin, async close() {
    if (serverProcess.exitCode !== null) return;
    const closed = new Promise(resolve => serverProcess.once("exit", resolve));
    serverProcess.kill("SIGTERM"); await closed;
  }};
}

test("studio: real draft completion, Review retry, restart recovery and starting another video", {skip: skipped, timeout: 60000}, async t => {
  const directory = await mkdtemp(path.join(tmpdir(), "yaas-review-lifecycle-"));
  let server, browser;
  t.after(async () => { if (browser) await browser.close(); if (server) await server.close(); await rm(directory, {recursive: true, force: true}); });
  server = await startServer(directory, "live");
  const origin = server.origin;
  const post = async (route, body = {}) => {
    const response = await fetch(origin + route, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    assert.equal(response.status, 200); return response.json();
  };
  const args = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  browser = await puppeteer.launch({executablePath: browserPath, headless: true, args, timeout: 20000});
  const page = await browser.newPage(), errors = [], responses = [], thumbnailWrites = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => { if (new URL(response.url()).pathname === "/api/output") responses.push(response.status()); });
  page.on("request", request => { if (new URL(request.url()).pathname === "/api/thumbnail" && request.method() === "POST") thumbnailWrites.push(request.postData()); });
  await page.setRequestInterception(true);
  page.on("request", request => request.url().startsWith(origin) ? request.continue() : request.abort());
  await page.setViewport({width: 1440, height: 1000});
  await page.goto(origin, {waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => document.querySelector("#status-text").textContent === "Making a draft…");
  await page.waitForSelector(".channel-choice");
  await post("/_fixture/complete");
  await page.waitForFunction(() => document.querySelector("#run-title").textContent === "Your draft is ready");
  await page.click('#run-done a[href="#review"]');
  await page.waitForFunction(() => !document.querySelector("#review-feedback").hidden && document.querySelector("#review-feedback").classList.contains("error")
    || document.querySelectorAll("#scene-list button").length === 7 && document.querySelector("#player video")?.readyState >= 2, {timeout: 10000})
    .catch(async error => { error.message += `; page: ${JSON.stringify(await page.evaluate(() => ({feedback: document.querySelector("#review-feedback").textContent, scenes: document.querySelectorAll("#scene-list button").length, video: document.querySelector("#player video")?.readyState, mediaError: document.querySelector("#player video")?.error?.message})))}, errors: ${errors.join("; ")}`; throw error; });
  assert.equal(await page.$eval("#review-feedback", node => !node.hidden && node.classList.contains("error")), false,
    await page.$eval("#review-feedback", node => node.textContent));
  assert.equal(await page.$eval("#edit-title", node => node.value), "Ideas in motion");
  assert.equal(await page.$eval("#edit-desc", node => node.value), "Saved description from the real output API.");
  assert.deepEqual(responses, [200], "The real output endpoint must supply Review metadata");
  assert.equal(await page.$eval("#review-empty", node => node.hidden), true);
  await page.waitForFunction(() => thumb && !thumbBusy);
  assert.deepEqual(thumbnailWrites, [], "Opening Review must not regenerate thumbnails or take the scene-edit lock");
  assert.equal((await (await fetch(origin + "/api/status")).json()).visual_editing, false);
  const thumbnailPreview = page.waitForResponse(response => new URL(response.url()).pathname === "/api/thumbnail" && response.request().method() === "POST");
  await page.$eval("#thumb-preview-btn", node => node.click());
  assert.equal((await thumbnailPreview).status(), 200);
  await page.waitForFunction(() => !thumbBusy);
  assert.equal(thumbnailWrites.length, 1, "The explicit preview action still generates style choices");

  await post("/_fixture/output-failure", {enabled: true});
  await page.click('#tabs a[data-view="home"]');
  await page.click('#run-done a[href="#review"]');
  await page.waitForSelector("#review-feedback.error:not([hidden])");
  assert.match(await page.$eval("#review-feedback", node => node.textContent), /unreadable response \(503\)/);
  assert.equal(await page.$eval("#review-empty", node => node.hidden), true, "A failed request is not an empty workspace");
  await post("/_fixture/output-failure", {enabled: false});
  await page.click("#review-retry");
  await page.waitForFunction(() => document.querySelector("#review-feedback").hidden && document.querySelector("#player video")?.readyState >= 2);
  assert.deepEqual(responses, [200, 503, 200]);
  assert.equal(thumbnailWrites.length, 1, "Retrying Review remains read-only");

  await page.click('#tabs a[data-view="home"]');
  await server.close();
  server = await startServer(directory, "restart", Number(new URL(origin).port));
  const restored = await (await fetch(origin + "/api/status")).json();
  assert.equal(restored.workspace_restored, true);
  assert.equal(restored.has_preview, true);
  assert.equal(restored.has_storyboard, true);
  assert.equal(restored.review_ready, true);
  await page.reload({waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => document.querySelector("#run-title").textContent === "Your saved draft is ready");
  const before = await readFile(path.join(directory, "output/motion_preview.mp4"));
  await page.click("#make-another-video");
  await page.waitForSelector("#hero-form:not([hidden])");
  await page.evaluate(() => pollOnce());
  assert.equal(await page.$eval("#hero-form", node => node.hidden), false, "Recovery must not force the completion card back over the creation form");
  assert.deepEqual(await readFile(path.join(directory, "output/motion_preview.mp4")), before, "Starting another video must preserve the existing draft until generation starts");
  await page.click('#tabs a[data-view="review"]');
  await page.waitForFunction(() => document.querySelectorAll("#scene-list button").length === 7 && document.querySelector("#player video")?.readyState >= 2);
  assert.equal(await page.$eval("#edit-desc", node => node.value), "Saved description from the real output API.");
  await page.$eval("#rv-watch", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 85, behavior: "instant"}));
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#rv-watch")).opacity) > .99);
  const screenshots = path.join(root, "renderers/hyperframes/.smoke/screenshots");
  await mkdir(screenshots, {recursive: true});
  await page.screenshot({path: path.join(screenshots, "studio-review-restored-real-api.png")});

  await post("/_fixture/storyboard-only");
  await page.click('#tabs a[data-view="home"]');
  await page.evaluate(async () => { status.running = true; await pollOnce(); });
  await page.waitForFunction(() => document.querySelector("#run-title").textContent === "Your storyboard is ready");
  assert.match(await page.$eval("#run-done", node => node.textContent), /no playable preview yet/);
  await page.click('#run-done a[href="#review"]');
  await page.waitForFunction(() => document.querySelector("#view-review").classList.contains("active")
    && document.querySelector("#review-heading").textContent === "Review your storyboard"
    && document.querySelectorAll("#scene-list button").length === 7 && document.querySelector("#review-feedback").hidden);
  assert.equal(await page.$eval("#motion-preview", node => node.hidden), true);
  assert.equal(await page.$eval("#rv-watch", node => node.hidden), true);

  await page.click('#tabs a[data-view="home"]');
  await post("/_fixture/clear");
  await page.evaluate(() => pollOnce());
  await page.waitForSelector("#hero-form:not([hidden])");
  assert.equal(await page.$eval("#run-done", node => node.textContent), "");
  assert.match(await page.$eval("#workspace-warning", node => node.textContent), /previous draft is not available/);
  await page.click('#tabs a[data-view="review"]');
  await page.waitForSelector("#review-empty:not([hidden])");
  assert.equal(await page.$eval("#review-feedback", node => node.hidden), true);
  await page.click("#review-new-video");
  await page.waitForSelector("#view-home.active #hero-form:not([hidden])");
  await page.type("#topic-input", "A new video from Home");
  const startResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/run" && response.request().method() === "POST");
  await page.click('#ask-form button[type="submit"]');
  assert.equal((await startResponse).status(), 200, "The real run route accepts a fresh Home request");
  await page.waitForFunction(() => document.querySelector("#run-error").textContent.includes("External generation is disabled in this browser fixture"));
  const jobs = await (await fetch(origin + "/_fixture/jobs")).json();
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].topic, "A new video from Home");
  assert.ok(jobs[0].steps.includes("research") && jobs[0].steps.includes("script") && jobs[0].steps.includes("video"));
  assert.deepEqual(errors, []);
});

test("studio: existing photo storyboard rebuild buttons pass the real guarded run route", {skip: skipped, timeout: 30000}, async t => {
  const directory = await mkdtemp(path.join(tmpdir(), "yaas-review-rebuild-"));
  let server, browser;
  t.after(async () => { if (browser) await browser.close(); if (server) await server.close(); await rm(directory, {recursive: true, force: true}); });
  server = await startServer(directory, "live");
  const origin = server.origin;
  const completed = await fetch(origin + "/_fixture/complete", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  assert.equal(completed.status, 200);
  const original = await (await fetch(origin + "/api/visuals")).json();
  assert.ok(original.plan.scenes.some(scene => scene.image), "The real saved storyboard must already contain a photo");
  assert.match(original.plan_revision, /^[a-f0-9]{64}$/);
  const retained = await Promise.all(["script.json", "narration.wav", "scene_plan.json"].map(name => readFile(path.join(directory, "output", name))));
  browser = await puppeteer.launch({executablePath: browserPath, headless: true,
    args: process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [], timeout: 20000});
  const page = await browser.newPage(), errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewport({width: 1440, height: 1000});
  await page.setRequestInterception(true);
  page.on("request", request => request.url().startsWith(origin) ? request.continue() : request.abort());
  await page.goto(origin + "/#review", {waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => document.querySelector("#view-review").classList.contains("active") && !document.querySelector("#legacy-motion-card").hidden && document.querySelector("#review-feedback").hidden);
  for (const [button, photos] of [["#rebuild-motion-graphics", false], ["#animate-legacy", true]]) {
    const started = page.waitForResponse(response => new URL(response.url()).pathname === "/api/run" && response.request().method() === "POST");
    await page.waitForSelector(button, {visible: true});
    await page.click(button);
    const response = await started;
    assert.equal(response.status(), 200, await response.text());
    const request = JSON.parse(response.request().postData());
    assert.deepEqual(request.steps, ["visuals", "video"]);
    assert.equal(request.expected_plan_revision, original.plan_revision);
    assert.equal(request.use_project_photos, photos);
    assert.equal(request.topic, "");
    await page.waitForFunction(() => location.hash === "#home" && document.querySelector("#run-error").textContent.includes("External generation is disabled in this browser fixture"));
    await page.click('#tabs a[data-view="review"]');
    await page.waitForFunction(() => document.querySelector("#view-review").classList.contains("active") && document.querySelector("#review-feedback").hidden && !document.querySelector("#legacy-motion-card").hidden && !document.querySelector("#animate-legacy").disabled);
  }
  const jobs = await (await fetch(origin + "/_fixture/jobs")).json();
  assert.equal(jobs.length, 2);
  assert.deepEqual(jobs.map(job => job.use_project_photos), [false, true]);
  assert.ok(jobs.every(job => job.profile_id === "default" && job.visual_mode === "motion" && job.preview_only && JSON.stringify(job.steps) === '["visuals","video"]'));
  for (const [index, name] of ["script.json", "narration.wav", "scene_plan.json"].entries()) {
    assert.deepEqual(await readFile(path.join(directory, "output", name)), retained[index]);
  }
  assert.deepEqual(errors, []);
});
