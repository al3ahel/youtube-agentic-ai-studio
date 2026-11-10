# Shot meaning, sound timing and reliable continuation

This update tightens the connection between the words spoken during a shot, the
action drawn in that shot, and its sound cues. It also adds complete recording
checkpoints and an explicit way to continue visual work using a published movie's
existing soundtrack.

The changes apply to the normal planner, scene editor, renderer and encoded
review worker. A sample's current acceptance status belongs in its saved review
report; the presence of these features does not approve an individual film.

The subsequent [opening, ending and review-evidence continuation](ARMOUR_CONTINUATION_REPORT.md)
records the next editorial pass and the remaining narration/sound approval.

## What changes in the pictures

| Previous failure | Updated behavior |
| --- | --- |
| A subtitle straddled a cut, so a mail detail was evaluated against the following steel-plate sentence. | Planning, editing and review use the measured words belonging to the current shot. Sentence context preserves questions and negation without importing a neighboring action. |
| A model replaced a narrated material interaction with a motionless reveal. | The planner retains the supported interaction and records the rejected omission while accepting other valid design choices. |
| A description of deflecting unspecified blows either showed no action or suggested an unsupported bullet. | A `glance` illustration shows incoming and redirected force arrows. A projectile is drawn only when the source supports one. |
| A 3D steel plate had visibly faceted shading. | The curved plate uses a more detailed surface and normals that follow its curvature. |
| Reinforcing steel appeared as generic rectangular slabs. | Steel-layer views use separate curved breastplate shells, distinct presentation tones and labels connected to their corresponding parts. Both shells remain the same stated material. |
| A lone subject remained faint until its name occurred late in the narration. | The subject establishes early; its spoken word still controls the later emphasis. The timing report identifies the combined timing method. |
| An explicitly named concrete subject, such as Armour or Firearms, was replaced by a generic concept cube. | An exact source-supported catalogue label retains its concrete actor. Abstract labels such as Armour limits can still use a concept illustration, and mechanism-part labels remain available. |

The material views remain qualitative illustrations, labeled as not to scale.
Force arrows explain direction; they do not calculate ballistic performance.
The plate's appearance does not simulate heat treatment or establish a material's
actual protective rating.

The existing settled-result behavior is preserved: a later dent view can retain
the preceding deformation without introducing a second projectile or replaying
contact. A held result and an establishing view can be appropriate shots; a
specific narrated interaction still needs a visible explanation.

## Each shot owns its spoken words

[The narration-scope helper](../agents/narration_scope.py) provides the same
source excerpt to planning, editing and encoded review. A timed word belongs to
the shot in which that word starts. When the complete section's returned word
events align with the original narration, the excerpt retains the source's
punctuation.

For example, if the recording says:

> Mail armour uses linked rings. Steel plate armour can deflect some blows away from the body.

the mail shot should explain linked rings. The next shot owns the plate and
deflection explanation, even if a displayed subtitle briefly includes words from
both shots. The complete original section remains in the saved audit evidence.
The vision prompt receives the current excerpt and limited grammatical context;
unrelated section sentences are omitted when word alignment is exact.

That context also prevents a cut from dropping the beginning of a question or
negation. An excerpt containing “steel armour stop a bullet” cannot turn the
original “We did not see steel armour stop a bullet” into an asserted impact.
Repeated ambiguous excerpts do not borrow a convenient positive occurrence.
These are bounded language checks, so decoded review is still necessary.

When word events exist but do not align with the source text, the evidence is
marked unverified and cannot establish reliable subject approval. A gap in
measured words does not borrow a later sentence. Older projects without word
events retain explicitly labeled subtitle-overlap or duration-based fallback
scope.

## Sound follows the current visual plan

For Motion and Hybrid runs that rebuild visuals and automatic sound, the worker
now plans the shots before choosing the soundtrack. The sound director can
therefore inspect both the measured narration and the current planned contact.

For a verified supported contact recording, the resolver requires a compatible
material, one positively stated physical contact and an unambiguous measured
word. If the current storyboard matches the script, channel and complete clock,
it also requires one corresponding visible interaction. The accepted cue records
the shot identity, measured word time and planned visual contact time.

A metal impact is not automatically placed at a section boundary when narration
only names rings, reveals a plate, adds a layer or discusses an existing dent.
Ambiguous, hypothetical, negated, mismatched or unsupported contact proposals
are omitted with a reason in `sound_design.json`. An explicit model word cue
must pass the same contact checks. Other supported music, ambience and effect
choices continue through the normal acquisition and mixing path.

The prompt offers eligible source-derived contact cues to the sound model. The
model still decides whether an accent is useful; an eligible contact does not
automatically add a sound. If a supplied storyboard is stale or belongs to a
different channel, it cannot override the current narration's timing. That case
is recorded, and the resolver uses only the applicable source evidence.

These changes affect new sound-design runs. An existing saved mix retains its
recorded timing when reused. Encoded audio checks still measure levels, silence
and clipping; they do not constitute listening approval for intelligibility or
musical fit.

## Choose the continuation workflow

| Workflow | Narration and sound | Visual planning and review | What its status means |
| --- | --- | --- | --- |
| Normal comparison | Records narration through the configured voice service and runs normal automatic sound design. | Uses the configured model route, renderer and encoded review. | Completion requires all normal comparison gates. |
| Resume an existing run or restored checkpoint | Reuses matching verified recordings and assets; automatic sound runs again. | Normal direction and review make fresh requests unless visual-response replay is explicitly selected. | Reuse is recorded separately from new inference. |
| Continue from a published movie | Preserves that movie's entire existing narration and mixed soundtrack. | Makes fresh local Ollama planning and review requests through the production APIs. | This visual-only workflow does not regenerate narration or mix. The separately authorized fresh audio is complete; see [the current armour follow-up](ARMOUR_APPLIED_REPAIRS.md). |

### Normal complete comparison

After installing the usual Python, Node, FFmpeg and browser dependencies, and an
appropriate local text/vision model, run from the repository root:

```powershell
python renderers/hyperframes/comparable_demo.py --render --local-model qwen3.5:4b --output output/armour-full
```

The specified local model must already be installed. The command does not
download models or alter saved profile settings. The configured Edge TTS voice
uses its network speech service; local model inference alone does not make this
whole workflow offline.

The existing reference target remains unchanged. Its [manifest](../renderers/hyperframes/fixtures/armor-reference.json)
specifies the topic, source timeline and delivery format. It also distinguishes
the available short silent reference excerpt from the full editable reference
timeline. Neither continuation mode establishes reference quality parity.

### Export a resumable recording checkpoint

Once a normal run has complete recorded narration, its original voice attempt
and a measured timeline, export them together:

```powershell
python renderers/hyperframes/comparable_demo.py --output output/armour-full --export-checkpoint checkpoints/armour-full
```

The checkpoint directory must be new and outside the source run directory.
Export is a standalone local file operation; it makes no model, speech-service
or render calls. A failed visual review does not prevent saving otherwise
complete recorded inputs.

The package keeps the raw narration, original selected voice recording, timing
metadata, subtitles, script and reference. It also retains available acquired
images, source metadata, model replies, scene plans, mixed audio and comparison
reports. Every included file has a size and SHA-256 entry in `checkpoint.json`.
Unrelated settings and credentials are outside the package's data allowlist.

Export verifies the recording chain and rebuilds the padded narration and
timeline on temporary copies. Restore checks file hashes, image and soundtrack
bindings, bounded sizes and safe relative paths before creating the new run.
Changed files, missing raw recordings and symbolic links are rejected.

An MP4 and a contact sheet alone are insufficient for this kind of checkpoint.
In particular, the original published armour sample does not contain the raw
voice-attempt files needed to restore a normal narration run.

### Restore and resume

```powershell
python renderers/hyperframes/comparable_demo.py --resume-from-checkpoint checkpoints/armour-full --output output/armour-restored --render --local-model qwen3.5:4b
```

The output directory must be new. The previous report is preserved under
`checkpoint_source/`, and `checkpoint_origin.json` records the restoration.
The subsequent comparison report includes that origin and distinguishes reused
recordings from new recording attempts.

Narration reuse still requires the matching script, selected voice, pitch,
Edge TTS package version and verified original audio. If those conditions no
longer match, the normal pipeline can request a fresh recording. Planning,
automatic sound and semantic review also follow their normal live paths.
Restoring files itself makes no service calls; the resumed pipeline uses the
configured services.

The existing `--replay-visual-responses` option remains separate. It requires an
explicit sealed response binding and unchanged measured inputs, model identity,
prompt and schema. It labels visual-response replay as replay, and does not
replace live sound or semantic review.

### Continue visuals with an archived soundtrack

When complete raw narration inputs are unavailable, use the published movie's
verified soundtrack for a specifically scoped visual continuation:

```powershell
python renderers/hyperframes/rerender_archive.py --local-model qwen3.5:4b --output output/armour-visual-continuation
```

The default source is `docs/samples/comparable-armour`; `--source` can select
another archive with the same provenance structure. `--local-url` selects a
supported loopback Ollama address. `--max-repairs 0`, `1` or `2` controls the
bounded repair loop. The output directory must be new.

This command verifies the source movie, script and portable plan against their
published hashes. It copies the exact archived section, caption and word
positions, checks the existing museum images, and calls the normal visual
planner with `use_ai=True`. The archived storyboard is source evidence; the
normal planner produces the new storyboard.

The original full mixed soundtrack is decoded locally for the renderer. After
each encode, the original AAC stream is copied into the new video without
trimming, mixing or audio re-encoding. The encoded review therefore inspects
the video with the retained soundtrack. Before accepting the result, the command
compares the decoded original and final PCM hashes and checks the target format.

Only installed local models are used. No narration service, new stock search,
hosted model or sound mixer is called. Model and asset credentials are disabled
for this run without changing saved settings. The renderer and browser must
already be installed.

The retained soundtrack includes its original effect timings and musical
choices. This workflow cannot demonstrate a newly synchronized contact accent,
new music selection or new mastering. This workflow does not regenerate narration
or sound, even when its new visual review passes. The separately authorized
fresh narration and automatic mix are complete; their actual continuation and
remaining review limitations are recorded in [the current armour follow-up](ARMOUR_APPLIED_REPAIRS.md).

### Read the saved result

A visual continuation writes its evidence as the corresponding stages finish:

| File or directory | Purpose |
| --- | --- |
| `archive_rerender.json` | Current phase, source hashes, original gates, responding local runtime, review result and outstanding requirements. |
| `archive_source/` | Copies of the original published source reports and measured plan. |
| `archived_timeline.json` | Recovered measured positions and explicit archived-audio origin. This is not a new raw narration manifest. |
| `archived_complete_mix.wav` | Locally decoded original narration plus its existing soundtrack. |
| `scene_plan.json` | New plan produced by the ordinary planner. |
| `model_responses/` | Actual captured local model replies, labeled by stage. |
| `visual_qa.json` and `render_review/` | Encoded-frame observations, technical/audio measurements and repair evidence. |
| `final_short.mp4` | The encoded result when rendering succeeds, with the original AAC soundtrack preserved. |

`visual_review_passed` describes the new visual check. The separate
`comparison_complete` field remains `false` for archived-audio continuation.
The command returns success only when its visual review passes; a nonzero result
can still retain a rendered movie and evidence requiring further review.

The review samples selected setup, action and result frames. It does not inspect
every animation frame, establish smooth playback, listen to the soundtrack, or
replace a creator's final artistic judgment. Regression checks protect the
behavioral contracts above; a particular film must earn its own decoded review.

## Implementation map

| Area | Source |
| --- | --- |
| Current-shot narration and grammatical context | [narration_scope.py](../agents/narration_scope.py) |
| Source-supported actions and contact clocks | [shot_planner.py](../agents/shot_planner.py) |
| Planning, actor reveal timing and scene editing | [visual_director.py](../agents/visual_director.py) |
| Material geometry, force directions and part labels | [shots.js](../renderers/hyperframes/templates/shots.js) |
| Encoded evidence and bounded repair acceptance | [render_review.py](../agents/render_review.py) |
| Contact eligibility, omissions and soundtrack choices | [audio_director.py](../agents/audio_director.py) |
| Visual-before-sound production ordering | [runner.py](../studio/runner.py) |
| Complete recording checkpoints | [comparable_demo.py](../renderers/hyperframes/comparable_demo.py) |
| Local visual continuation with preserved AAC | [rerender_archive.py](../renderers/hyperframes/rerender_archive.py) |

See [Shot pipeline overview](SHOT_PIPELINE_UPDATE.md) for the wider collage,
footage, 2D/3D, editor and review architecture.
