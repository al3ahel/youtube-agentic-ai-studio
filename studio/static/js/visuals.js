/* Narration-timed storyboard review. The preview runs in an opaque iframe. */
let storyboardData = null, selectedScene = null, editingScene = false;

function seekSelectedScene() {
  const scene = storyboardData?.plan?.scenes?.find(item => String(item.id) === selectedScene);
  const frame = $("#motion-preview");
  if (scene && frame.contentWindow && frame.getAttribute("src")) {
    frame.contentWindow.postMessage({ type: "yaas:seek", time: Number(scene.start) + Math.min(.35, Number(scene.duration) / 4) }, "*");
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
window.addEventListener("message", event => {
  if (event.source === $("#motion-preview").contentWindow && event.data?.type === "yaas:ready") {
    resizeMotionPreview(); seekSelectedScene();
  }
});

function updateVisualControls(st = status) {
  const locked = !!(st.running || st.visual_editing || st.uploading || editingScene);
  ["regenerate-scene", "render-draft", "render-final"].forEach(id => {
    const element = $("#" + id);
    if (element) element.disabled = locked || !storyboardData?.plan;
  });
  $("#scene-instructions").disabled = locked;
  $("#animate-legacy").disabled = locked;
}

function showScene(id) {
  const scene = storyboardData?.plan?.scenes?.find(item => String(item.id) === String(id));
  if (!scene) return;
  selectedScene = String(scene.id);
  $$("#scene-list button").forEach(button => {
    const selected = button.dataset.scene === selectedScene;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("#scene-details").innerHTML = `<span class="tag">${esc(String(scene.template || "motion").replaceAll("_", " "))}</span><h3>${esc(scene.title || "Scene")}</h3><p>${esc(scene.goal || scene.visual_goal || "Explain this part of the narration.")}</p>`;
  $("#scene-instructions").value = "";
  $("#scene-edit-result").textContent = "";
  seekSelectedScene();
}

async function loadStoryboard(st = status) {
  const data = await api("/api/visuals").catch(() => ({ plan: null }));
  storyboardData = data;
  const scenes = data.plan?.scenes || [], visible = scenes.length > 0;
  $("#rv-storyboard").hidden = !visible;
  $("#storyboard-nav").hidden = !visible;
  if (!visible) { $("#motion-preview").removeAttribute("src"); return; }
  $("#storyboard-status").textContent = st.has_video ? "Final rendered" : "Draft";
  $("#scene-list").innerHTML = scenes.map((scene, index) => `<button class="scene-choice" type="button" data-scene="${esc(scene.id)}" aria-pressed="false"><span class="scene-number">${String(index + 1).padStart(2, "0")}</span><span><b>${esc(scene.title || `Scene ${index + 1}`)}</b><small>${esc(String(scene.template || "motion").replaceAll("_", " "))}</small></span></button>`).join("");
  $$("#scene-list button").forEach(button => button.onclick = () => showScene(button.dataset.scene));
  showScene(scenes.some(scene => String(scene.id) === selectedScene) ? selectedScene : scenes[0].id);
  const frame = $("#motion-preview");
  frame.hidden = !data.preview_url;
  if (data.preview_url) {
    const source = data.preview_url + "&revision=" + encodeURIComponent(data.revision || 0);
    if (frame.getAttribute("src") !== source) frame.src = source;
  } else frame.removeAttribute("src");
  resizeMotionPreview();
  $("#motion-preview-note").textContent = data.preview_url ? "Play or scrub the local HTML preview. Draft and final MP4s use the same storyboard." : "The storyboard is ready. Generate narration and render a draft to build its playable preview.";
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
}

$("#regenerate-scene").onclick = async () => {
  if (!selectedScene || editingScene) return;
  editingScene = true; updateVisualControls();
  const result = $("#scene-edit-result"); result.textContent = "Rebuilding this scene…";
  const button = $("#regenerate-scene"), label = button.textContent;
  button.textContent = "Rebuilding scene…";
  try {
    await api(`/api/visuals/scenes/${encodeURIComponent(selectedScene)}/regenerate`, { instructions: $("#scene-instructions").value.trim() });
    await pollOnce();
    await loadReview();
    $("#scene-edit-result").textContent = "Scene updated. Render a new draft or the final video when ready.";
    toast("Scene updated", "ok");
  } catch (e) { result.textContent = e.message; toast(e.message, "err"); }
  finally { editingScene = false; button.textContent = label; updateVisualControls(); }
};

async function renderStoryboard(previewOnly) {
  if (!storyboardData?.plan) return;
  const started = await startRun("", false, {
    steps: ["video"], visual_mode: storyboardData.visual_mode || "motion",
    preview_only: previewOnly, video_type: status.video_type || "normal",
    profile_id: reviewProfileId || status.profile_id,
  });
  if (started) { location.hash = "home"; updateVisualControls(); }
}
$("#render-draft").onclick = () => renderStoryboard(true);
$("#render-final").onclick = () => renderStoryboard(false);
