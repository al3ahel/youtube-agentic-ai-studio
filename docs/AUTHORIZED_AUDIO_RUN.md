# Authorized armour narration and pipeline continuation

The user explicitly approved publishing the prepared review branch and
regenerating narration with Microsoft/Bing Edge TTS on 9 November 2026.
The implementation is published on `review/armour-continuation-20261109` at
[`7bbd33d999c79faa205092ff884da51259730a73`](https://github.com/al3ahel/youtube-agentic-ai-studio/commit/7bbd33d999c79faa205092ff884da51259730a73).
This authorization does not merge the review branch into `main` or upload a film
to YouTube.

## Fresh narration: complete

The normal narrator submitted all five original armour-script sections to Edge
TTS using `en-US-AndrewNeural`, pitch `+0Hz`, with voice caching disabled.
The initial complete take at `+0%` lasted 33.288 seconds. The ordinary bounded
duration fitting then requested a second complete take at `+14%`; that take
lasted 29.232 seconds. No words were cut and no returned speech was stretched.
Appending 0.268 seconds of silence produced the required 885-frame, 29.5-second
timeline at 1080 × 1920 and 30 fps.

| Check | Observed result |
| --- | --- |
| Narration sections | All 5 recorded in each complete take |
| Returned word events | 84 of 84, matching the script in order |
| Per-section word counts | 16, 16, 16, 17 and 19; all match |
| Original voice-cache reuse | 0 sections |
| Selected complete take | `voice_attempts/attempt-2`, rate `+14%` |
| Appended tail | 0.268 seconds of silence; existing speech samples unchanged |
| Audio format | Mono signed 16-bit PCM at 48,000 Hz; selected take and delivered WAV fully decode |
| Selected take / delivered samples | 1,403,136 / 1,416,000 samples |
| Stored-sample peak | −1.980183 dBFS; no samples at either digital full-scale endpoint |
| Raw narration preservation | Verified voice checkpoint exported before visual planning |
| Subjective listening / independent ASR | Not performed |

The selected unpadded recording has SHA-256
`f1eb0571c2ab241e77fd25cec59cdbc431ecd7f9dbeb0901763bf776b92ef5a1`.
The complete padded narration has SHA-256
`52173a70871a6fdd37dbd3fbbf439a1412824181bfc80c6f2ab6720c3a144be5`.
The word-event check verifies the returned timing/text stream; it is not an
independent recognition or pronunciation assessment.

A separate full-file signal inspection confirms that the delivered narration's
first 1,403,136 samples are byte-identical to the selected recording and that all
12,864 appended samples are zero. Both the selected recording and delivered WAV
decode completely without FFmpeg errors. This establishes sample preservation
and the measured digital peak; it does not assess listening quality or exclude
distortion that could already be present below full scale.

## Execution history

The initial normal harness reacquired all three declared museum images and
began live local direction using the installed `qwen3.5:4b` model. Its first
three-shot response passed request/schema validation. The next request returned
HTTP 500, so the planner correctly withheld a model-backed plan and the harness
stopped before sound or rendering. The raw response and terminal failure are
retained. Cumulative memory counters alone do not establish that first failure's
cause.

A separate normal continuation restored the verified fresh narration and museum
assets without another speech call. Its first planning batch completed. During
its second batch, another model process started in the shared 8 GiB workspace.
Recorded resource samples show both large model runners and an additional OOM
kill. Between the 19:43:24.578 and 19:43:30.894 UTC samples, `oom_kill` rose from
2 to 3 and a previously listed model runner was absent from the next snapshot of
processes using more than 100 MB. The server returned HTTP 500 with `unexpected
EOF` at 19:43:29.986 UTC, within that interval. These observations are consistent
with a memory-pressure interruption, but the diagnostics do not identify the
kernel's OOM victim. The interrupted continuation and its verified checkpoint
are retained unchanged.

The next continuation started after the other review's wrapper, model service
and loaded runner all exited naturally. It restored the verified 29-file
checkpoint and uses the same fresh narration, model, normal generation limits
and strict acceptance checks. No failed response has been relabeled as an
accepted model decision, and no unrelated process was interrupted.

## First encoded continuation and the sound-binding correction

The third attempt completed all four normal visual-planning batches and encoded
a fresh film. Its original planning journal accepts eight scene proposals and
rejects two unsupported proposals, retaining local source-grounded direction for
scenes 002 and 009. Six scenes retain nontext model changes consumed by the
renderer. The resulting plan has two photographic collages and eight `stage2d`
shots; it does not demonstrate a new three-dimensional result.

Its first automatic mix uses *Cylinder Five* and a book page-turn effect at
4.600 seconds. Both are bundled CC BY 4.0 recordings. The model also proposed a
later metal contact, but the saved low-intensity setting permits one effect for
this duration, and the earlier page turn occupied that slot. The omitted-contact
decision was not added to `omitted_events`. Neither the model's original intent
text nor the existence of that proposal establishes an applied proof-impact
sound.

The sound stage also reported that the saved storyboard did not match the
narration/channel and fell back to source timing. Read-only diagnosis established
that the storyboard was current: source, profile, signatures, word events,
dimensions, duration and plan validation all matched. The rejection came from
exact comparison of section times stored to six decimals with their original
30 fps fractions. The largest difference in these saved inputs was approximately
0.333333334 microseconds.

Commit [`b929f0bd1939a76d485995612cb0ae7110c2b069`](https://github.com/al3ahel/youtube-agentic-ai-studio/commit/b929f0bd1939a76d485995612cb0ae7110c2b069)
corrects that comparison in the normal audio director. Section times must be
finite and agree within one microsecond, with zero relative tolerance. Section
count, order and IDs, all existing source/profile/word/format bindings, and full
plan validation remain required. A producer-to-consumer regression fails on the
published baseline and passes with the fix; 120 focused audio/contact/motion
tests pass. The actual saved storyboard now returns unchanged from the binding
check. This correction does not change effect ordering or the intensity cap and
does not guarantee that the model will select a proof-contact accent.

That encoded attempt's review finished on **10 November 2026 at 00:10 Dubai
time**. All six encoded-format checks passed: 1080 × 1920, 30 fps, 885 decoded
frames, 29.5 seconds, H.264 and AAC. Its strict semantic result is
`needs_review`, with `passed: null`: six of ten shot responses passed review
validation, while scenes 003, 005, 007 and 009 had invalid or unavailable review.
No repair was applied. One technical observation remained on scene 010's
visible action. The film's SHA-256 is
`21a9dd8b786e26cbfad981a3b8de2cb18dab4409fa00dfd0a93eb15f74478b05`.

Independent inspection of all 30 decoded setup/action/result frames also found
that the closing breastplate and long-gun illustrations were labeled
"Linked mail rings" and "Early pistol." Those visible label mismatches were not
resolved by the automatic review. The opening still repeats a three-photo
collage, and the "Layered Defense Gap" title can imply a literal protective gap
not established by the narration. The original source, film, review responses,
sampled inspection and verified 50-file checkpoint are preserved.

A subsequent normal checkpoint continuation has started from the verified
checkpoint, replaying the four sealed visual proposals through the unchanged
visual validators. It runs fresh automatic sound, encoding and review using the
corrected audio binding. Preflight verified all 94 current production files
against the published fix and copied a new source snapshot. No additional speech
recording is needed.

## Final corrected pipeline result

The corrected continuation's actual mix, encoding, automatic-review result and
independent sampled-frame findings will be recorded here after it reaches a
terminal state. The complete fresh narration is already available separately.

## Source and evidence

The original authorized run started while branch publication was in progress.
All 94 original production-source fingerprints match published commit
`7bbd33d999c79faa205092ff884da51259730a73`; the original run receipt is preserved
without rewriting its initial Git state. Subsequent continuations retain their
own source and checkpoint-origin receipts. All 94 production files were also
checked against the published audio-binding fix: only `agents/audio_director.py`
changes relative to that original source snapshot. The new continuation records
those new fingerprints without relabeling the earlier run's source.

The earlier published implementation passed 247 focused Python tests and 33
browser tests. The separate audio-binding correction passed the 120 focused
tests described above. These implementation checks do not approve a later film. The prior
visual-only result, its sampled inspection and incomplete semantic review remain
in [the armour continuation report](ARMOUR_CONTINUATION_REPORT.md). Its preserved
old soundtrack is distinct from the newly recorded narration documented here.

Full audiovisual parity with the reference remains unverified: only the
previously documented short silent reference excerpt was accessible. The
complete source timeline and output-format checks are technical evidence, not
a reference-quality verdict.
