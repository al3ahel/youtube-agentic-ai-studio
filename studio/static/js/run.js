/* ═══════════════════════════ making a video ═══════════════════════════ */
const STEPS = [
  ["preparation", "Checking the setup",    "Verifying the dependencies needed for this run"],
  ["research",  "Researching the topic",   "Picking the angle and checking facts"],
  ["script",    "Writing the script",      "Hook, story and a strong ending"],
  ["images",    "Finding visual assets",   "Local assets or licensed footage for this visual style"],
  ["narration", "Recording the voiceover", "Natural AI voice with synced captions"],
  ["music",     "Mixing music and effects", "Applying this channel's saved audio choices beneath the voice"],
  ["visuals",   "Building the storyboard", "Choosing scenes and timing their motion to the voiceover"],
  ["video",     "Rendering the video",     "The slow part, usually 2–10 minutes"],
];
let status = {}, es = null, heroMode = "form", runSteps = [], videoType = "normal", lastRunError = "", lastRequest = null, statusRequestSerial = 0;
let streamScope = null, streamCursor = "", streamEndedScope = null;
let restoredWorkspaceShown = false;
const homeVisualChoices = new Map();
let pendingHomeVisualChoice = null;

function setHero(mode) {
  heroMode = mode;
  $("#hero-form").hidden = mode !== "form";
  $("#hero-run").hidden = mode === "form";
  $("#run-progress").hidden = mode !== "running";
  $("#run-done").hidden = mode !== "done";
  $("#run-error").hidden = mode !== "error";
  $("#stop-btn").hidden = mode !== "running";
}

function showNewVideoForm() {
  restoredWorkspaceShown = true;
  $("#topic-input").value = "";
  resetRunSteps();
  setHero("form");
  $("#topic-input").focus();
}

function resetRunSteps() {
  $$("#steps input").forEach(input => {
    input.checked = true;
    input.closest(".chip").classList.add("on");
  });
  updateVisualMode();
}

function updateRunIntent() {
  const reuse = !$('#steps input[value="research"]').checked;
  $('#ask-form button[type="submit"]').textContent = reuse ? "Redo selected steps →" : "Make video →";
  $("#surprise-btn").disabled = reuse;
  $("#run-intent-note").hidden = !reuse;
  $("#run-intent-note").textContent = reuse
    ? "Research is off. These steps reuse the open project's research and any unchecked outputs. Clear the topic to continue, or enable Research for a new topic."
    : "";
}

function showDraftCompletion(d) {
  const playable = reviewReady(d), mp4 = !!d.has_preview;
  $("#run-title").textContent = mp4 ? (d.workspace_restored ? "Your saved draft is ready" : "Your draft is ready")
    : playable ? "Your scene preview is ready" : "Your storyboard is ready";
  $("#run-topic").textContent = "";
  const heading = playable ? "Review the motion before the final render" : "Render a draft to play the planned scenes";
  const detail = mp4 ? "Watch the draft, change any scene, then render the final video from Review. Nothing has been published."
    : playable ? "Play the scene preview, adjust the storyboard, then render a draft MP4 or the final video."
    : "The scenes have been planned, but there is no playable preview yet. Open Review to render a draft using the saved narration.";
  const kind = mp4 ? "draft" : playable ? "preview" : "storyboard", card = $("#run-done");
  // Preserve focused/clicked controls across status polls of the same result.
  if (card.dataset.completionKind !== kind || !$(".draft-ready", card)) {
    card.dataset.completionKind = kind;
    card.innerHTML = `<div class="draft-ready"><span class="tag">${mp4 ? "Draft preview" : playable ? "Scene preview" : "Storyboard only"}</span><h3>${heading}</h3><p class="hint">${detail}</p><div class="draft-actions"><a class="btn btn-white btn-lg" href="#review">${playable ? "Review scenes" : "Review storyboard"} →</a><button class="btn btn-ghost btn-lg" id="make-another-video" type="button" onclick="showNewVideoForm()">Make another video</button></div></div>`;
  }
  setHero("done");
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
    const label = { done: "done", running: "working…", error: "failed", skipped: "skipped" }[s] || "";
    return `<div class="step" data-s="${s}"><span class="ic">${ic}</span><div><b>${title}</b><small>${sub}</small></div><em>${label}</em></div>`;
  }).join("");
  total = total || STEPS.length;
  $("#prog-fill").style.width = Math.min(100, (doneCount + (current ? .5 : 0)) / total * 100) + "%";
  $("#prog-step").textContent = current ? `Step ${Math.min(total, doneCount + 1)} of ${total}: ${current.toLowerCase()}` : d.running ? "Getting ready…" : "";
  $("#prog-time").textContent = d.started_at ? clock(Date.now() / 1000 - d.started_at) + " so far" : "";
}

function applyStatus(d) {
  const was = status.running;
  const previouslyAvailable = hasReviewContent(status);
  const completedSchedule = was && !d.running && status.schedule_id ? status : null;
  status = d;
  const state = d.running ? "running" : d.error ? "error" : hasReviewContent(d) ? "done" : "idle";
  $("#status-pill").className = "status-pill " + state;
  $("#status-text").textContent = d.uploading ? "Uploading…" : d.running ? (d.preview_only ? "Making a draft…" : "Making video…") : d.visual_editing ? "Updating project…" : d.error ? "Last run failed" : !d.has_video && reviewReady(d) ? "Draft ready" : d.has_storyboard && !d.has_video ? "Storyboard ready" : "Ready";
  $("#tabs a[data-view=review]").textContent = !d.has_video && reviewReady(d) ? "Review draft" : d.has_storyboard && !d.has_video ? "Review storyboard" : "Review & publish";
  $("#workspace-warning").textContent = typeof d.workspace_warning === "string" ? d.workspace_warning : "";
  $("#workspace-warning").hidden = !$("#workspace-warning").textContent;
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
    const scope = eventScopeKey(d);
    if (streamEndedScope !== scope && (!es || streamScope !== scope)) startSSE(d);
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
  else if (!d.running && !d.visual_editing) {
    if (d.workspace_restored && reviewReady(d) && !restoredWorkspaceShown && heroMode === "form") {
      restoredWorkspaceShown = true;
      onFinished(d);
    } else if (heroMode === "done") {
      if (!hasReviewContent(d)) {
        setHero("form"); $("#run-done").replaceChildren(); $("#run-title").textContent = "";
      } else if (!d.has_video) showDraftCompletion(d);
    }
    if (previouslyAvailable && !hasReviewContent(d) && location.hash === "#review") loadReview();
  }
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
  if (d.workspace_restored) restoredWorkspaceShown = true;
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
      <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap"><button class="btn btn-white" onclick="retry()">Try again</button><button class="btn btn-ghost" onclick="showNewVideoForm()">Change topic</button><a class="btn btn-ghost" href="#settings">Settings</a></div>`;
    setHero("error");
    $("#log-details").open = true;
    return;
  }
  if (!d.has_video && hasReviewContent(d)) {
    showDraftCompletion(d);
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
  $("#run-title").textContent = d.workspace_restored ? "Your saved video is ready" : "Your video is ready";
  $("#run-topic").textContent = "";
  $("#run-done").innerHTML = `<div class="done-box">
      <div class="thumb-frame">${d.has_thumb ? `<img src="/thumbnail/${Date.now()}" alt="">` : ""}</div>
      <div><div style="font-size:20px;line-height:1.25">${esc(out.title || "")}</div>
        <p class="hint" style="margin:8px 0 16px">${out.sources && out.sources.length ? `${out.sources.length} retrieved sources are attached for review. ` : ""}Next: watch it, polish the title and thumbnail, then publish.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn btn-white btn-lg" href="#review">Review &amp; publish →</a><button class="btn btn-ghost btn-lg" id="make-another-video" type="button" onclick="showNewVideoForm()">Make another video</button></div></div>
    </div>`;
  setHero("done");
  $("#tabs a[data-view=review]").classList.add("notify");
  toast("Your video is ready", "ok");
  if (!reviewed && location.hash === "#review") loadReview();
}

function eventScope(context = status) {
  return {run_id: context.run_id || null,
    profile_id: context.profile_id || (typeof activeChannelProfile === "function" ? activeChannelProfile()?.id : null) || null};
}
function eventScopeKey(context = status) { return JSON.stringify(eventScope(context)); }
function startSSE(context = status) {
  if (es) es.close();
  const scope = eventScope(context), key = JSON.stringify(scope);
  if (streamScope !== key) { streamScope = key; streamCursor = ""; streamEndedScope = null; }
  const query = new URLSearchParams();
  if (scope.run_id) query.set("run_id", scope.run_id);
  if (scope.profile_id) query.set("profile_id", scope.profile_id);
  if (streamCursor) query.set("after", streamCursor);
  const source = new EventSource(`/api/stream${query.size ? `?${query}` : ""}`);
  es = source;
  source.onmessage = ev => {
    if (es !== source) return;
    const d = JSON.parse(ev.data);
    if (scope.run_id && d.run_id && d.run_id !== scope.run_id) return;
    if (scope.profile_id && d.profile_id && d.profile_id !== scope.profile_id) return;
    const id = ev.lastEventId || d.id;
    if (id) {
      const [epoch, sequence] = id.split(":"), [oldEpoch, oldSequence] = streamCursor.split(":");
      if (epoch === oldEpoch && Number(sequence) <= Number(oldSequence)) return;
      streamCursor = id;
    }
    if (d.kind === "log") log(d.msg, d.timestamp);
    else if (d.kind === "gap") log(`[WARN] ${d.msg}`, d.timestamp);
    else if (d.kind === "stage") pollOnce();
    else if (d.kind === "research") showTopic();
    else if (["done", "error", "stopped"].includes(d.kind)) {
      if (d.kind === "error") lastRunError = d.msg;
      streamEndedScope = key;
      source.close(); es = null; pollOnce();
    }
  };
  // EventSource reconnects with Last-Event-ID after a transient disconnect.
  // Keep that native cursor instead of closing and losing the missing lines.
  source.onerror = () => {
    if (es === source && source.readyState === EventSource.CLOSED) es = null;
  };
}
async function showTopic() {
  const r = await api("/api/research").catch(() => null);
  if (r && r.title) $("#run-topic").innerHTML = `“${esc(r.title)}”${r.sources.length ? ` <span class="tag green" style="margin-left:6px">${r.sources.length} sources found</span>` : ""}`;
}
function log(msg, timestamp) {
  const c = $("#console");
  if (c.firstElementChild && c.firstElementChild.classList.contains("l-dim")) c.innerHTML = "";
  let cls = "";
  if (/\[ERROR\]|ERROR|Traceback|❌/.test(msg)) cls = "l-err";
  else if (/⚠|\[WARN\]/.test(msg)) cls = "l-warn";
  else if (/✅|complete|saved|Uploaded|Saved to Library/i.test(msg)) cls = "l-ok";
  const line = document.createElement("div");
  let when = timestamp ? new Date(timestamp) : new Date();
  if (Number.isNaN(when.getTime())) when = new Date();
  line.innerHTML = `<span class="t">${when.toTimeString().slice(0, 8)}</span><span class="${cls}">${esc(msg)}</span>`;
  c.appendChild(line);
  while (c.children.length > 600) c.firstChild.remove();
  c.scrollTop = c.scrollHeight;
}

async function startRun(topic, surprise, options = {}) {
  const profileId = options.profile_id || (typeof activeChannelProfile === "function" ? activeChannelProfile()?.id : null);
  const format = options.video_type || videoType;
  const mode = options.visual_mode || $("#visual-mode").value;
  const useProjectPhotos = options.use_project_photos ?? (mode !== "motion");
  const preview = mode !== "classic" && (options.preview_only ?? $("#draft-preview").checked);
  const steps = (options.steps || $$("#steps input:checked").map(i => i.value)).filter(step =>
    (mode !== "classic" || step !== "visuals") && (mode !== "motion" || useProjectPhotos || step !== "images"));
  if (!steps.length) return toast("Turn on at least one step under More options", "err");
  if ((surprise || topic) && !steps.includes("research")) {
    $("#topic-input").focus();
    return toast("Enable Research to generate a new topic, or clear the topic to redo the current project.", "err");
  }
  if (!surprise && !topic && steps.includes("research")) { $("#topic-input").focus(); return toast("Type a topic first, or let AI pick one"); }
  lastRequest = [topic, surprise, steps, mode, preview, profileId, format, options.expected_plan_revision, useProjectPhotos];
  runSteps = ["preparation", ...steps];
  lastRunError = "";
  if (es) { es.close(); es = null; }
  streamScope = null; streamCursor = ""; streamEndedScope = null;
  $("#console").innerHTML = "";
  $("#log-details").open = false;
  $("#run-title").textContent = preview ? "Making your draft…" : "Making your video…";
  $("#run-topic").textContent = surprise ? "AI is choosing a topic…" : topic ? `“${topic}”` : "Using your current storyboard and narration";
  setHero("running");
  renderRun({ stages: {}, running: true });
  const r = await api("/api/run", {
    steps, topic: surprise ? "" : topic, video_type: format,
    visual_mode: mode, use_project_photos: useProjectPhotos, preview_only: preview, profile_id: profileId,
    ...(options.expected_plan_revision ? { expected_plan_revision: options.expected_plan_revision } : {}),
    focus_angle: surprise ? $("#focus-angle").value : "",
    use_trends: surprise && $("#quick-trends").checked,
  }).catch(e => ({ ok: false, error: e.message }));
  if (!r.ok) {
    setHero("form");
    if (options.throwOnError) throw new Error(r.error || "Could not start the video render.");
    return toast(r.error, "err");
  }
  status = {...status, running: true, run_id: r.run_id || null, profile_id: r.profile_id || profileId || null};
  startSSE(status); pollOnce();
  if (options.scroll !== false) window.scrollTo({ top: 0, behavior: "smooth" });
  return true;
}
function retry() {
  if (!lastRequest) return setHero("form");
  if (lastRequest[7] && lastRequest[2].includes("video") && lastRequest[2].every(step => ["music", "video"].includes(step))) return renderStoryboard(lastRequest[4], {
    steps: lastRequest[2], visualMode: lastRequest[3], profileId: lastRequest[5], videoType: lastRequest[6], planRevision: lastRequest[7], useProjectPhotos: lastRequest[8],
  });
  return startRun(lastRequest[0], lastRequest[1], { steps: lastRequest[2], visual_mode: lastRequest[3], preview_only: lastRequest[4], profile_id: lastRequest[5], video_type: lastRequest[6], expected_plan_revision: lastRequest[7], use_project_photos: lastRequest[8] });
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
  input.addEventListener("change", () => { c.classList.toggle("on", input.checked); if (c.closest("#steps")) updateRunIntent(); });
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
    motion: "Animated subjects, actions and camera moves follow the narration. Motion uses generated graphics.",
    hybrid: "Animated scenes with project photos and subject-matched Pixabay or Pexels video. The agent chooses footage to fit the scene and film's colors.",
    classic: "The original photo, zoom and transition renderer.",
  }[mode];
  const storyboard = $('#steps input[value="visuals"]');
  storyboard.disabled = classic;
  storyboard.closest(".chip").classList.toggle("disabled", classic);
  const images = $('#steps input[value="images"]');
  images.disabled = mode === "motion";
  images.closest(".chip").classList.toggle("disabled", images.disabled);
  images.closest(".chip").title = images.disabled ? "Motion uses animated graphics. Choose Hybrid or Classic to include photos." : "";
  updateRunIntent();
  if (typeof renderChecklist === "function") renderChecklist();
}
function applyHomeVisualDefault(mode, profileId) {
  if (pendingHomeVisualChoice) {
    homeVisualChoices.set(profileId, pendingHomeVisualChoice);
    pendingHomeVisualChoice = null;
  }
  $("#visual-mode").value = homeVisualChoices.get(profileId) || mode || "motion";
  updateVisualMode();
}
$("#visual-mode").onchange = () => {
  const profileId = typeof activeProfileId !== "undefined" ? activeProfileId : null;
  if (profileId) homeVisualChoices.set(profileId, $("#visual-mode").value);
  else pendingHomeVisualChoice = $("#visual-mode").value;
  updateVisualMode();
};
function environmentHTML(result, failuresOnly = false) {
  return (result.checks || []).filter(check => !failuresOnly || !check.ok).map(check => `<div class="environment-check ${check.ok ? "ok" : "failed"}"><span>${check.ok ? "✓" : "!"}</span><div>${esc(check.message)}${check.fix ? `<pre><code>${esc(check.fix)}</code></pre>` : ""}</div></div>`).join("");
}
$("#check-environment").onclick = async () => {
  const button = $("#check-environment"); busy(button, true, "Checking…");
  try {
    const steps = $$("#steps input:checked:not(:disabled)").map(input => input.value);
    const result = await api("/api/preflight", { steps, visual_mode: $("#visual-mode").value });
    $("#environment-results").innerHTML = environmentHTML(result);
  } catch (e) { $("#environment-results").textContent = e.message; }
  busy(button, false);
};
