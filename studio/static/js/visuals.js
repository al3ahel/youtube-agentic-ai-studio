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
  $("#apply-review-audio").disabled = locked || !storyboardData?.plan;
  $("#apply-review-audio").textContent = reviewRender?.steps?.includes("music") ? "Applying music & effects…" : "Apply music & effects to current draft";
  $("#review-audio-settings").disabled = locked;
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
  $("#scene-details").innerHTML = `<span class="tag">${esc(String(scene.template || "motion").replaceAll("_", " "))}</span><h3>${esc(scene.title || "Scene")}</h3><p>${esc(scene.goal || scene.visual_goal || "Explain this part of the narration.")}</p>`;
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
  $("#rv-storyboard").hidden = !visible;
  $("#storyboard-nav").hidden = !visible;
  if (!visible) { $("#motion-preview").removeAttribute("src"); return true; }
  $("#scene-list").innerHTML = scenes.map((scene, index) => `<button class="scene-choice" type="button" data-scene="${esc(scene.id)}" aria-pressed="false"><span class="scene-number">${String(index + 1).padStart(2, "0")}</span><span><b>${esc(scene.title || `Scene ${index + 1}`)}</b><small>${esc(String(scene.template || "motion").replaceAll("_", " "))}</small></span></button>`).join("");
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
  const qa = data.qa;
  const issues = qa && (qa.issues || qa.warnings || []);
  const notes = Array.isArray(issues) ? issues.map(issue => typeof issue === "string" ? issue : issue.message || issue.description || "").filter(Boolean).slice(0, 8) : [];
  $("#visual-qa").textContent = qa?.summary || qa?.message || (notes.length ? notes.join(" · ") : qa?.status === "unavailable" ? "AI frame review was unavailable for this render. Review the preview before publishing." : qa?.status === "disabled" ? "AI frame review is off. You can enable it in Motion studio settings." : "Visual review results appear here after rendering.");
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
    setSceneEditResult("Scene updated. The replacement draft will open at this scene.");
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
    const retryAction = operation.steps.includes("music") ? "Apply music & effects to current draft" : `Render ${operation.previewOnly ? "draft MP4" : "final video"}`;
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
