/* ═══════════════════════════ review ═══════════════════════════ */
let tags = [], saved = null, thumbMode = "stock", uploadMode = "now", reviewProfileId = null, reviewVideoType = "normal";
let reviewLoadSerial = 0, reviewMediaSerial = 0;

function invalidateReviewLoads() {
  reviewLoadSerial++;
  if (typeof invalidateStoryboardLoads === "function") invalidateStoryboardLoads();
  setReviewFeedback("");
}

function setReviewFeedback(kind, message = "") {
  const panel = $("#review-feedback");
  if (!panel) { if (kind === "error") toast(message, "err"); return; }
  panel.hidden = !kind;
  panel.classList.toggle("error", kind === "error");
  panel.setAttribute("role", kind === "error" ? "alert" : "status");
  $("#view-review").setAttribute("aria-busy", String(kind === "loading"));
  $("#review-feedback-title").textContent = kind === "error" ? "Could not open Review" : kind === "loading" ? "Loading your project…" : "Saved project notice";
  $("#review-feedback-message").textContent = message;
  $("#review-retry").hidden = kind !== "error";
  $("#review-error-create").hidden = kind !== "error";
}

function updateReviewMedia(st) {
  const final = !!st.has_video, hasPlayable = final || st.has_preview;
  $("#review-heading").textContent = final ? "Review & publish" : reviewReady(st) ? "Review your draft" : "Review your storyboard";
  $("#review-intro").textContent = final ? "Watch the final video, polish its details, then publish when you are ready." : reviewReady(st)
    ? "Review the storyboard and adjust individual scenes. Each change rebuilds the draft video. Render a final video when the explanation looks right."
    : "The scenes have been planned. Render a draft to play the motion with the saved narration, then review the result here.";
  $("#rv-publish").hidden = !final;
  $("#publish-nav").hidden = !final;
  $("#rv-watch").hidden = !hasPlayable;
  $('.guide-steps a[href="#rv-watch"]').hidden = !hasPlayable;
  $("#watch-label").textContent = final ? "Watch the final video" : "Watch the draft MP4";
  $("#player").classList.toggle("shorts", st.video_type === "shorts");
  const path = final ? "/video" : "/preview-video";
  const revision = (final ? st.video_revision : st.preview_revision) || `${Date.now()}-${++reviewMediaSerial}`;
  const source = `${path}?revision=${encodeURIComponent(revision)}`;
  if (!hasPlayable) $("#player").replaceChildren();
  else if ($("#player video")?.getAttribute("src") !== source) {
    const video = document.createElement("video");
    video.controls = true; video.preload = "metadata"; video.src = source;
    $("#player").replaceChildren(video);
  }
  $("#dl-video").href = `${source}&download=1`;
  $("#dl-video").textContent = final ? "Final video (MP4)" : "Draft preview (MP4)";
  $("#dl-video").hidden = !hasPlayable;
  $("#review-src").textContent = st.library_id ? "from My videos" : final ? "Final render" : "Draft — not ready to publish";
}

async function loadReview({ preserveEdits = false, expectedPlanRevision = null, expectedProfileId = null, throwOnError = false } = {}) {
  const serial = ++reviewLoadSerial;
  preserveEdits = preserveEdits || (typeof editingScene !== "undefined" && editingScene) || (typeof reviewRender !== "undefined" && !!reviewRender);
  setReviewFeedback("loading", "Checking the saved video, storyboard and details.");
  $("#review-empty").hidden = true;
  if (!preserveEdits) $$(".review-part").forEach(el => el.hidden = true);
  try {
    const [st, d] = await Promise.all([api("/api/status"), api("/api/output")]);
    if (serial !== reviewLoadSerial) return false;
    if ((expectedPlanRevision && st.plan_revision !== expectedPlanRevision)
        || (expectedProfileId && st.profile_id !== expectedProfileId)) {
      throw new Error("The open storyboard changed. Reopen Review before rendering it again.");
    }
    if (!d || typeof d !== "object" || Array.isArray(d)) throw new Error("The studio did not return the project's details. Try again.");
    if (metaDirty() && reviewProfileId === st.profile_id && st.plan_revision
        && typeof storyboardData !== "undefined" && storyboardData?.plan_revision === st.plan_revision) preserveEdits = true;
    const has = hasReviewContent(st), warning = typeof st.workspace_warning === "string" ? st.workspace_warning : "";
    if (typeof updateChannelControls === "function") updateChannelControls(st);
    $("#review-empty").hidden = has;
    $$(".review-part").forEach(el => el.hidden = !has);
    if (!has) {
      $("#review-empty-note").textContent = warning || "Make a draft on Home, review its scenes here, then render the final video.";
      $("#player").replaceChildren(); $("#motion-preview").removeAttribute("src");
      $("#savebar").classList.remove("show");
      setReviewFeedback("");
      return false;
    }
    reviewProfileId = st.profile_id || null;
    reviewVideoType = st.video_type || "normal";
    $("#legacy-motion-card").hidden = st.visual_mode !== "classic" || !st.has_audio || !st.has_script;
    updateReviewMedia(st);
    $("#rv-audio").hidden = !st.has_audio || !st.has_script;
    if (typeof renderReviewAudio === "function") renderReviewAudio(d, st);
    renderEditorialChecks(d.script_quality);
    $("#description-warning").hidden = !d.description_warning;
    $("#description-warning").textContent = typeof d.description_warning === "string" ? d.description_warning : "";
    $("#thumb-wrap").innerHTML = st.has_thumb ? `<img src="/thumbnail/${Date.now()}" alt="Thumbnail">` : `<div class="empty">No thumbnail yet</div>`;
    if (st.has_thumb) loadThumb();
    $("#rv-thumb").hidden = !st.has_thumb;
    $('.guide-steps a[href="#rv-thumb"]').hidden = !st.has_thumb;
    $("#dl-thumb").hidden = !st.has_thumb;
    $("#dl-srt").hidden = !st.has_captions;
    const keepMetadata = preserveEdits && saved;
    saved = { title: typeof d.title === "string" ? d.title : "", desc: typeof d.description === "string" ? d.description : "", tags: Array.isArray(d.tags) ? d.tags.filter(tag => typeof tag === "string") : [] };
    if (keepMetadata) edited(true);
    else fillMeta(saved);
    const sections = Array.isArray(d.sections) ? d.sections.filter(section => section && typeof section === "object") : [];
    $("#sections").innerHTML = sections.map((s, i) => `
      <details class="section"><summary><span class="n">${String(i + 1).padStart(2, "0")}</span><span>${esc(s.title || s.section_type || "Section")}</span><span class="tag">${esc(s.section_type || "")}</span></summary><p>${esc(s.narration || "")}</p></details>`).join("") || `<div class="hint">No script</div>`;
    const credits = Array.isArray(d.credits) ? d.credits.filter(credit => credit && typeof credit.source === "string") : [];
    const bySrc = credits.reduce((a, c) => (a[c.source] = (a[c.source] || 0) + 1, a), Object.create(null));
    $("#review-sources").innerHTML = sourcesHTML(d.sources) +
      (credits.length ? `<div class="label" style="margin:16px 0 8px">Where the images came from</div><div class="chips">${Object.entries(bySrc).map(([k, v]) => `<span class="chip" style="cursor:default">${esc(k)} <span class="count">${v}</span></span>`).join("")}</div>` : "");
    $("#upload-status").innerHTML = st.yt_url ? `Already on YouTube: <a class="link" href="${esc(st.yt_url)}" target="_blank" rel="noopener">${esc(st.yt_url)}</a>` : "";
    if (!preserveEdits) {
      $("#ideas").innerHTML = "";
      if (typeof setReviewRenderResult === "function") setReviewRenderResult("");
    }
    loadKeywords();
    if (st.has_video) checkYt();
    if (typeof loadStoryboard !== "function") throw new Error("The scene editor did not load. Reload this page, then try again.");
    const loaded = await loadStoryboard(st, { preserveEditor: preserveEdits, expectedPlanRevision, isCurrent: () => serial === reviewLoadSerial });
    if (serial !== reviewLoadSerial) return false;
    if (loaded === false) throw new Error("The storyboard could not be loaded. Try again.");
    updateRebuildControls(st);
    setReviewFeedback(warning ? "notice" : "", warning);
    return true;
  } catch (error) {
    if (serial !== reviewLoadSerial) return false;
    $("#review-empty").hidden = true;
    setReviewFeedback("error", error.message || "The project could not be loaded. Try again.");
    if (throwOnError) throw error;
    return false;
  }
}
$("#review-retry")?.addEventListener("click", () => loadReview());
function renderEditorialChecks(report) {
  const box = $("#editorial-checks"), content = $("#editorial-checks-content");
  box.hidden = !report || typeof report !== "object" || Array.isArray(report);
  if (box.hidden) { content.replaceChildren(); return; }
  const checked = ["reviewed", "local_checks", "original_kept", "suggestions"].includes(report.status);
  const score = checked && Number.isFinite(report.score) ? `Writing check: ${Math.max(0, Math.min(100, report.score))}/100. ` : "";
  const status = report.status === "disabled" ? "Editorial checks were turned off." : !checked ? "Editorial checks were unavailable for this script." : report.rewritten ? "The script was revised before recording." : report.status === "suggestions" ? "Editorial suggestions are available; the original factual narration was kept." : report.status === "local_checks" ? "Local writing checks were applied before recording." : "The saved script was checked before recording.";
  const issues = Array.isArray(report.issues) ? report.issues.filter(issue => typeof issue === "string").slice(0, 10) : [];
  content.innerHTML = `<p>${esc(score + status)}</p><p class="hint">This is a writing check, not a factual accuracy or audience prediction score.</p>${issues.length ? `<ul>${issues.map(issue => `<li>${esc(issue)}</li>`).join("")}</ul>` : ""}`;
}
function sourcesHTML(sources) {
  sources = Array.isArray(sources) ? sources.filter(source => source && typeof source === "object") : [];
  if (!sources.length) return `<div class="hint">No retrieved sources are attached to this video.</div>`;
  return `<div class="label" style="margin-bottom:8px">${sources.length} retrieved sources <small>review these references; also listed in the description</small></div><div class="sources">${sources.map(s =>
    `<div class="source"><span class="tag ${s.source === "arXiv" ? "green" : ""}">${esc(s.source)}</span><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>`).join("")}</div>`;
}
function fillMeta(m) {
  $("#edit-title").value = m.title; $("#edit-desc").value = m.desc; tags = [...m.tags];
  renderTags(); edited(true);
}
const currentMeta = () => ({ title: $("#edit-title").value, desc: $("#edit-desc").value, tags });
const metaDirty = () => saved && JSON.stringify(currentMeta()) !== JSON.stringify(saved);
let scoreTimer;
function edited(quiet) {
  const t = $("#edit-title").value.length, dl = $("#edit-desc").value.length, tl = tags.join(",").length;
  $("#title-count").textContent = `${t}/70`; $("#title-count").classList.toggle("over", t > 70);
  $("#desc-count").textContent = `${dl}/5000`; $("#desc-count").classList.toggle("over", dl > 5000);
  $("#tag-count").textContent = `${tl}/500`; $("#tag-count").classList.toggle("over", tl > 500);
  if (location.hash === "#review") $("#savebar").classList.toggle("show", !!metaDirty());
  clearTimeout(scoreTimer); scoreTimer = setTimeout(scoreSEO, quiet ? 0 : 400);
}
function renderTags() {
  $("#tag-list").innerHTML = tags.map((t, i) => `<span class="chip" data-i="${i}" title="Remove">${esc(t)} <span class="x">×</span></span>`).join("") || `<span class="hint">No tags yet. Add some below.</span>`;
  $$("#tag-list .chip").forEach(c => c.onclick = () => { tags.splice(+c.dataset.i, 1); renderTags(); edited(); });
}
function addTag(v) {
  v = v.trim(); if (!v || tags.some(t => t.toLowerCase() === v.toLowerCase())) return;
  tags.push(v); renderTags(); edited();
}
$("#edit-title").oninput = () => edited(); $("#edit-desc").oninput = () => edited();
$("#new-tag").onkeydown = e => { if (e.key === "Enter") { addTag(e.target.value); e.target.value = ""; } };

async function scoreSEO() {
  if (!saved) return;
  const r = await api("/api/seo", { title: $("#edit-title").value, description: $("#edit-desc").value, tags }).catch(() => null);
  if (!r) return;
  const col = r.score >= 85 ? "var(--green)" : r.score >= 70 ? "var(--blue)" : r.score >= 50 ? "var(--amber)" : "var(--pink)";
  $("#seo-ring").style.strokeDashoffset = 263.9 * (1 - r.score / 100);
  $("#seo-ring").style.stroke = col;
  $("#seo-num").textContent = r.score;
  $("#seo-verdict").textContent = r.score >= 85 ? "Great, ready to publish" : r.score >= 70 ? "Good, a few quick wins left" : r.score >= 50 ? "OK, try the tips below" : "Needs work before publishing";
  const todo = r.checks.filter(c => !c.ok);
  $("#seo-sub").textContent = todo.length ? `${todo.length} tip${todo.length > 1 ? "s" : ""} to improve it` : "Every check passes";
  $("#seo-checks").innerHTML = todo.map(c => `<div class="check"><span class="mk">→</span><span>${esc(c.tip)}</span></div>`).join("") +
    (r.checks.length - todo.length ? `<div class="check ok"><span class="mk">✓</span><span class="dim">${r.checks.length - todo.length} checks passed</span></div>` : "");
}
async function loadKeywords() {
  $("#kw-chips").innerHTML = `<span class="hint">Asking YouTube what people search…</span>`;
  try {
    const k = await api("/api/keywords");
    const all = [...k.suggestions.slice(0, 12), ...k.related.slice(0, 6)].filter(w => !tags.some(t => t.toLowerCase() === w.toLowerCase()));
    $("#kw-chips").innerHTML = all.map(w => `<span class="chip" data-w="${esc(w)}">${esc(w)} <span class="x">+</span></span>`).join("") || `<span class="hint">No suggestions for this topic.</span>`;
    $$("#kw-chips .chip").forEach(c => c.onclick = () => { addTag(c.dataset.w); c.remove(); });
  } catch (e) { $("#kw-chips").innerHTML = `<span class="hint">Couldn't reach YouTube search suggestions.</span>`; }
}
$("#title-lab-btn").onclick = async () => {
  const b = $("#title-lab-btn"); busy(b, true, "Writing titles… (~15 s)");
  try {
    const r = await api("/api/title-ideas", {});
    if (!r.ok) throw new Error(r.error);
    $("#ideas").innerHTML = `<div class="hint" style="margin-bottom:2px">Click one to use it. The number is its findability score.</div>` + r.titles.map(t => `<button class="idea" data-t="${esc(t.title)}"><span>${esc(t.title)}<small>${esc(t.style)}</small></span><span class="tag ${t.score >= 80 ? "green" : t.score >= 60 ? "" : "amber"}">${t.score}</span></button>`).join("");
    $$("#ideas .idea").forEach(b => b.onclick = () => { $("#edit-title").value = b.dataset.t; edited(); toast("Title changed. Press Save to keep it"); });
  } catch (e) { toast("Couldn't write titles: " + e.message, "err"); }
  busy(b, false);
};
async function saveMeta() {
  const c = currentMeta();
  const titleChanged = c.title !== saved?.title;
  await api("/api/save-output", { title: c.title, description: c.desc, tags: c.tags });
  saved = { ...c, tags: [...c.tags] };
  edited(true); toast("Saved", "ok");
  // a thumbnail using the title as its text should follow a title change
  if (titleChanged && thumb && !thumb.custom_text) await thumbPost({previews: true});
}
window.addEventListener("beforeunload", e => { if (metaDirty() || settingsDirty()) { e.preventDefault(); e.returnValue = ""; } });
$("#regen-desc").onclick = async () => {
  const b = $("#regen-desc"); busy(b, true, "Writing…");
  const r = await api("/api/regenerate-description", {}).catch(e => ({ ok: false, error: e.message }));
  if (r.ok) { $("#edit-desc").value = r.description; edited(); toast("New description. Press Save to keep it"); } else toast(r.error, "err");
  busy(b, false);
};
/* ── thumbnail designer ── */
let thumb = null, thumbTimer, thumbBusy = 0;
async function loadThumb() {
  thumb = await api("/api/thumbnail").catch(() => null);
  if (!thumb) return;
  $("#thumb-text").value = thumb.text;
  $("#thumb-wrap").classList.toggle("tall", !!thumb.portrait);
  $("#thumb-styles").innerHTML = "";        // rebuild tiles in the right shape
  $("#thumb-ideas").innerHTML = "";
  renderStyles(0); bgNote();
}
$("#thumb-preview-btn").onclick = async () => {
  if (status.running || status.visual_editing || status.uploading || thumbBusy) return toast("Wait for the current job before generating thumbnail previews.");
  const button = $("#thumb-preview-btn"); busy(button, true, "Generating previews…");
  try { await thumbPost({previews: true}); }
  finally { busy(button, false); await pollOnce(); }
};
// swap an image in only once the new one has loaded, so nothing flashes blank
function swapImg(el, src) {
  const im = new Image();
  im.alt = "";
  im.onload = () => el.replaceChildren(im);
  im.src = src;
}
function renderStyles(ts) {
  $("#thumb-styles").classList.toggle("tall", !!thumb.portrait);
  if ($$("#thumb-styles .style-tile").length !== thumb.styles.length) {
    $("#thumb-styles").innerHTML = thumb.styles.map(s => `
      <button class="style-tile" data-s="${s.id}"><span class="pv"></span>
        <b>${esc(s.name)}</b><small>${esc(s.desc)}</small></button>`).join("");
    $$("#thumb-styles .style-tile").forEach(b => b.onclick = () => {
      $$("#thumb-styles .style-tile").forEach(x => x.classList.toggle("on", x === b));
      thumbPost({ style: b.dataset.s });
    });
  }
  $$("#thumb-styles .style-tile").forEach(b => {
    b.classList.toggle("on", b.dataset.s === thumb.style);
    if (ts) swapImg($(".pv", b), `/thumb-preview/${b.dataset.s}?t=${ts}`);
  });
}
function bgNote() {
  const ai = thumb.background === "ai";
  $("#bg-note").textContent = ai ? "AI artwork" : thumb.photos ? `photo ${thumb.photo_index} of ${thumb.photos} from your video` : "";
  $("#bg-prev").hidden = ai || thumb.photos < 2;
  $("#bg-next").hidden = !ai && thumb.photos < 2;
  $("#bg-next").textContent = ai ? "← Back to photos" : "Next photo →";
}
async function thumbPost(body) {
  thumbBusy++; $("#thumb-wrap").classList.add("busy");
  const r = await api("/api/thumbnail", body).catch(e => ({ ok: false, error: e.message }));
  if (--thumbBusy === 0) $("#thumb-wrap").classList.remove("busy");
  if (!r.ok) { toast(r.error, "err"); return; }
  thumb = r;
  swapImg($("#thumb-wrap"), `/thumbnail/${r.ts}`);
  renderStyles(body.previews ? r.ts : 0);
  bgNote();
}
$("#thumb-text").oninput = () => { clearTimeout(thumbTimer); thumbTimer = setTimeout(() => thumbPost({ text: $("#thumb-text").value, previews: true }), 700); };
$("#bg-next").onclick = () => thumbPost({ bg: "next", previews: true });
$("#bg-prev").onclick = () => thumbPost({ bg: "prev", previews: true });
$("#thumb-ideas-btn").onclick = async () => {
  const b = $("#thumb-ideas-btn"); busy(b, true, "Thinking…");
  const r = await api("/api/thumbnail-ideas", {}).catch(e => ({ ok: false, error: e.message }));
  busy(b, false);
  if (!r.ok) return toast("Couldn't get ideas: " + r.error, "err");
  $("#thumb-ideas").innerHTML = r.texts.map(t => `<span class="chip" data-t="${esc(t)}">${esc(t).replace(/\*([^*]+)\*/g, '<b style="color:var(--amber);font-weight:500">$1</b>')}</span>`).join("");
  $$("#thumb-ideas .chip").forEach(c => c.onclick = () => {
    $("#thumb-text").value = c.dataset.t;
    $$("#thumb-ideas .chip").forEach(x => x.classList.toggle("on", x === c));
    thumbPost({ text: c.dataset.t, previews: true });
  });
};
$$("#upload-seg button").forEach(b => b.onclick = () => {
  uploadMode = b.dataset.um;
  $$("#upload-seg button").forEach(x => x.classList.toggle("on", x === b));
  $("#upane-now").hidden = uploadMode !== "now";
  $("#upane-schedule").hidden = uploadMode === "now";
  $("#upload-btn").textContent = uploadMode === "now" ? "Upload to YouTube" : "Schedule on YouTube";
});
async function checkYt() {
  const profileId = reviewProfileId || status.profile_id;
  const d = await api("/api/yt-status" + (profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "")).catch(() => null);
  if (!d || profileId !== (reviewProfileId || status.profile_id)) return;
  const ready = !!(d.secret && d.connected && d.channel);
  $("#upload-btn").disabled = !ready || !!status.uploading || !!status.running;
  $("#yt-setup").innerHTML = ready
    ? `<span class="channel-verified">● Publishing to ${esc(d.channel.title || d.profile_name || "your verified channel")}</span><br><span class="hint">This video stays linked to its own channel profile.</span>`
    : `<span class="tag amber">${d.secret ? "connect channel" : "one-time setup"}</span> ${d.secret ? "Connect and verify this video's YouTube channel before publishing." : `Add your Google OAuth client JSON file to connect YouTube. <a href="${SETUP_URL}" target="_blank" rel="noopener">Setup guide</a>.`} <button type="button" class="link" id="review-profile-home">Open ${esc(d.profile_name || (typeof profileName === "function" ? profileName(profileId) : "channel"))} on Home</button>`;
  if ($("#review-profile-home")) $("#review-profile-home").onclick = async () => {
    if (profileId && profileId !== activeProfileId) await activateChannel(profileId);
    else location.hash = "#home";
  };
}
$("#upload-btn").onclick = async () => {
  try { if (metaDirty()) await saveMeta(); }
  catch (error) { return toast(error.message, "err"); }
  let publish_at = "";
  if (uploadMode === "schedule") {
    const v = $("#schedule-dt").value;
    if (!v) return toast("Pick a date and time", "err");
    const dt = new Date(v);
    if (dt < new Date(Date.now() + 15 * 60000)) return toast("Pick a time at least 15 minutes from now", "err");
    publish_at = dt.toISOString();
  }
  const r = await api("/api/upload", { privacy: $("#upload-privacy").value, publish_at, profile_id: reviewProfileId || status.profile_id }).catch(error => ({ ok: false, error: error.message }));
  if (!r.ok) return toast(r.error, "err");
  $("#upload-progress").hidden = false;
  $("#upload-btn").disabled = true;
  $("#upload-status").textContent = "Uploading… keep this window open.";
  const tick = async () => {
    const s = await api("/api/status");
    $("#upload-fill").style.width = s.upload_pct + "%";
    if (s.uploading) { $("#upload-status").textContent = `Uploading… ${s.upload_pct}%. Keep this window open.`; return setTimeout(tick, 1200); }
    $("#upload-btn").disabled = false;
    if (s.yt_url) {
      $("#upload-status").innerHTML = `${publish_at ? "Scheduled! It goes public at the time you picked." : "It's live!"} <a class="link" href="${esc(s.yt_url)}" target="_blank" rel="noopener">${esc(s.yt_url)}</a>` +
        (s.upload_warning ? `<br><span class="hint">${esc(s.upload_warning)}</span>` : "");
      toast(publish_at ? "Scheduled on YouTube" : "Published on YouTube", "ok"); loadLibrary(); checkYt();
    } else { $("#upload-status").textContent = "Upload failed: " + (s.error || "unknown error"); toast("Upload failed", "err"); }
  };
  tick();
};

function updateRebuildControls(st) {
  const photos = st.visual_mode === "classic" || storyboardUsesProjectPhotos() || Number(st.photo_count) > 0;
  const oldGraphics = ["motion", "hybrid"].includes(st.visual_mode) && storyboardData?.plan?.scenes?.some(scene => !scene.image && !["footage", "chart"].includes(scene.template) && scene.graphics_version !== 2);
  $("#legacy-motion-card").hidden = !st.has_audio || !st.has_script || !(photos || oldGraphics);
  $("#animate-legacy").hidden = !photos;
  $("#rebuild-motion-and-sound").hidden = !oldGraphics;
  $("#rebuild-visuals-title").textContent = oldGraphics ? "Update this draft's animation" : "Rebuild this project's visuals";
  $("#rebuild-visuals-note").textContent = oldGraphics
    ? "This draft uses the earlier scene design. Rebuild its animation with subjects, actions and camera framing while keeping the script and recorded voice. Animation + sound also reapplies this channel's saved sound direction. Existing scene designs are replaced."
    : "Keep the saved script and recorded voice. Choose animated graphics or animate the existing pictures. YAAS reuses available captions for timing and labels any estimated timing.";
}

async function rebuildReviewVisuals(useProjectPhotos, includeSound = false) {
  try { if (metaDirty()) await saveMeta(); }
  catch (error) { return toast(error.message || "Save your description changes before rebuilding.", "err"); }
  const started = await startRun("", false, {
    steps: includeSound ? ["music", "visuals", "video"] : ["visuals", "video"], visual_mode: "motion", preview_only: true,
    use_project_photos: useProjectPhotos,
    video_type: reviewVideoType, profile_id: reviewProfileId || status.profile_id,
    expected_plan_revision: storyboardData?.plan_revision,
  });
  if (started) location.hash = "#home";
}
$("#animate-legacy").onclick = () => rebuildReviewVisuals(true);
$("#rebuild-motion-graphics").onclick = () => rebuildReviewVisuals(false);
$("#rebuild-motion-and-sound").onclick = () => rebuildReviewVisuals(false, true);
