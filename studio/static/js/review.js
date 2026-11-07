/* ═══════════════════════════ review ═══════════════════════════ */
let tags = [], saved = null, thumbMode = "stock", uploadMode = "now", reviewProfileId = null, reviewVideoType = "normal";
let reviewLoadSerial = 0, reviewMediaSerial = 0;

function invalidateReviewLoads() {
  reviewLoadSerial++;
  invalidateStoryboardLoads();
}

function updateReviewMedia(st) {
  const final = !!st.has_video, hasPlayable = final || st.has_preview;
  $("#review-heading").textContent = final ? "Review & publish" : "Review your draft";
  $("#review-intro").textContent = final ? "Watch the final video, polish its details, then publish when you are ready." : "Review the storyboard and adjust individual scenes. Each change rebuilds the draft video. Render a final video when the explanation looks right.";
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

async function loadReview({ preserveEdits = false, expectedPlanRevision = null, expectedProfileId = null } = {}) {
  const serial = ++reviewLoadSerial;
  preserveEdits = preserveEdits || editingScene || !!reviewRender;
  const [st, d] = await Promise.all([api("/api/status"), api("/api/output")]);
  if (serial !== reviewLoadSerial) return false;
  if ((expectedPlanRevision && st.plan_revision !== expectedPlanRevision)
      || (expectedProfileId && st.profile_id !== expectedProfileId)) {
    throw new Error("The open storyboard changed. Reopen Review before rendering it again.");
  }
  if (metaDirty() && reviewProfileId === st.profile_id && st.plan_revision
      && storyboardData?.plan_revision === st.plan_revision) preserveEdits = true;
  const has = st.has_video || st.has_preview || st.has_storyboard;
  reviewProfileId = st.profile_id || null;
  reviewVideoType = st.video_type || "normal";
  if (typeof updateChannelControls === "function") updateChannelControls(st);
  $("#review-empty").hidden = has;
  $$(".review-part").forEach(el => el.hidden = !has);
  if (!has) return false;
  $("#legacy-motion-card").hidden = st.visual_mode !== "classic" || !st.has_audio || !st.has_script;
  updateReviewMedia(st);
  $("#thumb-wrap").innerHTML = st.has_thumb ? `<img src="/thumbnail/${Date.now()}" alt="Thumbnail">` : `<div class="empty">No thumbnail yet</div>`;
  if (st.has_thumb) loadThumb();
  $("#rv-thumb").hidden = !st.has_thumb;
  $('.guide-steps a[href="#rv-thumb"]').hidden = !st.has_thumb;
  $("#dl-thumb").hidden = !st.has_thumb;
  $("#dl-srt").hidden = !st.has_captions;
  const keepMetadata = preserveEdits && saved;
  saved = { title: d.title || "", desc: d.description || "", tags: [...(d.tags || [])] };
  if (keepMetadata) edited(true);
  else fillMeta(saved);
  $("#sections").innerHTML = (d.sections || []).map((s, i) => `
    <details class="section"><summary><span class="n">${String(i + 1).padStart(2, "0")}</span><span>${esc(s.title || s.section_type || "Section")}</span><span class="tag">${esc(s.section_type || "")}</span></summary><p>${esc(s.narration || "")}</p></details>`).join("") || `<div class="hint">No script</div>`;
  const credits = d.credits || [];
  const bySrc = credits.reduce((a, c) => (a[c.source] = (a[c.source] || 0) + 1, a), {});
  $("#review-sources").innerHTML = sourcesHTML(d.sources) +
    (credits.length ? `<div class="label" style="margin:16px 0 8px">Where the images came from</div><div class="chips">${Object.entries(bySrc).map(([k, v]) => `<span class="chip" style="cursor:default">${esc(k)} <span class="count">${v}</span></span>`).join("")}</div>` : "");
  $("#upload-status").innerHTML = st.yt_url ? `Already on YouTube: <a class="link" href="${esc(st.yt_url)}" target="_blank" rel="noopener">${esc(st.yt_url)}</a>` : "";
  if (!preserveEdits) {
    $("#ideas").innerHTML = "";
    setReviewRenderResult("");
  }
  loadKeywords();
  if (st.has_video) checkYt();
  await loadStoryboard(st, { preserveEditor: preserveEdits, expectedPlanRevision, isCurrent: () => serial === reviewLoadSerial });
  return serial === reviewLoadSerial;
}
function sourcesHTML(sources) {
  if (!sources || !sources.length) return `<div class="hint">No fact-check sources for this video.</div>`;
  return `<div class="label" style="margin-bottom:8px">Fact-checked against ${sources.length} sources <small>also listed in the description</small></div><div class="sources">${sources.map(s =>
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
  await api("/api/save-output", { title: c.title, description: c.desc, tags: c.tags });
  saved = { ...c, tags: [...c.tags] };
  edited(true); toast("Saved", "ok");
  // a thumbnail using the title as its text should follow a title change
  if (thumb && !thumb.custom_text) loadThumb();
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
  thumbPost({ previews: true });          // fresh previews of every style for this video
}
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
$("#bg-ai").onclick = async () => {
  const b = $("#bg-ai"); busy(b, true, "Painting… (~20 s)");
  await thumbPost({ bg: "ai", previews: true });
  busy(b, false);
};
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

$("#animate-legacy").onclick = async () => {
  const started = await startRun("", false, {
    steps: ["visuals", "video"], visual_mode: "motion", preview_only: true,
    video_type: reviewVideoType, profile_id: reviewProfileId || status.profile_id,
  });
  if (started) location.hash = "#home";
};
