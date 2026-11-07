import assert from "node:assert/strict";
import {after, before, test} from "node:test";
import {spawn} from "node:child_process";
import {mkdtemp, mkdir, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import puppeteer from "puppeteer-core";

const root = path.resolve(fileURLToPath(new URL("../../../", import.meta.url)));
const screenshots = path.join(root, "renderers/hyperframes/.smoke/screenshots");
const browserPath = process.env.YAAS_TEST_BROWSER || process.env.HYPERFRAMES_BROWSER_PATH;
const skipped = browserPath ? false : "Set YAAS_TEST_BROWSER to run studio browser checks";
let temporary, server, browser, origin, startupLog = "";

// A real Flask app and real profile/schedule storage, isolated from user data.
// External providers and generation are fixtures: no inference, OAuth, or upload.
const serverCode = String.raw`
import sys
import time
from pathlib import Path
from flask import g, jsonify
from werkzeug.serving import make_server
import config
scratch = Path(sys.argv[1])
config.DATA_DIR = str(scratch / ".yaas")
config.OUTPUT_DIR = str(scratch / "output")
config.IMAGES_DIR = str(scratch / "output" / "images")
config.SETTINGS_PATH = str(scratch / "settings.json")
config.YOUTUBE_CLIENT_SECRET = str(scratch / "client_secret.json")
config.YOUTUBE_API_KEY = ""
config.YOUTUBE_CHANNEL_ID = ""
from uploader import youtube
youtube.TOKEN_FILE = str(scratch / "youtube_token.json")
from studio import create_app
app = create_app()
app.view_functions["dashboard.trends"] = lambda: jsonify({"sources": {}, "fetched_at": time.time()})
def fixture_stats():
    from studio.channels import get_profile
    if get_profile(g.profile_id)["name"] == "Morning Brief":
        return jsonify({"ok": True, "configured": False, "source": "saved_channel",
            "fetched_at": time.time(), "title": "News channel · fixture",
            "subscribers": None, "views": 12500, "videos": 18, "recent": []})
    return jsonify({"ok": False, "configured": False})
app.view_functions["dashboard.channel_stats"] = fixture_stats
app.view_functions["pipeline.start_run"] = lambda: (jsonify({"ok": False, "error": "Generation is disabled in browser tests."}), 400)
list_profiles = app.view_functions["channels.channels_list_route"]
def fixture_profiles():
    payload = list_profiles().get_json()
    for profile in payload["profiles"]:
        if profile["name"] == "Morning Brief":
            profile.update(connected=True, token_present=True, connection={"status": "connected"},
                channel={"id": "UC" + "n" * 22, "title": "News channel · fixture", "thumbnail": ""})
    return jsonify(payload)
app.view_functions["channels.channels_list_route"] = fixture_profiles
server = make_server("127.0.0.1", 0, app, threaded=True)
print("YAAS_UI_ORIGIN=http://127.0.0.1:" + str(server.server_port), flush=True)
server.serve_forever()
`;

before(async () => {
  if (skipped) return;
  temporary = await mkdtemp(path.join(tmpdir(), "yaas-studio-ui-"));
  await mkdir(screenshots, {recursive: true});
  server = spawn(process.env.YAAS_TEST_PYTHON || "python", ["-u", "-c", serverCode, temporary], {
    cwd: root, env: {...process.env, STUDIO_NO_BROWSER: "1", STUDIO_SCHEDULER: "0"}, stdio: ["ignore", "pipe", "pipe"],
  });
  server.stderr.on("data", data => { startupLog = (startupLog + data).slice(-8000); });
  origin = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`Studio did not start: ${startupLog}`)), 15000);
    server.stdout.on("data", data => {
      output += data;
      const match = output.match(/YAAS_UI_ORIGIN=(http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    server.once("error", error => { clearTimeout(timer); reject(error); });
    server.once("exit", code => { clearTimeout(timer); reject(new Error(`Studio exited (${code}): ${startupLog}`)); });
  });
  const args = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  browser = await puppeteer.launch({executablePath: browserPath, headless: true, args, timeout: 20000});
});

after(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const exited = new Promise(resolve => server.once("exit", resolve));
    server.kill("SIGTERM"); await exited;
  }
  if (temporary) await rm(temporary, {recursive: true, force: true});
});

async function fill(page, selector, value) {
  await page.focus(selector);
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  if (value) await page.type(selector, String(value));
  assert.equal(await page.$eval(selector, node => node.value), String(value));
}

async function openStudio(onRequest = null) {
  const page = await browser.newPage(), errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", request => {
    if (!request.url().startsWith(origin)) return request.abort();
    if (onRequest && onRequest(request)) return;
    return request.continue();
  });
  await page.setViewport({width: 1440, height: 1100});
  await page.goto(origin, {waitUntil: "domcontentloaded"});
  await page.waitForSelector(".channel-choice");
  await page.waitForFunction(() => document.querySelector("#quick-voice").value && document.querySelector("#active-channel-title").textContent !== "Your channel");
  return {page, errors};
}

test("studio: profiles keep identities, defaults and recurring jobs separate", {skip: skipped}, async () => {
  const voiceRequests = [];
  let pendingVoiceRequest;
  const {page, errors} = await openStudio(request => {
    if (new URL(request.url()).pathname !== "/api/voice-preview") return false;
    voiceRequests.push(JSON.parse(request.postData()));
    pendingVoiceRequest = request;
    return true;
  });
  await page.click("#add-channel");
  await fill(page, "#profile-name", "Morning Brief");
  await page.select("#profile-preset", "news");
  await page.click("#save-channel");
  await page.waitForFunction(() => !document.querySelector("#channel-dialog").open && document.querySelector("#active-channel-title").textContent === "Morning Brief");
  await page.waitForFunction(() => document.querySelector("#visual-mode").value === "hybrid");
  assert.match(await page.$eval("#channel-identity", node => node.textContent), /News channel · fixture/);
  await page.waitForFunction(() => document.querySelector("#pulse-src").textContent.includes("saved totals"));
  assert.equal(await page.$eval("#pulse-stats .stat b", node => node.textContent), "—", "A hidden subscriber count is not zero");
  assert.match(await page.$eval("#pulse-stats", node => node.textContent), /13K/);
  assert.match(await page.$eval("#pulse-foot", node => node.textContent), /local videos/);
  assert.doesNotMatch(await page.$eval("#pulse-foot", node => node.textContent), /views per video/);
  assert.match(await page.$eval("#pulse-hint", node => node.textContent), /Saved channel totals fetched/);
  assert.doesNotMatch(await page.$eval("#pulse-hint", node => node.textContent), /Connect this channel/);
  const newsId = await page.$eval(".channel-choice.active", node => node.dataset.profileId);

  await page.click("#add-schedule");
  await fill(page, "#schedule-name", "Morning briefing");
  await fill(page, "#schedule-topic", "Explain today's important news with sources.");
  await fill(page, "#schedule-timezone", "Asia/Dubai");
  await fill(page, "#schedule-cron", "* * * * *");
  await page.click("#save-schedule");
  await page.waitForSelector("#schedule-form-error:not([hidden])");
  assert.match(await page.$eval("#schedule-form-error", node => node.textContent), /15|frequen|interval/i);
  assert.equal(await page.$eval("#schedule-dialog", node => node.open), true);
  await fill(page, "#schedule-cron", "0 9 * * 1-5");
  await page.click("#save-schedule");
  await page.waitForSelector(".schedule-item");
  await page.waitForFunction(() => !document.querySelector("#schedule-dialog").open);
  assert.match(await page.$eval(".schedule-item", node => node.textContent), /Asia\/Dubai/);
  assert.match(await page.$eval(".schedule-item", node => node.textContent), /Draft/);

  await fill(page, "#topic-input", "A morning news topic draft");
  await page.$eval("#focus-angle", node => { node.value = "News channel focus"; });
  await page.click("#add-channel");
  await fill(page, "#profile-name", "Quiet Hours");
  await page.select("#profile-preset", "asmr");
  await page.click("#save-channel");
  await page.waitForFunction(() => document.querySelector("#active-channel-title").textContent === "Quiet Hours" && document.querySelector("#visual-mode").value === "classic");
  assert.equal(await page.$eval("#topic-input", node => node.value), "");
  assert.equal(await page.$eval("#focus-angle", node => node.value), "");
  assert.doesNotMatch(await page.$eval("#pulse-src", node => node.textContent), /News channel|saved totals/);
  await page.waitForFunction(() => !document.querySelector(".schedule-item"));
  assert.equal(await page.$eval("#quick-voice", node => node.value), "en-US-BrianNeural");
  assert.match(await page.$eval("#format-seg", node => node.textContent), /10 min/);
  await page.click("#edit-channel");
  await page.waitForSelector("#channel-dialog[open]");
  assert.equal(await page.$eval("#profile-voice-rate", node => node.value), "-15%");
  assert.equal(await page.$eval("#profile-music", node => node.checked), false);
  await page.select("#profile-voice-rate", "+10%");
  assert.equal(voiceRequests.length, 0, "Editing a voice must not contact a speech provider");
  const voiceRequested = page.waitForRequest(request => new URL(request.url()).pathname === "/api/voice-preview");
  await page.click("#preview-profile-voice");
  await page.waitForFunction(() => document.querySelector("#preview-profile-voice").disabled);
  await voiceRequested;
  assert.equal(voiceRequests.length, 1);
  assert.deepEqual(voiceRequests[0], {voice_id: "en-US-BrianNeural", voice_rate: "+10%", voice_pitch: "+0Hz"});
  await pendingVoiceRequest.respond({status: 502, contentType: "application/json", body: JSON.stringify({ok: false, error: "Voice provider unavailable in this test."})});
  await page.waitForSelector("#voice-preview-status.error");
  assert.match(await page.$eval("#voice-preview-status", node => node.textContent), /Voice provider unavailable/);
  assert.equal(await page.$eval("#preview-profile-voice", node => node.disabled), false);
  await page.click('#channel-dialog [data-close-dialog="channel-dialog"]');
  await page.click(`[data-profile-id="${newsId}"]`);
  await page.waitForFunction(() => document.querySelector("#active-channel-title").textContent === "Morning Brief" && !!document.querySelector(".schedule-item"));
  assert.equal(await page.$eval("#topic-input", node => node.value), "A morning news topic draft");
  assert.equal(await page.$eval("#focus-angle", node => node.value), "News channel focus");

  await page.click('[data-schedule-action="toggle"]');
  await page.waitForSelector(".schedule-item.paused");
  await page.click('[data-schedule-action="edit"]');
  await page.waitForSelector("#schedule-dialog[open]");
  assert.equal(await page.$eval("#schedule-cron", node => node.value), "0 9 * * 1-5");
  assert.equal(await page.$eval("#schedule-enabled", node => node.checked), false);
  await page.click('#schedule-dialog [data-close-dialog="schedule-dialog"]');

  const data = await (await fetch(`${origin}/api/schedules?profile_id=${encodeURIComponent(newsId)}`)).json();
  assert.equal(data.schedules.length, 1);
  assert.equal(data.schedules[0].profile_id, newsId);
  assert.equal(data.schedules[0].preview_only, true);
  assert.equal(data.schedules[0].enabled, false);
  assert.equal(data.schedules[0].timezone, "Asia/Dubai");
  assert.equal(data.schedules[0].publish, undefined);
  assert.deepEqual(errors, []);
  await page.evaluate(() => { window.scrollTo({top: 0, behavior: "instant"}); document.querySelector("#toasts").replaceChildren(); });
  await page.screenshot({path: path.join(screenshots, "studio-channels-desktop.png"), fullPage: true});
  await page.close();
});

test("studio: settings and quick preferences stay with their channel across another tab's switch", {skip: skipped}, async () => {
  const profiles = (await (await fetch(`${origin}/api/channels`)).json()).profiles;
  const news = profiles.find(profile => profile.preset === "news"), calm = profiles.find(profile => profile.preset === "asmr");
  assert.ok(news && calm);
  const activate = id => fetch(`${origin}/api/channels/${id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  await activate(news.id);
  const {page, errors} = await openStudio();
  await page.click("#settings-btn");
  await page.waitForSelector("#view-settings.active");
  await page.waitForFunction(id => settingsProfileId === id, {}, news.id);
  await fill(page, "#s-channel_name", "News studio edited name");
  await page.waitForSelector("#savebar.show");
  assert.equal(await page.$eval("#s-youtube_channel", node => node.disabled), true);

  // A second tab changes the server's active profile while this form is dirty.
  await activate(calm.id);
  await page.evaluate(() => pollOnce());
  await page.waitForFunction(id => activeProfileId === id && document.querySelector("#quick-voice").value === "en-US-BrianNeural", {}, calm.id);
  assert.equal(await page.$eval("#s-channel_name", node => node.value), "News studio edited name");
  assert.match(await page.$eval("#settings-channel-note", node => node.textContent), /unsaved settings belong to Morning Brief/);
  const saving = page.waitForRequest(request => new URL(request.url()).pathname === "/api/save-settings");
  await page.click("#savebar-save");
  const request = await saving;
  assert.equal(new URL(request.url()).searchParams.get("profile_id"), news.id);
  assert.equal(JSON.parse(request.postData()).youtube_channel, undefined);
  await page.waitForFunction(id => settingsProfileId === id && !settingsSavePending, {}, calm.id);
  const newsSettings = await (await fetch(`${origin}/api/settings?profile_id=${news.id}`)).json();
  const calmSettings = await (await fetch(`${origin}/api/settings?profile_id=${calm.id}`)).json();
  assert.equal(newsSettings.channel_name, "News studio edited name");
  assert.notEqual(calmSettings.channel_name, "News studio edited name");

  // Home still shows Calm while another tab silently selects News. Its quick
  // preference must target Calm explicitly instead of the mutable server default.
  await page.click('#tabs a[data-view="home"]');
  await page.waitForSelector("#view-home.active");
  await page.$eval("#quick-voice", node => node.closest("details").open = true);
  await activate(news.id);
  const quickSaving = page.waitForRequest(request => new URL(request.url()).pathname === "/api/save-settings");
  await page.select("#quick-voice", "en-GB-LibbyNeural");
  const quickRequest = await quickSaving;
  assert.equal(new URL(quickRequest.url()).searchParams.get("profile_id"), calm.id);
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some(node => node.textContent === "Voice saved for Quiet Hours"));
  const updatedCalm = await (await fetch(`${origin}/api/settings?profile_id=${calm.id}`)).json();
  assert.equal(updatedCalm.voice_id, "en-GB-LibbyNeural");
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: an older settings response cannot replace the newly selected channel", {skip: skipped}, async () => {
  const profiles = (await (await fetch(`${origin}/api/channels`)).json()).profiles;
  const news = profiles.find(profile => profile.preset === "news"), calm = profiles.find(profile => profile.preset === "asmr");
  await fetch(`${origin}/api/channels/${news.id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const oldSettings = await (await fetch(`${origin}/api/settings?profile_id=${news.id}`)).json();
  let hold = false, pending;
  const {page, errors} = await openStudio(request => {
    const url = new URL(request.url());
    if (!hold || url.pathname !== "/api/settings" || url.searchParams.get("profile_id") !== news.id) return false;
    pending = request; hold = false; return true;
  });
  await page.waitForFunction(id => settingsProfileId === id, {}, news.id);
  hold = true;
  const requested = page.waitForRequest(request => new URL(request.url()).pathname === "/api/settings");
  await page.evaluate(() => { window.delayedSettingsLoad = loadSettings(); });
  await requested;
  await page.click(`[data-profile-id="${calm.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id, {}, calm.id);
  const currentName = await page.$eval("#s-channel_name", node => node.value);
  await pending.respond({status: 200, contentType: "application/json", body: JSON.stringify(oldSettings)});
  await page.evaluate(() => window.delayedSettingsLoad);
  assert.equal(await page.$eval("#s-channel_name", node => node.value), currentName);
  assert.equal(await page.evaluate(() => settingsProfileId), calm.id);
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: a finished scheduled run does not announce the restored editor as its result", {skip: skipped}, async () => {
  const profiles = await (await fetch(`${origin}/api/channels`)).json();
  const profile = profiles.profiles.find(item => item.id === profiles.active_profile_id);
  let phase = "idle";
  const {page, errors} = await openStudio(request => {
    if (new URL(request.url()).pathname !== "/api/status") return false;
    const running = phase === "running";
    request.respond({status: 200, contentType: "application/json", body: JSON.stringify({
      active_profile_id: profile.id, profile_id: profile.id, profile_name: profile.name,
      running, schedule_id: running ? "schedule-fixture" : null, stages: {},
      has_video: phase === "restored", error: phase === "restored" ? "An old editor error" : null,
    })});
    return true;
  });
  phase = "running";
  await page.evaluate(() => pollOnce());
  await page.waitForSelector("#hero-run:not([hidden])");
  phase = "restored";
  await page.evaluate(() => pollOnce());
  assert.equal(await page.$eval("#hero-form", node => node.hidden), false);
  assert.equal(await page.$eval("#run-error", node => node.hidden), true);
  assert.ok(await page.evaluate(() => [...document.querySelectorAll(".toast")].some(node => node.textContent.startsWith("Scheduled run finished for"))));
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: narrow layouts and profile forms remain usable without horizontal overflow", {skip: skipped}, async () => {
  const {page, errors} = await openStudio();
  for (const width of [390, 320]) {
    await page.setViewport({width, height: 844});
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".channel-mobile")).display !== "none");
    assert.ok(await page.$eval("#channel-select", node => node.getBoundingClientRect().width > 100));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Home overflows at ${width}px`);
    await page.click("#edit-channel");
    await page.waitForSelector("#channel-dialog[open]");
    assert.ok(await page.$eval("#channel-dialog", node => node.scrollWidth <= node.clientWidth + 1), `Profile editor overflows at ${width}px`);
    await page.screenshot({path: path.join(screenshots, `studio-profile-${width}.png`)});
    await page.click('#channel-dialog [data-close-dialog="channel-dialog"]');
    await page.click("#add-schedule");
    await page.waitForSelector("#schedule-dialog[open]");
    assert.ok(await page.$eval("#schedule-dialog", node => node.scrollWidth <= node.clientWidth + 1), `Schedule editor overflows at ${width}px`);
    await page.click('#schedule-dialog [data-close-dialog="schedule-dialog"]');
    await page.evaluate(() => window.scrollTo({top: 0, behavior: "instant"}));
    await page.screenshot({path: path.join(screenshots, `studio-home-${width}.png`)});
  }
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: legacy photo conversion preserves the reviewed project channel and voice", {skip: skipped}, async () => {
  let profiles = await (await fetch(`${origin}/api/channels`)).json();
  let project = profiles.profiles.find(profile => profile.preset === "asmr");
  if (!project) {
    project = (await (await fetch(`${origin}/api/channels`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "Quiet Hours", preset: "asmr"})})).json()).profile;
    profiles = await (await fetch(`${origin}/api/channels`)).json();
  }
  assert.ok(project);
  const page = await browser.newPage(), errors = [], requests = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = new URL(request.url());
    if (!request.url().startsWith(origin)) return request.abort();
    const respond = data => request.respond({status: 200, contentType: "application/json", body: JSON.stringify(data)});
    if (url.pathname === "/api/status") return respond({running: false, stages: {}, profile_id: project.id, profile_name: project.name, active_profile_id: profiles.active_profile_id, video_type: "normal", visual_mode: "classic", has_video: true, has_audio: true, has_script: true, has_thumb: false});
    if (url.pathname === "/api/output") return respond({title: "A quiet garden", description: "Existing narrated project", tags: [], sections: [{narration: "A quiet garden."}]});
    if (url.pathname === "/api/visuals") return respond({plan: null});
    if (url.pathname === "/api/keywords") return respond({suggestions: [], related: []});
    if (url.pathname === "/api/seo") return respond({score: 100, checks: []});
    if (url.pathname === "/api/yt-status") return respond({secret: false, token: false, connected: false, profile_id: project.id, profile_name: project.name});
    if (url.pathname === "/video") return request.respond({status: 204, contentType: "video/mp4", body: ""});
    if (url.pathname === "/api/run") { requests.push(JSON.parse(request.postData())); return respond({ok: false, error: "Generation is disabled in browser tests."}); }
    return request.continue();
  });
  await page.goto(`${origin}/#review`, {waitUntil: "domcontentloaded"});
  await page.waitForSelector("#legacy-motion-card:not([hidden])");
  assert.match(await page.$eval("#review-channel", node => node.textContent), /Quiet Hours/);
  assert.equal(await page.$eval("#upload-btn", node => node.disabled), true);
  await page.click("#animate-legacy");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some(node => node.textContent.includes("Generation is disabled")));
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].steps, ["visuals", "video"]);
  assert.equal(requests[0].profile_id, project.id);
  assert.equal(requests[0].visual_mode, "motion");
  assert.equal(requests[0].preview_only, true);
  assert.deepEqual(errors, []);
  await page.close();
});
