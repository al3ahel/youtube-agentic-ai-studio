# Armour repairs, actual model and Hybrid scope

**Status on 10 November 2026, Dubai time:** fresh Edge TTS narration and the
preceding automatic mix are complete. The additional visual, sound-selection,
review and planner-status corrections below are implemented and tested. A new
normal Studio continuation will apply the five scene edits and produce another
mix, encode and strict review. Its outcome is not yet claimed in this revision.

This is the current follow-up to [the authorized audio run](AUTHORIZED_AUDIO_RUN.md).
The earlier six-of-ten result, its four rejected responses and its unchanged
film remain historical evidence. A rejected review response is not sufficient
authority to repair a scene: its claimed defect still needs actual evidence.

## Concrete corrections

The following requests use the ordinary scene editor, with trusted photo
replacement disabled and a fresh plan revision for each request:

| Scene | Requested visible correction | Scope |
| --- | --- | --- |
| 002 | Close, locked collage; **Armour and weapon** | Two larger existing museum details; no invented weapon photograph. |
| 003 | **Linked rings** | A source-grounded heading for the visible mail detail. |
| 005 | Medium 3D view, orbit camera; **Hardening the Steel** | Restores material depth; a qualitative reveal, not a heat-treatment simulation. |
| 006 | Close 3D view, locked camera; **Reinforcing layer** | Removes the unsupported physical-gap claim. Separation is an explanatory diagram convention. |
| 010 | Medium, locked comparison; **Armour and firearms** | Reconstructs the source pair with **Armour** and **Firearms** labels. A torso plate is not labeled mail rings, and the stocked long gun is not labeled a pistol. |

The editor supports an explicit quoted heading without letting words inside
that heading accidentally alter a camera or style. Generated actor labels must
match both the source and the catalogue drawing. Generated material headings
cannot turn an exploded diagram into an unsupported gap, cavity, insulation,
damage or performance claim. These checks operate in the shared Motion/Hybrid
path. Rejected generated proposals remain rejected in the planning journal;
local editorial controls are not credited as new model inventions.

The timing/source inputs and trusted photo identities are preserved. The
preparation check applied all five requests sequentially to the archived plan
in memory and verified that the other five scene objects were unchanged.
That preparation did not establish the quality of a new encoded film.

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
retain their existing failure status and the driver's raw request captures.

The Review screen now distinguishes recorded model planning, replayed decisions,
template fallback and missing older provenance. It shows recorded model identity
and accepted/rejected counts where available, and links to the project's model
settings and existing rebuild controls. Viewing those controls does not generate
a film or change settings. Planning provenance is not visual approval.

## What generated this armour example

| Component | Actual source or implementation |
| --- | --- |
| Narration text | The fixed five-section, 84-word armour fixture, not a newly model-written script. |
| Scene proposals | Local Ollama **`qwen3.5:4b`**. The preceding complete plan accepted eight proposals, rejected two and retained six nontext model changes. A later audio-binding continuation replayed four sealed planning responses. |
| Sound choices | Fresh local `qwen3.5:4b` requests through the normal audio director. |
| Image review | The same installed `qwen3.5:4b`, using decoded film frames. |
| Voice | Edge TTS **`en-US-AndrewNeural`**, pitch `+0Hz`; selected complete take at `+14%`. |
| Photographs | Three acquired museum images with retained source identities and credits. |
| Drawn animation and encoding | The repository's Hyperframes HTML/SVG/GSAP scene renderer, Chromium and FFmpeg. |
| Current corrective direction | Inspected-frame editorial requests plus the reusable production safeguards above. |

The installed model digest is
`d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`.
The execution receipt is authoritative for the installed model. The current
[Ollama catalogue](https://registry.ollama.com/library/qwen3.5:4b) describes the
4b model as multimodal and lists the Q4_K_M model variant. The model chooses
structured scene decisions; the application renderer produces the movie pixels.
The result therefore depends on code, assets, source text, accepted proposals
and editorial work as well as the selected model.

## Why another free-model run can look different

The comparison command selected local Qwen, enabled review and selected automatic
sound for its own run. It did not persist those choices to the user's project
settings. The inspectable workspace had no saved local model selected and review
disabled; that observation does not establish the settings of another installation.

The [OpenRouter free router](https://openrouter.ai/docs/guides/routing/routers/free-router)
chooses among available free models that support the requested capabilities. It
is not a fixed Qwen model. The current adapter records the requested router/model
but does not retain the resolved provider model identity, so the Review screen
states that limitation. Request-format support also differs between provider
adapters; the generic text fallback validates JSON after generation. A structured
schema does not establish semantic accuracy, as the
[Groq structured-output documentation](https://console.groq.com/docs/structured-outputs)
also distinguishes.

For a comparable local run, use this code revision, select the installed Qwen
model in the current project's model settings, enable AI scene planning and
image review, choose automatic sound, and rebuild **Storyboard and Render**.
A Render-only operation deliberately reuses a saved plan; changing a model does
not retrospectively redesign that plan. Profile selection matters, and an active
job uses the settings captured when it started. An older graphics draft also
needs the existing rebuild workflow to adopt current scene design.

## Motion, Hybrid with photos and Hybrid with video

The preceding armour film is already **Hybrid**: two photographic collages and
eight animated graphic shots. It contains no stock-video clip. The requested
corrections add two material depth views while retaining the photographs.

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

## Verification before the new run

The integrated source passes **429 focused Python tests** with no failures or
skips. Three targeted real-browser checks cover model-status display, profile
navigation, model discovery and older-draft rebuild controls without generation
side effects. A new real encoded-review regression fails on the old implementation
because it never makes the correction request, and passes on the new code.
These are local checks, not a claim of repository-wide CI or semantic approval.

The new run will reuse the verified complete 29.5-second narration, use a new
output directory, preserve prior attempts, and invoke the normal Studio editor
and music/video routes. Any verified automatic repair must still be re-encoded
and re-reviewed before it can replace the selected film.
