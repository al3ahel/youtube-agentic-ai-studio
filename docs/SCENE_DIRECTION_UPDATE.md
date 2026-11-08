# Directed scenes and subject-matched stock video

The earlier illustrated renderer could turn a specific idea into the same small
symbol it used for every mention of that topic. The visual agent selected labels,
templates and a goal, but a later noun matcher selected the drawing. A sentence
about people adapting to floods could therefore become another rain icon. The
goal text was not an executable directing instruction.

This update connects supported directing decisions to actual rendered behavior.
It also connects the existing Pixabay key to the video endpoint; previously that
key only reached the photo adapter, while footage required Pexels.

## Runtime changes

The director supplies one coherent film brief and bounded decisions for all its
scenes. This uses the existing configured free/local provider routing, rather than
creating an independent chain of model calls for every section. Deterministic
validators and the renderer remain responsible for execution.

| Layer | New behavior | Boundary preserved |
| --- | --- | --- |
| Film direction | A saved theme, premise, continuity intention and named palette guide the film. | Rerendering a saved plan does not randomly restyle it. |
| Scene direction | Grounded actors have subject/support/context roles; a supported world, action and camera produce visible changes. | Scene IDs, narration, measured duration, captions and unrelated scenes stay authoritative. |
| Animation | New version-2 SVG worlds compose actors in a shared space and animate mechanisms and camera framing. | The local paused GSAP clock still controls preview, seek and frame encoding. No model-supplied JavaScript runs. |
| Stock selection | Hybrid can search Pixabay or Pexels for literal scene subjects. | Pure Motion remains graphics-only. Classic and the explicit old-photo workflow remain available. |
| Acquisition | Subject metadata qualifies a clip; theme, framing and sampled palette rank usable candidates. | A similar color cannot admit unrelated footage. No generic-topic rescue search supplies a random video. |
| Asset reuse | New clip receipts bind script, channel, scene, request and actual bytes. | Old filenames or old section numbers do not establish ownership of a new project. |
| Review | Scene details expose the action, camera and footage source; explicit edits produce a new revision and replacement draft. | Revision checks reject stale requests and preserve the original narration clock. |
| Visual critic | Opt-in review compares early/late frames with the specified subjects and action. | At most six images in one request; Local-only routing sends no frames remotely. Unavailable review is not a pass. |
| Archive and credits | Only the clips referenced by the current plan are credited and listed as used footage. | Receipt verification happens before archive restoration changes the open project. |

### Supported animation vocabulary

New scenes use `graphics_version: 2`, `staging: {version, world, action, camera}`
and one to four actors with exactly one subject. Film palettes are ocean, sand,
forest, ember, slate and mono; a scene may override the palette for an explicit
edit. Existing look and rhythm choices remain available.

The supported worlds include flood rise/adaptation, source-explicit floating
homes, census recording/record gaps, mining extraction/material transfer and
general reveal/assembly/connection/comparison. Cameras can hold, frame wide,
move into detail, follow an action or reveal the scene. General scenes can use
the shared actor library, including solar panels, batteries, buildings, people,
water, plants, networks, circuits, documents, trade and orbit diagrams.

The difference matters: a floating-home scene couples the home to changing water
height. A power scene can connect a panel, battery and building. Replacing a label
or naming a goal alone does not count as changing the actual scene design.

These are bounded schematic illustrations. The update does not implement arbitrary
3D filmmaking, infer geographic maps or manufacture numeric chart data. Unknown
subjects retain an explicit abstract explanation. Source-grounding checks are
lexical safeguards, not proof that a statement is true. Version-1 archives keep
their saved rendering until the user requests a rebuild.

## Pixabay video selection

The same `PIXABAY_API_KEY` now supports `https://pixabay.com/api/videos/` in
Hybrid. No second Pixabay credential is required. The adapter uses the documented
video parameters, including literal `q`, `video_type=film` and safe search. The
video API does not document the image API's color or orientation filters, so
orientation and color suitability are checked locally.

Each acquisition has a bounded search/download budget. Search metadata is cached
for 24 hours with account isolation; API keys are not written into cache files or
sent to media CDNs. A search is limited to one request per configured provider and
at most three candidate downloads overall. The actual clip must:

1. Match a concrete source-supported subject in provider tags or source-title metadata.
2. Fit the requested duration and resolution after FFprobe measurement.
3. Retain at least 65% of its image area when fitted to the target aspect ratio.
4. Decode sampled video frames successfully.
5. Carry a local immutable asset receipt and its provider/license/source details.

Color is measured from decoded frames after the subject gate. This is an
appearance preference, not an image-understanding model's assurance of semantic
relevance. Specific country/event footage is not verified by a broad topical
match. When no usable clip exists, the scene retains its animation or trusted
project picture and reports why. A short clip is not padded by freezing or looping it to pretend it covers
the narration. Stock clip audio remains muted; the existing sound director owns
the narration, music and effects mix.

## What the skill libraries contributed

The three requested libraries were reviewed at pinned commits. Their useful
principles are incorporated in YAAS's runtime schema, prompts, renderer and
checks; installing documentation alone would not extend those capabilities.

| Reference | Principle applied in YAAS |
| --- | --- |
| Herk Edit Kit | Compose a meaningful scene space, use purposeful camera framing, preserve spatial understanding and inspect actual frames. |
| HyperFrames community skills | A mechanism should explain the idea; avoid a sequence of text slides, preserve motion timing and check framing seams. |
| Claude Animation Skills | Explicit actors, props and actions; deterministic pose at a requested time; scene-specific mechanisms. |

See [Animation skill references](ANIMATION_SKILL_REFERENCES.md) for exact source
files, commits, licenses, limitations and features not imported. The new renderer
geometry is original repository code. No external install script, paid generation
service, automatic model download or separate personal ChatGPT skill is required.

## Update and test on Windows

Stop the running server with **Ctrl+C**, then update the checkout used to launch it:

```powershell
Set-Location C:\Users\Admin\youtube-agentic-ai-studio
git switch main
git pull --ff-only https://github.com/al3ahel/youtube-agentic-ai-studio.git main
npm ci --prefix renderers/hyperframes
$env:STUDIO_PORT = "8765"
.\.venv-yaas312\Scripts\python.exe gui.py
```

Open `http://localhost:8765` and check the displayed build. Refresh the browser
after restarting the process. Existing draft files do not automatically acquire
a different design just because code was updated.

For an older draft, open Review and choose **Rebuild as Motion graphics** or
**Rebuild animation + sound**. The latter uses the channel's saved sound settings.
For footage, choose **Hybrid — motion, photos and footage** on Home and include
the Storyboard stage. Save the Pixabay key in Settings; the Home provider card
now identifies it as **Stock photos + videos**. Review each scene's source link
and preview. Changing the visual style must rebuild Visuals rather than only
encoding the previous saved storyboard again.

## Verification and its limits

The completed integration gates passed **1,444 Python tests** and **46 browser
tests**, with no failures or skips. The Python run explicitly enabled the local
Chromium wrapper. The browser gate covers both renderer orientations and the
real application Review lifecycle, including automatic draft replacement,
channel isolation, saved-photo policies and refreshed footage attribution.

The primary [directed-motion samples](samples/README.md) are generated through
the real deterministic planner, renderer and encoder. They have authored phrase
cues and a licensed demonstration soundtrack, with no narration or live stock
search. They do not claim to demonstrate a remote model's output or word-level
speech alignment. Both orientations contain 168 decoded video frames: 14 seconds
at 12 fps.

Provider fixture tests exercise real local MP4 probing/decoding as well as
Pixabay/Pexels response handling, subject rejection, palette ranking, crop and
duration checks, cancellation, account-isolated caching and provenance. Project
tests cover changed bytes, wrong script/channel/scene, portable archive receipts
and exclusion of unused download credits. Browser checks cover backward seeks,
actual visual state changes, safe framing and the Review lifecycle.

Live Pixabay retrieval requires the user's saved key; no Pixabay/Pexels or LLM
credential was available in the verification environment. Mocked provider
responses are identified as fixtures. The tests do not promise that a given
topic will have matching real stock footage or that every model decision will
be a strong creative choice.
