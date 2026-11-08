# Illustrated Motion and automatic licensed sound

This update addresses the reported blue text-card output, missing music/effects,
and confusing soundtrack report. It preserves the previous channel, schedule,
project ownership, narration, preview-revision and publishing boundaries. The
changes are available from the normal Motion and Automatic sound workflows; no
extra paid model or personal agent-skill installation is required.

## What was wrong and what changed

| Reported symptom | Verified cause | Implemented behavior |
| --- | --- | --- |
| Animated output looked like two text boxes | The supported explain/text renderer largely animated card entrances and emphasis. Palette/composition changes did not create a pictured subject. | New and explicitly rebuilt plans select narration-grounded vector subjects. Census records build, drills work, ore moves, light travels toward panels, and other supported subjects have meaningful moving parts. |
| The same appearance survived a code update | Saved plans deliberately preserve their own design and prepared soundtrack; rerendering is not replanning. | Older Motion card plans expose **Rebuild animation + sound**, which runs Music, Visuals and Video while preserving script, recorded voice, measured clock and publishing prose. |
| A long AI request found no instrumental music | Search used artistic instructions, exclusions and use-case wording as literal required terms. Unusable candidate bytes could stop broader same-subject attempts. | Literal acoustic queries, independent instrumental checks, bounded related variants, and continued attempts after unusable files. Slow/calming constraints are retained in included-collection selection. |
| Common music/transition requests depended on a live search succeeding | There was no included source for those verified sound families. | Two real ambient instrumental excerpts, a book page turn and a short whoosh are shipped under CC BY 4.0. Compatible requests resolve offline; specific unmatched requests still use bounded Commons search. |
| A valid layer was too quiet, or a short effect played silence | Fixed low gain was applied to differently mastered recordings, followed by strong ducking. Some short clips contained leading silence. | Automatic mode measures source and voice levels, bounds gain/peak, trims transient onset silence, and uses gentler speech ducking. Source files and Manual ratios remain unchanged. |
| The report described a bed that had been omitted | AI prose described intended sound and appeared next to actual results without a clear distinction. | The applied summary is derived from committed layers. The proposal is separately labeled and collapsed. Failed/empty mixes do not count as applied sources. |
| Credits were hard to retrieve | The description formatter already preserved credits, but Review did not expose its exact block directly. | Review provides the complete **Audio credits — added to the video description** block, with copy and text-download actions. Upload still validates the current soundtrack and full description. |

## Using the update

1. Stop the running YAAS server, update this fork's `main`, and restart the same
   Python environment. On Windows the application entrypoint is `gui.py`; keep
   `--port 8765` if that is the port you use.
2. Open **Settings → Sound design**, or `/#settings/audio`. Set **Sound direction**
   to **Automatic — let the agent choose** and save if automatic choices are wanted.
   Existing explicit Manual/Off preferences are retained on upgrade.
3. For an older Motion card draft, open Review and choose **Rebuild animation +
   sound**. It replaces the old visual designs and soundtrack, but does not rerun
   Research, Script or Narration. If only audio needs changing, use **Regenerate
   sound design** instead.
4. Inspect the new preview, **In this soundtrack**, applied recording names and
   exact credits, then render final. Publishing remains a separate explicit action.

Home's **Make another video** remains the way to start a separate topic. Rebuilding
the current draft is not a new-topic request. Project ownership and the expected
storyboard revision are checked before accepting any rebuild.

## Why the Pixabay key does not provide music

[Pixabay's public API documentation](https://pixabay.com/api/docs/) documents
image and video access. It does not document a music/sound-effect search endpoint.
The website's audio downloads are therefore not unlocked through YAAS by adding
an image/video API key. Optional personal audio imports remain available; they
are not a prerequisite for Automatic sound.

The included audio collection uses licenses that allow both adapted video use
and redistribution of the recordings with attribution. See the
[complete source/license manifest and transformation notes](../assets/audio/bundled/ATTRIBUTION.md).
The application code's MIT license does not replace those recording licenses.

## Scope and evidence

The illustrated renderer is a bounded HTML/SVG animation system. Its supported
subjects include people/census, mining, solar, battery, circuit, water, plant,
network, trade, conflict, document, orbit and buildings. Unknown subjects use a
labeled abstract diagram. It does not generate arbitrary cinematic footage,
photorealistic characters, geographic evidence or invented statistical values.
Pure Motion still rejects automatic old-photo reuse. Hybrid and the explicit
photo workflow retain their existing source-bound media behavior.

The [new playable samples](samples/README.md#illustrated-animation-with-licensed-sound)
use census/mining graphics and actual licensed recordings in both orientations.
Each has 168 video frames at 12 fps. They contain no narration; their phrase cues
and listening balance are authored for a music-only example. They are not a
recreation of the user's original video or a test of its unseen source audio.

The separate real-FFmpeg regression in `tests/test_bundled_audio.py` executes
the application sound pipeline with the reported long query, the actual included
recordings, a controlled voice/tone fixture and a measured pause. It checks music
and both effects, unchanged original voice bytes, exact 24-second length,
current-owner attribution, complete description credits and the final audio hash.
The fixture's tone is confined to tests and is not a fallback used in generated
videos. Loudness and signal measurements are not a human listening verdict.

Browser checks inspect actual illustration actors, label bounds, reverse seeks,
old-photo compatibility, truthful sound reporting, credit copying and the guarded
rebuild request. Python checks cover source selection, silence rejection, cue
onset, current-project credits, scene boundaries and existing pipeline workflows.
The [pipeline map](PIPELINE_ARCHITECTURE.md) and
[agent capability contract](AGENT_CAPABILITIES.md) document the preserved boundaries.

Final local verification on 2026-11-08:

| Check | Result |
| --- | --- |
| Complete Python suite, with the real browser path configured | **1,148 passed**, no skips |
| Complete renderer/Studio browser suite against final templates | **41 passed**, no skips |
| Pinned HyperFrames compiled-composition check | Passed; silent source-clock and small secondary-chrome warnings are documented in the fixture |
| Final licensed landscape/portrait clips | **168 decoded video frames each**, 14.000 seconds, 12 fps, unchanged decoded video after sound mux |
| Decoded sample soundtrack | **672,000 stereo audio frames at 48 kHz**; −23.71 LUFS, −1.9 dBTP; actual music and two effects |

The old empty-library wording and pre-illustration fixture expectations were
updated in the browser suite. Its checks still cover profile-isolated imports,
delayed responses, exact saved render policies and both old/new draft behavior.

The source collection is intentionally small. A requested instrument, environment
or style outside it still needs a compatible live result; the agent must not
relabel unrelated audio simply to make every request appear successful.
