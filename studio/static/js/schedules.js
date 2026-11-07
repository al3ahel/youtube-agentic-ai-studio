/* Per-channel recurring generation. These jobs never publish to YouTube. */
let channelSchedules = [], schedulerInfo = {}, scheduleRequestSerial = 0;
let schedulePollTimer = null, editingScheduleId = null, editingScheduleProfileId = null;
let scheduleSavePending = false;
const pendingScheduleActions = new Set();

function scheduleDate(value, timezone) {
  if (!value) return "";
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  if (!Number.isFinite(date.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: timezone || "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
  } catch (_) { return date.toLocaleString(); }
}

function renderSchedules() {
  const active = activeChannelProfile();
  const enabled = channelSchedules.filter(schedule => schedule.enabled).length;
  $("#schedule-count").textContent = channelSchedules.length;
  $("#rail-schedule-count").textContent = enabled;
  if (!channelSchedules.length) {
    $("#schedule-list").innerHTML = `<div class="schedule-empty"><span class="schedule-empty-icon" aria-hidden="true">◷</span><div><b>${active ? `No schedules for ${esc(active.name)} yet` : "Create a channel profile first"}</b><p>${active ? "Give this channel a recurring topic and a time. Each video stays here for review." : "Each channel can have its own topics and generation times."}</p></div></div>`;
  } else {
    $("#schedule-list").innerHTML = channelSchedules.map(schedule => {
      const running = schedule.last_status === "running", queued = schedule.last_status === "queued";
      const label = running ? "Generating" : queued ? "Queued" : schedule.enabled ? "Enabled" : "Paused";
      const next = scheduleDate(schedule.next_run_at, schedule.timezone), last = scheduleDate(schedule.last_run_at, schedule.timezone);
      const format = schedule.video_type === "shorts" ? "Short · 9:16" : "Long · 16:9";
      const mode = { motion: "Motion", hybrid: "Hybrid", classic: "Classic" }[schedule.visual_mode] || "Motion";
      const statusText = { completed: "Completed", failed: "Failed", skipped: "Skipped", queued: "Queued", running: "Generating" }[schedule.last_status] || "";
      return `<article class="schedule-item${schedule.enabled ? "" : " paused"}" data-schedule-id="${esc(schedule.id)}">
        <div class="schedule-item-head"><h3>${esc(schedule.name)}</h3><span class="tag${running || queued ? " amber" : schedule.enabled ? " green" : ""}">${label}</span></div>
        <p class="schedule-topic">${esc(schedule.topic)}</p>
        <div class="schedule-timing"><code>${esc(schedule.cron)}</code><span>${esc(schedule.timezone)}</span><span>${format} · ${mode} · ${schedule.preview_only ? "Draft" : "Final MP4"}</span></div>
        <p class="schedule-next">${schedule.enabled ? `Next: <strong>${next ? esc(next) : "Waiting for scheduler"}</strong>` : "Paused. Turn it on to generate future videos."}</p>
        ${last || statusText ? `<p class="schedule-last">${statusText ? `${esc(statusText)}${last ? " · " : ""}` : "Last run · "}${esc(last)}${schedule.last_library_id ? ` · <button type="button" class="link" data-schedule-action="open" data-project-id="${esc(schedule.last_library_id)}">Open video</button>` : ""}</p>` : ""}
        ${schedule.last_error ? `<details class="schedule-error"><summary>Last run details</summary><p>${esc(String(schedule.last_error).slice(0, 1200))}</p></details>` : ""}
        <div class="schedule-actions"><button type="button" class="btn btn-ghost btn-xs" data-schedule-action="run"${running || queued ? " disabled" : ""}>Run now</button><button type="button" class="btn btn-ghost btn-xs" data-schedule-action="edit">Edit</button><button type="button" class="btn btn-ghost btn-xs" data-schedule-action="toggle">${schedule.enabled ? "Pause" : "Enable"}</button><button type="button" class="schedule-delete" data-schedule-action="delete">Delete</button></div>
      </article>`;
    }).join("");
  }
  const statusElement = $("#scheduler-status");
  statusElement.className = "scheduler-status " + (schedulerInfo.running ? "online" : "offline");
  statusElement.textContent = schedulerInfo.running ? "Scheduler active" : "Scheduler is not running";
  updateScheduleControls();
}

function updateScheduleControls() {
  $("#add-schedule").disabled = !activeChannelProfile() || scheduleSavePending;
  $$("#schedule-list .schedule-item").forEach(card => {
    const schedule = channelSchedules.find(item => item.id === card.dataset.scheduleId);
    $$("button[data-schedule-action]", card).forEach(button => {
      const pending = pendingScheduleActions.has(card.dataset.scheduleId);
      button.disabled = pending || (button.dataset.scheduleAction === "run" && ["running", "queued"].includes(schedule?.last_status));
    });
  });
}

async function loadSchedules() {
  clearTimeout(schedulePollTimer);
  const profileId = activeProfileId, serial = ++scheduleRequestSerial;
  if (!profileId) { channelSchedules = []; renderSchedules(); return null; }
  try {
    const data = await api(`/api/schedules?profile_id=${encodeURIComponent(profileId)}`);
    if (serial !== scheduleRequestSerial || profileId !== activeProfileId) return null;
    channelSchedules = Array.isArray(data.schedules) ? data.schedules.filter(schedule => schedule.profile_id === profileId) : [];
    schedulerInfo = data.scheduler || {};
    renderSchedules();
    return data;
  } catch (error) {
    if (serial !== scheduleRequestSerial || profileId !== activeProfileId) return null;
    $("#scheduler-status").className = "scheduler-status offline";
    $("#scheduler-status").textContent = "Scheduler unavailable";
    $("#schedule-list").innerHTML = `<div class="schedule-empty"><div><b>Couldn't load schedules</b><p>${esc(error.message)}</p><button type="button" class="btn btn-ghost btn-xs" id="retry-schedules" style="margin-top:10px">Try again</button></div></div>`;
    $("#retry-schedules").onclick = () => loadSchedules();
    return null;
  } finally {
    if (serial === scheduleRequestSerial) {
      schedulePollTimer = setTimeout(() => {
        if (!document.hidden && (!location.hash || location.hash === "#home")) loadSchedules();
      }, 15000);
    }
  }
}

function setScheduleError(message) {
  $("#schedule-form-error").textContent = message || "";
  $("#schedule-form-error").hidden = !message;
}

function syncScheduleOutput() {
  const classic = $("#schedule-visual-mode").value === "classic";
  $("#schedule-output").disabled = classic;
  if (classic) $("#schedule-output").value = "final";
  $("#schedule-output-note").textContent = classic ? "Classic creates a local MP4. It waits here for your review and publishing." : $("#schedule-output").value === "draft" ? "Review the scenes and voice before making a final video." : "Save a final MP4 locally. Publishing remains a separate action.";
}

function openScheduleEditor(id = null) {
  const schedule = channelSchedules.find(item => item.id === id), profile = activeChannelProfile();
  if (!profile) return toast("Add a channel profile before scheduling a video.", "err");
  if (scheduleSavePending) return;
  editingScheduleId = schedule?.id || null;
  editingScheduleProfileId = schedule?.profile_id || profile.id;
  $("#schedule-form").reset(); setScheduleError("");
  $("#schedule-dialog-title").textContent = schedule ? "Edit schedule" : "Add a schedule";
  $("#schedule-channel-name").textContent = `Channel · ${profileName(editingScheduleProfileId, profile.name)}`;
  $("#schedule-name").value = schedule?.name || "";
  $("#schedule-topic").value = schedule?.topic || "";
  $("#schedule-topic").placeholder = profile.preset === "news" ? "e.g. Explain today's most important space news for a general audience." : profile.preset === "asmr" ? "e.g. A calm, unhurried description of walking through a quiet garden." : "e.g. Explain one useful science idea with a clear example.";
  const cron = schedule?.cron || "0 9 * * *";
  $("#schedule-cron").value = cron;
  $("#schedule-preset").value = Array.from($("#schedule-preset").options).some(option => option.value === cron) ? cron : "custom";
  $("#schedule-timezone").value = schedule?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  $("#schedule-format").value = schedule?.video_type || profile.defaults?.video_type || "normal";
  $("#schedule-visual-mode").value = schedule?.visual_mode || profile.defaults?.visual_mode || "motion";
  $("#schedule-output").value = schedule && !schedule.preview_only ? "final" : "draft";
  $("#schedule-trends").checked = schedule ? !!schedule.use_trends : profile.preset === "news";
  $("#schedule-enabled").checked = schedule ? !!schedule.enabled : true;
  $("#save-schedule").textContent = schedule ? "Save schedule" : "Create schedule";
  syncScheduleOutput();
  $("#schedule-dialog").showModal();
  $("#schedule-name").focus();
}

$("#schedule-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (scheduleSavePending) return;
  const timezone = $("#schedule-timezone").value.trim(), cron = $("#schedule-cron").value.trim().replace(/\s+/g, " ");
  if (cron.split(" ").length !== 5) return setScheduleError("Enter a five-field cron expression, for example 0 9 * * *. ");
  try { new Intl.DateTimeFormat(undefined, { timeZone: timezone }).format(); }
  catch (_) { return setScheduleError("Choose an IANA time zone, for example Asia/Dubai or Europe/London."); }
  const body = {
    profile_id: editingScheduleProfileId,
    name: $("#schedule-name").value.trim(), topic: $("#schedule-topic").value.trim(),
    cron, timezone, video_type: $("#schedule-format").value, visual_mode: $("#schedule-visual-mode").value,
    preview_only: $("#schedule-visual-mode").value !== "classic" && $("#schedule-output").value === "draft",
    enabled: $("#schedule-enabled").checked, use_trends: $("#schedule-trends").checked,
  };
  if (!body.name || !body.topic) return setScheduleError("Add a schedule name and a topic or recurring brief.");
  scheduleSavePending = true; busy($("#save-schedule"), true, "Saving…"); setScheduleError(""); updateScheduleControls();
  try {
    await api(editingScheduleId ? `/api/schedules/${encodeURIComponent(editingScheduleId)}` : "/api/schedules", body, editingScheduleId ? "PATCH" : "POST");
    $("#schedule-dialog").close();
    await loadSchedules();
    toast(body.enabled ? "Schedule saved. Keep YAAS running for jobs to start." : "Schedule saved and paused.", "ok");
  } catch (error) { setScheduleError(error.message); }
  finally { scheduleSavePending = false; busy($("#save-schedule"), false); updateScheduleControls(); }
});

$("#schedule-list").addEventListener("click", async event => {
  const button = event.target.closest("button[data-schedule-action]");
  if (!button) return;
  const card = button.closest("[data-schedule-id]"), id = card?.dataset.scheduleId;
  const schedule = channelSchedules.find(item => item.id === id), action = button.dataset.scheduleAction;
  if (!schedule || pendingScheduleActions.has(id)) return;
  if (action === "edit") return openScheduleEditor(id);
  if (action === "open") return openProject(button.dataset.projectId);
  if (action === "delete" && button.dataset.confirm !== "1") {
    button.dataset.confirm = "1"; button.textContent = "Confirm delete";
    setTimeout(() => { if (button.isConnected) { button.dataset.confirm = ""; button.textContent = "Delete"; } }, 5000);
    return;
  }
  pendingScheduleActions.add(id); updateScheduleControls();
  try {
    if (action === "toggle") await api(`/api/schedules/${encodeURIComponent(id)}`, { enabled: !schedule.enabled }, "PATCH");
    else if (action === "delete") await api(`/api/schedules/${encodeURIComponent(id)}`, undefined, "DELETE");
    else if (action === "run") {
      await api(`/api/schedules/${encodeURIComponent(id)}/run`, {});
      toast(`Queued a video for ${profileName(schedule.profile_id)}.`, "ok");
      pollOnce();
    }
    await loadSchedules();
  } catch (error) { toast(error.message, "err"); }
  finally { pendingScheduleActions.delete(id); updateScheduleControls(); }
});

$("#add-schedule").onclick = () => openScheduleEditor();
$("#schedule-preset").onchange = event => { if (event.target.value !== "custom") $("#schedule-cron").value = event.target.value; };
$("#schedule-cron").oninput = () => $("#schedule-preset").value = "custom";
$("#schedule-visual-mode").onchange = syncScheduleOutput;
$("#schedule-output").onchange = syncScheduleOutput;
$("#schedule-dialog").addEventListener("cancel", event => { if (scheduleSavePending) event.preventDefault(); });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (!location.hash || location.hash === "#home")) { loadSchedules(); loadChannels(); }
});
