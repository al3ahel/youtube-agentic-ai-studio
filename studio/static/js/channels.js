/* Channel identities, profile defaults and explicit YouTube connections. */
let channelProfiles = [], activeProfileId = null, channelProfilesLoaded = false;
let channelRequestSerial = 0, channelMutation = false, channelPollTimer = null;
let editingChannelId = null, profileSavePending = false;
let voicePreviewSerial = 0, voicePreviewPending = false;
const channelTopicDrafts = new Map();

const CHANNEL_PRESETS = { general: "General", news: "News", asmr: "ASMR / calm" };
const PRESET_NOTES = {
  general: "A balanced starting point. Choose your own voice, visuals and music after creating the profile.",
  news: "Clear, natural narration, Hybrid visuals and music off. Add the subjects and audience your news channel covers.",
  asmr: "Slower, calm narration, gentle photo motion and music off. This is a calm speech preset; a true ASMR recording needs an appropriate audio source.",
};

function activeChannelProfile() {
  return channelProfiles.find(profile => profile.id === activeProfileId) || null;
}

function channelThumbnail(profile) {
  const value = profile.channel && profile.channel.thumbnail;
  if (typeof value !== "string" || !value) return "";
  try { const url = new URL(value); return url.protocol === "https:" ? url.href : ""; }
  catch (_) { return ""; }
}

function channelAvatar(profile, large = false) {
  const image = channelThumbnail(profile), preset = CHANNEL_PRESETS[profile.preset] ? profile.preset : "general";
  const initials = String(profile.name || "Channel").trim().split(/\s+/).slice(0, 2).map(word => Array.from(word)[0]).join("").toUpperCase();
  return `<div class="channel-avatar ${preset}${large ? " large" : ""}" aria-hidden="true">${image ? `<img src="${esc(image)}" alt="" referrerpolicy="no-referrer">` : esc(initials)}</div>`;
}

function profileName(id, fallback = "Channel") {
  return channelProfiles.find(profile => profile.id === id)?.name || fallback;
}

function showChannelFormError(message) {
  $("#channel-form-error").textContent = message || "";
  $("#channel-form-error").hidden = !message;
}

function profileIsBusy(d = status) {
  return channelMutation || !!(d.running || d.uploading || d.visual_editing);
}

function renderChannelProfiles() {
  const active = activeChannelProfile();
  $("#channel-list").innerHTML = channelProfiles.length ? channelProfiles.map(profile => {
    const connected = !!(profile.connected && profile.channel), selected = profile.id === activeProfileId;
    const sub = connected ? profile.channel.title : profile.connection?.status === "connecting" ? "Connecting…" : `${CHANNEL_PRESETS[profile.preset] || "General"} · not connected`;
    return `<button type="button" class="channel-choice${selected ? " active" : ""}" data-profile-id="${esc(profile.id)}" aria-pressed="${selected}" title="${esc(profile.name)}">
      ${channelAvatar(profile)}<span class="channel-choice-text"><strong>${esc(profile.name)}</strong><small>${esc(sub)}</small></span>${connected ? '<span class="connected-dot" title="YouTube connected"></span>' : ""}</button>`;
  }).join("") : '<p class="rail-note">Add a profile to give your channel its own defaults and schedules.</p>';
  $$("#channel-list [data-profile-id]").forEach(button => button.addEventListener("click", () => activateChannel(button.dataset.profileId)));
  $("#channel-select").innerHTML = channelProfiles.length ? channelProfiles.map(profile => `<option value="${esc(profile.id)}">${esc(profile.name)}</option>`).join("") : '<option value="">Add a channel profile</option>';
  $("#channel-select").value = activeProfileId || "";

  if (active) {
    const connected = !!(active.connected && active.channel), channel = active.channel || {}, defaults = active.defaults || {};
    const validChannel = typeof channel.id === "string" && /^UC[A-Za-z0-9_-]{22}$/.test(channel.id);
    const channelTitle = connected && channel.title ? esc(channel.title) : "YouTube not connected";
    const identity = connected
      ? `<span class="channel-verified"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8"/><path d="m6 10 3 3 5-6"/></svg>${validChannel ? `<a href="https://www.youtube.com/channel/${esc(channel.id)}" target="_blank" rel="noopener">${channelTitle} ↗</a>` : channelTitle}</span>`
      : '<span>Create locally; connect when you want to publish.</span>';
    const mode = { motion: "Motion", hybrid: "Hybrid", classic: "Classic" }[defaults.visual_mode] || "Motion";
    $("#channel-identity").innerHTML = `${channelAvatar(active, true)}<div><div class="channel-eyebrow">Creating for · ${esc(CHANNEL_PRESETS[active.preset] || "General")}</div><h2 id="active-channel-title">${esc(active.name)}</h2><div class="channel-details">${identity}<span>${mode} · ${defaults.music_enabled ? "music on" : "music off"}</span></div></div>`;
    $("#edit-channel").textContent = "Profile settings";
    $("#connect-channel").textContent = connected ? "Reconnect YouTube" : "Connect YouTube";
    $("#refresh-channel").hidden = !active.token_present;
    $("#library-channel-note").textContent = `Videos for ${active.name}. Open one to edit and publish it with its saved channel profile.`;
  } else {
    $("#channel-identity").innerHTML = '<div class="channel-avatar large" aria-hidden="true">+</div><div><div class="channel-eyebrow">Your workspace</div><h2 id="active-channel-title">Add your first channel</h2><p class="hint">Keep news, calm videos and other projects separate.</p></div>';
    $("#edit-channel").textContent = "Add a profile";
    $("#connect-channel").textContent = "Connect YouTube";
    $("#refresh-channel").hidden = true;
  }
  if (typeof updateSettingsChannelNote === "function") updateSettingsChannelNote();
  updateChannelControls(status);
}

function updateChannelControls(d = status) {
  const active = activeChannelProfile(), busyNow = profileIsBusy(d);
  const connecting = active?.connection?.status === "connecting";
  $$("#channel-list button, #add-channel, #add-channel-mobile").forEach(button => button.disabled = busyNow);
  $("#channel-select").disabled = busyNow || !channelProfiles.length;
  $("#edit-channel").disabled = busyNow;
  $("#connect-channel").disabled = busyNow || !active || connecting;
  $("#refresh-channel").disabled = busyNow || !active || connecting;
  if (connecting) $("#connect-channel").textContent = "Connecting…";
  const note = $("#channel-connection-note");
  note.classList.toggle("error", active?.connection?.status === "error");
  note.textContent = active?.connection?.status === "error" ? active.connection.error || "YouTube connection failed. Try connecting again."
    : connecting ? "Complete Google sign-in in the browser opened by YAAS. Choose the YouTube channel that belongs to this profile."
    : d.running ? `Making a video for ${d.profile_name || profileName(d.profile_id, active?.name)}. Channel switching is available when the run finishes.`
    : d.uploading ? `Publishing for ${d.profile_name || profileName(d.profile_id, active?.name)}…`
    : d.visual_editing ? "Updating the current project. Channel switching is available when this finishes." : "";
  if (d.profile_id || d.profile_name) {
    $("#review-channel").textContent = `Project channel · ${d.profile_name || profileName(d.profile_id)}`;
    $("#review-channel").hidden = false;
  }
  if (typeof updateScheduleControls === "function") updateScheduleControls();
}

async function loadChannels({ syncWorkspace = false } = {}) {
  const serial = ++channelRequestSerial;
  try {
    const data = await api("/api/channels");
    if (serial !== channelRequestSerial) return null;
    const previousId = activeProfileId;
    const previousConnections = new Map(channelProfiles.map(profile => [profile.id, profile.connection?.status]));
    channelProfiles = Array.isArray(data.profiles) ? data.profiles : [];
    activeProfileId = data.active_profile_id || null;
    channelProfilesLoaded = true;
    if (previousId && previousId !== activeProfileId) {
      channelTopicDrafts.set(previousId, { topic: $("#topic-input").value, focus: $("#focus-angle").value });
      const draft = channelTopicDrafts.get(activeProfileId);
      $("#topic-input").value = draft?.topic || "";
      $("#focus-angle").value = draft?.focus || "";
      resetRunSteps();
      clearChannelScopedViews();
    }
    renderChannelProfiles();
    for (const profile of channelProfiles) {
      if (previousConnections.get(profile.id) === "connecting" && profile.connection?.status === "connected") {
        toast(`Connected ${profile.channel?.title || profile.name}`, "ok");
        loadHealth();
        if (profile.id === activeProfileId) loadChannel();
        if (profile.id === status.profile_id && status.has_video && typeof checkYt === "function") checkYt();
      }
    }
    clearTimeout(channelPollTimer);
    if (channelProfiles.some(profile => profile.connection?.status === "connecting")) {
      channelPollTimer = setTimeout(() => loadChannels(), 2000);
    }
    if (syncWorkspace && previousId !== activeProfileId) await refreshChannelWorkspace();
    return data;
  } catch (error) {
    if (serial !== channelRequestSerial) return null;
    if (!channelProfilesLoaded) {
      $("#channel-list").innerHTML = `<p class="rail-note">${esc(error.message)}</p>`;
      $("#channel-connection-note").textContent = "Couldn't load channel profiles. Refresh the page to try again.";
    }
    if (channelProfiles.some(profile => profile.connection?.status === "connecting")) {
      clearTimeout(channelPollTimer);
      channelPollTimer = setTimeout(() => loadChannels(), 5000);
    }
    return null;
  }
}

function clearChannelScopedViews() {
  // Remove actions for the previous channel before its replacement data arrives.
  library = null; ++libraryLoadSerial;
  $("#lib-grid").innerHTML = '<article class="card s24 flat"><div class="empty">Loading this channel’s videos…</div></article>';
  $("#latest").innerHTML = '<div class="empty" style="min-height:110px">Loading this channel’s videos…</div>';
  ["lib-stats", "lib-bars", "pulse-stats", "pulse-bars", "pulse-src", "pulse-foot", "pulse-hint"].forEach(id => $("#" + id).textContent = "");
  channelSchedules = []; ++scheduleRequestSerial; clearTimeout(schedulePollTimer);
  $("#schedule-count").textContent = $("#rail-schedule-count").textContent = "0";
  $("#schedule-list").innerHTML = '<div class="schedule-empty"><p>Loading this channel’s schedules…</p></div>';
}

async function refreshChannelWorkspace() {
  const active = activeChannelProfile();
  await loadSettings().catch(error => toast(error.message, "err"));
  if (active?.defaults?.video_type && !status.running) {
    videoType = active.defaults.video_type;
    $$("#format-seg button").forEach(button => button.classList.toggle("on", button.dataset.fmt === videoType));
  }
  const longButton = $('#format-seg button[data-fmt="normal"]');
  if (longButton) longButton.textContent = `Long video · ~${active?.defaults?.duration_mins || 8} min`;
  await Promise.allSettled([loadLibrary(), loadSchedules(), loadHealth()]);
  pollOnce();
}

async function activateChannel(id) {
  if (!id || id === activeProfileId) return;
  if (profileIsBusy()) return toast("Wait for the current job before switching channels.");
  if (typeof settingsDirty === "function" && settingsDirty()) {
    $("#channel-select").value = activeProfileId || "";
    return toast("Save or undo your Settings changes before switching channels.", "err");
  }
  channelMutation = true; updateChannelControls();
  try {
    await api(`/api/channels/${encodeURIComponent(id)}/activate`, {});
    await loadChannels();
    await refreshChannelWorkspace();
    setHero("form");
    location.hash = "#home";
    toast(`Switched to ${activeChannelProfile()?.name || "channel"}`);
  } catch (error) {
    $("#channel-select").value = activeProfileId || "";
    toast(error.message, "err");
  } finally { channelMutation = false; updateChannelControls(); }
}

function setSelectValue(selector, value) {
  const select = $(selector), text = String(value ?? "");
  $$("option[data-custom]", select).forEach(option => option.remove());
  if (text && !Array.from(select.options).some(option => option.value === text)) {
    const option = document.createElement("option");
    option.value = text; option.textContent = text; option.dataset.custom = "1";
    select.appendChild(option);
  }
  select.value = text;
}

function openChannelEditor(id = null) {
  if (profileIsBusy()) return toast("Wait for the current job before changing profiles.");
  if (typeof settingsDirty === "function" && settingsDirty()) return toast("Save or undo your Settings changes first.", "err");
  const profile = channelProfiles.find(item => item.id === id), defaults = profile?.defaults || {};
  editingChannelId = profile?.id || null;
  resetProfileVoicePreview();
  $("#channel-form").reset();
  showChannelFormError("");
  $("#channel-dialog-title").textContent = profile ? "Profile settings" : "Add a channel";
  $("#channel-dialog-intro").textContent = profile ? `Voice, visuals and music for ${profile.name}. Your publishing connection belongs to this profile.` : "Give each channel its own voice, visual style and publishing connection.";
  $("#profile-name").value = profile?.name || "";
  $("#profile-preset").value = profile?.preset || "general";
  $("#profile-preset").disabled = !!profile;
  $("#profile-preset-note").textContent = profile ? "Adjust this channel's defaults below. Changes apply to its next generated videos." : PRESET_NOTES.general;
  $("#profile-description").value = defaults.channel_desc || "";
  $("#profile-defaults").hidden = !profile;
  if (profile) {
    $("#profile-voice").innerHTML = $("#s-voice_id").innerHTML;
    setSelectValue("#profile-voice", defaults.voice_id || settings.voice_id || "en-US-AndrewNeural");
    setSelectValue("#profile-voice-rate", defaults.voice_rate || "+0%");
    setSelectValue("#profile-voice-pitch", defaults.voice_pitch || "+0Hz");
    $("#profile-visual-mode").value = defaults.visual_mode || "motion";
    $("#profile-format").value = defaults.video_type || "normal";
    $("#profile-duration").value = defaults.duration_mins || 8;
    $("#profile-privacy").value = defaults.privacy || "private";
    $("#profile-music").checked = !!defaults.music_enabled;
    $("#profile-music-volume").value = Math.round((defaults.music_volume ?? .08) * 100);
    $("#profile-music-value").textContent = $("#profile-music-volume").value + "%";
    $("#profile-music-library").value = defaults.music_library || "";
    $("#profile-music-level").hidden = !defaults.music_enabled;
  }
  $("#profile-client-secret").value = "";
  $("#profile-bound-channel").hidden = !profile?.channel;
  $("#profile-bound-channel").textContent = profile?.channel ? `This profile stays linked to ${profile.channel.title || "its verified channel"}, including after disconnecting. Add another profile for a different YouTube channel.` : "";
  $("#delete-channel").hidden = !profile || profile.id === "default";
  $("#delete-channel").dataset.confirm = "";
  $("#delete-channel").textContent = "Delete profile";
  $("#disconnect-channel").hidden = !profile?.token_present;
  $("#disconnect-channel").dataset.confirm = "";
  $("#disconnect-channel").textContent = "Disconnect YouTube";
  $("#save-channel").textContent = profile ? "Save profile" : "Create profile";
  $("#channel-dialog").showModal();
  $("#profile-name").focus();
}

function resetProfileVoicePreview() {
  ++voicePreviewSerial;
  const player = $("#profile-voice-audio"), message = $("#voice-preview-status");
  player.pause(); player.removeAttribute("src"); player.load(); player.hidden = true;
  message.classList.remove("error");
  message.textContent = "Hear a short sample with these voice settings.";
}

$("#preview-profile-voice").addEventListener("click", async () => {
  if (voicePreviewPending || profileSavePending) return;
  resetProfileVoicePreview();
  const serial = ++voicePreviewSerial, message = $("#voice-preview-status");
  const button = $("#preview-profile-voice"), player = $("#profile-voice-audio");
  voicePreviewPending = true; busy(button, true, "Preparing sample…");
  message.textContent = "Preparing a short voice sample…";
  try {
    const data = await api("/api/voice-preview", {
      voice_id: $("#profile-voice").value,
      voice_rate: $("#profile-voice-rate").value,
      voice_pitch: $("#profile-voice-pitch").value,
    });
    if (serial !== voicePreviewSerial || !$("#channel-dialog").open) return;
    const source = new URL(data.url, location.origin);
    if (source.origin !== location.origin || !/^\/api\/voice-preview\/[a-zA-Z0-9_-]+$/.test(source.pathname)) throw new Error("The voice sample could not be loaded. Try again.");
    player.src = source.href; player.hidden = false;
    message.textContent = "Sample ready. Save the profile to use these settings in future videos.";
    player.play().catch(() => { /* The audio controls remain available if autoplay is blocked. */ });
  } catch (error) {
    if (serial !== voicePreviewSerial || !$("#channel-dialog").open) return;
    message.textContent = error.message;
    message.classList.add("error");
  } finally { voicePreviewPending = false; busy(button, false); }
});
["profile-voice", "profile-voice-rate", "profile-voice-pitch"].forEach(id => $("#" + id).addEventListener("change", resetProfileVoicePreview));
$("#channel-dialog").addEventListener("close", resetProfileVoicePreview);

$("#channel-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (profileSavePending) return;
  const name = $("#profile-name").value.trim();
  if (!name) return showChannelFormError("Give this profile a name.");
  const body = { name, preset: $("#profile-preset").value };
  const description = $("#profile-description").value.trim();
  if (editingChannelId) {
    body.defaults = {
      channel_desc: description, voice_id: $("#profile-voice").value,
      voice_rate: $("#profile-voice-rate").value, voice_pitch: $("#profile-voice-pitch").value,
      visual_mode: $("#profile-visual-mode").value, video_type: $("#profile-format").value,
      duration_mins: +$("#profile-duration").value,
      privacy: $("#profile-privacy").value, music_enabled: $("#profile-music").checked,
      music_volume: +$("#profile-music-volume").value / 100,
      music_library: $("#profile-music-library").value.trim(),
    };
    const clientPath = $("#profile-client-secret").value.trim();
    if (clientPath) body.client_secret_path = clientPath;
  } else if (description) body.defaults = { channel_desc: description };
  const visualChoice = homeVisualChoices.get(editingChannelId);
  const visualDefaultChanged = editingChannelId && body.defaults.visual_mode !== channelProfiles.find(profile => profile.id === editingChannelId)?.defaults?.visual_mode;
  profileSavePending = channelMutation = true;
  busy($("#save-channel"), true, "Saving…"); showChannelFormError(""); updateChannelControls();
  try {
    await api(editingChannelId ? `/api/channels/${encodeURIComponent(editingChannelId)}` : "/api/channels", body, editingChannelId ? "PATCH" : "POST");
    if (visualDefaultChanged && homeVisualChoices.get(editingChannelId) === visualChoice) homeVisualChoices.delete(editingChannelId);
    $("#channel-dialog").close();
    await loadChannels();
    await refreshChannelWorkspace();
    if (!editingChannelId) { setHero("form"); location.hash = "#home"; }
    toast(editingChannelId ? "Channel profile saved" : "Channel profile created", "ok");
  } catch (error) { showChannelFormError(error.message); }
  finally { profileSavePending = channelMutation = false; busy($("#save-channel"), false); updateChannelControls(); }
});

async function channelConnection(action) {
  const profile = activeChannelProfile();
  if (!profile || profileIsBusy()) return;
  let connectionError = "";
  channelMutation = true; updateChannelControls();
  try {
    await api(`/api/channels/${encodeURIComponent(profile.id)}/${action}`, {});
    await loadChannels();
    if (action === "refresh") { loadChannel(); toast("Channel details refreshed", "ok"); }
  } catch (error) {
    connectionError = error.message;
    toast(error.message, "err");
  } finally {
    channelMutation = false; updateChannelControls();
    if (connectionError) { $("#channel-connection-note").textContent = connectionError; $("#channel-connection-note").classList.add("error"); }
  }
}

function confirmProfileAction(button, label) {
  if (button.dataset.confirm === "1") return true;
  button.dataset.confirm = "1"; button.textContent = label;
  setTimeout(() => {
    if (button.dataset.confirm === "1") { button.dataset.confirm = ""; button.textContent = button.id === "delete-channel" ? "Delete profile" : "Disconnect YouTube"; }
  }, 5000);
  return false;
}

$("#delete-channel").addEventListener("click", async () => {
  const button = $("#delete-channel"), id = editingChannelId;
  if (!id || profileSavePending || !confirmProfileAction(button, "Confirm delete")) return;
  profileSavePending = channelMutation = true; busy(button, true, "Deleting…"); showChannelFormError("");
  try {
    await api(`/api/channels/${encodeURIComponent(id)}`, undefined, "DELETE");
    $("#channel-dialog").close(); await loadChannels(); await refreshChannelWorkspace();
    toast("Channel profile deleted");
  } catch (error) { showChannelFormError(error.message); }
  finally { profileSavePending = channelMutation = false; busy(button, false); updateChannelControls(); }
});

$("#disconnect-channel").addEventListener("click", async () => {
  const button = $("#disconnect-channel"), id = editingChannelId;
  if (!id || profileSavePending || !confirmProfileAction(button, "Confirm disconnect")) return;
  profileSavePending = channelMutation = true; busy(button, true, "Disconnecting…"); showChannelFormError("");
  try {
    await api(`/api/channels/${encodeURIComponent(id)}/disconnect`, {});
    await loadChannels(); button.hidden = true; loadHealth();
    toast("YouTube disconnected for this profile");
  } catch (error) { showChannelFormError(error.message); }
  finally { profileSavePending = channelMutation = false; busy(button, false); updateChannelControls(); }
});

$("#add-channel").onclick = $("#add-channel-mobile").onclick = () => openChannelEditor();
$("#edit-channel").onclick = () => openChannelEditor(activeProfileId);
$("#channel-select").onchange = event => activateChannel(event.target.value);
$("#connect-channel").onclick = () => channelConnection("connect");
$("#refresh-channel").onclick = () => channelConnection("refresh");
$("#profile-preset").onchange = event => $("#profile-preset-note").textContent = PRESET_NOTES[event.target.value] || PRESET_NOTES.general;
$("#profile-music").onchange = event => $("#profile-music-level").hidden = !event.target.checked;
$("#profile-music-volume").oninput = event => $("#profile-music-value").textContent = event.target.value + "%";

$$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => {
  if ((button.dataset.closeDialog === "channel-dialog" && profileSavePending) || (button.dataset.closeDialog === "schedule-dialog" && typeof scheduleSavePending !== "undefined" && scheduleSavePending)) return;
  $("#" + button.dataset.closeDialog).close();
}));
$("#channel-dialog").addEventListener("cancel", event => { if (profileSavePending) event.preventDefault(); });
$("#channel-schedules-link").onclick = () => {
  if (location.hash !== "#home") location.hash = "#home";
  requestAnimationFrame(() => $("#channel-schedules").scrollIntoView({ behavior: "smooth", block: "start" }));
};
