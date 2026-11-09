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
| Raw narration preservation | Verified voice checkpoint exported before visual planning |
| Subjective listening / independent ASR | Not performed |

The selected unpadded recording has SHA-256
`f1eb0571c2ab241e77fd25cec59cdbc431ecd7f9dbeb0901763bf776b92ef5a1`.
The complete padded narration has SHA-256
`52173a70871a6fdd37dbd3fbbf439a1412824181bfc80c6f2ab6720c3a144be5`.
The word-event check verifies the returned timing/text stream; it is not an
independent recognition or pronunciation assessment.

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
Recorded resource samples show both large model runners, an additional OOM kill,
and disappearance of this continuation's runner. The server returned HTTP 500
with `unexpected EOF`. The interrupted continuation and its verified checkpoint
are retained unchanged.

The next continuation started after the other review's wrapper, model service
and loaded runner all exited naturally. It restored the verified 29-file
checkpoint and uses the same fresh narration, model, normal generation limits
and strict acceptance checks. No failed response has been relabeled as an
accepted model decision, and no unrelated process was interrupted.

## Final pipeline result

The final automatic sound, encoding and review outcomes will be recorded here
after the active continuation reaches a terminal state. Fresh narration is
complete; a fresh mixed film has not yet been produced by these attempts.

## Source and evidence

The original authorized run started while branch publication was in progress.
All 94 recorded production-source fingerprints match published commit
`7bbd33d999c79faa205092ff884da51259730a73`; the original run receipt is preserved
without rewriting its initial Git state. Subsequent continuations retain their
own source and checkpoint-origin receipts.

The published implementation passed 247 focused Python tests and 33 browser
tests. Those implementation checks do not approve a later film. The prior
visual-only result, its sampled inspection and incomplete semantic review remain
in [the armour continuation report](ARMOUR_CONTINUATION_REPORT.md). Its preserved
old soundtrack is distinct from the newly recorded narration documented here.

Full audiovisual parity with the reference remains unverified: only the
previously documented short silent reference excerpt was accessible. The
complete source timeline and output-format checks are technical evidence, not
a reference-quality verdict.
