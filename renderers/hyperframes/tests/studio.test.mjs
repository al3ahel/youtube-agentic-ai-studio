import assert from "node:assert/strict";
import {after, before, test} from "node:test";
import {spawn} from "node:child_process";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
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
from flask import g, jsonify, request, send_file
from werkzeug.serving import make_server
import config
scratch = Path(sys.argv[1])
config.DATA_DIR = str(scratch / ".yaas")
config.OUTPUT_DIR = str(scratch / "output")
config.IMAGES_DIR = str(scratch / "output" / "images")
config.SETTINGS_PATH = str(scratch / "settings.json")
config.ENV_PATH = str(scratch / ".env")
config.YOUTUBE_CLIENT_SECRET = str(scratch / "client_secret.json")
for key in config.ENV_KEYS:
    setattr(config, key, "")
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
app.view_functions["visuals.preview_video"] = lambda: send_file(Path.cwd() / "docs" / "samples" / "motion-portrait.mp4", mimetype="video/mp4", conditional=True)
list_profiles = app.view_functions["channels.channels_list_route"]
def fixture_profiles():
    payload = list_profiles().get_json()
    for profile in payload["profiles"]:
        if profile["name"] == "Morning Brief":
            profile.update(connected=True, token_present=True, connection={"status": "connected"},
                channel={"id": "UC" + "n" * 22, "title": "News channel · fixture", "thumbnail": ""})
    return jsonify(payload)
app.view_functions["channels.channels_list_route"] = fixture_profiles
local_requests = []
@app.get("/api/tags")
def fixture_local_catalogue():
    local_requests.append("tags")
    return jsonify(models=[{"name": "fixture-small:latest", "size": 1024 ** 3,
        "digest": "a" * 64, "details": {"format": "gguf", "family": "fixture"}}])
@app.get("/_fixture/local-requests")
def fixture_local_requests():
    return jsonify(local_requests)
event_fixture_previous = None
@app.post("/_fixture/run-events")
def fixture_run_events():
    from copy import deepcopy
    from datetime import datetime, timezone
    from unittest.mock import patch
    from uuid import uuid4
    from studio.channels import get_profile
    from studio.state import event_context, fresh_project_state, log_queue, state
    global event_fixture_previous
    body = request.get_json()
    if body["action"] == "restore":
        if event_fixture_previous is not None:
            state.clear()
            state.update(event_fixture_previous)
            event_fixture_previous = None
        return jsonify(ok=True)
    if body["action"] == "start":
        event_fixture_previous = deepcopy(state)
        profile = get_profile(g.profile_id)
        state.clear()
        state.update(fresh_project_state())
        state.update(running=True, run_id=uuid4().hex, profile_id=profile["id"], profile_name=profile["name"],
                     job_profile_id=profile["id"], job_profile_name=profile["name"], started_at=time.time())
    class EventClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    with patch("studio.state.datetime", EventClock), event_context(
            run_id=state["run_id"], profile_id=state["job_profile_id"], profile_name=state["job_profile_name"],
            build=app.extensions["yaas_build"].running):
        for item in body.get("events", []):
            log_queue.put((item["kind"], item.get("msg", "")))
    rows, _ = log_queue.read_after(0, timeout=0)
    return jsonify(ok=True, run_id=state["run_id"], profile_id=state["job_profile_id"],
                   events=[row for row in rows if row["run_id"] == state["run_id"]])
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
  assert.equal(await page.$eval("#profile-sound-mode", node => node.value), "auto");
  assert.equal(await page.$eval("#profile-manual-sound", node => node.hidden), true);
  await page.select("#profile-sound-mode", "manual");
  assert.equal(await page.$eval("#profile-manual-sound", node => node.hidden), false);
  await page.select("#profile-sound-mode", "auto");
  await page.select("#profile-sound-intensity", "balanced");
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
  await page.select("#profile-voice-rate", "-15%");
  const calmId = await page.$eval(".channel-choice.active", node => node.dataset.profileId);
  const profileSaved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/channels/${calmId}` && response.request().method() === "PATCH");
  await page.click("#save-channel");
  assert.equal((await profileSaved).status(), 200);
  await page.waitForFunction(() => !document.querySelector("#channel-dialog").open && !channelMutation);
  assert.match(await page.$eval("#channel-identity", node => node.textContent), /automatic sound/);
  assert.equal((await (await fetch(`${origin}/api/settings?profile_id=${calmId}`)).json()).sound_intensity, "balanced");
  assert.equal((await (await fetch(`${origin}/api/settings?profile_id=${newsId}`)).json()).sound_intensity, "low");
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

test("studio: visible Sound design routes save and undo real per-channel mastering preferences", {skip: skipped, timeout: 60000}, async () => {
  const create = async name => (await (await fetch(`${origin}/api/channels`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name}),
  })).json()).profile;
  const selected = await create("Sound design channel"), other = await create("Separate sound channel");
  await fetch(`${origin}/api/channels/${selected.id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const writes = [];
  const {page, errors} = await openStudio(request => {
    const url = new URL(request.url());
    if (request.method() === "POST") writes.push({path: url.pathname, profile: url.searchParams.get("profile_id"), body: JSON.parse(request.postData() || "{}")});
    return false; // Settings, profiles, saves and build information use the actual Flask app.
  });
  const waitForSection = async section => {
    await page.waitForFunction(({section, profile}) => location.hash === `#settings/${section}`
      && settingsProfileId === profile && document.querySelector("#view-settings").classList.contains("active")
      && !pendingSettingsSection, {}, {section, profile: selected.id});
  };
  const openSection = async section => {
    await page.$eval("#settings-guide", node => node.scrollIntoView({block: "start", behavior: "instant"}));
    await page.click(`#settings-sections [data-section="${section}"]`);
    await waitForSection(section);
  };
  const save = async () => {
    await page.waitForFunction(() => {
      const r = document.querySelector("#savebar-save").getBoundingClientRect();
      return document.querySelector("#savebar").classList.contains("show") && r.top > 0 && r.bottom < innerHeight
        && Number(getComputedStyle(document.querySelector("#savebar")).opacity) > .99;
    });
    const [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === "/api/save-settings"),
      page.click("#savebar-save"),
    ]);
    assert.equal(response.status(), 200, await response.text());
    await page.waitForFunction(() => !settingsSavePending && !settingsDirty());
  };
  await page.click("#home-sound-settings");
  await waitForSection("audio");
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#settings-audio")).opacity) > .99);
  assert.equal(await page.$eval("#settings-btn", node => node.classList.contains("active")), true);
  assert.equal(await page.$$eval("#s-audio_mastering", nodes => nodes.length), 1);
  assert.equal(await page.$eval("#s-audio_mastering", node => node.checked), true);
  assert.equal(await page.$eval("#s-sound_mode", node => node.value), "auto");
  assert.ok(await page.$eval("#s-sound_mode", node => { const rect = node.getBoundingClientRect(); return rect.top > 60 && rect.bottom < innerHeight; }));
  await page.select("#s-sound_mode", "off");
  await page.click('label:has(#s-audio_mastering)');
  assert.equal(await page.$eval("#s-audio_mastering", node => node.checked), false);
  // A section change must retain the loaded profile and unsaved form. The save
  // bar must still call Settings, never the Review metadata endpoint.
  await openSection("voice");
  assert.equal(await page.$eval("#s-sound_mode", node => node.value), "off");
  assert.equal(await page.$eval("#s-audio_mastering", node => node.checked), false);
  await save();
  const savedOff = await (await fetch(`${origin}/api/settings?profile_id=${selected.id}`)).json();
  assert.equal(savedOff.sound_mode, "off"); assert.equal(savedOff.audio_mastering, false);
  assert.deepEqual(writes.filter(item => item.path === "/api/save-settings").map(item => [item.profile, item.body.sound_mode, item.body.audio_mastering]), [[selected.id, "off", false]]);

  await openSection("audio");
  await page.select("#s-sound_mode", "auto");
  await page.click('label:has(#s-audio_mastering)');
  await page.waitForFunction(() => document.querySelector("#savebar-undo").getBoundingClientRect().bottom < innerHeight);
  await page.click("#savebar-undo");
  await page.waitForFunction(() => !settingsDirty() && document.querySelector("#s-sound_mode").value === "off" && !document.querySelector("#s-audio_mastering").checked);
  await page.select("#s-sound_mode", "auto");
  await page.select("#s-sound_intensity", "balanced");
  await page.click('label:has(#s-audio_mastering)');
  await save();
  await page.reload({waitUntil: "domcontentloaded"});
  await waitForSection("audio");
  await page.waitForFunction(() => document.querySelector("#s-sound_mode").value === "auto" && document.querySelector("#s-sound_intensity").value === "balanced" && document.querySelector("#s-audio_mastering").checked);
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#settings-audio")).opacity) > .99 && document.querySelector("#build-warning").hidden);
  assert.equal(await page.evaluate(() => isSettingsView()), true);
  assert.match(await page.$eval("#settings-channel-note", node => node.textContent), /Sound design channel/);
  await page.screenshot({path: path.join(screenshots, "settings-sound-desktop.png")});

  const running = await (await fetch(`${origin}/api/build`)).json();
  assert.equal(running.status, "current"); assert.equal(running.diagnostics, undefined);
  await page.click("#build-info");
  await page.waitForFunction(() => document.querySelector("#build-details").textContent.includes("Project directory"));
  assert.match(await page.$eval("#build-details", node => node.textContent), /Python executable/);
  assert.ok((await page.$eval("#build-details", node => node.textContent)).includes(root));
  await page.click('#build-dialog [data-close-dialog="build-dialog"]');

  await page.setViewport({width: 390, height: 844});
  await page.click('#tabs a[data-view="home"]');
  await page.click("#settings-btn");
  await page.waitForFunction(() => location.hash === "#settings" && document.querySelector("#view-settings").classList.contains("active"));
  assert.ok(await page.$eval("#settings-btn em", node => getComputedStyle(node).display !== "none" && node.getBoundingClientRect().width > 0));
  assert.ok(await page.$eval("#s-sound_mode", node => node.getBoundingClientRect().bottom < innerHeight), "Sound direction is visible on the first mobile Settings screen");
  await openSection("audio");
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#settings-audio")).opacity) > .99);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: path.join(screenshots, "settings-sound-mobile.png")});

  const [first, second] = await Promise.all([selected, other].map(async profile => (await fetch(`${origin}/api/settings?profile_id=${profile.id}`)).json()));
  assert.deepEqual([first.sound_mode, first.sound_intensity, first.audio_mastering], ["auto", "balanced", true]);
  assert.deepEqual([second.sound_mode, second.sound_intensity, second.audio_mastering], ["auto", "low", true]);
  assert.equal(writes.filter(item => item.path === "/api/run").length, 0);
  assert.ok(writes.every(item => item.path === "/api/save-settings"), JSON.stringify(writes.map(item => item.path)));
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: a server without build diagnostics gives an explicit restart instruction", {skip: skipped}, async () => {
  const {page, errors} = await openStudio(request => {
    if (new URL(request.url()).pathname !== "/api/build") return false;
    request.respond({status: 404, contentType: "text/html", body: "Not Found"});
    return true;
  });
  await page.waitForSelector("#build-warning:not([hidden])");
  assert.match(await page.$eval("#build-warning", node => node.textContent), /running server does not have the updated version check/);
  assert.match(await page.$eval("#build-warning", node => node.textContent), /Ctrl\+C.*gui\.py.*same Python environment/);
  assert.doesNotMatch(await page.$eval("#build-warning", node => node.textContent), /clear.*cache/i);
  assert.equal(await page.$eval("#build-label", node => node.textContent), "Restart needed");
  await page.click("#build-info");
  await page.waitForFunction(() => document.querySelector("#build-dialog-status").textContent.includes("Ctrl+C"));
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

test("studio: Motion selection survives delayed defaults and a new video resets partial steps", {skip: skipped}, async () => {
  const create = async (name, visual_mode) => (await (await fetch(`${origin}/api/channels`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, defaults: {visual_mode}}),
  })).json()).profile;
  const project = await create("Motion controls fixture", "classic");
  const other = await create("Hybrid controls fixture", "hybrid");
  await fetch(`${origin}/api/channels/${project.id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const oldSettings = await (await fetch(`${origin}/api/settings?profile_id=${project.id}`)).json();
  let hold = false, pending;
  const requests = [];
  const {page, errors} = await openStudio(request => {
    const pathname = new URL(request.url()).pathname;
    if (hold && pathname === "/api/settings") { pending = request; hold = false; return true; }
    if (pathname === "/api/run") requests.push(JSON.parse(request.postData()));
    return false;
  });
  assert.equal(await page.$eval("#visual-mode", node => node.value), "classic");
  hold = true;
  const settingsRequested = page.waitForRequest(request => new URL(request.url()).pathname === "/api/settings");
  await page.evaluate(() => { window.delayedSettingsLoad = loadSettings(); });
  await settingsRequested;
  await page.select("#visual-mode", "motion");
  await pending.respond({status: 200, contentType: "application/json", body: JSON.stringify(oldSettings)});
  await page.evaluate(() => window.delayedSettingsLoad);
  assert.equal(await page.$eval("#visual-mode", node => node.value), "motion", "An older profile default must not replace the user's selection");
  assert.equal(await page.$eval('#steps input[value="images"]', node => node.disabled), true);
  await page.click(`[data-profile-id="${other.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id && document.querySelector("#visual-mode").value === "hybrid", {}, other.id);
  assert.equal(await page.$eval('#steps input[value="images"]', node => node.disabled), false);
  assert.equal(await page.$eval('#steps input[value="images"]', node => node.checked), true);
  await page.click(`[data-profile-id="${project.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id && document.querySelector("#visual-mode").value === "motion", {}, project.id);

  const submit = async () => {
    const response = page.waitForResponse(response => new URL(response.url()).pathname === "/api/run" && response.request().method() === "POST");
    await page.click('#ask-form button[type="submit"]');
    assert.equal((await response).status(), 400, "The fixture records the request without running providers");
    await page.waitForSelector("#hero-form:not([hidden])");
  };
  await fill(page, "#topic-input", "A new animated science explanation");
  await submit();
  assert.equal(requests[0].visual_mode, "motion");
  assert.equal(requests[0].use_project_photos, false);
  assert.equal(requests[0].profile_id, project.id);
  assert.ok(requests[0].steps.includes("research") && requests[0].steps.includes("script"));
  assert.ok(!requests[0].steps.includes("images"));
  await page.evaluate(() => loadSettings());
  assert.equal(await page.$eval("#visual-mode", node => node.value), "motion");

  await page.$eval("#steps", node => node.closest("details").open = true);
  for (const step of ["research", "script", "narration", "music", "visuals"]) await page.click(`#steps label:has(input[value="${step}"])`);
  assert.match(await page.$eval("#run-intent-note", node => node.textContent), /Research is off/);
  assert.match(await page.$eval('#ask-form button[type="submit"]', node => node.textContent), /Redo selected steps/);
  assert.equal(await page.$eval("#surprise-btn", node => node.disabled), true);
  await fill(page, "#topic-input", "A different topic must never reuse the previous project");
  await page.click('#ask-form button[type="submit"]');
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some(node => node.textContent.includes("Enable Research")));
  assert.equal(requests.length, 1, "A new topic cannot silently submit a cached render");
  await fill(page, "#topic-input", "");
  await submit();
  assert.deepEqual(requests[1].steps, ["video"]);
  assert.equal(requests[1].topic, "", "Explicit partial reruns remain available");
  await page.evaluate(() => showDraftCompletion({has_preview: true, has_storyboard: true, review_ready: true}));
  await page.click("#make-another-video");
  assert.equal(await page.$eval("#topic-input", node => node.value), "");
  assert.equal(await page.$eval("#visual-mode", node => node.value), "motion");
  assert.equal(await page.$eval("#run-intent-note", node => node.hidden), true);
  assert.equal(await page.$eval("#surprise-btn", node => node.disabled), false);
  await fill(page, "#topic-input", "A completely new topic after the old draft");
  await submit();
  assert.equal(requests[2].topic, "A completely new topic after the old draft");
  assert.deepEqual(requests[2].steps, ["research", "script", "narration", "music", "visuals", "video"]);
  assert.equal(requests[2].use_project_photos, false);
  await page.select("#visual-mode", "hybrid");
  await submit();
  assert.equal(requests[3].use_project_photos, true);
  assert.ok(requests[3].steps.includes("images"), "Hybrid restores the Photos step selection");
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: automatic sound and local model discovery are profile-pinned and never generate", {skip: skipped}, async () => {
  const create = async name => (await (await fetch(`${origin}/api/channels`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name}),
  })).json()).profile;
  const first = await create("Automatic sound fixture"), second = await create("Local settings isolation fixture");
  await fetch(`${origin}/api/channels/${first.id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const runs = [], discoveries = [];
  let delay = false, delayed;
  const {page, errors} = await openStudio(request => {
    const url = new URL(request.url());
    if (url.pathname === "/api/run") runs.push(request.postData());
    if (url.pathname === "/api/local-models") {
      discoveries.push({profile: url.searchParams.get("profile_id"), body: JSON.parse(request.postData())});
      if (delay) { delayed = request; delay = false; return true; }
    }
    return false;
  });
  await page.click("#settings-btn");
  await page.waitForFunction(id => settingsProfileId === id, {}, first.id);
  assert.equal(await page.$eval("#s-sound_mode", node => node.value), "auto");
  assert.equal(await page.$eval("#s-sound_intensity", node => node.value), "low");
  assert.equal(await page.$eval("#manual-audio-settings", node => node.hidden), true);
  assert.equal(await page.$eval("#audio-library-details", node => node.open), false);
  assert.equal(await page.$("#s-keyless_llm"), null);
  assert.equal(await page.$("#bg-ai"), null);
  assert.equal(await page.$('#img-sources input[value="pollinations"]'), null);
  await page.select("#s-sound_intensity", "balanced");
  await page.select("#s-caption_style", "highlight");
  await page.select("#s-llm_route", "local");
  await fill(page, "#s-ollama_base_url", origin);
  const response = page.waitForResponse(response => new URL(response.url()).pathname === "/api/local-models");
  await page.click("#discover-local-models");
  const result = await response;
  assert.equal(result.status(), 200, await result.text());
  await page.waitForFunction(() => document.querySelector("#local-model-discovery-status").textContent.includes("1 installed model"));
  assert.deepEqual(await page.$$eval("#local-models option", nodes => nodes.map(node => node.value)), ["fixture-small:latest"]);
  assert.deepEqual(discoveries, [{profile: first.id, body: {base_url: origin}}]);
  await fill(page, "#s-ollama_model", "fixture-small:latest");
  const saving = page.waitForResponse(response => new URL(response.url()).pathname === "/api/save-settings");
  await page.click("#savebar-save");
  assert.equal((await saving).status(), 200);
  await page.waitForFunction(() => !settingsSavePending && !settingsDirty());
  const saved = await (await fetch(`${origin}/api/settings?profile_id=${first.id}`)).json();
  assert.equal(saved.sound_mode, "auto"); assert.equal(saved.sound_intensity, "balanced");
  assert.equal(saved.llm_route, "local"); assert.equal(saved.ollama_base_url, origin);
  assert.equal(saved.ollama_model, "fixture-small:latest"); assert.equal(saved.caption_style, "highlight");
  await page.setViewport({width: 390, height: 844});
  await page.$eval("#settings-audio", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 80, behavior: "instant"}));
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#settings-audio")).opacity) > .99);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: path.join(screenshots, "studio-automatic-sound-mobile.png")});
  await page.setViewport({width: 1440, height: 1100});
  await page.$eval("#discover-local-models", node => node.scrollIntoView({block: "center", behavior: "instant"}));
  await page.waitForFunction(() => !document.querySelector("#discover-local-models").disabled);
  delay = true;
  const pending = page.waitForRequest(request => new URL(request.url()).pathname === "/api/local-models", {timeout: 5000});
  await page.click("#discover-local-models"); await pending;
  await page.click(`[data-profile-id="${second.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id, {}, second.id);
  await delayed.respond({status: 200, contentType: "application/json", body: JSON.stringify({ok: true, models: [{name: "stale-model:latest", size: 123}]})});
  await page.waitForFunction(() => !document.querySelector("#discover-local-models").disabled);
  assert.equal(await page.$eval("#s-llm_route", node => node.value), "auto");
  assert.equal(await page.$eval("#s-sound_intensity", node => node.value), "low");
  assert.deepEqual(await page.$$eval("#local-models option", nodes => nodes.map(node => node.value)), []);
  assert.doesNotMatch(await page.$eval("#local-model-discovery-status", node => node.textContent), /installed model found|stale-model/);
  assert.deepEqual(await (await fetch(`${origin}/_fixture/local-requests`)).json(), ["tags"], "Discovery only reads the local catalogue; it never pulls or runs a model");
  assert.equal(runs.length, 0);
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: real audio imports, previews and selections stay with their channel", {skip: skipped}, async () => {
  const create = async name => (await (await fetch(`${origin}/api/channels`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, defaults: {music_enabled: false, sfx_enabled: false}}),
  })).json()).profile;
  const project = await create("Audio fixture channel"), other = await create("Other audio fixture channel");
  await fetch(`${origin}/api/channels/${project.id}/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const frames = 2000, rate = 8000, wav = Buffer.alloc(44 + frames * 2);
  wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write("data", 36); wav.writeUInt32LE(frames * 2, 40);
  for (let index = 0; index < frames; index++) wav.writeInt16LE(Math.round(700 * Math.sin(index * 2 * Math.PI * 220 / rate)), 44 + index * 2);
  const filename = path.join(temporary, "local-audio-fixture.wav");
  await writeFile(filename, wav);
  const runs = [], imports = [];
  let hold = false, pending;
  const {page, errors} = await openStudio(request => {
    const url = new URL(request.url());
    if (url.pathname === "/api/run") runs.push(JSON.parse(request.postData()));
    if (url.pathname === "/api/audio-library/import") imports.push({profile: url.searchParams.get("profile_id"), body: JSON.parse(request.postData())});
    if (hold && url.pathname === "/api/audio-library" && url.searchParams.get("profile_id") === project.id) { pending = request; hold = false; return true; }
    return false;
  });
  await page.click("#settings-btn");
  await page.waitForFunction(id => settingsProfileId === id && audioLibraryProfileId === id && document.querySelector("#audio-library-status").textContent.includes("Automatic sound design works without importing files"), {}, project.id);
  await page.click("#audio-library-details > summary");
  assert.equal(await page.$eval("#s-sfx_enabled", node => node.checked), false);
  const importAudio = async (kind, title, contentId) => {
    await (await page.$("#audio-import-file")).uploadFile(filename);
    await page.select("#audio-import-kind", kind);
    await fill(page, "#audio-import-title", title);
    await page.$eval("#audio-import-source", node => node.closest("details").open = true);
    await fill(page, "#audio-import-source", `https://example.com/audio/${kind}`);
    await fill(page, "#audio-import-creator", "Local test fixture");
    await fill(page, "#audio-import-license", "Synthetic local browser fixture; no external audio downloaded.");
    await page.select("#audio-import-content-id", contentId);
    const response = page.waitForResponse(response => new URL(response.url()).pathname === "/api/audio-library/import");
    await page.click("#audio-import-submit");
    const result = await response;
    assert.equal(result.status(), 201);
    const asset = (await result.json()).asset;
    await page.waitForSelector(`[data-audio-id="${asset.id}"]`);
    await page.waitForFunction(() => !audioImportPending);
    return asset;
  };
  const music = await importAudio("music", "Fixture music", "registered");
  const effect = await importAudio("sfx", "Fixture ambience", "unknown");
  assert.ok(imports.every(item => item.profile === project.id));
  assert.equal(imports[0].body.content_id, "registered");
  assert.equal(await page.$eval("#s-music_enabled", node => node.checked), false);
  assert.equal(await page.$eval("#s-sfx_enabled", node => node.checked), false);
  assert.equal(await page.$eval("#s-music_track", node => node.value), "");
  assert.equal(await page.$eval("#s-sfx_track", node => node.value), "");
  assert.equal(await page.evaluate(() => !!settingsDirty()), false, "Imports do not select or enable audio");
  assert.deepEqual(await page.$$eval("#s-music_track option", nodes => nodes.map(node => node.value)), ["", music.id]);
  assert.deepEqual(await page.$$eval("#s-sfx_track option", nodes => nodes.map(node => node.value)), ["", effect.id]);
  assert.match(await page.$eval(`[data-audio-id="${music.id}"]`, node => node.textContent), /Content ID registered/);
  const previewResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/audio-library/${music.id}/preview`);
  await page.$eval(`[data-audio-id="${music.id}"] audio`, audio => audio.load());
  assert.ok([200, 206].includes((await previewResponse).status()));
  await page.waitForFunction(id => document.querySelector(`[data-audio-id="${id}"] audio`).readyState >= 1, {}, music.id);
  assert.equal(await page.$eval(`[data-audio-id="${music.id}"] audio`, audio => audio.paused), true);

  await page.select("#s-sound_mode", "manual");
  await page.select("#s-music_track", music.id);
  await page.select("#s-sfx_track", effect.id);
  await page.click('#settings-audio label:has(#s-music_enabled)'); await page.click('#settings-audio label:has(#s-sfx_enabled)');
  await page.select("#s-sfx_mode", "ambience");
  await page.$eval("#r-sfx_volume", node => { node.value = "12"; node.dispatchEvent(new Event("input", {bubbles: true})); });
  assert.match(await page.$eval("#audio-effect-mode-note", node => node.textContent), /Ambience loops/);
  const saving = page.waitForResponse(response => new URL(response.url()).pathname === "/api/save-settings");
  await page.click("#savebar-save");
  assert.equal((await saving).status(), 200);
  await page.waitForFunction(() => !settingsSavePending && !settingsDirty());
  const stored = await (await fetch(`${origin}/api/settings?profile_id=${project.id}`)).json();
  assert.equal(stored.music_track, music.id); assert.equal(stored.sfx_track, effect.id);
  assert.equal(stored.music_enabled, true); assert.equal(stored.sfx_enabled, true);
  assert.equal(stored.sfx_mode, "ambience"); assert.equal(stored.sfx_volume, .12);
  const oldList = await (await fetch(`${origin}/api/audio-library?profile_id=${project.id}`)).json();
  assert.equal(oldList.assets.length, 2);
  assert.equal(oldList.assets.find(asset => asset.id === music.id).license_note, imports[0].body.license_note);
  hold = true;
  const loading = page.waitForRequest(request => new URL(request.url()).pathname === "/api/audio-library");
  await page.evaluate(() => { window.delayedAudioLoad = loadAudioLibrary(); });
  await loading;
  await page.click(`[data-profile-id="${other.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id && audioLibraryProfileId === id && document.querySelector("#audio-library-status").textContent.includes("Automatic sound design works without importing files"), {}, other.id);
  await pending.respond({status: 200, contentType: "application/json", body: JSON.stringify(oldList)});
  await page.evaluate(() => window.delayedAudioLoad);
  assert.equal(await page.$$eval(".audio-asset", nodes => nodes.length), 0, "A stale response cannot show another channel's library");
  assert.equal(await page.$eval("#s-music_track", node => node.value), "");
  assert.equal(await page.$eval("#s-sfx_enabled", node => node.checked), false);
  const denied = await fetch(`${origin}/api/audio-library/${music.id}/preview?profile_id=${other.id}`);
  assert.equal(denied.status, 404);
  await page.click(`[data-profile-id="${project.id}"]`);
  await page.waitForFunction(id => settingsProfileId === id && document.querySelectorAll(".audio-asset").length === 2, {}, project.id);
  assert.equal(await page.$eval("#s-music_track", node => node.value), music.id);
  assert.equal(await page.$eval("#s-sfx_track", node => node.value), effect.id);
  assert.equal(await page.$eval("#s-sfx_mode", node => node.value), "ambience");
  assert.equal(runs.length, 0, "Importing, previewing and saving selections must never generate a video");
  await page.click("#settings-btn");
  await page.setViewport({width: 390, height: 844});
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#settings-audio")).opacity) > .99);
  await page.$eval("#settings-audio", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 80, behavior: "instant"}));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: path.join(screenshots, "studio-audio-library-mobile.png")});
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: two tabs receive every real run event and resume with emitted timestamps", {skip: skipped}, async () => {
  const a = await openStudio(), b = await openStudio();
  const streamRequests = [];
  a.page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname === "/api/stream") streamRequests.push(url);
  });
  const emit = async (action, rows = []) => {
    const response = await fetch(`${origin}/_fixture/run-events`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action, events: rows})});
    assert.equal(response.status, 200);
    return response.json();
  };
  try {
    const started = await emit("start", [{kind: "log", msg: "Recording section 1/3"}]);
    await Promise.all([a.page.evaluate(() => pollOnce()), b.page.evaluate(() => pollOnce())]);
    for (const page of [a.page, b.page]) {
      await page.waitForFunction(() => document.querySelector("#console").textContent.includes("Recording section 1/3"));
      const stamp = await page.evaluate(value => new Date(value).toTimeString().slice(0, 8), started.events[0].timestamp);
      assert.equal(await page.$eval("#console .t", node => node.textContent), stamp, "Log clock must use the server's original emission time");
    }
    await a.page.evaluate(() => { es.close(); es = null; });
    await emit("append", [{kind: "log", msg: "Recording section 2/3"}, {kind: "log", msg: "Recording section 3/3"}]);
    await a.page.evaluate(() => startSSE(status));
    for (const page of [a.page, b.page]) {
      await page.waitForFunction(() => document.querySelector("#console").textContent.includes("Recording section 3/3"));
      const lines = await page.$$eval("#console > div", nodes => nodes.map(node => node.lastElementChild.textContent));
      assert.deepEqual(lines, ["Recording section 1/3", "Recording section 2/3", "Recording section 3/3"]);
    }
    assert.ok(streamRequests.some(url => url.searchParams.get("after") === started.events[0].id), "Reconnect carries the last received event ID");
    assert.ok(streamRequests.every(url => url.searchParams.get("run_id") === started.run_id && url.searchParams.get("profile_id") === started.profile_id));
    await emit("append", [{kind: "stopped"}]);
    for (const page of [a.page, b.page]) {
      await page.waitForFunction(() => es === null && streamEndedScope === eventScopeKey(status));
      await page.evaluate(() => pollOnce());
      assert.equal(await page.evaluate(() => es === null), true, "A terminal event must not reopen while the worker finishes its checkpoint");
    }
    assert.deepEqual(a.errors, []);
    assert.deepEqual(b.errors, []);
  } finally {
    await emit("restore");
    await a.page.close();
    await b.page.close();
  }
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
  assert.equal(requests[0].use_project_photos, true);
  assert.equal(requests[0].preview_only, true);
  const rebuilding = page.waitForRequest(request => new URL(request.url()).pathname === "/api/run");
  await page.click("#rebuild-motion-graphics");
  await rebuilding;
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].steps, ["visuals", "video"]);
  assert.equal(requests[1].profile_id, project.id);
  assert.equal(requests[1].visual_mode, "motion");
  assert.equal(requests[1].use_project_photos, false);
  assert.equal(requests[1].preview_only, true);
  assert.equal(requests[1].topic, "");
  assert.deepEqual(errors, []);
  await page.close();
});

async function openSceneReview({visualMode = "hybrid", useProjectPhotos, legacyImage = false, illustrated = false, soundMode = "auto"} = {}) {
  let profiles = await (await fetch(`${origin}/api/channels`)).json();
  let project = profiles.profiles.find(profile => profile.name === "Review fixture");
  if (!project) project = (await (await fetch(`${origin}/api/channels`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "Review fixture"})})).json()).profile;
  await fetch(`${origin}/api/channels/default/activate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const plan = {mode: visualMode, width: 360, height: 640, fps: 12, duration: 14, scenes: [
    {id: "scene-one", title: "First scene", template: "explain", goal: "Keep this explanation", start: 0, duration: 7},
    {id: "scene-two", title: "Original second scene", template: visualMode === "hybrid" ? "footage" : "explain", goal: "Show this part differently", start: 7, duration: 7},
  ]};
  if (useProjectPhotos !== undefined) plan.use_project_photos = useProjectPhotos;
  if (legacyImage) plan.scenes[1].image = {path: "images/previous-photo.jpg"};
  if (illustrated) plan.scenes.forEach(scene => scene.graphics_version = 1);
  const st = {running: false, stages: {}, profile_id: project.id, profile_name: project.name,
    active_profile_id: "default", video_type: "shorts", visual_mode: visualMode, preview_only: true,
    has_preview: true, has_storyboard: true, has_audio: true, has_script: true, has_thumb: false,
    plan_revision: "1".repeat(64), preview_revision: "mp4-before"};
  const fixture = {regenerations: [], runs: [], regenerationError: null, startError: null, audioCredits: [], audioAttributionText: "", soundMode, soundDesign: null, audioMaster: null, scriptQuality: null, plan};
  let revision = 1, holdVisuals = null, holdRegeneration = null;
  const visuals = () => ({ok: true, plan: structuredClone(plan), revision, plan_revision: st.plan_revision,
    preview_revision: `html-${revision}`, visual_mode: visualMode, preview_url: "/api/visuals/preview/index.html?preview=1"});
  const {page, errors} = await openStudio(request => {
    const url = new URL(request.url());
    const respond = (data, code = 200) => { request.respond({status: code, contentType: "application/json", body: JSON.stringify(data)}); return true; };
    if (url.pathname === "/api/status") return respond({...st});
    if (url.pathname === "/api/output") return respond({title: "Saved title", description: "Saved description", tags: ["saved tag"], sections: [{narration: "The recorded narration stays unchanged."}], audio_credits: fixture.audioCredits, audio_attribution_text: fixture.audioAttributionText, sound_preferences: {mode: fixture.soundMode, intensity: "low"}, sound_design: fixture.soundDesign, audio_master: fixture.audioMaster, script_quality: fixture.scriptQuality});
    if (url.pathname === "/api/visuals") {
      const data = visuals();
      if (holdVisuals) { const resolve = holdVisuals; holdVisuals = null; resolve(() => request.respond({status: 200, contentType: "application/json", body: JSON.stringify(data)})); return true; }
      return respond(data);
    }
    if (url.pathname === "/api/visuals/scenes/scene-two/regenerate") {
      fixture.regenerations.push(JSON.parse(request.postData()));
      const answer = () => {
        if (fixture.regenerationError) { st.visual_editing = false; return respond({ok: false, error: fixture.regenerationError}, 422); }
        revision++;
        plan.scenes[1] = {...plan.scenes[1], title: "Updated second scene", template: "comparison"};
        plan.last_edit = {scene_id: "scene-two", status: "local", changes: ["Changed to a comparison layout", "Used another saved photo"]};
        Object.assign(st, {has_preview: false, has_video: false, preview_revision: null, visual_editing: false, plan_revision: String(revision).repeat(64)});
        return respond(visuals());
      };
      if (holdRegeneration) { const resolve = holdRegeneration; holdRegeneration = null; st.visual_editing = true; resolve(answer); return true; }
      return answer();
    }
    if (url.pathname === "/api/visuals/preview/index.html") {
      request.respond({status: 200, contentType: "text/html", headers: {"Cache-Control": "no-store"}, body: `<!doctype html><meta charset="utf-8"><body style="background:#13291e;color:#e8f4eb;font:24px sans-serif"><h1>${plan.scenes[1].title}</h1><output id="seek">0</output><script>window.addEventListener("message", event => { if(event.source === parent && event.data?.type === "yaas:seek") document.getElementById("seek").textContent = String(event.data.time); }); parent.postMessage({type:"yaas:ready"}, "*");</script>`});
      return true;
    }
    if (url.pathname === "/api/run") {
      fixture.runs.push(JSON.parse(request.postData()));
      if (fixture.startError) return respond({ok: false, error: fixture.startError}, 409);
      Object.assign(st, {running: true, error: null, stop_requested: false, started_at: Date.now() / 1000, stages: {preparation: "done", visuals: "done", video: "running"}});
      return respond({ok: true});
    }
    if (url.pathname === "/api/stream") { request.respond({status: 200, contentType: "text/event-stream", body: 'data: {"kind":"ping"}\n\n'}); return true; }
    if (url.pathname === "/api/keywords") return respond({suggestions: [], related: []});
    if (url.pathname === "/api/seo") return respond({score: 100, checks: []});
    return false;
  });
  await page.click('#tabs a[data-view="review"]');
  await page.waitForSelector('[data-scene="scene-two"]');
  await page.click('[data-scene="scene-two"]');
  await page.waitForFunction(() => document.querySelector("#player video")?.readyState >= 1);
  fixture.page = page; fixture.errors = errors; fixture.profileId = project.id;
  fixture.holdVisuals = () => new Promise(resolve => { holdVisuals = resolve; });
  fixture.holdRegeneration = () => new Promise(resolve => { holdRegeneration = resolve; });
  fixture.finish = async (error = null) => {
    Object.assign(st, {running: false, error, has_preview: !error, preview_revision: error ? null : `mp4-after-${fixture.runs.length}`, stages: {preparation: "done", visuals: "done", video: error ? "error" : "done"}});
    await page.evaluate(() => pollOnce());
  };
  return fixture;
}

test("studio: applying saved audio and both retry paths keep the narration, scenes and unsaved metadata", {skip: skipped}, async () => {
  const fixture = await openSceneReview({soundMode: "manual"}), {page, errors} = fixture;
  await fill(page, "#edit-title", "Unsaved title during audio mix");
  await fill(page, "#edit-desc", "Unsaved description during audio mix");
  await page.click("#apply-review-audio");
  await page.waitForFunction(() => reviewRender && !reviewRender.starting);
  assert.equal(fixture.runs.length, 1);
  assert.deepEqual(fixture.runs[0].steps, ["music", "video"]);
  assert.equal(fixture.runs[0].profile_id, fixture.profileId);
  assert.equal(fixture.runs[0].expected_plan_revision, "1".repeat(64));
  assert.equal(fixture.runs[0].use_project_photos, true);
  assert.equal(fixture.runs[0].preview_only, true);
  assert.equal(fixture.runs[0].topic, "");
  assert.equal(await page.$eval("#apply-review-audio", node => node.disabled), true);
  await fixture.finish("Fixture audio mix failed");
  await page.waitForFunction(() => !reviewRender && document.querySelector("#review-render-status").textContent.includes("Apply music & effects to current draft"));
  assert.equal(await page.$eval("#edit-title", node => node.value), "Unsaved title during audio mix");
  await page.click("#apply-review-audio");
  await page.waitForFunction(() => reviewRender && !reviewRender.starting);
  await fixture.finish("Fixture audio mix failed again");
  await page.waitForFunction(() => !reviewRender && document.querySelector("#review-render-status").classList.contains("error"));
  await page.click('#tabs a[data-view="home"]');
  await page.click("#run-error button");
  await page.waitForFunction(() => reviewRender && !reviewRender.starting);
  await page.click('#tabs a[data-view="review"]');
  await page.waitForSelector("#view-review.active");
  fixture.audioCredits = [{kind: "music", title: "Applied fixture soundtrack", creator: "Local test fixture", source_url: "https://example.com/audio/music", content_id: "registered", volume: .08}];
  await fixture.finish();
  await page.waitForFunction(() => !reviewRender && document.querySelector("#review-render-status").textContent.startsWith("Updated draft ready"));
  assert.equal(fixture.runs.length, 3);
  assert.ok(fixture.runs.every(run => JSON.stringify(run.steps) === JSON.stringify(["music", "video"]) && run.use_project_photos === true && run.expected_plan_revision === "1".repeat(64)));
  assert.equal(await page.$eval("#edit-title", node => node.value), "Unsaved title during audio mix");
  assert.equal(await page.$eval("#edit-desc", node => node.value), "Unsaved description during audio mix");
  assert.equal(await page.evaluate(() => selectedScene), "scene-two");
  await page.waitForFunction(() => document.querySelector("#player video")?.readyState >= 1 && Math.abs(document.querySelector("#player video").currentTime - 7.35) < .1);
  assert.match(await page.$eval("#review-audio-credits", node => node.textContent), /Applied fixture soundtrack/);
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#rv-audio")).opacity) > .99);
  await page.$eval("#rv-audio", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 80, behavior: "instant"}));
  await page.screenshot({path: path.join(screenshots, "studio-review-audio.png")});
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: Review renders preserve graphics-only and legacy photo policies", {skip: skipped}, async () => {
  for (const options of [
    {visualMode: "motion", useProjectPhotos: false, illustrated: true},
    {visualMode: "motion", legacyImage: true},
    {visualMode: "motion", useProjectPhotos: true},
  ]) {
    const fixture = await openSceneReview(options), {page, errors} = fixture;
    const photos = options.useProjectPhotos ?? true;
    assert.equal(await page.$eval("#replace-scene-image", node => node.closest("label").hidden), !photos);
    assert.equal(await page.$eval("#replace-scene-image", node => node.disabled), !photos);
    assert.equal(await page.$eval("#legacy-motion-card", node => node.hidden), !photos);
    await page.select("#visual-mode", photos ? "motion" : "hybrid");
    await page.click("#render-draft");
    await page.waitForFunction(() => reviewRender && !reviewRender.starting);
    assert.equal(fixture.runs.length, 1);
    assert.deepEqual(fixture.runs[0].steps, ["video"]);
    assert.equal(fixture.runs[0].visual_mode, "motion", "Home's next-video style must not replace the open storyboard's style");
    assert.equal(fixture.runs[0].use_project_photos, photos);
    assert.equal(fixture.runs[0].expected_plan_revision, "1".repeat(64));
    assert.deepEqual(errors, []);
    await page.close();
  }
});

test("studio: automatic sound report, safe cue text and regeneration preserve the current plan", {skip: skipped}, async () => {
  const fixture = await openSceneReview({visualMode: "motion", useProjectPhotos: false}), {page, errors} = fixture;
  assert.equal(await page.$eval("#apply-review-audio", node => node.textContent), "Regenerate sound design");
  await fill(page, "#edit-title", "Unsaved automatic sound title");
  await page.click("#apply-review-audio");
  await page.waitForFunction(() => reviewRender && !reviewRender.starting);
  assert.deepEqual(fixture.runs[0].steps, ["music", "video"]);
  assert.equal(fixture.runs[0].profile_id, fixture.profileId);
  assert.equal(fixture.runs[0].use_project_photos, false);
  assert.equal(fixture.runs[0].expected_plan_revision, "1".repeat(64));
  fixture.soundDesign = {mode: "auto", planner: {status: "ai", provider: "Local model fixture"}, summary: "A quiet bed supports the explanation.",
    music: {start: 0, duration: 14, asset: {title: "Soft keys"}, reason: "Keep the focus on speech."},
    events: [{start: 7, duration: 1.2, label: "<img src=x onerror=alert(1)>", reason: "Mark the second section gently."}]};
  fixture.audioMaster = {status: "applied", output_lufs: -16.1, output_true_peak_dbtp: -1.6};
  fixture.scriptQuality = {status: "reviewed", score: 88, rewritten: true, issues: ["Check the supplied source for this claim."]};
  await fixture.finish();
  await page.waitForFunction(() => !reviewRender && document.querySelector("#review-render-status").textContent.startsWith("Updated draft ready"));
  assert.equal(await page.$eval("#edit-title", node => node.value), "Unsaved automatic sound title");
  assert.match(await page.$eval("#review-sound-design", node => node.textContent), /0:07\.0–0:08\.2/);
  assert.match(await page.$eval("#review-sound-design", node => node.textContent), /Mark the second section gently/);
  assert.equal(await page.$("#review-sound-design img"), null);
  assert.match(await page.$eval("#review-audio-master", node => node.textContent), /-16\.1 LUFS · -1\.6 dBTP/);
  assert.match(await page.$eval("#editorial-checks-content", node => node.textContent), /Writing check: 88\/100/);
  await page.click("#review-sound-design summary");
  await page.$eval("#rv-audio", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 80, behavior: "instant"}));
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector("#rv-audio")).opacity) > .99);
  await page.screenshot({path: path.join(screenshots, "studio-automatic-sound-review.png")});
  fixture.soundMode = "off";
  await page.evaluate(() => loadReview({preserveEdits: true}));
  assert.equal(await page.$eval("#apply-review-audio", node => node.textContent), "Remove added sound from draft");
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: sound report separates an omitted music proposal from applied effects and exports exact credits", {skip: skipped}, async () => {
  const fixture = await openSceneReview({visualMode: "motion", useProjectPhotos: false}), {page, errors} = fixture;
  fixture.soundDesign = {mode: "auto", planner: {status: "ai", provider: "Free model fixture"},
    summary: "A music bed supports the film.", intent: "A gentle instrumental bed and page turns support this narration.",
    music: null, ambience: null, events: [{start: 7, duration: .4, label: "Chapter transition", reason: "A page turns at the new section."}],
    warnings: ["The requested music was unavailable; no music was applied."]};
  fixture.audioCredits = [{kind: "sfx", mode: "transitions", title: "Book page turns", creator: "Example artist",
    provider: "bundled", license: "CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0/", source_url: "https://example.org/page-turn"}];
  fixture.audioAttributionText = "Audio credits (updated by YAAS)\nSound effect: Book page turns · Example artist · https://example.org/page-turn · CC BY 4.0 · https://creativecommons.org/licenses/by/4.0/\nAudio adapted: excerpted or looped, faded and mixed beneath narration.\nEnd audio credits";
  await page.evaluate(() => loadReview({preserveEdits: true}));
  assert.equal(await page.$eval(".applied-sound-summary", node => node.textContent), "In this soundtrack: 1 timed effect.");
  assert.match(await page.$eval("#review-sound-design", node => node.textContent), /Music: not applied/);
  assert.equal(await page.$eval(".sound-proposal", node => node.open), false);
  assert.match(await page.$eval(".sound-proposal summary", node => node.textContent), /before recordings were checked/);
  assert.equal(await page.$eval("#audio-credit-text", node => node.value), fixture.audioAttributionText);
  assert.equal(await page.$eval('#review-audio-credits a[href="https://creativecommons.org/licenses/by/4.0/"]', node => node.textContent), "CC BY 4.0");
  await page.evaluate(() => {
    document.querySelector("#review-audio-attribution").open = true;
    Object.defineProperty(navigator, "clipboard", {configurable: true, value: {writeText: async value => { window.copiedAudioCredits = value; }}});
  });
  await page.click("#copy-audio-credits");
  assert.equal(await page.evaluate(() => window.copiedAudioCredits), fixture.audioAttributionText);
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: an older Motion card draft exposes guarded animation and sound rebuild without regenerating voice", {skip: skipped}, async () => {
  const fixture = await openSceneReview({visualMode: "motion", useProjectPhotos: false}), {page, errors} = fixture;
  assert.equal(await page.$eval("#legacy-motion-card", node => node.hidden), false);
  assert.equal(await page.$eval("#rebuild-motion-and-sound", node => node.hidden), false);
  assert.equal(await page.$eval("#animate-legacy", node => node.hidden), true);
  assert.match(await page.$eval("#rebuild-visuals-note", node => node.textContent), /earlier card-based scenes/);
  await page.click("#rebuild-motion-and-sound");
  await page.waitForFunction(() => location.hash === "#home");
  assert.equal(fixture.runs.length, 1);
  assert.deepEqual(fixture.runs[0].steps, ["music", "visuals", "video"]);
  assert.equal(fixture.runs[0].topic, "");
  assert.equal(fixture.runs[0].profile_id, fixture.profileId);
  assert.equal(fixture.runs[0].expected_plan_revision, "1".repeat(64));
  assert.equal(fixture.runs[0].use_project_photos, false);
  assert.equal(fixture.runs[0].preview_only, true);
  await fixture.finish();
  fixture.plan.scenes.forEach(scene => { scene.graphics_version = 1; });
  await page.evaluate(() => loadReview({preserveEdits: true}));
  assert.equal(await page.$eval("#legacy-motion-card", node => node.hidden), true);
  assert.deepEqual(errors, []);
  await page.close();
});

test("studio: scene regeneration refreshes the preview and automatically renders and seeks the draft", {skip: skipped, timeout: 60000}, async () => {
  const fixture = await openSceneReview(), {page, errors} = fixture;
  const before = await page.$eval("#player video", video => video.getAttribute("src"));
  await fill(page, "#edit-title", "My unsaved title");
  await fill(page, "#edit-desc", "My unsaved description");
  await fill(page, "#new-tag", "unsaved tag"); await page.keyboard.press("Enter");
  await fill(page, "#scene-instructions", "Compare the two ideas side by side.");
  await page.click("#replace-scene-image");

  const staleVisuals = fixture.holdVisuals();
  const staleLoad = page.evaluate(() => loadStoryboard(status, {preserveEditor: true}));
  const releaseStaleVisuals = await staleVisuals;
  const regeneration = fixture.holdRegeneration();
  await page.click("#regenerate-scene");
  const releaseRegeneration = await regeneration;
  await page.evaluate(() => loadReview());
  assert.equal(await page.$eval("#edit-title", node => node.value), "My unsaved title", "A Review load during regeneration must preserve edits");
  assert.equal(await page.$eval("#replace-scene-image", node => node.checked), true);
  releaseRegeneration();
  await page.waitForFunction(() => document.querySelector("#review-render-status").textContent.startsWith("Rendering the draft"));
  assert.equal(fixture.regenerations.length, 1);
  assert.deepEqual(fixture.regenerations[0], {instructions: "Compare the two ideas side by side.", replace_image: true, expected_plan_revision: "1".repeat(64)});
  assert.equal(fixture.runs.length, 1);
  assert.deepEqual(fixture.runs[0].steps, ["video"]);
  assert.equal(fixture.runs[0].use_project_photos, true);
  assert.equal(fixture.runs[0].profile_id, fixture.profileId);
  assert.equal(fixture.runs[0].visual_mode, "hybrid");
  assert.equal(fixture.runs[0].video_type, "shorts");
  assert.equal(fixture.runs[0].preview_only, true);
  assert.equal(fixture.runs[0].expected_plan_revision, "2".repeat(64));
  assert.match(await page.$eval("#scene-edit-result", node => node.textContent), /Changed to a comparison layout; Used another saved photo/);
  assert.equal(new URL(page.url()).hash, "#review");
  assert.equal(await page.$("#player video"), null, "The previous draft must not masquerade as the updated render");
  assert.equal(await page.$eval("#regenerate-scene", node => node.disabled), true);
  assert.equal(await page.$eval("#replace-scene-image", node => node.checked), false);
  await page.waitForFunction(() => document.querySelector("#motion-preview").getAttribute("src").includes("revision=html-2"));
  const frame = await (await page.$("#motion-preview")).contentFrame();
  await frame.waitForFunction(() => document.getElementById("seek").textContent === "7.35");
  assert.equal(await frame.$eval("h1", node => node.textContent), "Updated second scene");
  await releaseStaleVisuals();
  await staleLoad;
  assert.match(await page.$eval("#scene-details", node => node.textContent), /Updated second scene/);

  await fill(page, "#edit-desc", "An additional edit made while rendering");
  await fixture.finish();
  await page.waitForFunction(() => document.querySelector("#review-render-status").textContent.startsWith("Updated draft ready"));
  await page.waitForFunction(() => {
    const video = document.querySelector("#player video");
    return video?.readyState >= 1 && Math.abs(video.currentTime - 7.35) < .05;
  }, {timeout: 10000}).catch(async error => {
    const state = await page.evaluate(() => {
      const video = document.querySelector("#player video");
      return {readyState: video?.readyState, currentTime: video?.currentTime, duration: video?.duration, error: video?.error?.message, source: video?.getAttribute("src"), selected: selectedScene, target: selectedSceneTime()};
    });
    error.message += `; refreshed player: ${JSON.stringify(state)}`;
    throw error;
  });
  assert.notEqual(await page.$eval("#player video", video => video.getAttribute("src")), before);
  assert.match(await page.$eval("#player video", video => video.getAttribute("src")), /mp4-after-1/);
  assert.equal(await page.$eval("#player video", video => video.paused), true, "A refreshed draft must not autoplay its audio");
  assert.equal(await page.$eval("#edit-title", node => node.value), "My unsaved title");
  assert.equal(await page.$eval("#edit-desc", node => node.value), "An additional edit made while rendering");
  assert.match(await page.$eval("#tag-list", node => node.textContent), /unsaved tag/);
  assert.equal(await page.$eval("#savebar", node => node.classList.contains("show")), true);
  assert.equal(await page.$eval("#scene-list .selected", node => node.dataset.scene), "scene-two");
  assert.equal(await page.$eval("#regenerate-scene", node => node.disabled), false);
  assert.equal(new URL(page.url()).hash, "#review");
  await page.$eval("#rv-watch", node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 85, behavior: "instant"}));
  await page.waitForFunction(() => {
    const card = document.querySelector("#rv-watch"), video = document.querySelector("#player video");
    const style = getComputedStyle(card), box = video?.getBoundingClientRect();
    return !card.hidden && style.display !== "none" && style.visibility === "visible" && Number(style.opacity) > .99
      && box?.width > 0 && box.height > 0 && video.readyState >= 2 && !video.seeking;
  });
  assert.deepEqual(errors, []);
  await page.screenshot({path: path.join(screenshots, "studio-review-regenerated-draft.png")});
  await page.close();
});

test("studio: scene and render failures stay actionable on Review without losing edits", {skip: skipped, timeout: 60000}, async () => {
  const fixture = await openSceneReview(), {page, errors} = fixture;
  await fill(page, "#edit-title", "Keep this unsaved title");
  await fill(page, "#edit-desc", "Keep this unsaved description");
  await fill(page, "#new-tag", "keep this tag"); await page.keyboard.press("Enter");
  await fill(page, "#scene-instructions", "Use a different photo.");
  await page.click("#replace-scene-image");
  const previous = await page.$eval("#player video", video => video.getAttribute("src"));
  fixture.regenerationError = "There is no other saved photo for this section.";
  await page.click("#regenerate-scene");
  await page.waitForSelector("#scene-edit-result.error");
  assert.match(await page.$eval("#scene-edit-result", node => node.textContent), /no other saved photo/);
  assert.equal(fixture.runs.length, 0);
  assert.equal(await page.$eval("#replace-scene-image", node => node.checked), true);
  assert.equal(await page.$eval("#scene-instructions", node => node.value), "Use a different photo.");
  assert.equal(await page.$eval("#player video", video => video.getAttribute("src")), previous);

  fixture.regenerationError = null;
  fixture.startError = "Another job started. Wait for it to finish.";
  await page.click("#regenerate-scene");
  await page.waitForSelector("#review-render-status.error");
  assert.match(await page.$eval("#review-render-status", node => node.textContent), /Could not start the draft.*Another job started/);
  assert.equal(new URL(page.url()).hash, "#review");
  assert.equal(await page.$("#player video"), null);
  assert.equal(await page.$eval("#render-draft", node => node.disabled), false);

  fixture.startError = null;
  await page.click("#render-draft");
  await page.waitForFunction(() => document.querySelector("#review-render-status").textContent.startsWith("Rendering the draft"));
  await fixture.finish("The local renderer stopped unexpectedly.");
  await page.waitForSelector("#review-render-status.error");
  assert.match(await page.$eval("#review-render-status", node => node.textContent), /Draft rendering failed: The local renderer stopped unexpectedly.*Render draft MP4/);
  assert.equal(await page.$eval("#render-draft", node => node.disabled), false);
  assert.match(await page.$eval("#scene-details", node => node.textContent), /Updated second scene/);
  assert.equal(await page.$eval("#edit-title", node => node.value), "Keep this unsaved title");
  assert.equal(await page.$eval("#edit-desc", node => node.value), "Keep this unsaved description");
  assert.match(await page.$eval("#tag-list", node => node.textContent), /keep this tag/);

  await page.click('#tabs a[data-view="home"]');
  await page.waitForSelector('#run-error button[onclick="retry()"]');
  await page.click('#run-error button[onclick="retry()"]');
  await page.waitForFunction(() => document.querySelector("#review-render-status").textContent.startsWith("Rendering the draft"));
  await page.click('#tabs a[data-view="review"]');
  await page.waitForSelector("#view-review.active");
  await fixture.finish();
  await page.waitForFunction(() => document.querySelector("#review-render-status").textContent.startsWith("Updated draft ready"));
  assert.equal(fixture.regenerations.length, 2, "Retry rendering must reuse the updated scene without another regeneration");
  assert.equal(fixture.runs.length, 3);
  assert.ok(fixture.runs.every(run => run.steps.length === 1 && run.steps[0] === "video" && run.use_project_photos === true && run.expected_plan_revision === "2".repeat(64)));
  assert.equal(new URL(page.url()).hash, "#review");
  assert.equal(await page.$eval("#edit-title", node => node.value), "Keep this unsaved title");
  assert.equal(await page.$eval("#edit-desc", node => node.value), "Keep this unsaved description");
  assert.match(await page.$eval("#tag-list", node => node.textContent), /keep this tag/);
  assert.deepEqual(errors, []);
  await page.close();
});
