/* Local audio imports and selections belong to the Settings form's profile. */
let audioLibraryProfileId = null, audioLibraryAssets = [], audioLibraryLoadSerial = 0, audioImportPending = false;
let reviewSoundMode = "auto";
const AUDIO_CONTENT_ID = { unknown: "Content ID not checked", registered: "Content ID registered", not_registered: "Not registered, as listed by the source" };

function setAudioTrackSelection(kind, value) {
  const select = $(kind === "music" ? "#s-music_track" : "#s-sfx_track"), id = String(value || "");
  if (id && !Array.from(select.options).some(option => option.value === id)) {
    const option = document.createElement("option");
    option.value = id; option.textContent = "Saved track (loading…)";
    select.appendChild(option);
  }
  select.value = id;
}

function audioSourceURL(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function audioPreviewURL(value, profileId) {
  try {
    const url = new URL(value, location.origin);
    return url.origin === location.origin && url.pathname.startsWith("/api/audio-library/")
      && url.searchParams.get("profile_id") === profileId ? url.href : null;
  } catch { return null; }
}

function renderAudioLibrary() {
  ["music", "sfx"].forEach(kind => {
    const select = $(kind === "music" ? "#s-music_track" : "#s-sfx_track"), selected = select.value;
    const assets = audioLibraryAssets.filter(asset => asset.kind === kind);
    select.innerHTML = `<option value="">${kind === "music" ? "Use the existing music folder" : "Choose an imported effect"}</option>`
      + assets.map(asset => `<option value="${esc(asset.id)}">${esc(asset.title || asset.filename)}</option>`).join("");
    if (selected && !assets.some(asset => asset.id === selected)) {
      const option = document.createElement("option");
      option.value = selected; option.textContent = "Saved track unavailable — choose another";
      select.appendChild(option);
    }
    select.value = selected;
  });
  $("#audio-library-list").innerHTML = audioLibraryAssets.map(asset => {
    const source = audioSourceURL(asset.source_url), preview = audioPreviewURL(asset.preview_url, audioLibraryProfileId);
    const duration = Number.isFinite(asset.duration) ? `${asset.duration.toFixed(1)} s` : "";
    const size = Number.isFinite(asset.bytes) ? `${(asset.bytes / 1024 / 1024).toFixed(2)} MiB` : "";
    return `<article class="audio-asset" data-audio-id="${esc(asset.id)}"><span class="tag">${asset.kind === "music" ? "Music" : "Sound effect"}</span>
      <h4>${esc(asset.title || asset.filename)}</h4><p class="hint">${[asset.creator, duration, size].filter(Boolean).map(esc).join(" · ")}</p>
      ${preview ? `<audio controls preload="none" src="${esc(preview)}" aria-label="Preview ${esc(asset.title || asset.filename)}"></audio>` : '<p class="hint">Preview unavailable.</p>'}
      <details><summary class="hint">Source &amp; license note</summary><p class="hint audio-source-note">${esc(asset.filename || "")}\n${esc(AUDIO_CONTENT_ID[asset.content_id] || AUDIO_CONTENT_ID.unknown)}${asset.license_note ? `\n${esc(asset.license_note)}` : ""}</p>${source ? `<a class="link" href="${esc(source)}" target="_blank" rel="noopener">Source page ↗</a>` : ""}</details></article>`;
  }).join("");
  $$("#audio-library-list audio").forEach(player => player.addEventListener("play", () => {
    $$("#audio-library-list audio").forEach(other => { if (other !== player) other.pause(); });
  }));
}

async function loadAudioLibrary(profileId = settingsProfileId) {
  if (!profileId) return;
  const serial = ++audioLibraryLoadSerial, message = $("#audio-library-status");
  if (profileId !== audioLibraryProfileId) {
    $$("#audio-library-list audio").forEach(player => player.pause());
    audioLibraryProfileId = profileId; audioLibraryAssets = [];
    $("#audio-import-form").reset();
    $("#audio-import-status").textContent = "";
    renderAudioLibrary();
  }
  $("#audio-library-channel").textContent = profileName(profileId);
  message.classList.remove("error"); message.textContent = "Loading this channel's audio…";
  try {
    const data = await api("/api/audio-library" + settingsProfileQuery(profileId));
    if (serial !== audioLibraryLoadSerial || settingsProfileId !== profileId) return;
    if (!data.ok) throw new Error(data.error || "The audio library could not be loaded.");
    audioLibraryAssets = Array.isArray(data.assets) ? data.assets.filter(asset => asset && typeof asset.id === "string" && ["music", "sfx"].includes(asset.kind)) : [];
    renderAudioLibrary();
    message.textContent = audioLibraryAssets.length ? `${audioLibraryAssets.length} imported track${audioLibraryAssets.length === 1 ? "" : "s"}. Preview a track before choosing it above.`
      : "No imported audio for this channel yet. Import a downloaded track above.";
  } catch (error) {
    if (serial !== audioLibraryLoadSerial || settingsProfileId !== profileId) return;
    message.classList.add("error"); message.textContent = `${error.message} Use Refresh library to try again.`;
  }
}

function updateAudioSettingsControls() {
  const mode = $("#s-sound_mode").value || "auto";
  $("#manual-audio-settings").hidden = mode !== "manual";
  $("#sound-intensity-field").hidden = mode !== "auto";
  $("#sound-direction-note").textContent = mode === "auto"
    ? "Automatic uses the script and this channel's style to choose suitable music, ambience and timed effects. No track selection is required. Save these defaults, then use Review to regenerate an existing draft's sound."
    : mode === "manual" ? "Choose exact tracks and levels below. Save Settings, then apply them from Review to keep your recorded voice and visuals."
      : "The Music step keeps narration and removes added music, ambience and effects. Save Settings, then apply this choice from Review to update an existing draft.";
  $("#audio-effect-mode-note").textContent = $("#s-sfx_mode").value === "ambience"
    ? "Ambience loops the selected effect beneath the narration."
    : "Transitions plays the selected effect briefly between script sections.";
}
$("#s-sfx_mode").addEventListener("change", updateAudioSettingsControls);
$("#s-sound_mode").addEventListener("change", updateAudioSettingsControls);
$("#audio-library-refresh").onclick = () => loadAudioLibrary();

function readAudioFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("The selected audio file could not be read. Choose it again."));
    reader.readAsDataURL(file);
  });
}

$("#audio-import-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (audioImportPending || !settingsProfileId) return;
  const profileId = settingsProfileId, file = $("#audio-import-file").files[0], message = $("#audio-import-status");
  const fail = text => { message.classList.add("error"); message.textContent = text; };
  if (!file) return fail("Choose an audio file first.");
  if (!/\.(mp3|wav|ogg|m4a|flac)$/i.test(file.name)) return fail("Choose an MP3, WAV, OGG, M4A or FLAC file.");
  if (!file.size || file.size > 20 * 1024 * 1024) return fail("Choose an audio file larger than 0 bytes and no more than 20 MiB.");
  const body = {
    filename: file.name, kind: $("#audio-import-kind").value,
    title: $("#audio-import-title").value.trim(), source_url: $("#audio-import-source").value.trim(),
    creator: $("#audio-import-creator").value.trim(), license_note: $("#audio-import-license").value.trim(),
    content_id: $("#audio-import-content-id").value,
  };
  audioImportPending = true;
  $$("#audio-import-form input, #audio-import-form select, #audio-import-form textarea, #audio-import-submit").forEach(input => input.disabled = true);
  busy($("#audio-import-submit"), true, "Importing…");
  message.classList.remove("error"); message.textContent = `Importing for ${profileName(profileId)}…`;
  try {
    body.data_base64 = await readAudioFile(file);
    const result = await api("/api/audio-library/import" + settingsProfileQuery(profileId), body);
    if (!result.ok || !result.asset) throw new Error(result.error || "The audio file could not be imported.");
    if (settingsProfileId === profileId) {
      $("#audio-import-form").reset(); $("#audio-import-kind").value = body.kind;
      await loadAudioLibrary(profileId);
      if (settingsProfileId === profileId) message.textContent = `Imported “${result.asset.title || file.name}”. Choose Manual sound direction to select this exact track, enable its layer and save Settings.`;
    }
    toast(`Audio imported for ${profileName(profileId)}`, "ok");
  } catch (error) {
    if (settingsProfileId === profileId) fail(error.message);
    else toast(`Audio import for ${profileName(profileId)} failed: ${error.message}`, "err");
  } finally {
    audioImportPending = false;
    $$("#audio-import-form input, #audio-import-form select, #audio-import-form textarea, #audio-import-submit").forEach(input => input.disabled = false);
    busy($("#audio-import-submit"), false);
  }
});

function reviewSoundAction() {
  return reviewSoundMode === "auto" ? "Regenerate sound design" : reviewSoundMode === "off" ? "Remove added sound from draft" : "Apply music & effects to current draft";
}

function audioTime(value) {
  if (!Number.isFinite(value) || value < 0) return "";
  const minutes = Math.floor(value / 60), seconds = (value % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${seconds}`;
}

function renderReviewAudio(data, st) {
  const credits = data.audio_credits, design = data.sound_design, master = data.audio_master;
  reviewSoundMode = ["auto", "manual", "off"].includes(data.sound_preferences?.mode) ? data.sound_preferences.mode : "auto";
  const sound = $("#review-sound-design");
  sound.replaceChildren();
  if (design && typeof design === "object" && !Array.isArray(design)) {
    const details = [];
    for (const [key, label] of [["music", "Music"], ["ambience", "Ambience"]]) {
      const layer = design[key];
      if (layer && typeof layer === "object") details.push({label: `${label}: ${layer.asset?.title || layer.title || layer.label || "selected layer"}`, reason: layer.reason, start: layer.start, duration: layer.duration});
    }
    if (Array.isArray(design.events)) details.push(...design.events.filter(event => event && typeof event === "object").slice(0, 100));
    const planner = design.planner && typeof design.planner === "object" ? design.planner : {};
    const plannerLabel = ["fallback", "local"].includes(planner.status) ? "Local editorial choices" : planner.status === "ai" || planner.status === "planned" ? "AI planned" : "";
    sound.innerHTML = `${typeof design.summary === "string" ? `<p>${esc(design.summary)}</p>` : ""}${typeof design.intent === "string" && design.intent !== design.summary ? `<p class="hint">${esc(design.intent)}</p>` : ""}${plannerLabel || planner.provider ? `<p class="hint">${esc([plannerLabel, planner.provider].filter(Boolean).join(" · "))}</p>` : ""}`
      + (details.length ? `<details class="more"><summary>Sound choices &amp; timing</summary><ul class="sound-cue-list">${details.map(event => {
        const start = audioTime(event.start), end = Number.isFinite(event.duration) && Number.isFinite(event.start) ? audioTime(event.start + event.duration) : "";
        return `<li>${start ? `<span class="mono">${esc(start)}${end ? `–${esc(end)}` : ""}</span> ` : ""}<b>${esc(event.label || event.title || event.asset?.title || "Sound effect")}</b>${typeof event.reason === "string" && event.reason ? `<p class="hint">${esc(event.reason)}</p>` : ""}</li>`;
      }).join("")}</ul></details>` : "");
    if (Array.isArray(design.warnings)) {
      for (const warning of design.warnings.filter(value => typeof value === "string").slice(0, 3)) {
        const note = document.createElement("p"); note.className = "hint"; note.textContent = warning; sound.append(note);
      }
    }
  } else sound.innerHTML = `<p class="hint">${reviewSoundMode === "auto" ? "No sound design report yet. Regenerate sound design to let the audio agent choose layers and timing for this story." : "No sound design report for this draft yet. Apply the channel's saved sound settings to update its soundtrack."}</p>`;
  const masterNote = $("#review-audio-master");
  masterNote.hidden = !master || typeof master !== "object";
  if (!masterNote.hidden) {
    const levels = [Number.isFinite(master.output_lufs) ? `${master.output_lufs.toFixed(1)} LUFS` : "", Number.isFinite(master.output_true_peak_dbtp) ? `${master.output_true_peak_dbtp.toFixed(1)} dBTP` : ""].filter(Boolean).join(" · ");
    const warnings = Array.isArray(master.warnings) ? master.warnings.filter(warning => typeof warning === "string").slice(0, 2).join(" ") : "";
    masterNote.textContent = `${master.status === "applied" ? "Soundtrack level checked" : master.status === "target_shortfall" ? "Soundtrack dynamics preserved" : master.status === "silent" ? "Soundtrack is silent" : master.status === "disabled" ? "Audio finishing is off" : "Mastering unavailable"}${levels ? ` · ${levels}` : ""}.${warnings ? ` ${warnings}` : ""}`;
  }
  const imported = Array.isArray(credits) ? credits.filter(asset => asset && ["music", "sfx"].includes(asset.kind)) : [];
  $("#review-audio-credits").innerHTML = imported.length ? imported.map(asset => {
    const source = audioSourceURL(asset.source_url), title = asset.title || asset.filename || "Imported audio";
    const label = asset.kind === "music" ? "Music" : asset.mode === "ambience" ? "Ambience" : "Transition effect";
    return `<div><span class="tag">${label}</span> ${source ? `<a class="link" href="${esc(source)}" target="_blank" rel="noopener">${esc(title)} ↗</a>` : esc(title)}${asset.creator ? `<span class="hint"> · ${esc(asset.creator)}</span>` : ""}</div>`;
  }).join("") : "";
  const classic = st.visual_mode === "classic";
  $("#apply-review-audio").hidden = classic;
  $("#apply-review-audio").textContent = reviewSoundAction();
  $("#review-audio-note").textContent = classic
    ? `Uses saved sound settings for ${profileName(reviewProfileId)}. To update this Classic video, clear the topic on Home and select only Music and Render under More options. Your recorded voice is reused.`
    : `Uses saved ${reviewSoundMode === "auto" ? "automatic" : reviewSoundMode} sound settings for ${profileName(reviewProfileId)}. Your script, recorded voice and storyboard stay the same. Save any Settings changes before applying them here.`;
}

$("#review-audio-settings").onclick = async () => {
  if (status.running || status.visual_editing || status.uploading) return toast("Wait for the current job before changing audio settings.");
  if (reviewProfileId && activeProfileId !== reviewProfileId) {
    await activateChannel(reviewProfileId);
    if (activeProfileId !== reviewProfileId) return;
  }
  location.hash = "#settings/audio";
};
$("#apply-review-audio").onclick = () => {
  if (settingsProfileId === reviewProfileId && settingsDirty()) return toast("Save this channel's Settings changes before applying its audio.", "err");
  return renderStoryboard(true, {steps: ["music", "video"]});
};
