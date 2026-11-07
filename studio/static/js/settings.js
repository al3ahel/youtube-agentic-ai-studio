/* ═══════════════════════════ settings ═══════════════════════════ */
const KEYS = [
  ["gemini_key", "Google Gemini", "Writes much better scripts. Recommended, takes 1 minute.", "https://aistudio.google.com/apikey"],
  ["pexels_key", "Pexels", "High-quality stock photos for your scenes.", "https://www.pexels.com/api/"],
  ["pixabay_key", "Pixabay", "Even more stock photos.", "https://pixabay.com/api/docs/"],
  ["groq_key", "Groq", "Backup AI for when Gemini runs out of free quota.", "https://console.groq.com/keys"],
  ["openrouter_key", "OpenRouter", "Free scene planning and optional visual review, plus a script backup.", "https://openrouter.ai/keys"],
  ["youtube_key", "YouTube Data API", "Shows your subscribers and views on Home.", "https://console.cloud.google.com/apis/library/youtube.googleapis.com"],
  ["youtube_channel", "Your channel", "Channel ID or @handle, used with the key above.", ""],
];
$("#key-fields").innerHTML = KEYS.map(([id, name, why, link]) => `
  <div class="field">
    <div class="label">${name}${link ? ` <a class="link" style="font-weight:400;font-size:11.5px" href="${link}" target="_blank" rel="noopener">get a free key ↗</a>` : ""}</div>
    <input type="${id === "youtube_channel" ? "text" : "password"}" id="s-${id}" autocomplete="off" spellcheck="false">
    <div class="hint">${why}</div>
  </div>`).join("");

const RANGES = {
  voice_rate:     { to: v => parseInt(v) || 0, from: v => (v >= 0 ? "+" : "") + v + "%", label: v => (v >= 0 ? "+" : "") + v + "%" },
  voice_pitch:    { to: v => parseInt(v) || 0, from: v => (v >= 0 ? "+" : "") + v + "Hz", label: v => (v >= 0 ? "+" : "") + v + "Hz" },
  overlay:        { to: v => Math.round(v * 100), from: v => v / 100, label: v => v + "%" },
  music_volume:   { to: v => Math.round(v * 100), from: v => v / 100, label: v => v + "%" },
  broll_interval: { to: v => Math.round(v), from: v => +v, label: v => v + " s" },
  broll_xfade:    { to: v => Math.round(v * 10), from: v => v / 10, label: v => (v / 10).toFixed(1) + " s" },
};
const TEXT = ["channel_name", "channel_desc", "brand_text", "thumb_style", "gemini_model", "groq_model", "openrouter_model", "render_preset", "kb_start", "kb_end", "privacy", "voice_id", "trends_geo", "visual_mode", "visual_planner_model", "visual_review_model", "motion_quality", "motion_workers"];
const BOOLS = ["fact_grounding", "keyless_llm", "music_enabled", "show_brand", "credit_yaas", "visual_ai_enabled", "visual_review_enabled", "openrouter_free_only"];
Object.entries(RANGES).forEach(([k, r]) => { const el = $("#r-" + k); el.addEventListener("input", () => $("#v-" + k).textContent = r.label(+el.value)); });

let settingsSnap = "";
function collectSettings() {
  const body = {};
  KEYS.forEach(([id]) => body[id] = $("#s-" + id).value);
  TEXT.forEach(k => body[k] = $("#s-" + k).value);
  BOOLS.forEach(k => body[k] = $("#s-" + k).checked);
  Object.entries(RANGES).forEach(([k, r]) => body[k] = r.from(+$("#r-" + k).value));
  body.image_sources = $$("#img-sources input:checked").map(i => i.value);
  return body;
}
const settingsDirty = () => settingsSnap && JSON.stringify(collectSettings()) !== settingsSnap;
async function loadSettings() {
  const d = settings = await api("/api/settings");
  KEYS.forEach(([id]) => { const el = $("#s-" + id); el.value = d[id] || ""; el.placeholder = id === "youtube_channel" ? "@yourhandle" : "not set (optional)"; });
  TEXT.forEach(k => { const el = $("#s-" + k); if (el && d[k] !== undefined) el.value = d[k]; });
  BOOLS.forEach(k => $("#s-" + k).checked = !!d[k]);
  Object.entries(RANGES).forEach(([k, r]) => { const v = r.to(d[k]); $("#r-" + k).value = v; $("#v-" + k).textContent = r.label(v); });
  $$("#img-sources input").forEach(i => { i.checked = (d.image_sources || []).includes(i.value); i.closest(".chip").classList.toggle("on", i.checked); });
  $("#s-banned").value = (d.banned_topics || []).filter(t => !["The Boltzmann Brain", "Simulation Theory", "Quantum Immortality"].includes(t)).join("\n");
  $("#quick-voice").value = d.voice_id;
  $("#quick-grounding").checked = !!d.fact_grounding;
  if (!status.running) { $("#visual-mode").value = d.visual_mode || "motion"; updateVisualMode(); }
  $("#upload-privacy").value = d.privacy;
  brandPreview();
  settingsSnap = JSON.stringify(collectSettings());
  settingsChanged();
  renderChecklist();
}
function settingsChanged() {
  if (location.hash === "#settings") $("#savebar").classList.toggle("show", !!settingsDirty());
}
function brandPreview() {
  const on = $("#s-show_brand").checked, text = ($("#s-brand_text").value.trim() || $("#s-channel_name").value.trim()).toUpperCase();
  $("#brand-field").style.opacity = on ? 1 : .4;
  $("#brand-preview").innerHTML = on ? `Your videos will show <span class="mono" style="color:var(--on-card)">▶ ${esc(text || "—")}</span>` : "No channel name will appear in your videos or thumbnails.";
}
["input", "change"].forEach(ev => $("#view-settings").addEventListener(ev, e => {
  if (e.target.id === "s-banned") return;
  brandPreview(); settingsChanged();
}));
async function saveSettings() {
  const r = await api("/api/save-settings", collectSettings()).catch(e => ({ ok: false, error: e.message }));
  if (r.ok) { toast("Settings saved", "ok"); await loadSettings(); loadHealth(); pollOnce(); }
  else toast(r.error, "err");
}
$("#discover-models").onclick = async () => {
  const button = $("#discover-models"), note = $("#model-discovery-status");
  busy(button, true, "Loading catalogue…");
  note.textContent = "Checking available free models…";
  try {
    const results = await Promise.all(["planner", "reviewer"].map(purpose => api(`/api/visuals/models?purpose=${purpose}&limit=30`)));
    results.forEach((result, index) => {
      const purpose = index ? "reviewer" : "planner";
      $("#free-" + purpose + "-models").innerHTML = result.models.map(model => `<option value="${esc(model.id)}">${esc(model.name || model.id)}</option>`).join("");
    });
    note.textContent = `${results[0].models.length} planners and ${results[1].models.length} reviewers loaded. Select a model in the fields above, then save. Availability can change.`;
  } catch (e) { note.textContent = e.message; }
  busy(button, false);
};
$("#save-banned").onclick = async () => {
  const topics = $("#s-banned").value.split("\n").map(s => s.trim()).filter(Boolean);
  const r = await api("/api/save-banned-topics", { topics });
  r.ok ? toast("List saved", "ok") : toast(r.error, "err");
};

// one floating save bar, shared by Review and Settings
$("#savebar-save").onclick = () => location.hash === "#settings" ? saveSettings() : saveMeta();
$("#savebar-undo").onclick = () => location.hash === "#settings"
  ? loadSettings().then(() => toast("Changes undone"))
  : (fillMeta(saved), toast("Changes undone"));
