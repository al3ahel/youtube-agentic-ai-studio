# YAAS motion renderer

This directory contains the **pinned HyperFrames 0.9.19 renderer** and fixed
HTML/SVG/GSAP/Three.js templates. Python supplies a validated storyboard, narration and
downloaded footage. The browser receives data, not generated JavaScript.

## Install once

Use Node.js **22 or newer**, plus FFmpeg and FFprobe on `PATH`. In this directory:

```powershell
npm ci
node node_modules/hyperframes/bin/hyperframes.mjs browser ensure
```

The lockfile pins the dependency tree. Initial installation and the browser
download need network access. If a browser download is blocked, an installed
Chrome can be selected explicitly, for example on Windows:

```powershell
$env:HYPERFRAMES_BROWSER_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

Use the path that actually exists on your computer. Node, FFmpeg, browser and
filesystem permissions are checked separately; administrator mode is not a
normal setup requirement. `npm run doctor` invokes HyperFrames diagnostics.

The pinned HyperFrames compiler may also try to cache supplemental fallback font
faces on its first `check` or render. This can delay a cold first encode when that
network is unavailable. YAAS's prepared composition uses copied local font files;
the isolated browser preview does not fetch remote fonts or media. Compiler font
discovery and the preview's network boundary are separate behaviors.

## Offline smoke video

Run in the project's Python environment from the repository root:

```powershell
python renderers/hyperframes/smoke.py
python renderers/hyperframes/smoke.py --portrait
```

Each command renders all seven templates at a small test resolution. The fixture
uses a synthetic tone soundtrack, clearly labelled synthetic chart values, and
a local FFmpeg test pattern. It does not call an LLM, stock service or TTS API.
Generated projects and MP4s are under this directory's ignored `.smoke/` folder.
`--prepare-only` prepares the same project without encoding an MP4.

After preparation, a local HTTP server can preview the generated project:

```powershell
python -m http.server 8766 --bind 127.0.0.1 --directory renderers/hyperframes/.smoke/landscape/motion
```

Open `http://localhost:8766/index.html?preview=1`. It starts paused. Its Play,
Pause and seek controls use the narration clock. The clean `index.html` URL is
the render composition. YAAS's normal application serves the same files inside
its isolated preview iframe; no separate server is needed there.

## Python contract

```python
from video.renderers.html import prepare_motion_project, render_motion_video

entry = prepare_motion_project(scene_plan, audio_path, output_dir)
video_path = render_motion_video(
    script, audio_path, output_dir, scene_plan,
    video_type="normal", preview=False,
    stop_event=stop_event, progress=print,
)
```

`prepare_motion_project` returns `output_dir/motion/index.html` and writes
`project.json`, `scene.js`, `objects.js`, `scene.css`, `preview.js`, local GSAP, Inter font files
and copied media, plus `shots.js`, `shots.css` and local Three.js modules for
shot compositions. It does not mutate the input plan. Text is inserted with
`textContent`; the embedded JSON escapes HTML delimiters. No remote asset URL is
loaded by the composition. Footage must already exist under the job directory
or configured output directory. Credits and source URLs remain in the manifest.

Templates: `flow`, `comparison`, `timeline`, `chart`, `explain`, `text`, `footage`.
Both 16:9 and 9:16 use dedicated layouts. The chart template requires a supplied
finite numeric value on every item. It does not infer numbers from narration.
The caller remains responsible for grounding those supplied values in evidence.

Scenes with `graphics_version: 1` use the bounded qualitative illustrated subjects
in `objects.js`. An item `symbol` is selected from a fixed enum and must be grounded
by the director in the section's narration. Actors, progressive paths and labels
use the same paused timeline, including arbitrary reverse seeks. Missing versions
retain legacy card layouts; charts and footage keep their existing semantics.

Actor-based `graphics_version: 2` scenes without shot-specific rendering use
`worlds.js` and `worlds.css`; generic 2D shot builders also delegate to these modules.
Their validated `staging` data selects a supported world/action/camera; one to four actor items
have a single subject and optional support/context roles. The film's validated
creative brief supplies palette tokens, with optional per-scene overrides.
All actor poses and camera movement use the same seekable GSAP clock. The normal
planner fixture is `directed_demo.py`; `objects_demo.py` explicitly preserves
version-1 regression coverage. [Current shot pipeline](../../docs/SHOT_PIPELINE_UPDATE.md).

Scenes with versioned `shot` direction use `shots.js`: individual framing,
transitions, multi-picture collage, footage composition and staged 2D or 3D
mechanisms. A `diagram3d` shot requires a validated source-grounded `mechanism`.
It is rendered with actual local Three.js geometry on the same seekable clock.
`media_layers` contains two to four distinct trusted local pictures; it cannot
contain model-provided paths. `shot_smoke.py` exercises these renderers in both
orientations. `comparable_demo.py` runs the strict narrated reference-comparison
pipeline and writes a blocked or incomplete report when applied model direction,
sound, output checks or semantic review do not satisfy its gates.

For the separate 14-second illustrated example, run `objects_demo.py` in both
orientations, then `objects_sound_demo.py --input <output-directory>` to add real
licensed recordings. The example has authored phrase cues and no narration; its
listening balance is explicitly separate from production speech-mix ratios. See
[sample reproduction and credits](../../docs/samples/README.md).

Each scene supports up to six items. Beat times are scene-local seconds. Targets
are `title`, `scene`/`all`, and `item-0` through `item-5`; explicit item IDs and
labels are also accepted. Actions are `reveal`, `highlight`, `connect`, `draw`
and `count` (`emphasize` aliases highlight). Connect/draw target graph paths,
timeline rules and chart bars; count animates numeric chart labels. Media timing
is handled by HyperFrames, independently of the browser-only preview controller.

The frame renderer uses a paused GSAP timeline registered as `yaas-motion`.
`requestAnimationFrame` is used only in `preview.js`, which is not loaded by a
render. Preview functions are exposed as `window.YAASMotion.play()`, `pause()`
and `seek(seconds)`; `window.YAASMotionReady` resolves once fonts and the timeline
are ready. The preview can be sandboxed with `allow-scripts` alone; its font and
media responses must permit the iframe's opaque origin.
The isolated preview accepts only parent-window messages of the form
`{type: "yaas:seek", time: 1.25}` and announces `{type: "yaas:ready"}` to its parent
after setup; the parent must verify the sending frame before handling that event.

`render_motion_video` calls the installed CLI using an argument list and known
working directory. It uses software browser capture, one worker by default,
SDR H.264/AAC MP4, and local audio mixing. It probes narration and footage first,
rejects a materially mismatched narration duration or short footage, and checks
the output's dimensions, frame rate, duration and audio stream. A new final MP4
replaces the old one only after successful verification. Failures leave a
diagnostic `motion/render.log`; cancellation raises `MotionRenderCancelled`, a
subclass of `InterruptedError`, and terminates the render process tree.

Supported optional configuration defaults:

| Setting | Default |
| --- | --- |
| `MOTION_NODE_BINARY` | `node` |
| `MOTION_QUALITY` | `standard` |
| `MOTION_WORKERS` | `1` (range 1–4) |
| `MOTION_RENDER_TIMEOUT_SECONDS` | `3600` (range 30–86400) |

Preview renders use `draft` quality and write `motion_preview.mp4`. Final outputs
are `final_video.mp4` or `final_short.mp4`. Resolution and fps come from the
storyboard. Raising either increases work; the smoke fixture is a correctness
check, not a performance guarantee for full-resolution production videos.

## Verification

From the repository root:

```powershell
python -m pytest tests/test_html_renderer.py -q
```

For browser layout, arbitrary seek, captions and paused-preview checks, select
an installed browser and run from this directory:

```powershell
$env:YAAS_TEST_BROWSER = "C:\Program Files\Google\Chrome\Application\chrome.exe"
npm test
```

`YAAS_TEST_CHROME_ARGS` optionally accepts a JSON array of browser flags for a
constrained test container. Those flags are never applied to the application.
For Linux containers that require a sandbox override, keep it confined to the
test environment. Browser tests skip with an explanation when no test browser
is selected. Test screenshots are written under `.smoke/screenshots/`.
Random-seek tests require identical computed geometry, content and timing. The
pixel check permits Chromium's antialiased edge repaint rounding up to 8/255 on
at most 0.5% of pixels, rather than claiming every browser repaint is byte-identical.

## Licenses and sources

- [HyperFrames](https://github.com/heygen-com/hyperframes): Apache-2.0.
- [GSAP](https://gsap.com/standard-license/): Standard No Charge License. Its
  restrictions still apply; it is not an MIT dependency.
- [Inter](https://github.com/rsms/inter): SIL Open Font License 1.1. The font
  license is copied with each generated project.
- [Three.js](https://github.com/mrdoob/three.js), pinned at 0.187.0: MIT. The local
  module dependencies and license are copied with each generated project.
- The implementation uses the installed CLI's `render --help`, its audio/data
  attribute documentation, and the official
  [GSAP guide](https://hyperframes.heygen.com/guides/gsap-animation) and
  [rendering guide](https://hyperframes.heygen.com/guides/rendering).

Pinning the software and local fonts makes a frame reproducible within the
chosen environment. Different browser versions or operating systems can still
produce small text rasterization differences; byte-identical cross-platform
MP4s are not promised.
