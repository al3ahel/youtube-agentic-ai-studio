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

1. Stop the running YAAS server, update this fork's `main`, reinstall
   `requirements.txt` in the same Python environment, then restart. The provider
   timeout/retry controls require the tested `google-genai>=2.33.0` dependency;
   pulling Git alone does not upgrade an already installed SDK. The complete
   [Windows update commands](../SETUP.md#upgrading-from-an-earlier-checkout) also
   verify the renderer and FFmpeg. The application entrypoint is `gui.py`; set
   `$env:STUDIO_PORT = "8765"` in PowerShell before starting it on that port.
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

Audiovisual release verification on 2026-11-08:

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

## Follow-up: interpreting incomplete generation logs

The reported earlier log showed repeated minute-long Gemini waits, narration
section 1 followed by section 3, sound acquisition warnings, and frame encoding.
The high-level order is expected: record and measure speech, prepare sound,
plan visual scenes on the same clock, then encode. The log did not show every
stage, and it did not establish whether every spoken claim was correct.

The follow-up corrects three independent observability/routing problems:

- Stream subscribers previously consumed a single shared queue. Two browser tabs
  could take messages from one another. Each connection now has its own event
  cursor, with bounded replay and run/channel identity.
- A new AI stage could retry a provider that had already failed during the same
  video. A generation-scoped provider session now remembers those failures and
  bounds retry work. A later job starts fresh; one channel cannot blacklist a
  provider for another concurrent context. Attempt and success logs are distinct.
- New visual planning had no useful start/completion summary in the text log.
  The worker now reports stage boundaries, the measured narration clock, reuse,
  planned scene/media counts and the requested job/build identity when available.

A cached narration section was already appended to the final audio; the jump in
the supplied log alone cannot establish a missing recording or a cache hit.
The clarified log includes a reused section's title and ends with an explicit
assembled N/N, measured duration, and reused/recorded counts. A regression warms
only the middle of three sections and checks every PCM sample and timing entry.

The completion message specifies media-structure checks—dimensions, frame rate,
duration and audio stream. Source counts on Home and Review are labeled retrieved
references. Neither is presented as a factual-accuracy or audiovisual-quality
approval.

The shared text-agent route uses a 120-second cloud retry budget per request,
with a 30-second HTTP attempt and 45-second provider budget when an alternative
is available. A sole remaining provider may use up to 60 seconds. These are
request/retry controls, not a separate hard wall-clock watchdog. An installed
local model keeps its own longer cancellable timeout. Stop requests propagate to
agent calls that do not supply an explicit stop event. Model-specific failures
can still try another configured Gemini model; a timed-out or inaccessible
provider is not retried by every later text stage in that video.

The SDK floor was exercised against `google-genai` 2.33.0 and 3.0.0. Version
2.33.0 declares Python 3.10 support; the local executable checks used Python
3.12. This dependency update does not require a paid model or a change to saved
provider keys.

Final follow-up verification on 2026-11-08:

| Check | Result |
| --- | --- |
| Complete Python suite with the real browser configured | **1,193 passed**, no skips |
| Complete renderer/Studio browser suite | **42 passed**, no skips |
| Focused routing, local/free and app/audio checks against SDK 2.33.0 and 3.0.0 | **89 passed with each SDK** |
| Actual Flask/JavaScript two-tab stream | Both tabs receive every section once; reconnect resumes, server timestamps display correctly, terminal events stay scoped |
| Mixed cached/new narration | All three sections, PCM audio, captions and timing entries remain in order; reuse and assembly are explicit in the log |

Provider failures are controlled fixtures in these routing tests; they do not
establish current availability of the user's provider accounts. The licensed
sample videos and real mixing evidence above remain unchanged by the log fix.
