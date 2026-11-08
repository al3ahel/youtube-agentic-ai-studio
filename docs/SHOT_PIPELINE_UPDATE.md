# Shot planning, richer compositions and encoded review

New and explicitly rebuilt Motion/Hybrid projects now plan individual shots
inside the measured narration sections. The planner, renderer, editor, archive
and review worker all consume the same saved shot data. An instruction in a
prompt counts as a capability only when it changes the rendered sequence.

## What renders

| Technique | Actual behavior | Appropriate use |
| --- | --- | --- |
| Expressive 2D | Shared scene spaces, illustrated actors, staged reveal/assembly/connection, material sections, contact and consequence, with deterministic framing. | Explanations that benefit from clear shapes and relationships. |
| Collage | Two to four distinct existing pictures arranged in an overlapping composition, with staged entrances, framing and captions from their own trusted identity/credit metadata. | Several relevant photographs from the same narration section. A single picture retains its photo composition. |
| Footage composition | An acquired clip placed in a shot composition with framing and captions. The real clip remains seekable and muted. | Hybrid projects with suitable licensed footage. Clips failing subject-metadata or duration checks are rejected; actual relevance still requires decoded-frame review. |
| Explanatory 3D | Actual Three.js geometry for curved steel plate, interlocking mail and layered ceramic/fiber structures, with supported reveal, separation and contact actions. | A source-supported material mechanism where depth helps explain the relationship. |

Collage captions are hidden while another photograph obscures their text, and
reappear when unobstructed. Their visibility follows the same saved clock as
the photo entrances, including reverse seeks.

These are bounded illustrations, not arbitrary character filmmaking or physical
simulations. The material vocabulary does not silently turn unknown historical
materials into steel. Bounded lexical checks reject recognized negation and
questions; they do not establish general language understanding or factual truth.
Some contractions, including "hadn't", are not recognized by the general mechanism
grounder, although the stricter contact-timing and settled-result guards reject them.
Decoded review remains necessary. The renderer labels material mechanisms as illustrative and not
to scale. Other subjects can use the existing 2D actor/world catalogue, including
source-supported armour and firearm illustrations. The generic local fallback
prioritizes named concrete subjects instead of letting narration fragments fill
the cast with abstract placeholders. Reveal-only equipment views do not imply
firing or energy transfer.

All animation is evaluated from the paused GSAP narration clock, including
arbitrary reverse seeks. Three.js **0.187.0**, its local modules and its MIT
license are copied into the prepared project. There are no runtime CDN imports.
Unsupported WebGL fails visibly; it does not substitute a blank successful
render. Hidden shot canvases release their large backing buffers and reconstruct
their state when revisited.

## Shot planning and editing

`agents/shot_planner.py` subdivides narration sections near measured words,
phrase ends and speech gaps. Ordinary explainers aim for roughly four seconds
per shot; calm profiles hold longer. The original recording, section duration,
caption clock and total duration remain authoritative. A short section can
remain a single shot.

Each saved shot has a role (establish, detail, interaction or payoff), framing,
technique, camera, transition, subject focus and visible action. A narration
section can therefore establish a subject, move into a detail and show an
interaction without holding one composition through every sentence.

An explicit result statement can hold the consequence of the adjacent material
interaction. For example, a detail of a surviving dent begins with the dent
already present; it does not reset the plate and fire another projectile. This
state is derived from the original narration and measured words, with matching
section/material/outcome checks. New events, questions, negation and ambiguous
source spans do not qualify. The renderer-only flag cannot be supplied by a model
or a saved storyboard.

The configured model can choose supported directions, and source validators
check them before they reach the renderer. Local model requests are divided
into batches of at most three shots with a shared film brief. Local generation
has a ten-minute ceiling per request and remains cancellable while waiting for
the model. This accommodates CPU inference without hiding failed or unfinished
responses as model success. Existing free-only provider routing remains in use.

New-design requests provide the measured shot window and supported source
mechanism, allowing the model to choose its own framing and camera. Scene edits
also provide the existing shot controls so unaffected decisions can be preserved.
Material shots are grounded against the mechanism they render. Structural
validation still covers the whole response; semantic actor checks apply when the
generic actor builder is used. Unused actor metadata cannot count as applied
direction. A structurally invalid actor or staging proposal is rejected for that
shot, with its reason retained; it does not discard valid proposals for other
shots. The application does not rewrite invalid model roles into accepted output.

The provenance journal distinguishes generated responses, accepted changes,
deterministic fallback and surviving renderer-affecting choices. The strict
comparison requires accepted, surviving non-text model decisions;
`ai_status.status = planned` alone does not satisfy that requirement. Renderer
provenance is separate from approval of the decoded result. Native Ollama
structured requests use `format=schema`, and every provider response still passes
the bounded schema and source-grounding validators.

Review exposes shot role, technique, framing, camera, timing and the requested
visible action. Individual edits such as **closer framing**, **wide view** or
**use 2D** affect only the chosen shot. A 3D edit requires a supported mechanism;
a collage edit requires existing distinct pictures. Older saved plans keep their
renderer until explicitly rebuilt. Material shots reject edits that change only
unused legacy composition or actor metadata. A combined edit reports the fields
that visibly affect the material diagram.

## Review the encoded movie, then verify repairs

`agents/render_review.py` decodes the produced MP4 instead of trusting the HTML
preview. It checks setup, action and result images, repeated layouts, blank or
nearly static stages, dimensions, frame rate, duration and the encoded soundtrack.
Samples bracket a validated contact cue when available; if no decoded result frame
fits after contact, motion remains unassessed.
Technical coverage is capped at 24 shots; longer films explicitly report their
sampling. Optional semantic review covers up to 12 shots using the configured
local vision model or free hosted route. Local-only mode keeps frames on the
computer. Each local request contains one shot/three frames; hosted requests
contain up to two shots/six frames.

Model observations must identify the supplied frames and describe visible
evidence. The reviewer receives the original punctuated narration, the shot
window and the scope of its excerpt. Setup/action/result names identify the
chronological samples; they do not require every establishing question or
intentional hold to depict a physical impact. Each review category is assessed
separately, including whether visible labels add unsupported factual claims.
Missing capability, incomplete coverage, uncertain observations or a
failed response remain **needs review**. A successful decode is not an artistic
approval. Audio checks measure levels, clipping and long silences; they do not
claim to hear intelligibility, musical fit or aesthetic quality.

The production workflow permits one repair pass, affecting at most three shots.
The worker API accepts zero to two passes, with at most one source redirection per
run. A reliable subject or action failure can trigger that source-grounded
redirection; camera, color or caption changes alone cannot repair a wrong subject.
The worker renders again using the same narration and mix, then reviews the new
encoded result. A changed plan alone is insufficient: the relevant decoded subject
pixels must change, and every targeted finding must clear without introducing new
errors or losing previously reliable review coverage. A semantic repair also needs
a reliable passing assessment of the repaired category. A rejected attempt restores
the previous MP4 and its matching editable preview.

The automatic subject-repair path requires an actual subject or action change.
When the drawings are appropriate but their labels make an unsupported claim,
the normal scene editor can correct that wording before another encode and
review. Such an inspection-driven editorial correction is recorded separately
from an accepted automatic subject repair.

The current run retains plans, frame evidence, hashes, review results and attempted
movies. Archives retain up to three attempts, 225 files and 1 GiB of review evidence;
each JSON or decoded evidence image is limited to 8 MiB. Oversized attempt movies
may be omitted with an explicit manifest entry; the accepted project movie is
archived separately.

## Sound design

Automatic mode still chooses actual licensed recordings, measures the voice and
mixes and masters the result through the production audio path. The sound director
sees the profile's uploaded audio and a bounded list of verified included
recordings, with the exact supported search phrase for each. Missing, altered or
unlicensed files are not offered. The model can choose an appropriate recording,
request another sound or leave a layer silent; acquisition still uses the normal
source matcher and preserves complete credits. The plan records the offered
recordings and the sound prompt's hash.

The sound director can anchor a purposeful accent to a unique measured spoken
word within a section. The application resolves that word to its real time.
When the model omits a word cue for a verified compatible metal-contact recording,
the app can resolve a unique, positively stated physical contact from measured
speech. It preserves explicit model cues and records whether timing came from the
model's word, the source-derived contact or the section boundary. An ambiguous or
unsupported contact retains its section-boundary timing, with the fallback reason
recorded. An arbitrary model timestamp, fabricated asset or test tone cannot become
a sound cue.

News and ASMR retain their restrained policies. Effects are bounded and are not
automatically added to every cut. A proposed recording counts as applied only
after it was acquired and successfully mixed; the soundtrack's actual credits
remain attached to the project.

## Reproduce the reference comparison

The committed reference manifest identifies Artem Novitckii's **Could ancient
armour stop a bullet?** at source commit
`5dbea55c23aa6a5c93dc3cfb5583840513b56b94`. Its editable source specifies 29.49
seconds and the documented delivery target is 1080 × 1920 at 30 fps, H.264/AAC
MP4. A frame-exact result is 885 frames, or 29.5 seconds: a 0.01-second rounding
difference. The author's available web excerpt is only 3.6 seconds and silent;
the manifest distinguishes that measured excerpt from the full source target.

From the repository root, after installing the normal Python, Node, FFmpeg and
browser dependencies:

```powershell
npm ci --prefix renderers/hyperframes
python renderers/hyperframes/comparable_demo.py --render --local-model qwen3.5:4b
```

Use an already installed local model or omit `--local-model` to use the selected
channel's configuration. The command never installs a model or changes saved
settings. A text-only local model cannot provide the required vision review.
The example voice remains the configured free Edge TTS service; local reasoning
does not make narration an offline service.

The harness records every section, measures real word events, fits the complete
recording to the target using bounded new voice-rate attempts, and allows only a
short labelled end pause. It uses the normal visual planner, automatic sound
director, mixer, HyperFrames encoder and encoded review/repair loop. It does not
substitute a hand-authored ideal storyboard or sound cue list. The original
narration qualifies broad historical claims using the linked museum sources.

Results and provenance are written to
`renderers/hyperframes/.smoke/comparable-armour/`. `--resume` reuses only verified
narration/assets. A strict run exits unsuccessfully if accepted, surviving non-text
model direction, applied sound, output specification or complete reliable semantic
review is missing. Receiving an unchanged or entirely rejected model response does
not meet the planning requirement. The explicit
`--allow-technical-fallback` option retains an incomplete demonstration with its
unmet requirements; it cannot turn that result into a completed comparison.

The separate `--replay-visual-responses <binding.json>` option can reapply
previously captured real local responses after a validator correction. It requires
`--resume` and verifies the measured inputs, model identity, prompt/schema code
and capture hashes. The report labels this as recorded-response replay, with the
original timestamps. Changing a prompt requires fresh inference. Replay applies
only to visual planning; sound design, encoding and semantic review still use
their normal live paths. The ordinary command above always generates new visual
responses.

`shot_smoke.py` and the earlier small samples remain synthetic regression
fixtures. They prove renderer behavior, not complete narrated production or
equality with a professionally directed reference.

## Update an existing installation

Stop the server, update the checkout and install the pinned renderer dependency:

```powershell
git switch main
git pull --ff-only origin main
npm ci --prefix renderers/hyperframes
.\.venv-yaas312\Scripts\python.exe gui.py
```

Use the Python environment normally used for this installation if its name
differs. In Settings, **Plan individual shots** and **Repair and check rendered shots** are
enabled by default. **AI visual review** enables semantic inspection. For an old
draft, choose **Rebuild animation + sound** in Review to plan new shots with the
existing narration. Merely encoding a saved storyboard preserves its design.

See the [renderer contract](../renderers/hyperframes/README.md),
[agent capability map](AGENT_CAPABILITIES.md) and
[sample evidence](samples/README.md) for implementation and verification details.
