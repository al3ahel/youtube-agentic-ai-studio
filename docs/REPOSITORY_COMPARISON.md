# YAAS and Purffle Shorts: implementation review

## Scope and conclusion

This review compares [Purffle Shorts at `38d49cd`](https://github.com/Chamanrajragu/purffle-shorts/tree/38d49cd956319a73cf983153816acdbda3dc0e0c) with [this YAAS fork before these changes, `890f3ae`](https://github.com/al3ahel/youtube-agentic-ai-studio/tree/890f3ae6ec98b7606224af7ee36a0e27007091d9), and records the improvements delivered alongside this document. The findings come from source inspection, focused execution of Purffle's offline media tests, and YAAS integration/browser/render tests. README marketing claims are not treated as proof of output quality or performance.

**Purffle is stronger as a broad Shorts production toolkit. YAAS has a stronger foundation for an interactive studio with separate channels, durable per-channel schedules, and editable explanatory motion.** Before this release, YAAS's repetitive appearance, restricted visual-model routing and unclear soundtrack controls undermined that advantage. The changes target those concrete failures instead of replacing the working channel/scheduler/publishing systems with an unrelated framework.

Neither repository is better at every use case. This release does not add Purffle's podcast clipping, dialogue/chat formats, full CLI/MCP or multi-platform posting to YAAS. Those remain genuine gaps. There is no controlled cross-repository speed, retention, revenue or audience-quality benchmark.

## Capability matrix

| Area | Purffle at the audited commit | YAAS and this release | Assessment |
| --- | --- | --- | --- |
| Intended workflow | Broad CLI, Studio and MCP Shorts toolkit | Local interactive production/review studio with channel profiles | Different strengths; preserve the studio workflow for this user's News/ASMR channels |
| Content intake | Topic sources include RSS, Reddit, Wikipedia, file and queue; supports clip/import workflows | Topic/angle, channel description, trend ideas and retrieved background | Purffle has broader intake |
| Story formats | Facts, story, listicle, myth, quiz, motivational, news, explainer, dialogue, chat, Reddit and Auto choices | Section-based long/Short scripts, semantic motion or photo rendering | Purffle has more ready-made narrative formats |
| Long-video repurposing | Local transcription, highlight selection, optional face crop and filler/silence processing | No comparable podcast/video clipper | Purffle clearly leads here |
| Aspect ratios | 9:16, 16:9, 1:1 and 4:5 | 9:16 and 16:9 | Purffle offers more export targets |
| Multiple channels | Separate configurations via environment files | Sidebar/mobile profiles, fetched channel identity, isolated OAuth and saved per-channel defaults | YAAS is more convenient for managing several channels in one UI |
| Recurring generation | CLI autopilot and publishing slots; Studio jobs held in process memory | Persistent SQLite scheduling/queue, cron and IANA time zone, separate scheduled workspaces and recovery policy | YAAS better matches a per-channel recurring studio workflow |
| Publishing destinations | YouTube plus direct/third-party crossposting routes | YouTube and downloadable media | Purffle has broader reach; third-party service access is not necessarily free |
| Publish control | CLI can upload by default; Studio/MCP expose explicit upload controls | Final Review action, saved-project owner check and authenticated channel verification; scheduled generation does not publish | YAAS emphasizes explicit reviewed publishing |
| LLM choices | Broad provider/custom-endpoint support, including local and paid providers | Installed local Ollama plus existing free-tier Gemini/Groq/OpenRouter; free-only OpenRouter enforcement | Purffle is more flexible; YAAS is deliberately narrower for this user's no-paid-model requirement |
| Speech engines | Edge, optional Kokoro/Coqui/system and optional paid OpenAI/ElevenLabs | Edge narration with measured PCM sections, available speech events and new verified section cache | Purffle offers more engines; YAAS now avoids rerecording unchanged sections |
| Voice caching | Bounded LRU cache and parallel remote narration | Atomic audio/caption/timing bundles keyed by text/voice/rate/pitch/engine version; validates contents before reuse | Both cache; YAAS focuses on keeping timing and audio inseparable |
| Alignment | Speech word boundaries, optional local Whisper and explicit paid alignment option; estimated fallbacks | Measured original section timing; imported SRT/estimated legacy timing; verified word highlight only | Purffle offers more alignment options; neither estimate is exact word timing |
| Caption design | Several caption styles, word emphasis, speaker colors and script shaping | New measured word highlighting, plain/off options and safe RTL handling; phrase fallback for incomplete timing | Purffle retains broader styling; YAAS adds the most relevant sync improvement |
| Visual sourcing | Stock, local media, optional generated images; media overlays | Original photo pipeline, optional Hybrid footage and graphics-only Motion; new photo provenance | Keep the old pipeline, stop unrelated cached media reuse |
| Explanatory motion | Primarily composited footage/stills with Ken Burns, captions and effects | Flow, comparison, timeline, supplied-data chart, concept and kinetic-text scenes | YAAS is better suited to semantic explanatory graphics |
| Visual variety | Formats, color grades, transitions and background choices | Five bounded art directions, composition alternatives and motion rhythm; old plans retain their look | New variety addresses YAAS's single-theme bottleneck; this is not free-form generative video |
| Scene editing | Editable pre-render script/settings | Selected-scene edits, exact revision checks, immediately rebuilt preview and replacement draft | YAAS offers more direct scene-level review; verified actual frame changes matter |
| Music selection | Can randomly choose a local music-folder file | Automatic agent selects intent/search/owned asset; Manual preserves exact existing choices | YAAS now removes manual track selection from the default workflow |
| Sound effects | Procedural hit/pop/whoosh construction, placed by fixed rules | AI-proposed sparse contextual cues from licensed recordings; exact measured anchors, no synthetic-noise fallback | Purffle's effects are not an AI sound director; YAAS now has that agent boundary |
| Audio finishing | Voice enhancement, ducked music and two-pass loudness normalization | Music/ambience/effect ducking from clean voice, exact-duration PCM, measured constant-gain finishing with separate ASMR limits | Purffle was ahead of the old mixer; YAAS now measures and reports finishing without forcing energetic processing on calm material |
| Failure/restart handling | Isolated render folders and persistent history; pending Studio jobs are in-memory; work cleanup can remove failed intermediates | Source-bound workspace recovery, staged scene edits, immutable soundtrack revisions and persistent scheduled jobs | YAAS prioritizes recoverable, inspectable editing |
| Rendering performance | Native FFmpeg shot composition, parallel preparation and hardware encoder choices | Browser/HTML/SVG Motion renderer and original MoviePy Classic path | Purffle has architectural performance advantages for ordinary stock-video compositions; no measured speed ratio is claimed |
| Security | Studio request token, Host checks and CSP; optional password for exposed binding | Localhost defaults, request/profile isolation, path checks, inert scene data, sandboxed preview, bounded providers | Both have protections; public exposure is not a supported assumption for this local studio |
| Packaging | Installable Python CLI package, optional dependency groups, Dockerfile and multiple translated READMEs | Python environment plus pinned Node/browser dependencies, Windows setup and local doctor | Purffle is more packaged; YAAS retains a larger dependency burden to preserve both renderers |
| Verification | Real media tests covering FFmpeg, formats, timing, captions and effects | Python tests plus real Flask/browser lifecycle checks, actual audio processing and encoded-video proofs | Test counts alone do not rank quality; test the failure boundaries that matter |
| Licensing | MIT application code; asset/provider obligations remain separate | MIT application code; applied audio attribution is kept with the actual soundtrack | Both allow code reuse under their licenses; no competitor source was copied for these changes |

### Source map for the matrix

Purffle implementation evidence:

- [CLI inputs, formats and flags](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/cli.py), [configuration](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/config.py), [pipeline](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/pipeline.py).
- [Narration/cache](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/tts.py), [timing](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/timing.py), [caption/overlay rendering](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/overlays.py).
- [Audio and render processing](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/render.py), [effect generation and fixed event rules](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/sfx.py), [FFmpeg process handling](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/ffmpeg.py).
- [Studio jobs and request protections](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/studio.py), [Studio interface](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/web/app.js), [MCP tools](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/mcp_server.py).
- [YouTube integration](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/youtube.py), [crossposting](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/purffle_shorts/crosspost.py), [package/extras](https://github.com/Chamanrajragu/purffle-shorts/blob/38d49cd956319a73cf983153816acdbda3dc0e0c/pyproject.toml).

For YAAS, the complete component/input/output map and invalidation rules are in [Pipeline architecture](PIPELINE_ARCHITECTURE.md). The agent/skill boundary is in [Agent capabilities](AGENT_CAPABILITIES.md).

## Important differences in depth

### Audio autonomy is separate from mixing quality

Purffle's renderer has useful audio engineering: optional high-pass/de-essing/compression, music ducking and final normalization. Its default effect events, however, are fixed hook/message/scene rules. Its music-folder selection can be random. Those are different capabilities from a model deciding whether a particular explanation needs music, what ambience suits a calm scene, and whether an accent would distract.

YAAS's new sound director receives the actual script, measured sections and channel tone. It proposes intent, short search terms or owned recording IDs. Trusted code enforces timing, profile ownership, sparse placement, duration and volume. It automatically retrieves suitable declared-license Commons audio, records source metadata, mixes and measures the result. It can omit a poor or unavailable asset. The UI reports what was actually applied; an enabled control is not proof of music in the exported file.

This is semantic planning over recording metadata, not acoustic audition by an audio model. Commons can return no suitable result or be unreachable. The system does not fill that gap with the synthetic noise effects the user disliked. The exact reason is shown; the original voice remains usable.

The provider also distinguishes an instrumental music candidate from a generic
sound recording using explicit metadata evidence and speech/vocal exclusions.
An empty eligible search can retry once with a shorter version of the same subject;
it does not substitute an unrelated sound or broaden after a network/quota error.
The original time/download bounds still apply. These rules improve the retrieval
boundary without claiming that a model listened to the downloaded recording.

### Why visual output previously looked repetitive

The old YAAS scene schema exposed mainly template/title/labels. Its theme and entrance behavior were largely fixed in the renderer. A stronger prompt alone could not create a different look because there was no supported field for that decision. Also, visual planning was gated on an OpenRouter key even if another configured text model could write the script.

The new direction contract adds a coherent film look, varied supported compositions and motion rhythm, and lets configured local/free text models plan visuals. These choices reach actual CSS/layout/animation code. New Motion ignores stock-photo fetching and attachment. Photo-capable workflows keep the old pipeline through an explicit, source-bound map. The solution expands the renderer's controlled vocabulary rather than executing arbitrary model-generated HTML.

### A successful edit must mean the requested change happened

The review found a misleading fallback: an unavailable/unchanged AI edit could toggle two templates even when that did not fulfill the user's request. Now supported local operations are explicit, unsupported instructions preserve the old plan with an explanation, and successful edits expose actual changed fields. The rendered plan revision is checked again before the job starts.

A real browser/encoder test also exposed concurrent in-place writes of template/vendor files while the preview iframe was loading. These copies are now atomic. Opening Review no longer starts an unrelated thumbnail-generation mutation. Verification compares the replacement MP4 bytes and decoded frames, preserves unaffected scenes/narration, and checks the browser loads the replacement URL.

### Channel identity and scheduling are worth keeping

YAAS already separates channel settings, OAuth tokens, project ownership and durable schedule jobs. The active Home channel is not allowed to redirect an existing project's upload or sound rebuild. This is particularly valuable when one channel is news and another calm/ASMR. Purffle's separate environment-file approach is useful for automation, but it does not provide the same unified channel-management workflow.

Scheduled generation and YouTube publication remain separate operations. A cron job can make a draft or local final file without publishing it. The scheduler's persistent queue, missed-slot policy and separate workspaces should not be replaced merely to imitate another repository's CLI.

### Free usage is a routing/account property

Purffle supports paid providers as optional choices; that is flexibility, not evidence that all of its outputs are free. YAAS keeps the narrower requirement: installed local Ollama inference, or configured free-tier hosted accounts, plus free-only OpenRouter selection. Local-only is enforced at the inference boundary, including remote frame review. It never silently downloads a model or sends a failed local prompt to cloud.

The former keyless Pollinations text/image generation routes are retired from normal use instead of being advertised as an unlimited fallback. Current Gemini/Groq model defaults replace obsolete IDs. Hosted account tiers and quotas still matter; an arbitrary API key cannot guarantee zero billing. Local generation uses the user's hardware, memory and electricity.

Primary service references: [Ollama local/cloud authentication](https://docs.ollama.com/api/authentication), [Groq model deprecations](https://console.groq.com/docs/deprecations), [Groq free/developer billing](https://console.groq.com/docs/billing-faqs), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Pixabay public API](https://pixabay.com/api/docs/), [Commons reuse guidance](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia), [MediaWiki file metadata API](https://www.mediawiki.org/wiki/API:Imageinfo).

Pixabay's site contains music and effects, but its documented public API covers images/videos. The previous manual import option remains optional. Automatic audio uses a documented keyless catalogue rather than an undocumented Pixabay music endpoint or browser scraping.

## Delivered priorities and remaining gaps

This release addresses automatic audio direction; measured finishing; atomic soundtrack revisions and complete applied credits; local model discovery/routing; narration caching; verified word captions; bounded editorial review; richer art direction; truthful scene edits; atomic preview assets; project-photo provenance; and clearer automatic/manual/off settings. The existing channel/scheduler/archive/Classic paths are preserved and regression-tested.

The next major competitive gaps, in useful order, are:

1. **Source-to-script intake:** explicit article/document/RSS import with retained evidence and dates, especially for the news profile.
2. **Local speech and alignment options:** a free local TTS engine and optional local forced alignment for old recordings, with hardware/quality checks.
3. **Long-video clipping:** imported video/podcast highlight selection, transcript editing and crop review.
4. **More story formats:** dialogue/chat/Reddit and translated variants as separate validated production recipes.
5. **CLI/MCP:** expose the same channel-bound operations rather than a second incompatible pipeline.
6. **Native stock-composition export and more aspect ratios:** benchmark before replacing the current rendering strategy.
7. **Crossposting:** only after provider terms, costs and destination confirmation are explicit.

These are not claimed as completed. A broad feature copy would expand the failure surface before resolving the user's current generation/editing problems. The architecture map identifies where each could be added without bypassing ownership, timing or revision checks.

## Verification and limits

The comparative audit executed 53 focused Purffle offline media tests covering audio, effects/emphasis, timing, formats and end-to-end rendering; they passed. This does not certify its entire application or live providers.

YAAS release verification on 2026-11-07:

| Gate | Result and scope |
| --- | --- |
| Full Python suite | **1,040 passed, none skipped**, with FFmpeg/ffprobe, the pinned renderer dependencies and an explicitly configured local browser. Covers agent contracts, free/local routing, channel ownership, old projects, cancellation, timing, media, archive/restart and API boundaries. |
| Full browser suite | **31 passed, none skipped**. Covers actual Flask routes, draft recovery, replacement-video loading/seeking, profile settings and schedules, word captions, five art directions and dense layouts in both orientations. |
| Actual Review edit proof | A 4-second, 48-frame encoded draft changed its plan revision, video hash and served URL after a selected-scene edit. Served MP4 bytes matched disk; decoded edited frames changed; narration, other scene plans and unsaved publishing edits were retained; no JavaScript errors or photos appeared in the graphics-only plan. |
| Actual soundtrack/render integration | Real FFmpeg music/cue mixing and mastering, a 16-second/32-frame low-resolution MP4, video-only reuse without source/model/TTS calls, and archive/reopen with matching soundtrack hash and credits. Cancellation after a prepared new master retained the previously committed audio and metadata. Provider/model responses in this test are controlled fixtures. |
| Existing shared sample files | Landscape and portrait each remain **168 frames**, 14 seconds at 12 fps. Their test-tone audio and illustrative assets are renderer fixtures, not demonstrations of narration or soundtrack quality. |
| Repository hygiene | `git diff --check` clean; generated browser fixtures, model/cache files and local credentials are excluded from the change. |

Run Python checks with `python -m pytest -q`. The actual browser-render integration
requires `HYPERFRAMES_BROWSER_PATH` pointing to an installed local browser (or an
appropriate launcher); otherwise that test intentionally skips instead of
downloading software. Run `npm test --prefix renderers/hyperframes` with the
documented `YAAS_TEST_BROWSER`/`YAAS_TEST_PYTHON` environment for browser checks.
See [Motion verification](MOTION_STUDIO.md#verification) for setup and commands.

No paid inference or real YouTube upload was used during verification. Commons metadata transport was tested with controlled responses and real audio validation; the live Commons request timed out in this environment. Local Ollama's installed-model checks, transport and cancellation were tested against a loopback server, without downloading/running model weights. Hosted model quality, voice quality and real catalogue relevance therefore still require an ordinary user test with available providers. No comparative render-speed or subjective audience-quality winner is asserted.
