/* The running process and the checkout can differ after an update. */
let buildRequestSerial = 0, buildStatus = null;
const restartHelp = "In the terminal running YAAS, press Ctrl+C, then restart gui.py from the updated project using the same Python environment as your original launch command. You can still inspect the project or stop a running job.";

function setBuildWarning(title, message, information = false) {
  const warning = $("#build-warning");
  warning.hidden = !message;
  warning.classList.toggle("info", information);
  $("#build-warning-title").textContent = title;
  $("#build-warning-message").textContent = message || "";
}

function showRestartRequired(message) {
  $("#build-info").classList.add("needs-restart");
  $("#build-label").textContent = "Restart needed";
  setBuildWarning("Restart YAAS to finish updating", message || restartHelp);
  if ($("#build-dialog").open) $("#build-dialog-status").textContent = message || restartHelp;
}

function renderBuildDetails(data) {
  const running = data.running || {}, disk = data.disk || {}, diagnostics = data.diagnostics;
  $("#build-dialog-status").textContent = data.message || "Build information is unavailable.";
  if (!diagnostics) return;
  const rows = [
    ["Running commit", running.commit || "Unversioned checkout"],
    ["Running branch", running.branch || "Detached or unversioned"],
    ["Local code changes", running.runtime_modified === true ? "Yes — executable files differ from the commit" : running.runtime_modified === false ? "No" : "Not available without Git"],
    ["Started", running.started_at || "Unavailable"],
    ["Checkout commit", disk.commit || "Unversioned checkout"],
    ["Running code digest", running.code_digest || "Unavailable"],
    ["Checkout code digest", disk.code_digest || "Unavailable"],
    ["Project directory", diagnostics.project_dir || "Unavailable"],
    ["Python executable", diagnostics.python_executable || "Unavailable"],
    ["Python version", diagnostics.python_version || "Unavailable"],
    ["Process ID", diagnostics.pid ?? "Unavailable"],
  ];
  $("#build-details").innerHTML = rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("");
}

function renderBuildStatus(data) {
  buildStatus = data;
  const running = data.running || {};
  const identity = running.short_commit || `Unversioned${running.code_digest ? " " + running.code_digest.slice(0, 8) : ""}`;
  $("#build-label").textContent = `${identity}${running.runtime_modified ? " + local changes" : ""}`;
  $("#build-info").title = `Running YAAS ${identity}${running.runtime_modified ? " with local executable code changes" : ""}. Show version details.`;
  $("#build-info").classList.toggle("needs-restart", !!data.restart_required);
  if (data.restart_required) setBuildWarning("Restart YAAS to finish updating", data.message || restartHelp);
  else if (data.status === "unavailable") setBuildWarning("Build check unavailable", data.message || "The running files could not be verified. Open version details to check the project directory.");
  else if (data.checkout_changed) setBuildWarning("Checkout updated", "The running Python code matches the files on disk. Reload this page for any updated browser assets; version details show both commits.", true);
  else setBuildWarning("", "");
  renderBuildDetails(data);
}

async function loadBuildStatus({diagnostics = false} = {}) {
  const serial = ++buildRequestSerial;
  try {
    const response = await fetch(`/api/build${diagnostics ? "?diagnostics=1" : ""}`, {cache: "no-store"});
    if (serial !== buildRequestSerial) return null;
    if (response.status === 404) {
      const message = `This running server does not have the updated version check. ${restartHelp}`;
      showRestartRequired(message);
      $("#build-dialog-status").textContent = message;
      $("#build-details").replaceChildren();
      return null;
    }
    if (!response.ok) throw new Error(`Build check returned ${response.status}.`);
    const data = await response.json();
    if (serial !== buildRequestSerial) return null;
    if (!data.ok || !data.running || !data.disk) throw new Error("The server returned incomplete build information.");
    renderBuildStatus(data);
    return data;
  } catch (error) {
    if (serial !== buildRequestSerial) return null;
    const message = "Could not check the running version. Check that this local studio is running, then refresh status.";
    $("#build-label").textContent = "Build unavailable";
    setBuildWarning("Build check unavailable", message, true);
    $("#build-dialog-status").textContent = message;
    return null;
  }
}

async function openBuildDetails() {
  $("#build-dialog").showModal();
  $("#build-dialog-status").textContent = "Checking the running server…";
  await loadBuildStatus({diagnostics: true});
}
$("#build-info").onclick = $("#build-warning-details").onclick = openBuildDetails;
$("#build-refresh").onclick = () => loadBuildStatus({diagnostics: true});
$("#build-reload").onclick = () => {
  if ((typeof settingsDirty === "function" && settingsDirty()) || (typeof metaDirty === "function" && metaDirty())) {
    $("#build-dialog-status").textContent = "Save or undo your unsaved Settings or Review changes before reloading this page.";
    return;
  }
  location.reload();
};
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadBuildStatus({diagnostics: $("#build-dialog").open});
});
setInterval(() => { if (!document.hidden) loadBuildStatus({diagnostics: $("#build-dialog").open}); }, 30000);
