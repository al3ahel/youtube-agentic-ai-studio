/* Local audio imports and selections belong to the Settings form's profile. */
let audioLibraryProfileId = null, audioLibraryAssets = [], audioLibraryLoadSerial = 0, audioImportPending = false;
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
  $("#audio-effect-mode-note").textContent = $("#s-sfx_mode").value === "ambience"
    ? "Ambience loops the selected effect beneath the narration."
    : "Transitions plays the selected effect briefly between script sections.";
}
$("#s-sfx_mode").addEventListener("change", updateAudioSettingsControls);
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
      if (settingsProfileId === profileId) message.textContent = `Imported “${result.asset.title || file.name}”. Select it above, enable the layer and save Settings.`;
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

function renderReviewAudio(credits, st) {
  const imported = Array.isArray(credits) ? credits.filter(asset => asset && ["music", "sfx"].includes(asset.kind)) : [];
  $("#review-audio-credits").innerHTML = imported.length ? imported.map(asset => {
    const source = audioSourceURL(asset.source_url), title = asset.title || asset.filename || "Imported audio";
    const label = asset.kind === "music" ? "Music" : asset.mode === "ambience" ? "Ambience" : "Transition effect";
    return `<div><span class="tag">${label}</span> ${source ? `<a class="link" href="${esc(source)}" target="_blank" rel="noopener">${esc(title)} ↗</a>` : esc(title)}${asset.creator ? `<span class="hint"> · ${esc(asset.creator)}</span>` : ""}</div>`;
  }).join("") : '<p class="hint">Imported soundtrack credits appear here after an audio mix is applied.</p>';
  const classic = st.visual_mode === "classic";
  $("#apply-review-audio").hidden = classic;
  $("#review-audio-note").textContent = classic
    ? `Save audio choices for ${profileName(reviewProfileId)} in Settings. To apply them to this Classic video, clear the topic on Home and select only Music and Render under More options. Your recorded voice is reused.`
    : `Save audio choices for ${profileName(reviewProfileId)} in Settings, then apply them to this draft. Your script, recorded voice and storyboard stay the same.`;
}

$("#review-audio-settings").onclick = async () => {
  if (status.running || status.visual_editing || status.uploading) return toast("Wait for the current job before changing audio settings.");
  if (reviewProfileId && activeProfileId !== reviewProfileId) {
    await activateChannel(reviewProfileId);
    if (activeProfileId !== reviewProfileId) return;
  }
  location.hash = "#settings";
};
$("#apply-review-audio").onclick = () => {
  if (settingsProfileId === reviewProfileId && settingsDirty()) return toast("Save this channel's Settings changes before applying its audio.", "err");
  return renderStoryboard(true, {steps: ["music", "video"]});
};
