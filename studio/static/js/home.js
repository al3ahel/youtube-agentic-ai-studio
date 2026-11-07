/* ═══════════════════════════ home: trending ideas ═══════════════════════════ */
const SRC_COLORS = { google: "#5cc8ff", wikipedia: "#f3f3f3", hackernews: "#ff9a3d", spaceflight: "#a98bff", arxiv: "#38ffb3" };
const SRC_NAMES = { google: "Searches", wikipedia: "Wikipedia", hackernews: "Tech", spaceflight: "Space", arxiv: "Science papers" };
let trends = null, trendSrc = "all";
async function loadTrends(refresh) {
  $("#trend-refresh").classList.add("spin");
  try { trends = await api("/api/trends" + (refresh ? "?refresh=1" : "")); renderTrends(); }
  catch (e) { $("#trend-list").innerHTML = `<div class="empty" style="grid-column:1/-1">Couldn't load trends. Check your internet connection.</div>`; }
  $("#trend-refresh").classList.remove("spin");
}
function renderTrends() {
  const ok = Object.entries(trends.sources).filter(([, s]) => s.ok && s.items.length);
  $("#src-filter").innerHTML = `<span class="chip ${trendSrc === "all" ? "on" : ""}" data-k="all">All</span>` +
    ok.map(([k, s]) => `<span class="chip ${trendSrc === k ? "on" : ""}" data-k="${k}"><i style="background:${SRC_COLORS[k]}"></i>${SRC_NAMES[k] || s.label}</span>`).join("");
  $$("#src-filter .chip").forEach(c => c.onclick = () => { trendSrc = c.dataset.k; renderTrends(); });
  const lists = ok.filter(([k]) => trendSrc === "all" || trendSrc === k).map(([k, s]) => s.items.map(it => ({ ...it, k })));
  const items = [];
  for (let i = 0; lists.some(l => l[i]); i++) lists.forEach(l => l[i] && items.push(l[i]));
  $("#trend-list").innerHTML = items.slice(0, trendSrc === "all" ? 14 : 20).map(it =>
    `<button class="trend-item" data-title="${esc(it.title)}" title="${esc(it.detail || it.title)}"><i style="background:${SRC_COLORS[it.k]}"></i><span>${esc(it.title)}</span><em>Use →</em></button>`).join("")
    || `<div class="empty" style="grid-column:1/-1">Nothing here right now.</div>`;
  $$("#trend-list .trend-item").forEach(el => el.onclick = () => useTopic(el.dataset.title));
  const age = Math.round((Date.now() / 1000 - trends.fetched_at) / 60);
  $("#trend-age").textContent = age < 1 ? "just updated" : `updated ${age} min ago`;
}
function useTopic(title) {
  if (heroMode === "running") return toast("A video is already being made. Wait for it to finish");
  showNewVideoForm();
  $("#topic-input").value = title;
  window.scrollTo({ top: 0, behavior: "smooth" });
  setTimeout(() => { $("#topic-input").focus(); $("#topic-input").select(); }, 350);
  toast("Topic added. Edit it if you like, then press Make video");
}
$("#trend-refresh").onclick = () => loadTrends(true);

/* ═══════════════════════════ home: setup, services, stats, latest ═══════════════════════════ */
let providers = [], settings = {}, library = null;
const SETUP_URL = "https://github.com/raunakpatil/youtube-agentic-ai-studio/blob/main/SETUP.md";
function renderChecklist() {
  if (!providers.length) return;
  const P = id => providers.find(p => p.id === id) || {};
  const gemini = P("gemini").ready, backup = P("groq").ready || P("openrouter").ready;
  const named = settings.channel_name && settings.channel_name !== "My AI Channel";
  const mode = $("#visual-mode").value || settings.visual_mode || "motion";
  const items = [
    { ok: true, t: "Voice service", s: "Free Edge TTS. Local setup is checked before recording" },
    { ok: true, t: "Visual style", s: mode === "motion" ? "Animated diagrams, comparisons and text" : mode === "hybrid" ? "Photos, motion scenes and licensed footage" : P("pexels").ready ? "Pexels + free image sources" : "Classic photos and transitions" },
    { ok: gemini || backup, opt: true, t: "Script writer", s: gemini ? "Gemini connected" : backup ? "Backup AI connected" : "Free backup AI works. A free Gemini key writes better scripts", act: !gemini && ["Add key", "#settings"] },
    { ok: named, opt: true, t: "Channel name", s: named ? settings.channel_name : "Shown in your videos. Set it once", act: !named && ["Set name", "#settings"] },
    { ok: P("youtube_upload").ready, t: "Publishing", s: P("youtube_upload").ready ? "Ready to upload" : "Only needed to upload from here. You can always download the MP4", act: !P("youtube_upload").ready && ["Guide", SETUP_URL] },
  ];
  const n = items.filter(i => i.ok).length;
  $("#ready-num").textContent = `${n}/${items.length}`;
  $("#ready-text").textContent = "service options configured. Check local setup below";
  $("#checklist").innerHTML = items.map(i => `
    <div class="ck ${i.ok ? "ok" : i.opt ? "opt" : ""}"><span class="ic">${i.ok ? "✓" : i.opt ? "!" : "○"}</span>
      <div><b>${i.t}</b><small>${esc(i.s)}</small></div>
      ${i.act ? `<a class="btn btn-ghost btn-xs" href="${i.act[1]}" ${i.act[1].startsWith("http") ? 'target="_blank" rel="noopener"' : ""}>${i.act[0]}</a>` : ""}</div>`).join("");
}
async function loadHealth(probe) {
  const btn = $("#api-probe");
  if (probe) { busy(btn, true, "Testing…"); $("#api-details").open = true; }
  try {
    providers = (await api("/api/health" + (probe ? "?probe=1" : ""))).providers;
    const ready = providers.filter(p => p.ready && !(p.probe && !p.probe.ok)).length;
    $("#api-count").textContent = `${ready} of ${providers.length} on`;
    const groups = [...new Set(providers.map(p => p.group))];
    const GROUP = { AI: "Writing", Media: "Voice & visuals", Research: "Research & ideas", Growth: "Growth & publishing" };
    $("#api-grid").innerHTML = groups.map(g => `<div class="api-group">${GROUP[g] || g}</div>` +
      providers.filter(p => p.group === g).map(p => {
        const down = p.probe && !p.probe.ok;
        const meta = down ? "offline" : p.probe ? `${p.probe.ms} ms` : p.keyless ? "free" : p.ready ? "connected" : "needs key";
        return `<div class="api ${down ? "down" : p.ready ? "ready" : ""}" title="${esc(p.probe && p.probe.error || p.role)}"><i></i><div><b>${esc(p.name)}</b><small>${esc(p.role)}</small></div>${!p.ready && !p.keyless ? `<a class="tag amber" href="#settings">${meta}</a>` : `<span class="tag ${down ? "pink" : p.keyless ? "green" : ""}">${meta}</span>`}</div>`;
      }).join("")).join("");
    renderChecklist();
  } catch (e) { }
  if (probe) { busy(btn, false); toast("Connection test finished"); }
}
$("#api-probe").onclick = () => loadHealth(true);

async function loadChannel() {
  const lib = library || { totals: {}, activity: [] };
  const profileId = typeof activeProfileId !== "undefined" ? activeProfileId : null;
  const ch = await api("/api/channel-stats" + (profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "")).catch(() => null);
  if (typeof activeProfileId !== "undefined" && profileId !== activeProfileId) return;
  if (ch && ch.ok) {
    const savedChannel = ch.source === "saved_channel";
    const channelTotal = value => value == null ? "—" : fmt(value);
    $("#pulse-src").textContent = `${ch.title || "YouTube channel"}${savedChannel ? " · saved totals" : ""}`;
    $("#pulse-stats").innerHTML = [[channelTotal(ch.subscribers), "subscribers"], [channelTotal(ch.views), "total views"], [channelTotal(ch.videos), "videos on YouTube"], [lib.totals.videos || 0, "made with YAAS"]]
      .map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    if (savedChannel) {
      const activity = lib.activity || [];
      drawBars($("#pulse-bars"), activity.map(a => a.count), activity.map(a => `${a.day}: ${a.count} video${a.count === 1 ? "" : "s"} made with YAAS`));
      $("#pulse-foot").innerHTML = `<span>local videos · last 60 days</span><span>today</span>`;
      const fetched = ch.fetched_at ? new Date(typeof ch.fetched_at === "number" && ch.fetched_at < 1e12 ? ch.fetched_at * 1000 : ch.fetched_at) : null;
      const fetchedNote = fetched && Number.isFinite(fetched.getTime()) ? `Saved channel totals fetched ${fetched.toLocaleString()}.` : "Saved channel totals from your verified connection.";
      const refreshNote = activeChannelProfile()?.token_present ? "Use Refresh above to update them." : "Connect this channel above to update these totals.";
      $("#pulse-hint").textContent = [ch.warning, fetchedNote, refreshNote, "Bars show local video production; — means a count was not supplied."].filter(Boolean).join(" ");
    } else {
      const recent = (ch.recent || []).slice(-30);
      drawBars($("#pulse-bars"), recent.map(v => v.views), recent.map(v => `${fmt(v.views)} views · ${v.title.slice(0, 36)}`));
      $("#pulse-foot").innerHTML = `<span>views per video</span><span>newest →</span>`;
      $("#pulse-hint").textContent = "";
    }
  } else {
    $("#pulse-src").textContent = "";
    $("#pulse-stats").innerHTML = [[lib.totals.videos || 0, "videos made"], [lib.totals.uploaded || 0, "published"], [lib.totals.shorts || 0, "shorts"], [lib.totals.minutes || 0, "minutes of video"]]
      .map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    drawBars($("#pulse-bars"), lib.activity.map(a => a.count), lib.activity.map(a => `${a.day}: ${a.count} video${a.count === 1 ? "" : "s"}`));
    $("#pulse-foot").innerHTML = `<span>last 60 days</span><span>today</span>`;
    $("#pulse-hint").innerHTML = ch && ch.configured ? `Couldn't reach YouTube: ${esc(ch.error || "")}` : `Connect this channel to YouTube above to fetch its identity and statistics.`;
  }
}
function renderLatest() {
  const m = library && library.items[0];
  if (!m) { $("#latest").innerHTML = `<div class="empty" style="min-height:110px">Your videos will show up here.</div>`; return; }
  $("#latest").innerHTML = `
    <div class="thumb-frame ${m.video_type === "shorts" ? "tall" : ""}">${m.files.thumb ? `<img src="/library/${m.id}/thumbnail.jpg?t=${Date.now()}" alt="">` : ""}<span class="badge tag ${m.yt_url ? "green" : m.preview_only ? "amber" : ""}">${m.yt_url ? "published" : m.preview_only ? "draft" : m.video_type === "shorts" ? "short · not published" : "not published"}</span></div>
    <div style="margin-top:10px"><div class="vid-title">${esc(m.title)}</div><div class="vid-meta"><span>${ago(m.created_at)}</span>${m.duration ? `<span>· ${clock(m.duration)}</span>` : ""}</div></div>
    <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap"><button class="btn btn-white btn-sm" onclick="openProject('${m.id}')">${m.preview_only ? "Review draft" : "Review &amp; publish"}</button>${m.yt_url ? `<a class="btn btn-ghost btn-sm" href="${esc(m.yt_url)}" target="_blank" rel="noopener">Watch on YouTube ↗</a>` : ""}</div>`;
}
