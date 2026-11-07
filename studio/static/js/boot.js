/* ═══════════════════════════ boot ═══════════════════════════ */
Object.assign(loaders, {
  home: () => { if (channelProfilesLoaded) { loadLibrary(); loadSchedules(); } },
  review: loadReview,
  library: () => { if (channelProfilesLoaded) loadLibrary(); },
  settings: loadSettings,
});
setHero("form");
route();
pollStatus();
loadTrends();
loadChannels().then(refreshChannelWorkspace).catch(error => toast(error.message, "err"));
document.fonts && document.fonts.ready.then(moveIndicator);
