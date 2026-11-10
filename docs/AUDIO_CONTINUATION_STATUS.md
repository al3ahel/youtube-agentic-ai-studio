# Armour demo: narration and sound continuation

The [current repair follow-up](ARMOUR_APPLIED_REPAIRS.md) tracks the requested
scene corrections, contact-priority fix and new Studio continuation. The fresh
recording documented here is complete and is reused for that continuation.

**Historical first fresh-audio run, before the additional repairs:** narration and
a first automatic mix/encode completed; semantic approval remained incomplete.
The user approved both publishing the review branch and regenerating narration
with Edge TTS. All five sections were recorded, all 84 returned word events match
the script, and the complete recordings are preserved in a verified checkpoint.
The first fresh film passed encoded-format checks but did not receive complete
semantic review. A precision bug in sound/storyboard binding has been corrected
and tested. The corrected normal continuation reused the preserved narration and
visual model captures, completed its mix/encode/review, and confirmed the fix.
That preceding run obtained valid responses for six of ten shots and remained
unapproved. See [the authorized audio run report](AUTHORIZED_AUDIO_RUN.md) for
that execution history, and [the applied repairs report](ARMOUR_APPLIED_REPAIRS.md)
for the latest film, soundtrack and review result.
The audit below describes the preceding preserved-audio continuation.

The visual continuation retains the complete original narration, music and effect.
No new speech request, model sound-selection request, remix or listening assessment
was performed for this audio audit.

## Approval and exact scope

The preceding session reported that automatic approval review rejected sending
the complete narration to Microsoft/Bing's Edge TTS service because that external
submission had not been explicitly confirmed. This is the recovered prior
approval result, **not a new rejection or an attempted request in this continuation**.

The subsequently approved action is to send the five narration sections
below, totaling 84 words, to the configured Edge TTS endpoint at
`speech.platform.bing.com` and receive generated speech plus timing events. The
configured voice is `en-US-AndrewNeural`, with pitch `+0Hz`. The earlier selected
recording used rate `+14%`; the current saved default is `+0%`. The normal harness
starts with the current profile rate and permits at most four complete recording
attempts to fit the target, adjusting the requested rate without cutting or
retiming returned speech. Saved profile settings have not been changed.

### Exact speech payload

1. **question:** Could old armour stop a bullet? Sometimes. The answer depends on the armour and the weapon.
2. **construction:** Mail armour uses linked rings. Steel plate armour can deflect some blows away from the body.
3. **reinforcement:** When firearms spread, steel armour was hardened, thickened, or given a reinforcing layer over the breastplate.
4. **proof:** In proof tests, bullets hit some steel breastplates. A surviving dent showed that particular shot had stopped.
5. **answer:** But one successful test was no universal guarantee. Armour and firearms evolved together for centuries; protection always had limits.

The authoritative input is [armor-script.json](../renderers/hyperframes/fixtures/armor-script.json).
The endpoint and WordBoundary request behavior are implemented by the installed
Edge TTS package and [narrator.py](../video/narrator.py). This service uses a network
connection; the local Ollama model does not synthesize the narration.

## Authorized normal continuation command

Run from the repository root with its installed Python, FFmpeg, browser and local
`qwen3.5:4b` dependencies available. The output directory must be new:

```bash
python renderers/hyperframes/comparable_demo.py --render --local-model qwen3.5:4b --output output/armour-full
```

The [normal comparison harness](../renderers/hyperframes/comparable_demo.py) records
speech, builds its measured timeline, plans visuals, selects and mixes automatic
sound, encodes the film and runs the strict review. The command was prepared and
its flags verified during the earlier audit, then executed after explicit
authorization using a new output directory. The fresh recording and subsequent
checkpoint continuations are documented in [the authorized audio run
report](AUTHORIZED_AUDIO_RUN.md). The harness does not upload the result to
YouTube. It may also acquire the comparison's declared museum assets if they are
absent. Model responses, available services and review outcomes can differ
between runs.

The [reference target](../renderers/hyperframes/fixtures/armor-reference.json) is
29.49 seconds, 1080 × 1920 at 30 fps. The nearest whole-frame delivery is 885
frames, or 29.5 seconds. The harness measures complete speech and permits a short
tail to that frame clock; it does not truncate words to make them fit.

Once complete raw narration and its selected voice attempt exist, preserve them
with the existing checkpoint command, even if visual review needs more work:

```bash
python renderers/hyperframes/comparable_demo.py --output output/armour-full --export-checkpoint checkpoints/armour-full
```

The checkpoint destination must also be new. Export makes no speech, sound-model
or rendering request. See [Shot pipeline repair](SHOT_PIPELINE_REPAIR.md) for
verification and restore behavior.

## What was available during the preceding audit

The then-latest interrupted fresh attempt contained an empty, zero-byte raw WAV and no
complete word-timing/SRT package. A targeted inspection of this workspace and the
preceding project workspace found no matching complete raw armour recording or
verified voice-cache bundle. No usable installed offline TTS runtime/model was
found in the checked project environments or model cache. No replacement model
was downloaded and no different speech service was called.

The complete original soundtrack is available in the
[published film](samples/comparable-armour/video.mp4). It contains narration plus
music and the old accent in one AAC track. Treating that mixed track as clean
narration would duplicate the music/effect during remixing and would misstate its
provenance. The preceding visual-only continuation therefore stream-copies the original
AAC track.

A fresh local check decoded both the published film and the completed selected
continuation (`a76c733fa652e5fabd19c8d3c241378ce232ddc68357b6b1b742d382db5cba64`)
using the production archive helper's `pcm_s16le` format, retaining the source
sample rate and channel count. Both decoded streams contain **5,664,768 bytes**
and have SHA-256:

```text
3b220e7db1c8369c33e3744c925c464bfc8824747d39e20c3ae587e09c79b00a
```

This proves soundtrack identity for those two files under that decoding format.
It does not assess intelligibility, musical fit or subjective loudness. Earlier
reports used a different PCM sample format; their hashes must not be compared
directly to this `pcm_s16le` hash.

## Retained sound limitation and the supported next mix

The [original sound plan](samples/comparable-armour/sound-design.json) selected
*Cylinder One* as a quiet music bed and one generic metal-contact recording at
**4.600 seconds**, at the start of the mail-construction section. It did not select
a proof-impact accent. That inherited cue remains in the preserved soundtrack.

A read-only call to the current production timing resolver rejects that old
construction contact proposal with `source_contact_not_unique`: the construction
narration does not establish one positively stated metal impact to synchronize.
The preserved source and visual-only storyboard instead expose one eligible illustrative metal
contact, **scene-007 at 17.134458 seconds**, aligned to the returned word **“hit.”**
Eligibility does not force the sound model to select an effect, and this audit
did not insert or move one. These timings describe recorded metadata and planned
visual contact, not a listening judgment about a prominent audible transient.

All five existing bundled recordings matched their catalog hashes in the local
audit: *Cylinder One*, *Cylinder Five*, book page turn, small whoosh and metal
surface impact. Their retained catalog entries include CC BY 4.0 attribution and
source provenance; see [the catalog](../assets/audio/bundled/catalog.json). Fresh,
verified narration is now available for a new automatic mix using those local
recordings. The first fresh automatic mix has completed; its timing-binding
limitation and the subsequent correction are recorded separately. The ordinary
mixer accepts a properly bound visual plan and
enforces its contact checks; no alternate mixer or archive-audio workaround is
needed.

**Subsequent evidence:** the authorized complete speech run, measured word
coverage, first automatic mix and encoded-format checks are recorded in
[the authorized audio run report](AUTHORIZED_AUDIO_RUN.md). The corrected
sound-binding continuation is complete: storyboard recognition is fixed, but the
actual mix still retains the earlier page turn and lacks a proof-impact accent.
A separate listening review remains outstanding. None of these is implied by preservation of the old
soundtrack or by the preceding local timing audit.
