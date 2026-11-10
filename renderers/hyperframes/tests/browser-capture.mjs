// Pixel comparisons need the same raster path before and after a paused seek.
// Chromium's partial-tile reuse can round geometry/shadow edges differently;
// LCD text can acquire colored edge pixels when a scene re-enters the paint tree.
// Full raster and grayscale text retain the actual DOM, geometry and timeline,
// and let the existing exact/tightly bounded pixel assertions detect changes.
// These flags belong only to the browser-test harness, not production rendering.
export function captureBrowserArgs() {
  const extra = process.env.YAAS_TEST_CHROME_ARGS ? JSON.parse(process.env.YAAS_TEST_CHROME_ARGS) : [];
  return [...new Set([...extra, "--disable-partial-raster", "--disable-lcd-text"])];
}
