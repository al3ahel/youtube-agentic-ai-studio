# Motion Studio

This fork adds a narration-driven video path to YAAS. The Python app researches,
writes and records a script. A visual director produces a constrained JSON
storyboard. Fixed HTML/SVG templates animate that storyboard on a paused GSAP
timeline, and the pinned HyperFrames CLI captures it to an MP4 with narration.

## Choose a mode

| Mode | Visuals | Requirements |
| --- | --- | --- |
| Motion (default) | Flow diagrams, comparisons, timelines, supplied-data charts, explainer cards and kinetic text | Node 22+, FFmpeg/ffprobe, installed renderer/browser |
| Hybrid | Motion scenes plus downloaded Pexels video clips with source credits | Same as Motion; optional Pexels key |
| Classic | Existing photo zooms, captions and transitions | Original MoviePy/NumPy dependencies |

Hybrid falls back to animated scenes when footage is unavailable. Motion does not
download a series of still photographs. All three modes retain the existing title,
description, thumbnail and YouTube publishing tools.

## Workflow

1. Follow the [quick start](../README.md#-quick-start), then click **Check setup**.
2. Choose Motion or Hybrid and **Draft preview**. Enter a topic and make a video.
3. Open **Review → Storyboard**. Play or scrub the local HTML preview and inspect
   the scenes alongside their visual goals.
4. Select a scene, enter a change such as "use a comparison" or "less text", and
   regenerate it. This preserves narration and the measured scene boundaries.
5. Render a draft to check the MP4, or choose **Render final**. These actions reuse
   the storyboard and voiceover; they do not repeat the research or planning calls.
6. Final renders are archived. Reopening one restores its audio, timeline,
   storyboard, local media, credits, preview and optional review result.

Drafts are separate from final videos and cannot be uploaded as a final render.
Changing a scene invalidates the previous final output. A render-only run preserves
the prepared soundtrack, even if global music settings have since changed; select
the Music step to apply new music settings. Changing spoken script text requires
new narration and visual planning.

## How synchronization works

Narration is recorded section by section and decoded to 48 kHz mono PCM. The app
joins the actual samples and saves a WAV, SRT and `narration.timings.json` manifest.
Returned word events are preserved. If the speech service omits them, captions
use phrase events or an explicitly estimated fallback within each measured section.

`video/timeline.py` converts absolute audio positions to frame boundaries. It
rounds cumulative positions rather than repeatedly rounding durations, and covers
the final partial frame. A hash of the audio and timeline rejects stale combinations
of speech, captions and storyboard. No transition shortens the measured clock.
Chapters are emitted only from matching measured section timing.

Motion beats anchor to spoken labels when matching word events exist; otherwise
the plan records distributed or mixed timing. Section synchronization does not
imply every individual diagram reveal is perfectly aligned to a spoken word.

## Templates and data contract

The seven supported templates are `flow`, `comparison`, `timeline`, `chart`,
`explain`, `text` and `footage`. Layouts support 16:9 and 9:16. Scene data contains
an ID, section ID, start, duration, title, visual goal, up to six short items,
local-time motion beats and optional downloaded video metadata.

The planner returns JSON, never executable HTML or JavaScript. The code templates
live in `renderers/hyperframes/templates`. Text is escaped, fonts/scripts are
copied locally and the UI preview runs in an opaque-origin sandbox with network
connections disabled. The preview cannot call the studio's settings or upload APIs.

Charts require numeric values explicitly provided as a section's `chart_data` or
`data_points`; the planner does not invent statistics to make an animation. Hybrid
footage comes through the bounded download adapter, with an attribution manifest
and no arbitrary remote URLs in generated scenes.

## Free OpenRouter use

`openrouter/free` is the default planner and reviewer. A chosen model ID is checked
against the live catalogue for zero pricing and appropriate capabilities. Planning
requires schema support; reviewing frames also requires image input. Explicit paid
models are rejected, including a paid sibling of a model whose free ID disappeared.
OpenRouter text fallback uses the same free-only validation.

Without a key, planning uses deterministic local templates. Provider errors,
rate limits and invalid model output fall back to those templates. Attempts and
local request budgets are bounded. No free generative image/video service is
promised by this integration: the model chooses an animation plan, and local code
renders it. Provider availability and quotas remain external constraints.

Frame review is off by default. Enabling it sends up to four sampled rendered
frames to the selected free vision model. Its layout findings are advisory; the
local MP4 checks independently verify dimensions, duration and presence of audio.

## Important files

| File / directory | Purpose |
| --- | --- |
| `video/narrator.py`, `video/timeline.py` | Measured voiceover, word events and shared clock |
| `agents/visual_director.py` | Validated local/AI scene planning and scene regeneration |
| `apis/openrouter.py`, `apis/stock_video.py` | Free-only model access and stock video |
| `video/renderers/html.py` | Safe project preparation, subprocess lifecycle, MP4 verification |
| `renderers/hyperframes/` | Pinned npm project, templates and offline smoke fixture |
| `studio/preflight.py`, `studio/visuals.py` | Setup checks and storyboard/preview endpoints |
| `output/scene_plan.json`, `output/timeline.json` | Editable plan and measured timeline |
| `output/motion/` | Portable HTML project with local assets and renderer log |
| `output/library/` | Reopenable final projects, media, metadata and credits |

The application remains a local, single-job studio. The cloud fork stores the
source and CI configuration; it does not expose the Flask app as a public service.

## Verification

Install development test dependencies in the project venv:

```bash
python -m pip install pytest
python -m pip check
python -m pytest -q
```

The Python suite uses mocked providers, synthetic audio and local fixtures. It
does not spend API credits or publish videos. FFmpeg is needed for the decoded
audio integration checks.

Prepare and render offline fixtures containing all seven templates:

```bash
python renderers/hyperframes/smoke.py --seconds-per-scene 2
python renderers/hyperframes/smoke.py --portrait --seconds-per-scene 2
```

Outputs are under `renderers/hyperframes/.smoke/`. The fixtures use an explicitly
synthetic tone, test-pattern footage and synthetic chart values. They test rendering,
not speech quality or external provider availability.

For pixel-repeatability, template bounds and preview-control browser checks, set
`YAAS_TEST_BROWSER` to an installed Chrome/Chromium executable, activate the venv
so `python` resolves correctly, then run:

```bash
npm test --prefix renderers/hyperframes
```

In a managed environment that already provides Chromium, `HYPERFRAMES_BROWSER_PATH`
may point HyperFrames to that executable. It is an optional local environment
setting; ordinary installations can use the `browser ensure` command from the README.
Normal renders do not install npm packages or execute code supplied by a model.

## Limits

- Legacy photo projects can still be opened; synchronized motion requires
  regenerating their narration once to produce measured timing metadata.
- Free provider inference, stock selection and the external speech service depend
  on network access and service availability. Offline fixtures do not verify those
  services or the quality of their answers.
- Fixed templates provide controllable explainer motion. They do not generate
  photorealistic characters or cinematic synthetic footage.
- Rendering is CPU-intensive. The default is one worker; use a draft before a
  long final render. Stop cancels the HyperFrames process tree, and failed renders
  leave the prior completed file intact on disk while the app marks it stale.
