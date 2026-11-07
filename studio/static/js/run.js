/* ═══════════════════════════ making a video ═══════════════════════════ */
const STEPS = [
  ["preparation", "Checking the setup",    "Verifying the dependencies needed for this run"],
  ["research",  "Researching the topic",   "Picking the angle and checking facts"],
  ["script",    "Writing the script",      "Hook, story and a strong ending"],
  ["images",    "Finding visual assets",   "Local assets or licensed footage for this visual style"],
  ["narration", "Recording the voiceover", "Natural AI voice with synced captions"],
  ["visuals",   "Building the storyboard", "Choosing scenes and timing their motion to the voiceover"],
  ["video",     "Rendering the video",     "The slow part, usually 2–10 minutes"],
];
let status = {}, es = null, heroMode = "form", runSteps = [], videoType = "normal", lastRunError = "", lastRequest = null, statusRequestSerial = 0;

function setHero(mode) {
  heroMode = mode;
  $("#hero-form").hidden = mode !== "form";
  $("#hero-run").hidden = mode === "form";
  $("#run-progress").hidden = mode !== "running";
  $("#run-done").hidden = mode !== "done";
  $("#run-error").hidden = mode !== "error";
  $("#stop-btn").hidden = mode !== "running";
}

function renderRun(d) {
  const stages = d.stages || {};
  let doneCount = 0, current = null, total = 0;
  $("#steps-list").innerHTML = STEPS.map(([k, title, sub]) => {
    let s = stages[k] || "pending";
    if (runSteps.length && !runSteps.includes(k)) s = "skipped";
    if (s !== "skipped") total++;
    if (s === "done") doneCount++;
    if (s === "running") current = title;
    const ic = { done: "✓", error: "!", skipped: "–" }[s] || "";
    const label = { done: "done", running: "working…", error: "failed", skipped: "reused" }[s] || "";
    return `<div class="step" data-s="${s}"><span class="ic">${ic}</span><div><b>${title}</b><small>${sub}</small></div><em>${label}</em></div>`;
  }).join("");
  total = total || STEPS.length;
  $("#prog-fill").style.width = Math.min(100, (doneCount + (current ? .5 : 0)) / total * 100) + "%";
  $("#prog-step").textContent = current ? `Step ${Math.min(total, doneCount + 1)} of ${total}: ${current.toLowerCase()}` : d.running ? "Getting ready…" : "";
  $("#prog-time").textContent = d.started_at ? clock(Date.now() / 1000 - d.started_at) + " so far" : "";
}

function applyStatus(d) {
  const was = status.running;
  const completedSchedule = was && !d.running && status.schedule_id ? status : null;
  status = d;
  const state = d.running ? "running" : d.error ? "error" : d.has_video || d.has_preview || d.has_storyboard ? "done" : "idle";
  $("#status-pill").className = "status-pill " + state;
  $("#status-text").textContent = d.uploading ? "Uploading…" : d.running ? (d.preview_only ? "Making a draft…" : "Making video…") : d.visual_editing ? "Updating project…" : d.error ? "Last run failed" : d.has_storyboard && !d.has_video ? "Draft ready" : "Ready";
  $("#tabs a[data-view=review]").textContent = d.has_storyboard && !d.has_video ? "Review draft" : "Review & publish";
  if (typeof updateVisualControls === "function") updateVisualControls(d);
  if (typeof updateChannelControls === "function") updateChannelControls(d);
  if (typeof channelProfilesLoaded !== "undefined" && channelProfilesLoaded && d.active_profile_id && d.active_profile_id !== activeProfileId && !channelMutation) {
    loadChannels({ syncWorkspace: true });
  }
  if (d.running) {
    if (heroMode !== "running") {
      setHero("running");
      if (d.schedule_id) {
        runSteps = [];
        lastRunError = "";
        $("#run-title").textContent = "Making a scheduled video…";
      }
      // page opened mid-run: show the topic once research has picked it
      if ((d.stages || {}).research === "done") showTopic();
    }
    renderRun(d);
    if (!es) startSSE();
  }
  if (completedSchedule) {
    // The scheduler restores the previously open project. Its final/error
    // state belongs to that editor, not to the job which just finished.
    if (es) { es.close(); es = null; }
    runSteps = []; lastRunError = "";
    setHero("form");
    loadLibrary(); loadSchedules();
    if (location.hash === "#review") loadReview();
    toast(`Scheduled run finished for ${completedSchedule.profile_name || profileName(completedSchedule.profile_id)}. Check its schedule for the result.`);
  } else if (was && !d.running) onFinished(d);
}
async function pollStatus() {
  await pollOnce();
  setTimeout(pollStatus, status.running || status.uploading ? 1500 : 5000);
}
async function pollOnce() {
  const serial = ++statusRequestSerial;
  try {
    const current = await api("/api/status");
    if (serial === statusRequestSerial) applyStatus(current);
  } catch (e) { }
}

async function onFinished(d) {
  const reviewed = typeof finishReviewRender === "function" && await finishReviewRender(d);
  renderRun(d);
  loadLibrary();
  const msg = d.error || lastRunError;
  if (msg) {
    const failed = STEPS.find(([k]) => (d.stages || {})[k] === "error");
    const quota = /429|quota|RESOURCE_EXHAUSTED|All .*failed/i.test(msg);
    $("#run-title").textContent = "That didn't work";
    $("#run-topic").textContent = "";
    const preparation = d.preflight && !d.preflight.ok ? environmentHTML(d.preflight, true) : "";
    $("#run-error").innerHTML = `<div class="err-box"><b>Stopped at: ${failed ? failed[1].toLowerCase() : "preparation"}</b>${esc(msg.slice(0, 800))}
      <div style="margin-top:8px;color:#d9a8ae">${quota ? "The free AI ran out of quota. Wait a minute and try again, or add a free key in Settings for more quota."
        : preparation ? "Fix the missing items below in the same environment, then try again." : "The technical log below has the details."}</div>${preparation}</div>
      <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap"><button class="btn btn-white" onclick="retry()">Try again</button><button class="btn btn-ghost" onclick="setHero('form')">Change topic</button><a class="btn btn-ghost" href="#settings">Settings</a></div>`;
    setHero("error");
    $("#log-details").open = true;
    return;
  }
  if (!d.has_video && (d.has_preview || d.has_storyboard)) {
    $("#run-title").textContent = "Your draft is ready";
    $("#run-topic").textContent = "";
    $("#run-done").innerHTML = `<div class="draft-ready"><span class="tag">Draft preview</span><h3>Review the motion before the final render</h3><p class="hint">Play the storyboard, change any scene, then render the final video from Review. This draft has not been archived or published.</p><a class="btn btn-white btn-lg" href="#review">Review scenes →</a></div>`;
    setHero("done");
    $("#tabs a[data-view=review]").classList.add("notify");
    if (!reviewed && location.hash === "#review") loadReview();
    return;
  }
  if (!d.has_video || (lastRequest && !lastRequest[2].includes("video"))) {
    setHero("form");
    if (d.stop_requested) toast("Stopped");
    return;
  }
  const out = await api("/api/output").catch(() => ({}));
  $("#run-title").textContent = "Your video is ready";
  $("#run-topic").textContent = "";
  $("#run-done").innerHTML = `<div class="done-box">
      <div class="thumb-frame">${d.has_thumb ? `<img src="/thumbnail/${Date.now()}" alt="">` : ""}</div>
      <div><div style="font-size:20px;line-height:1.25">${esc(out.title || "")}</div>
        <p class="hint" style="margin:8px 0 16px">${out.sources && out.sources.length ? `Fact-checked against ${out.sources.length} sources. ` : ""}Next: watch it, polish the title and thumbnail, then publish.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn btn-white btn-lg" href="#review">Review &amp; publish →</a><button class="btn btn-ghost btn-lg" onclick="setHero('form')">Make another</button></div></div>
    </div>`;
  setHero("done");
  $("#tabs a[data-view=review]").classList.add("notify");
  toast("Your video is ready", "ok");
  if (!reviewed && location.hash === "#review") loadReview();
}

function startSSE() {
  if (es) es.close();
  es = new EventSource("/api/stream");
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.kind === "log") log(d.msg);
    else if (d.kind === "stage") pollOnce();
    else if (d.kind === "research") showTopic();
    else if (["done", "error", "stopped"].includes(d.kind)) {
      if (d.kind === "error") lastRunError = d.msg;
      es.close(); es = null; pollOnce();
    }
  };
  es.onerror = () => { es && es.close(); es = null; };
}
async function showTopic() {
  const r = await api("/api/research").catch(() => null);
  if (r && r.title) $("#run-topic").innerHTML = `“${esc(r.title)}”${r.sources.length ? ` <span class="tag green" style="margin-left:6px">${r.sources.length} sources found</span>` : ""}`;
}
function log(msg) {
  const c = $("#console");
  if (c.firstElementChild && c.firstElementChild.classList.contains("l-dim")) c.innerHTML = "";
  let cls = "";
  if (/\[ERROR\]|ERROR|Traceback|❌/.test(msg)) cls = "l-err";
  else if (/⚠|\[WARN\]/.test(msg)) cls = "l-warn";
  else if (/✅|complete|saved|Uploaded|Saved to Library/i.test(msg)) cls = "l-ok";
  const line = document.createElement("div");
  line.innerHTML = `<span class="t">${new Date().toTimeString().slice(0, 8)}</span><span class="${cls}">${esc(msg)}</span>`;
  c.appendChild(line);
  while (c.children.length > 600) c.firstChild.remove();
  c.scrollTop = c.scrollHeight;
}

async function startRun(topic, surprise, options = {}) {
  const profileId = options.profile_id || (typeof activeChannelProfile === "function" ? activeChannelProfile()?.id : null);
  const format = options.video_type || videoType;
  const mode = options.visual_mode || $("#visual-mode").value;
  const preview = mode !== "classic" && (options.preview_only ?? $("#draft-preview").checked);
  const steps = (options.steps || $$("#steps input:checked").map(i => i.value)).filter(step => mode !== "classic" || step !== "visuals");
  if (!steps.length) return toast("Turn on at least one step under More options", "err");
  if (!surprise && !topic && (steps.includes("research") || steps.includes("script"))) { $("#topic-input").focus(); return toast("Type a topic first, or let AI pick one"); }
  lastRequest = [topic, surprise, steps, mode, preview, profileId, format, options.expected_plan_revision];
  runSteps = ["preparation", ...steps];
  lastRunError = "";
  $("#console").innerHTML = "";
  $("#log-details").open = false;
  $("#run-title").textContent = preview ? "Making your draft…" : "Making your video…";
  $("#run-topic").textContent = surprise ? "AI is choosing a topic…" : topic ? `“${topic}”` : "Using your current storyboard and narration";
  setHero("running");
  renderRun({ stages: {}, running: true });
  const r = await api("/api/run", {
    steps, topic: surprise ? "" : topic, video_type: format,
    visual_mode: mode, preview_only: preview, profile_id: profileId,
    ...(options.expected_plan_revision ? { expected_plan_revision: options.expected_plan_revision } : {}),
    focus_angle: surprise ? $("#focus-angle").value : "",
    use_trends: surprise && $("#quick-trends").checked,
  }).catch(e => ({ ok: false, error: e.message }));
  if (!r.ok) {
    setHero("form");
    if (options.throwOnError) throw new Error(r.error || "Could not start the video render.");
    return toast(r.error, "err");
  }
  status.running = true;
  startSSE(); pollOnce();
  if (options.scroll !== false) window.scrollTo({ top: 0, behavior: "smooth" });
  return true;
}
function retry() {
  if (!lastRequest) return setHero("form");
  if (lastRequest[7]) return renderStoryboard(lastRequest[4], {
    visualMode: lastRequest[3], profileId: lastRequest[5], videoType: lastRequest[6], planRevision: lastRequest[7],
  });
  return startRun(lastRequest[0], lastRequest[1], { steps: lastRequest[2], visual_mode: lastRequest[3], preview_only: lastRequest[4], profile_id: lastRequest[5], video_type: lastRequest[6] });
}
$("#ask-form").onsubmit = e => { e.preventDefault(); startRun($("#topic-input").value.trim(), false); };
$("#surprise-btn").onclick = () => startRun("", true);
$("#stop-btn").onclick = () => api("/api/stop", {}).then(() => { toast("Stopping after the current step…"); $("#run-title").textContent = "Stopping…"; });
$$("#format-seg button").forEach(b => b.onclick = () => {
  videoType = b.dataset.fmt;
  $$("#format-seg button").forEach(x => x.classList.toggle("on", x === b));
});
$$("#steps .chip, #img-sources .chip").forEach(c => {
  const input = $("input", c);
  c.classList.toggle("on", input.checked);
  input.addEventListener("change", () => c.classList.toggle("on", input.checked));
});
async function saveQuickPreference(key, value, element, message = "") {
  const profile = activeChannelProfile(), profileId = profile?.id;
  if (!profileId) return toast("Wait for the channel profile to load.", "err");
  const previous = profile.defaults?.[key];
  try {
    const result = await api("/api/save-settings" + settingsProfileQuery(profileId), { [key]: value });
    if (!result.ok) throw new Error(result.error || "Couldn't save this preference.");
    const current = channelProfiles.find(item => item.id === profileId);
    if (current) current.defaults[key] = value;
    if (activeProfileId === profileId) settings[key] = value;
    if (settingsProfileId === profileId && !settingsDirty()) {
      const field = $("#s-" + key);
      if (field) { if (typeof value === "boolean") field.checked = value; else field.value = value; }
      settingsSnap = JSON.stringify(collectSettings());
    }
    if (message) toast(`${message} for ${profileName(profileId)}`);
  } catch (error) {
    if (activeProfileId === profileId && previous !== undefined) {
      if (typeof previous === "boolean") element.checked = previous; else element.value = previous;
    }
    toast(error.message, "err");
  }
}
$("#quick-voice").onchange = e => saveQuickPreference("voice_id", e.target.value, e.target, "Voice saved");
$("#quick-grounding").onchange = e => saveQuickPreference("fact_grounding", e.target.checked, e.target);

function updateVisualMode() {
  const mode = $("#visual-mode").value, classic = mode === "classic";
  $("#draft-preview").disabled = classic;
  if (classic) $("#draft-preview").checked = false;
  $("#visual-mode-note").textContent = {
    motion: "Animated scenes reuse the pipeline's photos and move with the narration.",
    hybrid: "Your pipeline's photos, animated explanations and licensed video footage.",
    classic: "The original photo, zoom and transition renderer.",
  }[mode];
  const storyboard = $('#steps input[value="visuals"]');
  storyboard.disabled = classic;
  storyboard.closest(".chip").classList.toggle("disabled", classic);
  if (typeof renderChecklist === "function") renderChecklist();
}
$("#visual-mode").onchange = updateVisualMode;
function environmentHTML(result, failuresOnly = false) {
  return (result.checks || []).filter(check => !failuresOnly || !check.ok).map(check => `<div class="environment-check ${check.ok ? "ok" : "failed"}"><span>${check.ok ? "✓" : "!"}</span><div>${esc(check.message)}${check.fix ? `<pre><code>${esc(check.fix)}</code></pre>` : ""}</div></div>`).join("");
}
$("#check-environment").onclick = async () => {
  const button = $("#check-environment"); busy(button, true, "Checking…");
  try {
    const steps = $$("#steps input:checked").map(input => input.value);
    const result = await api("/api/preflight", { steps, visual_mode: $("#visual-mode").value });
    $("#environment-results").innerHTML = environmentHTML(result);
  } catch (e) { $("#environment-results").textContent = e.message; }
  busy(button, false);
};
