# Motion Studio

Motion Studio adds a narration-driven video path to YAAS. The app researches a
topic, writes a script and records narration. A visual director produces a
constrained JSON storyboard. Fixed HTML/SVG templates animate explanatory graphics
on a paused GSAP timeline, and HyperFrames captures it as an MP4. Hybrid and
Classic also use the original stock-picture pipeline; saved pictures can be
animated through the explicit **Animate these photos** action.

The storyboard in **Review** is part of that same pipeline. HyperFrames already
runs during **Make video** in Motion and Hybrid modes; Review lets you inspect,
change and render the saved composition again.

## Choose a mode

| Mode | Visuals | Requirements |
| --- | --- | --- |
| Motion | Animated flow diagrams, comparisons, timelines, supplied-data charts and explanatory text; no automatic photos or footage | Node 22+, FFmpeg/ffprobe, installed renderer/browser |
| Hybrid | Animated explanations with pipeline pictures and optional downloaded Pexels video clips with source credits | Same as Motion; optional Pexels key |
| Classic | The original MoviePy photo renderer, captions and transitions | Original MoviePy/NumPy dependencies |

Motion skips photo fetching and does not attach cached photos to new scenes.
Hybrid falls back to pictures and animated graphics when suitable footage is
unavailable. Classic and Hybrid retain the existing picture sources. Saved photos
remain available for explicit photo animation, thumbnails and project reopening.
All three modes retain title, description, thumbnail and YouTube publishing tools.

Visual, narrator and music defaults belong to the selected channel profile.
The News preset starts with Hybrid; ASMR/calm starts with slower, gentler Classic
photo motion. Both are editable. See [Channels and schedules](CHANNELS_AND_SCHEDULES.md).

Changing the Home style applies to the next generation. An existing draft retains
its saved composition when it is reopened or rendered again. To convert a photo
or footage project without changing the script or recording, choose **Rebuild as
Motion graphics** in Review. The project is replanned as graphics and a new draft
MP4 is rendered. **Animate these photos** explicitly keeps the project's pictures.

## Make and review a new video

1. Follow the [quick start](../README.md#-quick-start), then click **Check local setup**.
2. Select the channel in the sidebar or mobile selector. In **Profile settings**,
   choose a voice and use **Preview voice** to hear the current speed and pitch
   before saving.
3. Choose Motion or Hybrid and **Draft preview first**. Enter a topic and make a video.
4. Open **Review → Storyboard**. Play or scrub the HTML preview and inspect the
   scenes beside their visual goals.
5. Select a scene, enter a change such as “use a comparison” or “less text”, and
   regenerate it. The browser preview updates and a new draft MP4 renders
   automatically while you stay on Review. The recording, scene boundaries and
   other scenes are preserved. In a Hybrid or explicitly photo-based project,
   enable **Use another saved photo** to choose a different downloaded picture
   from the same section. Graphics-only Motion does not add photos during editing.
6. Watch the refreshed draft at the edited scene, then choose **Render final video**
   when ready. If encoding fails, the updated browser preview remains available;
   **Render draft MP4** retries using the same storyboard and recording.
7. Review the final video, metadata and channel identity, then publish separately.

The HTML preview runs the same composition used for encoding. Scrubbing it does
not create another MP4. Scene edits invalidate the previous final render, and a
draft cannot be published as a final video. Changing the spoken script requires
new narration and visual planning; merely changing a scene does not. Draft updates
preserve saved titles, descriptions, tags and the existing thumbnail, along with
unsaved metadata edits in the open Review form. If there is no different saved
photo for the selected section, the editor explains that and keeps the previous
scene. Regeneration also reports when the requested design makes no visible change.

Final renders are archived. Scheduled runs also archive their drafts, marked
**Draft** in **My videos**. Opening an archive restores its saved channel profile,
script, audio, timing, pictures, storyboard, local media and credits.

### Continue after restarting YAAS

The current working project is also saved locally for restart recovery. After
restarting the server, reload the page and open Review to continue with its existing
recording, pictures and storyboard. A saved draft MP4 is offered only when it still
matches the project; an old render left behind after a scene edit is not marked
ready. If only the storyboard is recoverable, use **Render draft MP4** to finish it.
To create something else, choose **Make another video** on Home. This clears the
previous topic and restores the full generation steps. If there is no draft or
storyboard, Home returns to the topic form automatically. For a new topic,
**Research** must be enabled; a partial rerun with Research off reuses the current
project and requires the topic field to be empty.

This working copy is separate from **My videos** and is replaced when you start a
new project. Review shows a loading error with a retry action if its data cannot be
loaded. Each page loads one version of the interface assets so an update does not
mix old preview code with new Review code.

## Animate an existing photo project

1. Open the project from **My videos**.
2. On **Review**, choose **Animate these photos**.
3. Inspect the imported timing note and storyboard. Adjust any scene that needs
   a different picture, explanation or layout.
4. Render a draft to inspect the result, then render the final video.

This action explicitly opts the project into photo animation and runs only
**Visuals** and **Video**. It does not research the topic,
replace the project's pictures or record its voice again. The active Review
project keeps its saved channel even if the sidebar had been showing another one.

YAAS uses the best available timing information:

| Available material | What YAAS reuses | What the timing means |
| --- | --- | --- |
| Matching narration and measured timing manifest | The recording and saved section/word events | Section boundaries come from decoded audio; individual word alignment depends on events supplied by the speech service |
| Old narration and SRT captions | The recording and caption cue times | Cue timing is imported; section placement or word positions inferred from those cues are explicitly identified |
| Old narration without usable captions | The recording's measured total duration | Section timing is estimated and shown as an estimate in Review |
| Old MP4 without a separate recording | Audio recovered from the MP4, plus saved captions when available | The soundtrack is reused under the same imported/estimated timing rules |

Recovering an MP4's soundtrack preserves everything mixed into it, including old
background music or effects. It does not separate those sounds from speech.
Use a clean original recording, or regenerate narration, if you want to replace
an already mixed soundtrack.

Older archives sometimes contain pictures without their original section-to-photo
map. YAAS reuses the available files and surfaces an **image reuse note** in
Review; check that each scene's picture makes sense.

A fresh narration run is optional for unchanged legacy speech. It is useful when
you want the speech service's own word events rather than inferred timing, though
the service may still omit those events. Estimated legacy timing is never presented
as freshly measured word synchronization.

## How synchronization works

Fresh narration is recorded section by section and decoded to 48 kHz mono PCM.
YAAS joins the actual samples and saves a WAV, SRT and
`narration.timings.json` manifest. Available word events are preserved. If the
speech service omits them, captions use phrase events or an explicitly estimated
fallback within each measured section.

`video/timeline.py` converts audio positions to frame boundaries. It rounds
cumulative positions instead of repeatedly rounding durations and covers the
final partial frame. A hash of the audio and timeline rejects stale combinations
of speech, captions and storyboard. Transitions do not shorten the narration
clock. Legacy imports carry their timing source and warnings through the plan.

Motion beats anchor to spoken labels when matching word events exist. Otherwise,
the plan records distributed or mixed timing. An audio-length match does not imply
that every diagram reveal lands exactly on an individual spoken word. Review the
preview, particularly when importing older recordings.

## Voice, music and visual pacing

The default narration pace is natural (`+0%`) and pitch is unchanged (`+0Hz`).
The ASMR/calm preset uses slower speech (`-15%`) and calmer script instructions.
It is a calm spoken-video preset, not a binaural audio or ASMR-trigger generator.
Use an appropriate original recording for that kind of content.

**Preview voice** makes a short sample only after you click it. It uses the
editor's unsaved voice, rate and pitch. Saving the profile applies those choices
to future narration; it does not alter an already recorded project.

Background music and sound effects are **off by default**, each with a starting
level of `0.08` (8%). **Settings → Audio library** imports downloaded MP3, WAV,
OGG, M4A and FLAC files (up to 20 MiB and two hours) into the selected channel's
library. Preview a track, select it as music or an effect, enable that layer and
save Settings. Imports alone do not change a soundtrack or generate a video.

Music uses the selected imported track, or the existing local music folder when
no imported music is selected. It loops a stable bed with softened seams and
fades. Effects have two modes: **Transitions** plays a one-shot at each script
section boundary after the first, capped at two seconds and shortened before
the next boundary; **Ambience** loops the selected effect beneath the recording.
The original narration controls speech ducking for both layers, followed by peak
limiting. Motion prepares the soundtrack as lossless WAV before video encoding.
No generated sound effects or test tones are added to normal videos.

Use **Review → Music & effects → Apply music & effects to current draft** to apply
saved channel choices to a Motion or Hybrid preview. This reruns Music and Video,
preserving the script, recorded voice, measured timeline, storyboard and edited
publishing metadata. For Classic, leave the Home topic empty and select only
Music and Render. Choose Narration only when you want a new recorded voice.
Video-only renders and scene edits retain the prepared soundtrack even if profile
settings have changed. Missing selected audio is reported without substituting a
different imported track. The recording remains available.

Pixabay's [public API](https://pixabay.com/api/docs/) documents image and video
search, not music or sound-effect search. Download audio from its website and
import it locally. Optional source URL, creator, license note and Content ID
metadata are kept with the track. `audio_credits.json` records only assets applied
to the current recording; Review shows those sources, generated descriptions
include them, and archived projects retain both the soundtrack and credits.
Keep any available download certificate: some Pixabay tracks are registered with
YouTube Content ID and may receive claims, as explained in its
[FAQ](https://pixabay.com/service/faq/).

Photo movement and explanatory transitions are gentler than the earlier demo.
The [verification videos](samples/README.md) each contain 168 frames: 14 seconds
at 12 fps. They exercise all seven templates with a generated picture, test-pattern
footage and synthetic test-tone audio. They verify rendering and audio presence;
they do not demonstrate voice quality or synchronization to speech. The separate
optional NASA photo demonstration remains available through `demo.py`.

## Templates and media contract

The seven supported templates are `flow`, `comparison`, `timeline`, `chart`,
`explain`, `text` and `footage`. Layouts support 16:9 and 9:16. Scene data includes
an ID, section ID, start, duration, title, visual goal, short items, local-time
motion beats and optional local picture/footage metadata.

New plans record `use_project_photos`: it defaults to `false` for Motion and
`true` for Hybrid. The explicit photo-animation action sets it to `true` while
keeping the Motion renderer. A graphics-only plan cannot contain photo scenes;
graphics-only Motion cannot contain footage either. Legacy plans without this
field remain reviewable, and explicit rendering from Review preserves their
existing media choices.

The planner returns JSON, never executable HTML or JavaScript. The templates live
in `renderers/hyperframes/templates`. Text is escaped, fonts/scripts are copied
locally, and Review's preview runs in an opaque-origin sandbox with network
connections disabled. The preview cannot call the studio's settings or upload APIs.

Charts require numeric values explicitly supplied as a section's `chart_data` or
`data_points`; the planner does not invent statistics to fill a chart. Hybrid
footage goes through the bounded download adapter, with an attribution manifest
and no arbitrary remote URLs in generated scenes.

## Free OpenRouter use

`openrouter/free` is the default planner and reviewer. A chosen model ID is checked
against the live catalogue for zero pricing and appropriate capabilities. Planning
requires schema support; reviewing frames also requires image input. Explicit paid
models are rejected, including a paid sibling of a model whose free ID disappeared.
OpenRouter text fallback uses the same free-only validation.

Without a key, planning uses local templates. Provider errors, rate limits and
invalid output fall back to those templates. Attempts and local request budgets
are bounded. The model chooses an animation plan and local code renders it; this
integration does not promise access to a free generative video service. Provider
availability and quotas remain external constraints.

Frame review is **off by default**. Enabling it sends up to four sampled rendered
frames to the selected free vision model. Its layout findings are advisory; local
MP4 checks independently verify dimensions, duration and presence of audio.
Neither a visual review nor retrieved background sources verify live breaking news.

## Important files

| File / directory | Purpose |
| --- | --- |
| `video/narrator.py`, `video/timeline.py` | Fresh narration, legacy timing imports and shared audio clock |
| `video/stock.py` | Original picture pipeline and photo reuse |
| `video/music.py` | Optional music bed, speech ducking, fades and limiting |
| `video/sound_effects.py` | Selected transition effects or ambience using the narration clock |
| `studio/audio_library.py` | Per-channel local audio imports, metadata and previews |
| `agents/visual_director.py` | Validated local/AI planning and scene regeneration |
| `apis/openrouter.py`, `apis/stock_video.py` | Free-only model access and stock video |
| `video/renderers/html.py` | Local project preparation, subprocess lifecycle and MP4 verification |
| `renderers/hyperframes/` | Pinned npm project, templates, smoke fixtures and silent photo demo |
| `studio/preflight.py`, `studio/visuals.py` | Setup checks and storyboard/preview endpoints |
| `studio/channels.py`, `studio/schedules.py` | Profile isolation and recurring generation |
| `output/scene_plan.json`, `output/timeline.json` | Editable plan and labelled timing |
| `output/motion/` | HTML project with local assets and renderer log |
| `output/library/` | Reopenable final projects and scheduled drafts |

The app remains a local studio with one generation job at a time. A GitHub fork
stores source and CI configuration; it does not run the Flask app or scheduled
videos by itself. See [running the scheduler](CHANNELS_AND_SCHEDULES.md#keep-the-scheduler-running)
for local and hosted-machine operation.

## Verification

Install development test dependencies in the project environment:

```bash
python -m pip install -r requirements.txt pytest
python -m pip check
python -m pytest -q
```

The Python suite uses mocked providers and local fixtures. It covers measured and
imported timing, photo reuse, profile/token isolation, channel-bound publishing,
schedule validation/recovery, music mixing and renderer lifecycle. It does not
spend API credits or publish videos. FFmpeg is needed for decoded-audio checks.
`croniter==6.2.4` and `tzdata>=2025.2` are installed by the normal requirements.

Prepare and render the offline regression fixtures containing all seven templates:

```bash
python renderers/hyperframes/smoke.py --seconds-per-scene 2
python renderers/hyperframes/smoke.py --portrait --seconds-per-scene 2
```

Those **test fixtures** use a synthetic tone, test-pattern footage and labelled
synthetic chart values. With two seconds per scene they produce the 168-frame
verification samples linked in the README. They test encoding and audio presence,
not sound quality. The optional silent photo demonstration uses `demo.py` instead.
Outputs are under `renderers/hyperframes/.smoke/`.

For renderer and studio browser checks, set `YAAS_TEST_BROWSER` to an installed
Chrome/Chromium executable and activate the project environment so `python`
resolves correctly. If needed, set `YAAS_TEST_PYTHON` to that interpreter explicitly.
Prepare both fixtures, then run:

```bash
python renderers/hyperframes/smoke.py --prepare-only --seconds-per-scene 2
python renderers/hyperframes/smoke.py --portrait --prepare-only --seconds-per-scene 2
npm test --prefix renderers/hyperframes
```

Browser checks include repeatable rendering, template bounds, preview controls,
profile/schedule separation, explicit voice preview, mobile layouts and the
legacy-photo action. Their fetched YouTube identities are test fixtures.

In a managed environment, `HYPERFRAMES_BROWSER_PATH` can point to an existing
compatible browser. Ordinary installations can use the `browser ensure` command
in the README. Normal renders do not install packages or execute model-supplied code.

## Limits

- Legacy imported or estimated timing is less precise than original speech events.
  The UI labels that distinction; inspect the recording and captions together.
- Recovering an old MP4 soundtrack cannot remove music already mixed into it.
- External inference, stock selection and speech depend on network access and
  service availability. Offline tests do not verify those services or their output quality.
- Fixed templates create explanatory motion; they do not generate photorealistic
  actors or cinematic synthetic footage.
- Rendering is CPU-intensive. The default is one worker; use a draft before a
  long final render. Stop cancels the HyperFrames process tree, and failed renders
  keep the previous completed file on disk while marking it stale in the app.
