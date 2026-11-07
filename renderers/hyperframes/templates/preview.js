/* Browser-only playback. This file is not loaded by the frame renderer.
 * Its wall clock and media controls never drive HyperFrames' render timeline. */
(() => {
  "use strict";
  const api = window.YAASMotion;
  const plan = JSON.parse(document.getElementById("scene-plan").textContent);
  const timeline = window.__timelines["yaas-motion"];
  const audio = document.getElementById("narration");
  const videos = [...document.querySelectorAll("video")];
  const root = document.getElementById("composition");
  const shell = document.getElementById("preview-shell");
  const controls = document.getElementById("preview-controls");
  const playButton = document.getElementById("preview-play");
  const seekInput = document.getElementById("preview-seek");
  const timeLabel = document.getElementById("preview-time");
  const status = document.getElementById("preview-status");
  let playing = false, animationFrame = 0, startedAt = 0, startedTime = 0;

  function fit() {
    const scale = Math.min(shell.clientWidth / plan.width, shell.clientHeight / plan.height);
    root.style.transform = `scale(${Math.max(0.01, scale)})`;
    root.style.left = `${(shell.clientWidth - plan.width * scale) / 2}px`;
    root.style.top = `${(shell.clientHeight - plan.height * scale) / 2}px`;
  }

  function format(seconds) {
    return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
  }

  function update(time) {
    seekInput.value = String(time);
    timeLabel.textContent = `${format(time)} / ${format(plan.duration)}`;
    playButton.textContent = playing ? "Pause" : "Play";
  }

  function syncVideos(time, seek = false) {
    videos.forEach(video => {
      const start = Number(video.dataset.start), duration = Number(video.dataset.duration);
      const active = time >= start && time < start + duration;
      const desired = Number(video.dataset.mediaStart || 0) + Math.max(0, time - start);
      if (active) {
        if (seek || Math.abs(video.currentTime - desired) > .25) video.currentTime = desired;
        if (playing && video.paused) video.play().catch(() => {});
      } else if (!video.paused) video.pause();
    });
  }

  function pause() {
    playing = false;
    cancelAnimationFrame(animationFrame);
    timeline.pause();
    if (audio) audio.pause();
    videos.forEach(video => video.pause());
    update(timeline.time());
  }

  function seek(time) {
    const value = Math.max(0, Math.min(plan.duration, Number(time)));
    timeline.pause().seek(value, false);
    if (audio) audio.currentTime = value;
    startedAt = performance.now();
    startedTime = value;
    syncVideos(value, true);
    update(value);
    return value;
  }

  function tick(now) {
    if (!playing) return;
    const time = audio ? audio.currentTime : startedTime + (now - startedAt) / 1000;
    const frameTime = Math.min(plan.duration, Math.floor(time * plan.fps) / plan.fps);
    timeline.pause().seek(frameTime, false);
    syncVideos(time);
    update(time);
    if (time >= plan.duration || (audio && audio.ended)) { pause(); return; }
    animationFrame = requestAnimationFrame(tick);
  }

  async function play() {
    if (playing) return;
    status.textContent = "";
    if (timeline.time() >= plan.duration - 1 / plan.fps) seek(0);
    startedAt = performance.now();
    startedTime = timeline.time();
    if (audio) {
      audio.currentTime = startedTime;
      try { await audio.play(); }
      catch (error) { status.textContent = "Click Play to enable narration"; pause(); throw error; }
    }
    playing = true;
    syncVideos(startedTime, true);
    update(startedTime);
    animationFrame = requestAnimationFrame(tick);
  }

  document.body.classList.add("preview-mode");
  controls.hidden = false;
  seekInput.step = String(1 / plan.fps);
  playButton.addEventListener("click", () => { if (playing) pause(); else play().catch(() => {}); });
  seekInput.addEventListener("input", () => seek(seekInput.value));
  window.addEventListener("resize", fit);
  window.addEventListener("pagehide", pause);
  window.addEventListener("message", event => {
    if (event.source !== window.parent || !event.data || event.data.type !== "yaas:seek") return;
    if (typeof event.data.time === "number" && Number.isFinite(event.data.time)) seek(event.data.time);
  });
  window.YAASMotionPreview = {play, pause, seek};
  // No autoplay: the first sound and frame advancement require a user action.
  fit();
  seek(Math.min(.35, plan.duration / 10));
  if (window.parent !== window) window.parent.postMessage({type: "yaas:ready"}, "*");
})();
