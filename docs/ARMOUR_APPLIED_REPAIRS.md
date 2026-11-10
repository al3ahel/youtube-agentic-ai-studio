# Armour repairs, actual model and Hybrid scope

**Status on 10 November 2026, Dubai time:** the five requested scene edits,
fresh Edge TTS narration, corrected automatic mix and new 29.5-second Hybrid
film are complete. The normal Studio continuation finished at **01:37 Dubai
time**. All ten shots now have valid final review responses; eight meet the
validator's reliability requirements. Scenes 003 and 009 remain uncertain, and
the selected film retains `needs_review` / `passed: null`. The five editorial
repairs are real; the automated repair loop made zero additional scene edits.

This is the current follow-up to [the authorized audio run](AUTHORIZED_AUDIO_RUN.md).
The earlier six-of-ten result, its four rejected responses and its unchanged
film remain historical evidence. A rejected review response is not sufficient
authority to repair a scene: its claimed defect still needs actual evidence.

## Concrete corrections

The following requests were applied through the ordinary scene editor, with
trusted photo replacement disabled and a fresh plan revision for each request.
The table describes the requested corrections, all now visible in the 30
sampled frames of the selected attempt-0 encode:

| Scene | Requested visible correction | Scope |
| --- | --- | --- |
| 002 | Close, locked collage; **Armour and weapon** | Two larger existing museum details; no invented weapon photograph. |
| 003 | **Linked rings** | A source-grounded heading for the visible mail detail. |
| 005 | Medium 3D view, orbit camera; **Hardening the Steel** | Restores material depth; a qualitative reveal, not a heat-treatment simulation. |
| 006 | Close 3D view, locked camera; **Reinforcing layer** | Removes the unsupported physical-gap claim. Separation is an explanatory diagram convention. |
| 010 | Medium, locked comparison; **Armour and firearms** | Reconstructs the source pair with **Armour** and **Firearms** labels. A torso plate is not labeled mail rings, and the stocked long gun is not labeled a pistol. |

The local editor parser separates an explicit quoted heading from camera/style
commands; the model still receives the complete edit request. Generated actor labels must
match both the source and the catalogue drawing. Generated material headings
cannot turn an exploded diagram into an unsupported gap, cavity, insulation,
damage or performance claim. These checks operate in the shared Motion/Hybrid
path. Rejected generated proposals remain rejected in the planning journal;
local editorial controls are not credited as new model inventions.

The live editor calls preserved timing/source inputs and trusted photo
identities and verified that untargeted scenes were unchanged at every step.
Scenes 002, 003 and 005 retained accepted model contributions; scenes 006 and
010 used the supported local controls after real model replies. The model also
changed styling in some edited scenes. Dormant descriptive metadata in scene
005 remains imperfect and must not be treated as proof of rendered content.

## Sound and review corrections

The sound director now allocates the existing sparse effect budget after source,
asset, contact and duration validation. A verified visual contact has priority
over a generic transition. Minimum spacing, licensing, gain and mastering rules
remain enforced, and every cap/spacing omission has an explicit reason in the
sound plan. This fixes the old ordering behavior that let an early page turn
consume the only effect slot before a proposed proof contact. It does not insert
an impact that the model did not propose or the resolver cannot verify.

The strict reviewer first covers all selected shots. A returned parsed response
that fails evidence/schema validation receives at most one new assessment of
the same decoded images and source, with the validation problem explained.
Both responses are retained separately with hashes. Valid uncertainty and
valid failures are not retried to obtain a pass. Source alignment, sampling,
confidence and repair-authority requirements remain unchanged; the hosted
eight-request budget also includes corrections. Transport/adapter failures
retain their existing failure status and the driver's raw response captures.

The Review screen now distinguishes recorded model planning, replayed decisions,
template fallback and missing older provenance. It shows recorded model identity
and accepted/rejected counts where available, and links to the project's owning
channel settings and existing rebuild controls. Viewing those controls does not
generate a film or change settings. Planning provenance is not visual approval.

## Continuation history and completed render

The first edited Studio run completed all five editor calls and its automatic
mix. It selected *Cylinder One* and a licensed metal-contact recording at
**17.134458 seconds**, bound to scene 007 and the spoken word **hit**. The earlier
page-turn proposal is explicitly omitted with `effect_budget_exhausted`.

Rendering reached a last saved progress of 26% before automatic approval review
rejected an unexpected connection to the npm registry. The installed Hyperframes
0.9.19 code identifies this as an optional public package-version GET, not an
operation required to use the already installed renderer. No owned application,
model or renderer process remained afterward. The original application report
is retained with its last saved `running` state; a separate external-termination
receipt records the interruption rather than fabricating an application result.
The five edits, complete narration and new mix were exported in a verified
67-file checkpoint.

The production render subprocess now forces the CLI's supported
`HYPERFRAMES_NO_UPDATE_CHECK=1` and `HYPERFRAMES_NO_AUTO_INSTALL=1` settings while
preserving its existing telemetry opt-outs. This prevents the optional lookup;
it does not change a permission gate, install a package or alter `node_modules`.
The new real benign-child regression fails on the old environment and passes
with the fix. All **67 affected renderer tests** pass. The continuation reused the
edited plan and recording through normal Studio restoration and the music/video
routes in a new output directory.

An initial run of that offline continuation stopped during its first sound-model
prompt, before a new response, mix or encode was completed. Its last saved
application status is also retained unchanged. No second automatic-review
rejection or increased OOM count was observed, and a later session query returned
only an unknown-process result; the cause is not established. A separate
interruption receipt records those limits. One bounded resume then completed
using the same verified 67-file checkpoint and published offline renderer in
another new output directory, preserving both interrupted attempts.

## Final review result and exact selected film

| Review measure | Previous audio-binding film | Selected repaired film |
| --- | ---: | ---: |
| Shots decoded at three phases | 10 / 10 | 10 / 10 |
| Shots with a valid final review response | 6 / 10 | 10 / 10 |
| Shots meeting the validator's reliability requirements | 6 / 10 | 8 / 10 |
| Unresolved invalid/unavailable responses | 4 | 0 |
| Additional automatic scene repairs | 0 | 0 |
| Semantic status | `needs_review` | `needs_review` |

Nine new first responses validated. Scene 009's first response failed because
its asserted defect was not quoted from the cited visible evidence. The single
bounded correction returned a valid uncertain assessment, and both original
JSON responses remain retained with separate hashes. Scene 003's valid motion
uncertainty was not retried. No uncertainty was converted into repair authority
or a pass.

The two retained warnings are scene 009's uncertain subject-relevance finding
and scene 010's technical `action_not_visible` observation. The latter correctly
limits the static closing comparison: correct object labels do not depict
centuries of evolution. The automated review reports zero errors, two
observations and incomplete semantic approval. Its `repairs_applied: 0` counts
additional automatic repairs, not the five earlier editor changes.

A valid structured response is still not proof that every model observation is
accurate. The raw reviewer calls the scene 006 plates “shields” and describes
scene 005 as a reinforcing-layer change. The independent pixel inspection sees
breastplate forms and an orbiting plate; it does not establish hardening or a
new layer in scene 005. The scene 010 response describes only changing captions
yet passes all categories; the separate technical action warning remains. Those
raw responses remain unchanged in the evidence.

The selected film is **1080 × 1920**, **30 fps**, **885 frames**, **29.5 seconds**,
with H.264 video and 48 kHz stereo AAC. It is 7,575,349 bytes.

- Movie SHA-256: `52f713ec790133b918e1045ddf606ba826f90edcac285365ea029d060731c398`.
- Canonical storyboard SHA-256: `9425ecebd75405cbffeecffd3ef40fb2f64ab1f79c912d64e39cc20e60c21a7e`.
- Executed source: [`b7c41b9e6a9cee06be46d8aceb23aca66203d4bd`](https://github.com/al3ahel/youtube-agentic-ai-studio/commit/b7c41b9e6a9cee06be46d8aceb23aca66203d4bd), verified against 95 production-file hashes.
- Normal Studio run: `b536f61b3e3240f09a94d1c4b0d04bb4`; selected encoded-review attempt: `4f0f9f31bac84e8a9c313a8f49d8d04f/attempt-0`.

## Completed narration and soundtrack

The fresh Edge TTS take uses **en-US-AndrewNeural**, **+14% rate**, **+0 Hz
pitch**. All **84 returned word events** match the five-section script in order.
The original complete speech lasts 29.232 seconds; 0.268 seconds of silence
extends the timeline to 29.5 seconds without cutting or stretching speech.

The final automatic mix contains *Cylinder One* by Chris Zabriskie and licensed
metal-contact foley. The impact begins at **17.134458 seconds**, matching the
measured start of “hit” and scene 007's planned contact beat. The latest fresh
sound response proposed only that event, so its omission list is empty. The
page-turn rejection described in the history above belongs to the first
interrupted Studio attempt. Both choices remain traceable to their actual raw
responses. The final mixed WAV is byte-identical to that first corrected mix.

| Audio evidence | Measured result |
| --- | --- |
| Complete narration | 48 kHz mono PCM; SHA-256 `52173a70871a6fdd37dbd3fbbf439a1412824181bfc80c6f2ab6720c3a144be5` |
| Complete mix | 29.5 s, 48 kHz stereo, 24-bit PCM; SHA-256 `6fd6661b14fcae69c543d13e5fe4f28738fa7396330fe16ae3081489a5d1dbb5` |
| Mastered WAV, before AAC | −16.93 LUFS integrated; −1.60 dBTP true peak |
| Selected MP4, full decoded AAC track | **−16.95 LUFS integrated; −1.70 dBTP true peak; 1.30 LU loudness range** |
| Audio ceiling | Measured post-AAC true peak remains below the configured −1.50 dBTP ceiling |

The mastering report retains `target_shortfall`: constant gain of +3.35 dB
preserved peak headroom instead of forcing −16 LUFS. The post-AAC numbers are
the FFmpeg full-stream `input_*` measurements; discarded normalization output
is not reported as delivered audio. The film's separate 8 kHz mono PCM check
found no full-scale clipped samples or detected silence intervals. These are
signal and schedule checks, not independent speech recognition or a listening
assessment. The metal cue is illustrative foley, not an authenticated historical
armour-test recording. Source credits and license records are retained.

## What generated this armour example

| Component | Actual source or implementation |
| --- | --- |
| Narration text | The fixed five-section, 84-word armour fixture, not a newly model-written script. |
| Scene proposals | Local Ollama **`qwen3.5:4b`**. The preceding complete plan accepted eight proposals, rejected two and retained nontext model choices in six scenes. A later audio-binding continuation replayed four sealed planning responses. |
| Sound choices | Fresh local `qwen3.5:4b` requests through the normal audio director. |
| Image review | The same installed `qwen3.5:4b`, using decoded film frames. |
| Voice | Edge TTS **`en-US-AndrewNeural`**, pitch `+0Hz`; selected complete take at `+14%`. |
| Photographs | Three acquired museum images with retained source identities and credits. |
| Drawn animation and encoding | The repository's Hyperframes HTML/SVG/GSAP scene renderer, Three.js for 3D views, Chromium and FFmpeg. |
| Current corrective direction | Inspected-frame editorial requests plus the reusable production safeguards above. |

The installed model digest is
`d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`.
The execution receipt is authoritative for the installed model. The current
[Ollama catalogue](https://registry.ollama.com/library/qwen3.5:4b) describes the
4b model as multimodal and lists the Q4_K_M model variant. The model chooses
structured scene decisions; the application renderer produces the movie pixels.
The result therefore depends on code, assets, source text, accepted proposals
and editorial work as well as the selected model. This armour example also
benefits from implemented mail, plate, projectile/contact, deflection and layer
components. The model can select and direct those components; a stronger model
alone does not add a missing drawable mechanism or a new trusted photograph.
ChatGPT contributed the code corrections and the five inspection-directed
editorial requests; this is not an untouched default-model generation.

## Why another free-model run can look different

The comparison command selected local Qwen, enabled review and selected automatic
sound for its own run. It did not persist those choices to the owning channel's
saved settings. The inspectable workspace had no saved local model selected and
review disabled; that observation does not establish the settings of another installation.

The [OpenRouter free router](https://openrouter.ai/docs/guides/routing/routers/free-router)
chooses among available free models that support the requested capabilities. It
is not a fixed Qwen model. The current adapter records the requested router/model
but does not retain the resolved provider model identity, so the Review screen
states that limitation. Request-format support also differs between provider
adapters; the generic text fallback validates JSON after generation. A structured
schema does not establish semantic accuracy, as the
[Groq structured-output documentation](https://console.groq.com/docs/structured-outputs)
also distinguishes.

For a comparable run with existing complete voice and photographs, use the
published review-branch code and the project's owning channel profile:

1. In Review, **Model settings & discovery** opens that owner's settings. Under
   **Writing models**, choose **Model route → Local only — Ollama**. Use
   **Find local models**, select **qwen3.5:4b** under **Installed local model**,
   and **Save**. Discovery lists installed models; it does not install them.
   The normal local address is `http://127.0.0.1:11434`. The separate
   **Scene planner** and **Visual reviewer** fields are OpenRouter preferences,
   not the local-model selector.
2. Under **Visuals & captions**, enable **AI scene planning**, **Plan individual
   shots**, **AI visual review** and **Repair and check rendered shots**. Select
   **Motion & Hybrid captions → Highlight measured words** and
   **Render workers → 1 — low memory use**.
3. Under **Sound design**, set **Sound direction → Automatic — let the agent
   choose**, **Intensity → Low — subtle and calm**, and enable **Audio mastering**.
   Save these channel settings.
4. On Home, choose **Visual style → Hybrid — motion, photos and footage**. For
   the already-open project, leave Topic blank. Under **More options → Redo only
   some steps**, select **Music**, **Storyboard** and **Render**. Retain the
   existing script, voice and assets by leaving their steps unselected. Leave
   **Draft preview first** off for a complete encode, then use
   **Redo selected steps →**.

Settings are defaults for the owning channel profile, not a separate model
configuration stored inside each project. Home mode/step choices are per-request;
a running job freezes the profile settings when it starts. Provider keys remain
studio-wide. **Render draft MP4** and **Render final video** deliberately reuse
the saved storyboard and verified mix. Changing a model does not redesign that
plan, and omitting **Music** does not create a fresh soundtrack. The older-draft
Review rebuild buttons currently choose Motion, so use the Home Hybrid route
above when preserving Hybrid mode. New inference can make different choices;
these settings do not reproduce the five editorial requests by themselves.

## Motion, Hybrid with photos and Hybrid with video

The revised armour movie is already **Hybrid**: two photographic collages,
two 3D material views and six 2D graphic shots. The preceding film had two
collages and eight 2D shots. Neither contains a stock-video clip.

| Mode/content | Shared improvements | What still needs content-specific judgment |
| --- | --- | --- |
| Motion graphics | Source-scoped shots, label/heading grounding, camera controls, contact timing, encoded review and bounded repair. | Actual legibility, depicted mechanism, variety and continuity; source support alone does not prove good pixels. |
| Hybrid with photos | The same graphic path plus trusted image identities, collage framing, captions, timing and review. | Photo choice, crop, focal subject, resolution, readable credit and useful movement. Moving a still photograph does not create the narrated physical event. |
| Hybrid with video | The same composition/timing/review path around validated footage. | Correct subject and action, clip duration, vertical crop, continuity, licensing and whether the footage actually demonstrates the claim. |
| Classic | A separate legacy rendering path. | These shared Motion/Hybrid improvements do not automatically migrate every Classic draft. |

The fixes should improve all shared paths, but this armour continuation can
establish only its actual photo-and-graphics result. It cannot demonstrate the
quality of an untested stock-video sequence or full audiovisual parity with a
reference available only as a short silent excerpt.

## Independent inspection of the revised encode

Both assistants inspected every setup, action and result frame from attempt 0:
30 images across all ten scenes. The changed collage composition, correct
mail/firearm labels and 3D material depth are visible. Scene 004 depicts an
incoming arrow, contact and a deflected outgoing arrow. Scenes 007–008 depict
projectile contact, a continuous deformed plate and a surviving dent without an
apparent through-hole. No obvious title/caption overlap or clipping was observed
in these samples.

The inspected images also preserve concrete limits:

- Scene 002 shows armour photographs only; the weapon half of its title is verbal.
- Scene 005 shows a shaded plate and orbit, not a heat-treatment process.
- Scene 006 uses diagrammatic layer separation; it establishes no real air gap.
  Its setup plate remains relatively small despite the close framing request.
- Scene 009 remains sparse: the protection caveat is mainly in captions, without
  a comparison of weapon, range, material or test conditions.
- Scene 010 correctly labels armour and a firearm but does not animate historical
  evolution across centuries.

This is sampled visual inspection, not continuous playback, physical validation,
audio listening, independent speech recognition or an override of model review.
The selected attempt remained attempt 0. The inspection is bound to its exact
movie, canonical storyboard and all 30 original frame hashes.

## Verification

The integrated source passes **429 focused Python tests** with no failures or
skips. Three targeted real-browser checks cover model-status display, profile
navigation, model discovery and older-draft rebuild controls without generation
side effects. A new real encoded-review regression fails on the old implementation
because it never makes the correction request, and passes on the new code.
These are local checks, not a claim of repository-wide CI or semantic approval.

The completed retry reused the verified complete 29.5-second narration and the
five edits already applied through the Studio editor. It made zero new editor
or TTS requests and invoked the normal music/video routes in a new output
directory. The normal pipeline confirmed that production source, saved settings
and the inherited checkpoint remained unchanged. All six encoded-format checks
passed: dimensions, frame rate, decoded frame count, video codec, audio codec
and duration. There was no automatic repair candidate authorized by this review.

## Deliverables and preserved evidence

The delivered `armour-fresh-pipeline.mp4` is the selected film identified above.
`armour-fresh-narration.wav` and `armour-fresh-mix.wav` preserve the complete
recording and mastered mix. `armour-repairs-before-after.jpg` compares action
frames from the prior audio-binding film and the selected Studio film for scenes
002, 003, 005, 006 and 010. Image pixels are resized for the sheet; labels are
outside the frames. No scene pixels were retouched or generated for the comparison.
The overview and full contact sheet are also actual selected-film decodes.

`armour-fresh-evidence.zip` contains 791 payload files plus its own manifest,
including all six preceding outputs, their distinct terminal/interruption
records, original and corrected model responses, preserved voice takes,
complete source credits, all selected frames and three inspection-report pairs.
Prior incomplete reviews retain their original findings. Repository source code
is published in Git and is not included in the archive. Packaging re-decoded
all 30 selected JPEGs from the delivered MP4 and verified exact agreement with
the encoded review's hashes. An independent inventory check found no missing,
extra or changed payload file.

- Evidence ZIP SHA-256: `55e057e60593905a8cc0aed4b2bc1fb24fa72719c7fb42048a14dcadcb164b49`.
- Frame manifest SHA-256: `150687029c445549886c42dbeb32eb55c5d3f589d9da6c4663597d3672a1a666`.
- Before/after sheet SHA-256: `843d99168ed59906636853a0db4d428a7cb0d420fb0c7ea5c160fba5cf6c3070`.

Publication remains on `review/armour-continuation-20261109`. The reviewed
production fixes were published in commits `3d1c390` and `b7c41b9`; this final
report records the actual completed run. No `main` merge or YouTube publication
was performed. The downloadable film remains explicitly awaiting full semantic
approval and a listening assessment.
