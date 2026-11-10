# Renderer verification samples

## Hybrid armour sample with local Qwen3.5:4b

[Sample, credits and reproduction](armour-hybrid-qwen3.5-4b/README.md) ·
[Model and run manifest](armour-hybrid-qwen3.5-4b/sample.json).

**Video upload pending.** The completed film's model and run records are below;
the movie is not yet included in this repository.

| Property | Selected repaired film |
| --- | --- |
| Delivery | 29.5 seconds; 1080 × 1920; 30 fps; 885 frames; H.264/AAC |
| Actual model | Local Ollama `qwen3.5:4b` for direction, automatic sound and image review |
| Inputs | Supplied 84-word script, complete Edge Andrew narration at +14%, three museum photographs |
| Hybrid composition | Two collages, six 2D shots, two 3D views; no stock-video clip |
| Editorial work | Five scene edits: three with accepted model contributions, two using local controls |
| Review | Ten valid responses, eight reliable assessments; scenes 003/009 uncertain; scene 010 static-action warning; `needs_review` |

This is an inspected and edited sample, not an autonomous default-generation
benchmark. See [the repair report](../ARMOUR_APPLIED_REPAIRS.md) for exact
provenance, remaining limitations and the free/local-model rebuild settings.

## Historical narrated local-model armour comparison

This earlier movie and its evidence remain unchanged. Its results belong to
that run, not to the newer Hybrid sample above.

[Watch video.mp4](comparable-armour/video.mp4) ·
[Detailed run and limitations](comparable-armour/README.md) ·
[Ten-shot overview](comparable-armour/overview.jpg) ·
[Before/after frames](comparable-armour/before-after.jpg) ·
[All decoded shot phases](comparable-armour/contact-sheet.jpg).

| Property | Measured or recorded result |
| --- | --- |
| Encoded delivery | 1080 × 1920, 30 fps, 885 frames, 29.5 seconds, H.264/AAC MP4 |
| Narration | Complete 84-word script; all returned word events match the script in order |
| Sequence | Ten shots in five narration sections; two collages, six 2D views, two 3D views |
| Planning | Real local `qwen3.5:4b` generation; seven accepted proposals retain renderer-affecting non-text choices |
| Sound | Real local-model Automatic sound direction; licensed music and a construction-section metal accent |
| Editorial loop | Four checked per-scene model edits after decoded inspection, plus a general collage-caption occlusion repair |
| Evidence | Complete movie, decoded phases, original/portable plan bindings, actual response captures, credits and measured checks |

The original source target is Artem Novitckii's **Could ancient armour stop a
bullet?**, pinned to its 29.49-second editable source and documented delivery
format. 885 frames rounds that duration to 29.5 seconds. The available public
excerpt is only 3.6 seconds and silent; this sample does not establish parity
with the full reference's production quality.

The [comparison report](comparable-armour/comparison.json) and
[visual QA](comparable-armour/visual-qa.json) retain the actual automatic-review
status: **rendered, needs review**, with one reliable shot pass, seven uncertain
judgments and two rejected inconsistent replies. `comparison_complete` is false.
Independent frame inspection and audio measurements do not turn an unreliable
model verdict into an approval.
Sound has not received a perceptual listening assessment. Reproduction and the
exact editorial requests are documented with the sample and in the
[pipeline update](../SHOT_PIPELINE_UPDATE.md).

## Earlier deterministic scene fixtures with licensed sound

| File | Dimensions | Duration | Frame rate | Decoded video frames | Streams |
| --- | --- | --- | --- | --- | --- |
| [directed-motion-landscape.mp4](directed-motion-landscape.mp4) | 640 × 360 | 14 seconds | 12 fps | 168 | H.264 + AAC |
| [directed-motion-portrait.mp4](directed-motion-portrait.mp4) | 360 × 640 | 14 seconds | 12 fps | 168 | H.264 + AAC |

The deterministic local planner produces three connected ideas: floodwater reaches
coastal homes, a floating home rises with water, and solar panels feed a battery
and home. It selects the actors, their roles, the action, camera and a shared
palette. The fixture does not manually substitute an ideal storyboard after
planning. The subject is a qualitative hypothetical illustration, not a forecast,
geographical map or engineering specification.

**No remote AI, live footage search or speech service is used by this fixture.**
Phrase cues and the 14-second clock are authored. The soundtrack contains Chris
Zabriskie's *Cylinder One* and a short licensed whoosh at the two scene changes
(4 and 10 seconds). The listening balance is authored for this music-only example;
production sound design measures the user's voice. The examples do not establish
narration quality, word-level alignment or successful live Pixabay access.

[Audio credits](directed-motion-audio-credits.txt) ·
[Encoded-media verification](directed-motion-verification.json) ·
[Landscape frame](../screenshots/directed-motion-landscape.png) ·
[Portrait frame](../screenshots/directed-motion-portrait.png) ·
[Capability and pipeline map](../SCENE_DIRECTION_UPDATE.md).

Reproduce with the installed dependencies and browser described below:

```bash
python renderers/hyperframes/directed_demo.py
python renderers/hyperframes/directed_demo.py --portrait
python renderers/hyperframes/objects_sound_demo.py --directed --input renderers/hyperframes/.smoke/directed-landscape
python renderers/hyperframes/objects_sound_demo.py --directed --input renderers/hyperframes/.smoke/directed-portrait
```

The final files are `directed-motion.mp4` within those output directories. The
original silent-clock render and applied sound report remain alongside them.
The audio helper stream-copies video, preserving its decoded frames.

## Previous version-1 illustrated animation

| File | Dimensions | Duration | Frame rate | Video frames | Streams |
| --- | --- | --- | --- | --- | --- |
| [illustrated-motion-landscape.mp4](illustrated-motion-landscape.mp4) | 640 × 360 | 14 seconds | 12 fps | 168 | H.264 + AAC |
| [illustrated-motion-portrait.mp4](illustrated-motion-portrait.mp4) | 360 × 640 | 14 seconds | 12 fps | 168 | H.264 + AAC |

These clips show a census ledger/scanner and a mining cutaway with a moving drill
and conveyor. The input is a general explanation of records and extraction; it is
not a claim about a particular country, census or mine. The diagrams are
qualitative and the drawn people/ore do not encode numerical measurements.

**The soundtrack contains actual licensed recordings, with no narration or test
tones:** Chris Zabriskie's *Cylinder One*, a paper turn at 3.5 seconds and a short
whoosh at the 7-second chapter change. The phrase cue times are authored for
this example, not measured speech alignment. The listening balance is authored
for a music-only sample; production Automatic mode measures the user's actual
voice and uses different bed/effect ratios.

[Complete video-description credits](illustrated-motion-audio-credits.txt) are
generated by the same credit formatter as the application. The source files,
licenses, hashes and transformations are documented in
[the included collection](../../assets/audio/bundled/ATTRIBUTION.md).
The [encoded-media verification](illustrated-motion-verification.json) records
dimensions, frame counts, unchanged decoded video hashes, exact 672,000 stereo
audio frames at 48 kHz, and measured AAC loudness/true peak. The music-only example
measures −23.71 LUFS and −1.9 dBTP in both orientations.

### Reproduce the illustrated samples

With the dependencies below installed, run from the repository root:

```bash
python renderers/hyperframes/objects_demo.py --output renderers/hyperframes/.smoke/objects-landscape
python renderers/hyperframes/objects_demo.py --portrait --output renderers/hyperframes/.smoke/objects-portrait
python renderers/hyperframes/objects_sound_demo.py --input renderers/hyperframes/.smoke/objects-landscape
python renderers/hyperframes/objects_sound_demo.py --input renderers/hyperframes/.smoke/objects-portrait
```

Each directory contains `illustrated-motion.mp4`, `audio-credits.txt`, the plan,
the original silent clock, the unmastered/mixed recordings, and a measured
`listening-sample.json`. The soundtrack helper validates all three source hashes,
uses the production music/effect/mastering functions, and stream-copies the
already encoded video; it cannot change the video frames. No inference provider,
speech service or live media search is used by this fixture.

## Original template/media verification

The original landscape and portrait verification renders remain below.
**Each contains exactly 168 video frames: 14 seconds at 12 fps.**

| File | Dimensions | Duration | Frame rate | Video frames | Streams |
| --- | --- | --- | --- | --- | --- |
| [motion-landscape.mp4](motion-landscape.mp4) | 640 × 360 | 14 seconds | 12 fps | 168 | H.264 + AAC |
| [motion-portrait.mp4](motion-portrait.mp4) | 360 × 640 | 14 seconds | 12 fps | 168 | H.264 + AAC |

Seven two-second scenes cover `text`, `flow`, `comparison`, `timeline`, `chart`,
`explain` and `footage`. The picture is a locally generated raster, the footage is
an FFmpeg test pattern, and the chart uses explicitly synthetic values.
**Both clips contain audible synthetic test tones.** They verify encoding, media
handling and audio presence; they do not demonstrate narration quality or alignment
to spoken words. No NASA photograph appears in these two clips.

[The landscape poster](../screenshots/motion-photo-demo.png) is extracted from the
same landscape MP4 at 7.25 seconds.

## Reproduce and verify

Use the supported Python environment, Node.js 22+, FFmpeg/ffprobe and an installed
Chrome/Chromium. Install the pinned renderer dependencies:

```bash
npm ci --prefix renderers/hyperframes
```

From the repository root, reproduce both 168-frame outputs:

```bash
python renderers/hyperframes/smoke.py --seconds-per-scene 2 --output renderers/hyperframes/.smoke/release-landscape
python renderers/hyperframes/smoke.py --portrait --seconds-per-scene 2 --output renderers/hyperframes/.smoke/release-portrait
```

Each command writes `motion_preview.mp4` in its output directory, alongside the
scene plan, generated assets and local HTML project. The checked-in samples were
copied directly from those outputs. Add `--prepare-only` to build the project
without encoding. No API keys, network image search or speech service are used.
`HYPERFRAMES_BROWSER_PATH` can select an existing compatible browser.

Count decoded video frames in each checked-in sample:

```bash
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_read_frames -of json docs/samples/motion-landscape.mp4
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_read_frames -of json docs/samples/motion-portrait.mp4
```

Both should report `r_frame_rate: "12/1"` and `nb_read_frames: "168"`.

## Separate optional photo demonstration

`renderers/hyperframes/demo.py` still uses the bundled NASA photograph to create
a separate 18-second, 24 fps silent photo demonstration. Running it replaces the
sample filenames and poster above with that different demonstration. Use
`smoke.py` above to reproduce the checked-in 168-frame verification clips.

The retained photograph's attribution is:

- **Photograph:** NASA / Johnson Space Center (JSC), Skylab above Earth.
- **NASA asset:** `sl4-143-4707` — [original source record](https://images.nasa.gov/details/sl4-143-4707).
- **Bundled file:** [skylab.jpg](skylab.jpg), originally retrieved through YAAS's NASA image adapter.
- **Usage information:** [NASA Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/).
