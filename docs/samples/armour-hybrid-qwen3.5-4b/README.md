# Armour Hybrid sample — local Qwen3.5:4b

**Actual model: local Ollama `qwen3.5:4b`.** This edited film combines museum
photographs with animated explanations. Automated status: **`needs_review`**.

[Manifest](sample.json) ·
[Complete audio/photo credits](credits.txt).

**Video upload pending.** The original 29.5-second film, fresh narration and mix
are complete. The GitHub transfers did not return a success result, and neither
attempted movie blob was present on readback. This page records the completed
original film; it does not claim that the movie is available in this repository.

The code fixes were merged in [PR #2](https://github.com/al3ahel/youtube-agentic-ai-studio/pull/2)
on 10 November 2026 at 06:07 Dubai time. All 1,886 local Python tests and all
80 browser tests passed, with no skips. These are local validation results.

| Component | Actual selected result |
| --- | --- |
| Delivery | 29.5 seconds; 1080 × 1920; 30 fps; 885 frames; H.264/AAC |
| Narration | Supplied 84-word, five-section script; Edge `en-US-AndrewNeural`, +14% rate |
| Visuals | Three credited museum photos; two collages, six 2D shots, two 3D views; zero footage clips |
| Model | Local Qwen3.5:4b scene direction, automatic sound choices and image review |
| Sound | Chris Zabriskie's *Cylinder One* plus licensed metal-contact foley |
| Audio measurement | Delivered AAC: −16.95 LUFS integrated; −1.70 dBTP true peak |

## What produced this sample

Qwen directs structured scene decisions; Hyperframes renders the movie. The
script and photographs were supplied. ChatGPT contributed code fixes and five
editorial requests following inspection. The normal scene editor applied:

| Scene | Visible correction | Accepted edit path |
| --- | --- | --- |
| 002 | Distinct close photo composition; **Armour and weapon** | Model contribution |
| 003 | **Linked rings** matches the mail detail | Model contribution |
| 005 | 3D plate depth and orbit; **Hardening the Steel** | Model contribution |
| 006 | 3D **Reinforcing layer** replaces the unsupported gap heading | Local controls |
| 010 | **Armour and firearms**, with correct object labels | Local controls |

The final Studio continuation reused the corrected plan and complete narration,
then ran automatic sound, rendering and review. This includes editorial work;
it is not an untouched autonomous or default-model generation.

The separately archived [older comparable-armour sample](../comparable-armour/README.md)
is a different movie and retains its own run evidence.

## Review and remaining limits

All ten final review responses are valid; eight meet the validator's reliability
requirements. Scene 003 retains motion uncertainty, scene 009 is uncertain across
all three criteria, and scene 010 has a technical static-action warning. No
additional automatic scene repair was applied. `passed` remains `null`.

Independent inspection covered all 30 setup/action/result samples. Scene 002
shows armour photographs only; its weapon reference is verbal. Scene 005 shows
plate depth and an orbit, not heat treatment. Scene 006's separated plates are
an exploded diagram, not evidence of an air gap; its setup remains small.
Scene 009 remains sparse. The closing comparison labels its objects correctly
but does not depict centuries of evolution. Model descriptions can still be
wrong even when structurally valid. Continuous playback, perceptual listening,
physical accuracy and full reference parity remain unassessed.

## Regenerate direction with a local model

For an existing project with complete narration and photographs:

1. Open the owning channel's settings. Under **Writing models**, choose
   **Local only — Ollama**, find and select installed **qwen3.5:4b**, then save.
2. Enable **AI scene planning**, **Plan individual shots**, **AI visual review**
   and **Repair and check rendered shots**. Set sound direction to **Automatic**,
   intensity **Low**, and enable **Audio mastering**; save.
3. On **Home**, select **Hybrid**, leave Topic blank, and open **More options →
   Redo only some steps**. Select only **Music + Storyboard + Render**; leave
   **Research**, **Script**, **Photos / footage** and **Voice** off to reuse existing
   inputs. Turn off **Draft preview first** and run the selected steps.

Render-only buttons reuse the saved plan and mix. A different free-model route
can select a different model; settings, accepted proposals, renderer components,
assets and editorial work also affect the result. New inference can vary and
does not reproduce these five edits automatically. Local Qwen inference does
not make Edge speech generation an offline operation.

Motion and Hybrid share graphic planning, rendering and review. Hybrid adds
photo/footage selection and framing; these still need subject, crop, action and
license checks. This sample exercises photos plus graphics, with **no stock-video
quality test**. See the [full repair, model and reproduction report](../../ARMOUR_APPLIED_REPAIRS.md)
for exact settings, source bindings and review evidence.
