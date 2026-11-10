/* Narration-timed storyboard review. The preview runs in an opaque iframe. */
let storyboardData = null, selectedScene = null, editingScene = false;
let storyboardLoadSerial = 0, reviewRender = null;

function invalidateStoryboardLoads() { storyboardLoadSerial++; }

function storyboardUsesProjectPhotos(data = storyboardData) {
  const plan = data?.plan;
  if (typeof plan?.use_project_photos === "boolean") return plan.use_project_photos;
  // Plans saved before the photo policy was explicit retain their existing look.
  return ["hybrid", "footage"].includes(plan?.mode || data?.visual_mode)
    || !!plan?.scenes?.some(scene => scene.image);
}

function showPlannerStatus(plan) {
  const saved = plan?.ai_status, info = saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
  const providers = Array.isArray(info.batch_providers)
    ? info.batch_providers.filter(row => row && typeof row === "object" && !Array.isArray(row)) : [];
  const records = providers.length ? providers : [info];
  const modelName = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,159}$/.test(value) ? value : "model not recorded";
  const providerNames = {ollama: "Ollama", openrouter: "OpenRouter", gemini: "Gemini", groq: "Groq"};
  const providerName = value => Object.hasOwn(providerNames, value) ? providerNames[value] : "provider not recorded";
  const identity = row => row.provider === "openrouter"
    ? `${providerName(row.provider)} · requested ${row.model === "openrouter/free" || row.model_is_router ? "router" : "model"} ${modelName(row.model)} (responding model not recorded)`
    : `${providerName(row.provider)} · ${modelName(row.model)}`;
  let headline, detail;
  if (info.status === "planned") {
    const replayed = records.filter(row => row.response_origin === "recorded_local_response" || row.new_inference === false).length;
    headline = replayed === records.length ? "Replayed model decisions" : replayed ? "Model decisions with some replayed responses" : "Recorded model planning";
    const identities = [...new Set(records.map(identity))];
    headline += " · " + identities.slice(0, 4).join("; ") + (identities.length > 4 ? "; additional recorded providers" : "");
    detail = replayed === records.length ? "Saved responses were reused for this planning step; it did not run new scene-planning inference."
      : replayed ? "This planning record combines new and previously saved responses." : "This is the saved planning record for this storyboard.";
    const from = [...new Set(records.map(row => row.fallback_from).filter(value => typeof value === "string" && value))];
    if (from.length) detail += ` A fallback model supplied decisions after ${from.map(providerName).join(" / ")} was unavailable or unusable.`;
    const accepted = info.provenance?.accepted_scene_count, rejected = info.provenance?.rejected_scene_count;
    if (Number.isInteger(accepted) && Number.isInteger(rejected) && accepted >= 0 && rejected >= 0 && accepted + rejected <= (plan?.scenes?.length || 0)) {
      detail += ` ${accepted} scene proposals accepted; ${rejected} rejected.`;
    }
  } else if (["fallback", "unavailable", "disabled"].includes(info.status)) {
    headline = info.status === "fallback" ? "Template fallback" : "Local templates";
    detail = info.status === "disabled" ? "AI scene planning was off when this storyboard was built."
      : info.status === "unavailable" ? "No configured planner was available when this storyboard was built."
      : "The configured planner did not return a usable design, so local animated templates supplied the storyboard.";
  } else {
    headline = "Planner not recorded";
    detail = "This saved storyboard has no planner record. Its existing scene designs are retained.";
  }
  if (plan?.last_edit) detail += " Later scene edits are also present.";
  $("#planner-status").textContent = headline;
  $("#planner-detail").textContent = detail + " Planning records do not establish visual approval.";
}

$("#review-planner-settings").onclick = async () => {
  if (status.running || status.visual_editing || status.uploading) return toast("Wait for the current job before changing model settings.");
  if (reviewProfileId && activeProfileId !== reviewProfileId) {
    await activateChannel(reviewProfileId);
    if (activeProfileId !== reviewProfileId) return;
  }
  location.hash = "#settings/models";
};
$("#planner-rebuild-options").onclick = () => {
  const controls = $("#legacy-motion-card");
  if (!controls.hidden) controls.scrollIntoView({behavior: "smooth", block: "center"});
};

function selectedSceneTime() {
  const scene = storyboardData?.plan?.scenes?.find(item => String(item.id) === selectedScene);
  return scene ? Number(scene.start) + Math.min(.35, Number(scene.duration) / 4) : null;
}

function seekSelectedScene() {
  const time = selectedSceneTime(), frame = $("#motion-preview"), video = $("#player video");
  if (time === null || !Number.isFinite(time)) return;
  if (frame.contentWindow && frame.getAttribute("src")) {
    frame.contentWindow.postMessage({ type: "yaas:seek", time }, "*");
  }
  if (video) {
    const seek = () => {
      const current = selectedSceneTime();
      if (current !== null && Number.isFinite(current)) {
        video.currentTime = Math.max(0, Math.min(current, Number.isFinite(video.duration) ? Math.max(0, video.duration - .05) : current));
      }
    };
    // Replace a pending seek if another scene is selected before metadata loads.
    video.onloadedmetadata = seek;
    if (video.readyState >= 1) seek();
  }
}

function resizeMotionPreview() {
  const frame = $("#motion-preview"), plan = storyboardData?.plan;
  if (!plan || frame.hidden) return;
  const width = frame.getBoundingClientRect().width;
  const height = Math.round(Math.max(220, Math.min(680, width * Number(plan.height) / Number(plan.width) + 66)));
  if (Number.isFinite(height)) frame.style.height = height + "px";
}
new ResizeObserver(resizeMotionPreview).observe($("#motion-preview").parentElement);
$("#motion-preview").addEventListener("load", () => { resizeMotionPreview(); seekSelectedScene(); });
window.addEventListener("message", event => {
  if (event.source === $("#motion-preview").contentWindow && event.data?.type === "yaas:ready") {
    resizeMotionPreview(); seekSelectedScene();
  }
});

function updateVisualControls(st = status) {
  const locked = !!(st.running || st.visual_editing || st.uploading || editingScene || reviewRender);
  ["regenerate-scene", "render-draft", "render-final"].forEach(id => {
    const element = $("#" + id);
    if (element) element.disabled = locked || !storyboardData?.plan;
  });
  $("#scene-instructions").disabled = locked;
  const photos = storyboardUsesProjectPhotos();
  $("#replace-scene-image").disabled = locked || !photos;
  $("#replace-scene-image").closest("label").hidden = !photos;
  $("#replace-scene-image-note").hidden = !photos;
  if (!photos) $("#replace-scene-image").checked = false;
  $$("#scene-list button").forEach(button => button.disabled = locked);
  $("#animate-legacy").disabled = locked;
  $("#rebuild-motion-graphics").disabled = locked;
  $("#rebuild-motion-and-sound").disabled = locked;
  $("#apply-review-audio").disabled = locked || !storyboardData?.plan;
  $("#apply-review-audio").textContent = reviewRender?.steps?.includes("music") ? "Updating sound design…" : reviewSoundAction();
  $("#review-audio-settings").disabled = locked;
  $("#review-planner-settings").disabled = locked;
  $("#planner-rebuild-options").disabled = locked;
  $("#rv-storyboard").setAttribute("aria-busy", String(locked));
  $("#regenerate-scene").textContent = editingScene ? "Updating scene…" : reviewRender ? "Rendering video…" : "Regenerate scene & draft";
  $("#storyboard-status").textContent = reviewRender ? (reviewRender.previewOnly ? "Rendering draft" : "Rendering final")
    : st.has_video ? "Final rendered" : st.has_preview ? "Draft rendered" : storyboardData?.preview_url ? "Scene preview" : "Storyboard only";
  if (reviewRender) {
    const label = reviewRender.previewOnly ? "draft" : "final video";
    const elapsed = st.running && st.started_at ? ` ${clock(Date.now() / 1000 - st.started_at)} elapsed.` : "";
    setReviewRenderResult(reviewRender.finishing ? `Loading the updated ${label}…`
      : reviewRender.starting ? `Starting the ${label} render…`
      : `Rendering the ${label}…${elapsed} You can review the updated storyboard above.`);
  }
}

function setReviewRenderResult(message, error = false) {
  const element = $("#review-render-status");
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle("error", error);
}

function setSceneEditResult(message, error = false) {
  $("#scene-edit-result").textContent = message;
  $("#scene-edit-result").classList.toggle("error", error);
}

function showScene(id, { preserveEditor = false } = {}) {
  const scene = storyboardData?.plan?.scenes?.find(item => String(item.id) === String(id));
  if (!scene) return;
  preserveEditor = preserveEditor && selectedScene === String(scene.id);
  selectedScene = String(scene.id);
  $$("#scene-list button").forEach(button => {
    const selected = button.dataset.scene === selectedScene;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const look = scene.look || storyboardData?.plan?.visual_direction?.look;
  const styleTags = [scene.shot?.role, scene.shot?.technique, scene.shot?.framing, scene.template || "motion", look, scene.shot?.camera || scene.staging?.camera,
    scene.composition && scene.composition !== "default" ? scene.composition : null].filter(Boolean);
  $("#scene-details").innerHTML = `${styleTags.map(tag => `<span class="tag">${esc(String(tag).replaceAll("_", " "))}</span>`).join(" ")}<h3>${esc(scene.title || "Scene")}</h3><p>${esc(scene.goal || scene.visual_goal || "Explain this part of the narration.")}</p>`;
  if (scene.shot) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = `${Number(scene.start).toFixed(2)}–${(Number(scene.start) + Number(scene.duration)).toFixed(2)}s · Section ${scene.section_id} · ${scene.shot.action}`;
    $("#scene-details").append(note);
  }
  if (scene.mechanism && !scene.footage && !scene.image) {
    const materials = {steel: "Steel", mail: "Linked mail", ceramic: "Ceramic", fiber: "Fiber", mixed: "Combined materials"};
    const actions = {reveal: "Subject detail", approach: "Approaching projectile", impact: "Contact and reaction", deflect: "Deflection", penetrate: "Penetration", separate: "Separated layers", absorb: "Impact absorption", compare: "Material views"};
    const note = document.createElement("p"); note.className = "hint";
    note.textContent = `${materials[scene.mechanism.material] || "Material"} · ${actions[scene.mechanism.action] || "Illustrative mechanism"}`;
    $("#scene-details").append(note);
  } else if (scene.staging && !scene.footage && !scene.image) {
    const actions = { rise: "Rising water", adapt: "Adapting to rising water", float: "Homes rising with water", record: "Recording people", gap: "Revealing a recording gap",
      extract: "Extracting material", transfer: "Moving material", reveal: "Revealing the subject", assemble: "Assembling the parts",
      connect: "Connecting the parts", compare: "Comparing the subjects" };
    const cast = (Array.isArray(scene.items) ? scene.items : []).filter(item => item.role === "subject" || item.role === "support")
      .map(item => item.label).filter(label => typeof label === "string").slice(0, 4);
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = [actions[scene.staging.action] || "Animated scene", cast.join(" · ")].filter(Boolean).join(" — ");
    $("#scene-details").append(note);
  }
  if (scene.footage) {
    try {
      const source = new URL(scene.footage.source_url);
      if (source.protocol === "https:" && !source.username && !source.password && ["pixabay.com", "www.pixabay.com", "pexels.com", "www.pexels.com"].includes(source.hostname)) {
        const note = document.createElement("p"), link = document.createElement("a");
        note.className = "hint";
        link.className = "link"; link.href = source.href; link.target = "_blank"; link.rel = "noopener noreferrer";
        link.textContent = scene.footage.credit || "View footage source";
        note.append(link);
        const subjects = scene.footage.selection?.matched_subjects;
        if (Array.isArray(subjects) && subjects.length) note.append(document.createTextNode(` · Matching source tags: ${subjects.slice(0, 6).join(", ")}`));
        $("#scene-details").append(note);
      }
    } catch (_) { /* Older projects can omit a source link. */ }
  }
  const directionRows = storyboardData?.plan?.direction_report?.scenes;
  const directed = Array.isArray(directionRows) ? directionRows.find(row => row?.scene_id === scene.id) : null;
  if (directed) {
    const note = document.createElement("p"); note.className = "hint";
    const issues = Array.isArray(directed.issues) ? directed.issues.filter(issue => typeof issue === "string").slice(0, 3) : [];
    note.textContent = [(directed.source === "ai" ? "AI scene direction." : "Local scene direction."), ...issues].join(" ");
    $("#scene-details").append(note);
  }
  if (!preserveEditor) {
    $("#scene-instructions").value = "";
    $("#replace-scene-image").checked = false;
    setSceneEditResult("");
  }
  seekSelectedScene();
}

async function loadStoryboard(st = status, { data = null, preserveEditor = false, expectedPlanRevision = null, isCurrent = () => true } = {}) {
  const serial = ++storyboardLoadSerial;
  data = data || await api("/api/visuals");
  if (!data || serial !== storyboardLoadSerial || !isCurrent()) return false;
  if (data.ok === false) throw new Error(data.error || "The storyboard could not be loaded.");
  if (expectedPlanRevision && data.plan_revision !== expectedPlanRevision) {
    throw new Error("The open storyboard changed. Reopen Review before rendering it again.");
  }
  const scenes = Array.isArray(data.plan?.scenes) ? data.plan.scenes.filter(scene => scene && typeof scene === "object") : [], visible = scenes.length > 0;
  if (st.has_storyboard && !visible) throw new Error("The saved storyboard has no reviewable scenes. Try loading the project again.");
  storyboardData = data;
  showPlannerStatus(data.plan);
  const brief = data.plan?.creative_brief;
  $("#film-direction").hidden = !brief;
  $("#film-direction").textContent = brief ? [brief.theme, brief.premise, brief.palette ? `Palette: ${brief.palette}.` : ""].filter(item => typeof item === "string").join(" · ") : "";
  const directionReport = data.plan?.direction_report;
  $("#direction-check-note").hidden = !directionReport?.summary;
  $("#direction-check-note").textContent = directionReport?.summary ? `${data.plan?.shot_report?.summary || ""} ${directionReport.summary} These are planning checks; encoded review appears below.`.trim() : "";
  $("#rv-storyboard").hidden = !visible;
  $("#storyboard-nav").hidden = !visible;
  if (!visible) { $("#motion-preview").removeAttribute("src"); return true; }
  $("#scene-list").innerHTML = scenes.map((scene, index) => `<button class="scene-choice" type="button" data-scene="${esc(scene.id)}" aria-pressed="false"><span class="scene-number">${String(index + 1).padStart(2, "0")}</span><span><b>${esc(scene.title || `Scene ${index + 1}`)}</b><small>${esc(scene.shot ? `${scene.shot.role} · ${scene.shot.framing} · ${Number(scene.duration).toFixed(1)}s` : String(scene.template || "motion").replaceAll("_", " "))}</small></span></button>`).join("");
  $$("#scene-list button").forEach(button => button.onclick = () => showScene(button.dataset.scene));
  showScene(scenes.some(scene => String(scene.id) === selectedScene) ? selectedScene : scenes[0].id, { preserveEditor });
  const frame = $("#motion-preview");
  frame.hidden = !data.preview_url;
  if (data.preview_url) {
    const source = data.preview_url + (data.preview_url.includes("?") ? "&" : "?") + "revision=" + encodeURIComponent(data.preview_revision || data.plan_revision || data.revision || 0);
    if (frame.getAttribute("src") !== source) frame.src = source;
  } else frame.removeAttribute("src");
  resizeMotionPreview();
  $("#motion-preview-note").textContent = data.preview_url ? "Play or scrub the scene preview. Draft and final MP4s use the same storyboard." : "The storyboard is ready. Generate narration and render a draft to build its playable preview.";
  const timingWarnings = Array.isArray(st.timing_warnings) ? st.timing_warnings.filter(warning => typeof warning === "string").slice(0, 2) : [];
  const timingLabel = st.timing_source === "legacy_srt" ? "Timing from the saved captions." : st.timing_source === "estimated_duration" ? "Timing estimated from the existing audio." : "";
  $("#timing-source-note").hidden = !timingLabel && !timingWarnings.length;
  $("#timing-source-note").textContent = [timingLabel, ...timingWarnings].filter(Boolean).join(" ");
  $("#image-reuse-note").hidden = !st.image_reuse_note;
  $("#image-reuse-note").textContent = typeof st.image_reuse_note === "string" ? st.image_reuse_note : "";
  const stock = data.plan?.stock_status;
  $("#footage-selection-note").hidden = data.plan?.mode === "motion" || !stock;
  const stockReasons = { missing_key: "Save a Pixabay or Pexels key in Settings to include video footage.",
    no_eligible_scenes: "No filmed subject was selected for these scenes.",
    subject_mismatch: "No footage matched the scene subject; the existing scene design was kept.",
    no_matching_footage: "No clip met this scene's subject, framing and duration requirements; the existing scene design was kept.",
    excessive_crop: "The available clips would crop away too much of the picture; the existing scene design was kept.",
    clip_too_short: "The available clips were shorter than the scene; the existing scene design was kept.",
    rate_limited: "The footage provider reached its request limit. You can rebuild Visuals later.",
    access_denied: "The footage provider did not accept the saved key. Check its key and account access in Settings.",
    provider_unavailable: "The footage provider was unavailable; the existing scene design was kept.",
    download_failed: "The selected footage could not be downloaded; the existing scene design was kept.",
    media_probe_failed: "The downloaded clip did not pass the video checks; the existing scene design was kept.",
    media_validation_failed: "The downloaded clip did not pass the video checks; the existing scene design was kept.",
    ffprobe_unavailable: "FFprobe is needed to check downloaded footage. Run the local setup check.",
    ffmpeg_unavailable: "FFmpeg is needed to check downloaded footage. Run the local setup check.",
    downloaded: "The selected clips are included in the storyboard." };
  $("#footage-selection-note").textContent = stock ? `${Number(stock.clips) || 0} footage scenes. ${stockReasons[stock.reason] || "Footage is selected only when a clip matches the scene and fits its framing and duration."}` : "";
  const qa = data.qa;
  const issues = qa && (qa.issues || qa.warnings || []);
  const notes = Array.isArray(issues) ? issues.map(issue => typeof issue === "string" ? issue : issue.message || issue.description || "").filter(Boolean).slice(0, 8) : [];
  $("#visual-qa").textContent = qa?.summary || qa?.message || (notes.length ? notes.join(" · ") : qa?.status === "unavailable" ? "AI frame review was unavailable for this render. Review the preview before publishing." : qa?.status === "disabled" ? "AI frame review is off. You can enable it in Motion studio settings." : "Visual review results appear here after rendering.");
  if (qa?.summary && notes.length) {
    const list = document.createElement("ul");
    notes.forEach(note => { const item = document.createElement("li"); item.textContent = note; list.append(item); });
    $("#visual-qa").append(list);
  }
  if (Array.isArray(qa?.repair_history) && qa.repair_history.length) {
    const detail = document.createElement("p");
    detail.textContent = `Repair attempts: ${qa.repair_history.length}. Applied changes: ${Number(qa.repairs_applied) || 0}. Earlier render evidence is retained.`;
    $("#visual-qa").append(detail);
  }
  updateVisualControls(st);
  return true;
}

$("#regenerate-scene").onclick = async () => {
  if (!selectedScene || editingScene || reviewRender || status.running || status.visual_editing || status.uploading) return;
  const context = {
    sceneId: selectedScene, profileId: reviewProfileId || status.profile_id,
    videoType: reviewVideoType, visualMode: storyboardData?.visual_mode || "motion",
    useProjectPhotos: storyboardUsesProjectPhotos(),
    planRevision: storyboardData?.plan_revision,
  };
  const instructions = $("#scene-instructions").value.trim(), replaceImage = context.useProjectPhotos && $("#replace-scene-image").checked;
  invalidateReviewLoads();
  editingScene = true; updateVisualControls();
  setSceneEditResult("Updating this scene…");
  setReviewRenderResult("");
  try {
    const updated = await api(`/api/visuals/scenes/${encodeURIComponent(context.sceneId)}/regenerate`, {
      instructions, replace_image: replaceImage,
      ...(context.planRevision ? { expected_plan_revision: context.planRevision } : {}),
    });
    if (!updated.ok) throw new Error(updated.error || "Could not update this scene.");
    statusRequestSerial++;
    const st = status = { ...status, has_video: false, has_preview: false, has_storyboard: true,
      has_html_preview: !!updated.preview_url, review_ready: !!updated.preview_url, library_id: null,
      yt_url: null, error: null, visual_editing: false, video_type: context.videoType, visual_mode: context.visualMode,
      plan_revision: updated.plan_revision, preview_revision: null, video_revision: null };
    updateReviewMedia(st);
    await loadStoryboard(st, { data: { ...storyboardData, ...updated, visual_mode: context.visualMode,
      plan_revision: updated.plan_revision, preview_revision: updated.preview_revision }, preserveEditor: true });
    $("#scene-instructions").value = "";
    $("#replace-scene-image").checked = false;
    const edit = updated.last_edit || updated.plan?.last_edit;
    const changes = Array.isArray(edit?.changes) ? edit.changes.filter(change => typeof change === "string" && change.trim()).slice(0, 4).map(change => change.slice(0, 250)) : [];
    setSceneEditResult(`Scene updated${changes.length ? `: ${changes.join("; ")}` : ""}. The replacement draft will open at this scene.`);
    await renderStoryboard(true, { ...context, planRevision: updated.plan_revision });
  } catch (e) { setSceneEditResult(e.message, true); toast(e.message, "err"); }
  finally { editingScene = false; updateVisualControls(); }
};

async function renderStoryboard(previewOnly, context = {}) {
  if (!storyboardData?.plan || reviewRender) return false;
  if (status.running || status.uploading) {
    setReviewRenderResult("The updated storyboard is ready, but another job is running. Wait for it to finish, then choose Render draft MP4 or Render final video.", true);
    return false;
  }
  invalidateReviewLoads();
  const operation = reviewRender = {
    previewOnly, starting: true,
    steps: context.steps || ["video"],
    profileId: context.profileId || reviewProfileId || status.profile_id,
    videoType: context.videoType || reviewVideoType,
    visualMode: context.visualMode || storyboardData.visual_mode || "motion",
    useProjectPhotos: context.useProjectPhotos ?? storyboardUsesProjectPhotos(),
    planRevision: context.planRevision || storyboardData.plan_revision,
  };
  updateVisualControls();
  try {
    const started = await startRun("", false, {
      steps: operation.steps, visual_mode: operation.visualMode, use_project_photos: operation.useProjectPhotos,
      preview_only: previewOnly, video_type: operation.videoType,
      profile_id: operation.profileId, expected_plan_revision: operation.planRevision,
      scroll: false, throwOnError: true,
    });
    if (!started) throw new Error("The video render could not be started.");
    if (reviewRender === operation) { operation.starting = false; updateVisualControls(); }
    return true;
  } catch (error) {
    if (reviewRender === operation) reviewRender = null;
    updateVisualControls();
    setReviewRenderResult(`Could not start the ${previewOnly ? "draft" : "final video"}: ${error.message} Your updated storyboard is still available above.`, true);
    return false;
  }
}

async function finishReviewRender(st) {
  const operation = reviewRender;
  if (!operation || operation.finishing) return false;
  operation.finishing = true; updateVisualControls(st);
  let message, error = false;
  try {
    if (operation.profileId && st.profile_id && st.profile_id !== operation.profileId) {
      throw new Error("The open project changed. Reopen Review to see the current project.");
    }
    const refreshed = await loadReview({ preserveEdits: true, expectedPlanRevision: operation.planRevision, expectedProfileId: operation.profileId, throwOnError: true });
    if (!refreshed) throw new Error("Could not refresh the video. Reopen Review to load the latest draft.");
    const failure = st.error || lastRunError;
    if (failure) throw new Error(failure);
    if (st.stop_requested) throw new Error("Rendering was stopped.");
    if (!(operation.previewOnly ? st.has_preview : st.has_video)) throw new Error("Rendering finished without a playable video.");
    seekSelectedScene();
    message = `${operation.previewOnly ? "Updated draft" : "Final video"} ready. The video below is set to the selected scene.`;
    toast(operation.previewOnly ? "Updated draft ready" : "Final video ready", "ok");
  } catch (failure) {
    error = true;
    const retryAction = operation.steps.includes("music") ? reviewSoundAction() : `Render ${operation.previewOnly ? "draft MP4" : "final video"}`;
    message = `${operation.previewOnly ? "Draft" : "Final video"} rendering failed: ${failure.message} The storyboard is still available; use ${retryAction} to try again.`;
  } finally {
    if (reviewRender === operation) reviewRender = null;
    updateVisualControls(st);
    setReviewRenderResult(message, error);
  }
  return true;
}
$("#render-draft").onclick = () => renderStoryboard(true);
$("#render-final").onclick = () => renderStoryboard(false);
