"""
Narrator — Microsoft Edge TTS (100% free)
Records each section, joins decoded PCM samples, and preserves speech events.
Requires edge-tts 7.3.1 or later. Captions without returned word/phrase events
are explicitly marked as estimated; section durations are always measured.
"""
import os
import re
import asyncio
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import shutil
import subprocess
import tempfile
import wave
import zipfile
from pathlib import Path
import config


_VOICE_CACHE_VERSION = 1
_VOICE_CACHE_BYTES = 400 * 1024 * 1024
_VOICE_CACHE_ENTRY_BYTES = 64 * 1024 * 1024
_VOICE_CACHE_ENTRIES = 256


def _voice_cache_key(text):
    try:
        engine_version = version("edge-tts")
    except PackageNotFoundError:
        # An unknown engine build must not reuse another build's speech.
        return None
    identity = {"schema": _VOICE_CACHE_VERSION, "engine": "edge-tts", "engine_version": engine_version,
                "text": text, "voice": config.VOICE_ID, "rate": config.VOICE_RATE, "pitch": config.VOICE_PITCH,
                "boundary": "WordBoundary", "sample_rate": 48000, "channels": 1, "sample_width": 2}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _voice_cache_root(use_cache):
    if use_cache is not None and not isinstance(use_cache, bool):
        raise ValueError("Choose whether to reuse cached voice sections with a boolean.")
    if not (getattr(config, "VOICE_CACHE_ENABLED", True) if use_cache is None else use_cache):
        return None
    location = getattr(config, "VOICE_CACHE_DIR", "") or Path(config.DATA_DIR) / "voice-cache"
    return Path(location).expanduser().resolve()


def _validate_cached_section(wav, srt, timing):
    """Check the decoded format and local timing before any cached audio is used."""
    from video.timeline import read_srt
    with wave.open(str(wav), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, 48000, "NONE"):
            raise ValueError("Cached narration has a different audio format.")
        frames = stream.getnframes()
        if not 0 < frames <= _VOICE_CACHE_ENTRY_BYTES // 2:
            raise ValueError("Cached narration has an invalid length.")
        count = 0
        while block := stream.readframes(65536):
            count += len(block)
        if count != frames * 2:
            raise ValueError("Cached narration is incomplete.")
    duration = frames / 48000
    local = json.loads(timing.read_text(encoding="utf-8"))
    if (not isinstance(local, dict) or local.get("source") not in {"word", "phrase", "estimated"}
            or not isinstance(local.get("words"), list) or len(local["words"]) > 20000):
        raise ValueError("Cached narration has invalid speech events.")
    for word in local["words"]:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str) or not word["word"].strip():
            raise ValueError("Cached narration has an invalid word.")
        start, end = word.get("start"), word.get("end")
        if (any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (start, end))
                or not 0 <= start < end <= duration + .5):
            raise ValueError("Cached narration has invalid word timing.")
    if local["source"] == "word" and not local["words"]:
        raise ValueError("Cached narration is missing its word events.")
    if not read_srt(str(srt), duration):
        raise ValueError("Cached narration has no usable captions.")
    return frames


def _restore_voice_section(root, key, wav, srt, timing, stop_event=None):
    if root is None or key is None:
        return False
    entry = root / f"v{_VOICE_CACHE_VERSION}_{key}.zip"
    try:
        if entry.is_symlink() or not entry.is_file() or entry.stat().st_size > _VOICE_CACHE_ENTRY_BYTES:
            return False
        files = {"audio.wav": wav, "captions.srt": srt, "timings.json": timing}
        with zipfile.ZipFile(entry) as archive:
            members = archive.infolist()
            if (len(members) != 4 or {item.filename for item in members} != {*files, "manifest.json"}
                    or any(item.compress_type != zipfile.ZIP_STORED for item in members)
                    or sum(item.file_size for item in members) > _VOICE_CACHE_ENTRY_BYTES
                    or archive.getinfo("manifest.json").file_size > 16384):
                return False
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("version") != _VOICE_CACHE_VERSION or manifest.get("key") != key:
                return False
            for name, destination in files.items():
                digest = hashlib.sha256()
                with archive.open(name) as source, destination.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        if stop_event is not None and stop_event.is_set():
                            raise InterruptedError("Narration stopped while reading cached speech")
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != manifest["sha256"][name]:
                    return False
        if _validate_cached_section(wav, srt, timing) != manifest["samples"]:
            return False
        try:
            os.utime(entry, None)
        except OSError:
            pass  # A readable cache remains useful on a read-only volume.
        return True
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError, wave.Error, zipfile.BadZipFile):
        return False


def _prune_voice_cache(root, keep):
    # Only this cache's fixed, hashed filenames are eligible for removal.
    entries = []
    for path in root.glob("v*_*.zip"):
        if re.fullmatch(r"v[1-9][0-9]*_[a-f0-9]{64}\.zip", path.name) and not path.is_symlink():
            info = path.stat()
            entries.append((info.st_mtime_ns, info.st_size, path))
    total, count = sum(item[1] for item in entries), len(entries)
    for _, size, path in sorted(entries):
        if total <= _VOICE_CACHE_BYTES and count <= _VOICE_CACHE_ENTRIES:
            break
        if path != keep:
            path.unlink(missing_ok=True)
            total -= size
            count -= 1


def _save_voice_section(root, key, wav, srt, timing, stop_event=None):
    if root is None or key is None:
        return
    temporary = None
    try:
        files = {"audio.wav": wav, "captions.srt": srt, "timings.json": timing}
        if sum(path.stat().st_size for path in files.values()) + 16384 > _VOICE_CACHE_ENTRY_BYTES:
            return
        samples = _validate_cached_section(wav, srt, timing)
        from video.timeline import audio_signature
        manifest = {"version": _VOICE_CACHE_VERSION, "key": key, "samples": samples,
                    "sha256": {name: audio_signature(path) for name, path in files.items()}}
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".voice-", suffix=".tmp", dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, path in files.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Narration stopped before saving cached speech")
        entry = root / f"v{_VOICE_CACHE_VERSION}_{key}.zip"
        # One atomic bundle prevents readers from combining speech from one
        # attempt with timestamps from another, even across simultaneous jobs.
        os.replace(temporary, entry)
        _prune_voice_cache(root, entry)
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError, KeyError, EOFError, wave.Error, zipfile.BadZipFile):
        print("   → Voice cache unavailable for this section; the new recording is retained.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _format_srt_time(ms: float) -> str:
    """Convert milliseconds → SRT timestamp  HH:MM:SS,mmm"""
    ms = max(0, round(ms))
    h  = ms // 3_600_000;  ms %= 3_600_000
    m  = ms // 60_000;     ms %= 60_000
    s  = ms // 1_000;      ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _get_audio_duration_ms(audio_path: str) -> float:
    """Measure media duration; never guess from compressed file size."""
    from video.timeline import probe_duration
    return probe_duration(audio_path) * 1000


# ══════════════════════════════════════════════════════════════
#  SRT WRITERS
# ══════════════════════════════════════════════════════════════

def _write_srt_blocks(blocks: list[tuple[float, float, str]], srt_path: str) -> int:
    """
    Write a list of (start_ms, end_ms, text) tuples as a valid SRT file.
    Returns the number of cues written.
    """
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (start_ms, end_ms, text) in enumerate(blocks, 1):
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_time(start_ms)} --> {_format_srt_time(end_ms)}\n")
            f.write(f"{text}\n")
            f.write("\n")
    return len(blocks)


def _group_word_events(word_events: list, words_per_cue: int = 5) -> list:
    """Group word-boundary events into (start_ms, end_ms, text) caption blocks."""
    blocks = []
    for i in range(0, len(word_events), words_per_cue):
        chunk = word_events[i : i + words_per_cue]
        blocks.append((
            chunk[0]["start_ms"],
            chunk[-1]["end_ms"],
            " ".join(w["word"] for w in chunk),
        ))
    return blocks


def _try_submaker_srt(submaker, srt_path: str) -> int:
    """
    Ask SubMaker to produce SRT content, trying every known method name
    across all edge-tts versions:

      6.1.0–6.1.8 : submaker.generate_subs(words_in_cue=N)  → str
      6.1.9–6.x   : submaker.merge_subs(words_in_cue=N)     → str
      7.x          : str(submaker)  or  submaker.srt         → str

    Returns cue count, or 0 if nothing produces valid SRT content.
    """
    srt_content = None

    # Named methods first (most reliable)
    for method in ("merge_subs", "generate_subs"):
        fn = getattr(submaker, method, None)
        if callable(fn):
            try:
                result = fn(words_in_cue=5)
                if result and "-->" in result:
                    srt_content = result
                    break
            except Exception as e:
                print(f"   → SubMaker.{method}() error: {e}")

    # .srt property (7.x)
    if not srt_content:
        raw = getattr(submaker, "srt", None)
        if isinstance(raw, str) and "-->" in raw:
            srt_content = raw

    # __str__ (some 7.x builds)
    if not srt_content:
        try:
            raw = str(submaker)
            if "-->" in raw:
                srt_content = raw
        except Exception:
            pass

    if not srt_content:
        return 0

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return srt_content.count(" --> ")


def _build_sentence_srt(text: str, audio_path: str, srt_path: str) -> int:
    """
    Fallback SRT builder using CHARACTER-based proportional timing.

    Why character-based instead of word-based:
      TTS engines speak at roughly constant character rate, not word rate.
      "if" (~100ms) and "internationalization" (~700ms) should not get the
      same time budget. Character count × ms_per_char + inter-word pause
      is a much better model of actual speech duration per word.

    Each word is allocated:
      duration_ms = len(word) * ms_per_char + INTER_WORD_MS

    Where ms_per_char is derived from total_ms / total_weighted_chars so
    that all word durations sum exactly to total_ms.

    Captions are grouped into chunks of up to 6 words. No cue ever ends
    past total_ms — the last cue is pinned to total_ms exactly.
    """
    total_ms  = _get_audio_duration_ms(audio_path)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?…])\s+', text) if s.strip()]
    all_words = [w for s in sentences for w in s.split()]

    if not all_words:
        open(srt_path, "w").close()
        return 0

    # Each gap between words is modelled as 1.5 "equivalent characters"
    INTER_WORD_CHAR_EQ = 1.5
    total_weighted = sum(len(w) for w in all_words) + len(all_words) * INTER_WORD_CHAR_EQ
    ms_per_char    = total_ms / total_weighted

    # Pre-compute cumulative start time for every word
    word_starts = []
    cursor      = 0.0
    for w in all_words:
        word_starts.append(cursor)
        cursor += len(w) * ms_per_char + INTER_WORD_CHAR_EQ * ms_per_char

    blocks     = []
    word_index = 0   # absolute index into all_words / word_starts

    for sentence in sentences:
        s_words = sentence.split()
        # Group sentence words into chunks of up to 6
        for ci in range(0, len(s_words), 6):
            chunk      = s_words[ci : ci + 6]
            chunk_abs  = word_index + ci
            c_start    = word_starts[chunk_abs]
            # End = start of the word AFTER the last word in chunk
            last_abs   = chunk_abs + len(chunk) - 1
            last_word  = all_words[last_abs]
            c_end      = min(
                word_starts[last_abs] + len(last_word) * ms_per_char,
                total_ms,
            )
            if c_start >= total_ms:
                break
            blocks.append((c_start, c_end, " ".join(chunk)))
        word_index += len(s_words)

    # Pin last cue to exact audio end (floating-point safety)
    if blocks:
        blocks[-1] = (blocks[-1][0], total_ms, blocks[-1][2])

    cues       = _write_srt_blocks(blocks, srt_path)
    actual_end = blocks[-1][1] / 1000 if blocks else 0.0
    print(f"   → Estimated captions: {cues} cues, ending at {actual_end:.1f}s "
          f"(audio: {total_ms/1000:.1f}s; individual phrase sync is approximate)")
    return cues


# ══════════════════════════════════════════════════════════════
#  TTS SYNTHESIS
# ══════════════════════════════════════════════════════════════

async def _synthesise(text: str, audio_path: str, srt_path: str,
                      timings_path: str | None = None, stop_event=None) -> int:
    """
    Stream edge-tts audio + word-boundary events.
    Tries SubMaker → raw WordBoundary events → sentence fallback.
    Returns the number of SRT cues written.
    """
    import edge_tts
    communicate = edge_tts.Communicate(
        text,
        config.VOICE_ID,
        rate=config.VOICE_RATE,
        pitch=config.VOICE_PITCH,
        boundary="WordBoundary",
    )

    # Instantiate SubMaker (may not exist in very old edge-tts)
    try:
        submaker     = edge_tts.SubMaker()
        use_submaker = True
    except Exception:
        submaker     = None
        use_submaker = False

    audio_chunks = []
    word_events  = []   # manual fallback list
    phrase_events = []

    async for event in communicate.stream():
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Narration stopped by user")
        if event["type"] == "audio":
            audio_chunks.append(event["data"])

        elif event["type"] == "SentenceBoundary":
            start = event["offset"] / 10_000
            phrase_events.append((start, start + event["duration"] / 10_000,
                                  event["text"]))

        elif event["type"] == "WordBoundary":
            # Feed SubMaker using whichever ingestion method this version has
            if use_submaker and submaker is not None:
                for feed_name in ("feed", "create_sub"):
                    fn = getattr(submaker, feed_name, None)
                    if not callable(fn):
                        continue
                    try:
                        if feed_name == "feed":
                            fn(event)   # 6.1.9+: takes the whole event dict
                        else:
                            fn(         # 6.1.0–6.1.8: takes (offset_tuple, text)
                                (event["offset"], event["duration"]),
                                event["text"],
                            )
                    except Exception:
                        pass
                    break

            # Always collect manually too — used if SubMaker yields nothing
            offset_ms   = event["offset"]   / 10_000   # 100-ns ticks → ms
            duration_ms = event["duration"] / 10_000
            word_events.append({
                "word":     event["text"],
                "start_ms": offset_ms,
                "end_ms":   offset_ms + duration_ms,
            })

    # ── Save audio (must happen before any duration checks) ─────────────────
    if not audio_chunks:
        raise RuntimeError("The voice service returned no audio. Try again or choose another voice.")
    with open(audio_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)

    # ── SRT generation cascade ───────────────────────────────────────────────
    cue_count = 0

    # Actual events are the source of truth for both SRT and the motion clock.
    timing_source = "estimated"
    if word_events:
        cue_count = _write_srt_blocks(_group_word_events(word_events), srt_path)
        timing_source = "word"
    elif phrase_events:
        cue_count = _write_srt_blocks(phrase_events, srt_path)
        timing_source = "phrase"

    # SubMaker supports older versions that expose a different ingest API.
    if cue_count == 0 and use_submaker and submaker is not None:
        cue_count = _try_submaker_srt(submaker, srt_path)
        if cue_count:
            print(f"   → SubMaker SRT: {cue_count} cues  ✓")

    # 2. Raw WordBoundary events (covers 6.0.x)
    if cue_count == 0 and word_events:
        print(f"   → SubMaker empty; building from {len(word_events)} WordBoundary events…")
        blocks    = _group_word_events(word_events)
        cue_count = _write_srt_blocks(blocks, srt_path)
        if cue_count:
            print(f"   → WordBoundary SRT: {cue_count} cues  ✓")

    # 3. Sentence-level fallback (always works, uses AudioFileClip for duration)
    if cue_count == 0:
        if not word_events:
            # Microsoft periodically disables WordBoundary events server-side.
            # A duration-matched fallback is approximate within the section.
            WORD_SYNCED = [
                ("en-US-AndrewNeural", "Andrew (US, Warm male)"),
                ("en-GB-LibbyNeural",  "Libby  (UK, Clear female)"),
            ]
            alternatives = [(v, n) for v, n in WORD_SYNCED if v != config.VOICE_ID]
            print(f"   ⚠ '{config.VOICE_ID}' sent no word-timing data (Microsoft server change).")
            print( "      Caption positions are estimated within this measured section.")
            if alternatives:
                print( "      For word-synced captions, switch voice in Settings → Voice:")
                for voice_id, label in alternatives:
                    print(f"        {label:<28}  VOICE_ID = \"{voice_id}\"")
        print("   → Using sentence-level caption fallback (approximate sync)…")
        cue_count = _build_sentence_srt(text, audio_path, srt_path)

    if timings_path:
        Path(timings_path).write_text(json.dumps({
            "version": 1, "source": timing_source,
            "words": [{"word": w["word"], "start": w["start_ms"] / 1000,
                       "end": w["end_ms"] / 1000} for w in word_events],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    return cue_count


# ══════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════

def _generate_segmented(script: dict, output_dir: str, stop_event=None, *, use_cache=None) -> str:
    """Synthesize each section and join decoded PCM samples without overlaps.

    Word events are kept when available. Without them, section boundaries are
    still measured exactly, while the manifest honestly marks captions as
    phrase-based or estimated. WAV avoids a second lossy encode and MP3 padding
    accumulating at every section boundary.
    """
    from video.timeline import read_srt, script_signature
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    sections = script.get("sections", [])
    if not sections:
        raise ValueError("The script has no narration sections.")
    ids = [str(section.get("id", i + 1)) for i, section in enumerate(sections)]
    if len(ids) != len(set(ids)):
        raise ValueError("Each script section must have a unique id.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError("FFmpeg is required to prepare synchronized narration.") from exc
    measured, words, captions = [], [], []
    sample_rate, cursor_samples = 48000, 0
    cache_root = _voice_cache_root(use_cache)
    cache_hits = 0

    def check_stop():
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Narration stopped by user")

    with tempfile.TemporaryDirectory(prefix="narration-", dir=destination) as work:
        work = Path(work)
        joined = work / "narration.wav"
        with wave.open(str(joined), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            for index, section in enumerate(sections):
                check_stop()
                text = str(section.get("narration", "")).strip()
                if not text or text.startswith(("Start with", "Deep explanation", "Open with")):
                    raise ValueError(f"Section {index + 1} needs finished narration text. Regenerate the script.")
                mp3, srt, timing = (work / f"{index}.{suffix}" for suffix in ("mp3", "srt", "json"))
                wav = work / f"{index}.wav"
                key = _voice_cache_key(text) if cache_root is not None else None
                reused = _restore_voice_section(cache_root, key, wav, srt, timing, stop_event)
                if reused:
                    cache_hits += 1
                    print(f"   → Reusing section {index + 1}/{len(sections)}: {section.get('title', '')} "
                          "(validated voice cache and speech timing).")
                else:
                    print(f"   → Recording section {index + 1}/{len(sections)}: {section.get('title', '')}")
                    try:
                        asyncio.run(_synthesise(text, str(mp3), str(srt), str(timing), stop_event))
                    except Exception as exc:
                        if "403" in str(exc):
                            raise RuntimeError("The voice service rejected the request. Update edge-tts in this environment or choose another voice.") from exc
                        raise
                    check_stop()
                    conversion = subprocess.run(
                        [ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(mp3),
                         "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                        capture_output=True, text=True, timeout=180)
                    if conversion.returncode:
                        raise RuntimeError(f"Could not decode voice section {index + 1}: {conversion.stderr[-500:]}")
                    _save_voice_section(cache_root, key, wav, srt, timing, stop_event)
                check_stop()
                start = cursor_samples / sample_rate
                with wave.open(str(wav), "rb") as source:
                    sample_count = source.getnframes()
                    if sample_count <= 0:
                        raise ValueError(f"Section {index + 1} returned empty audio.")
                    while True:
                        frames = source.readframes(65536)
                        if not frames:
                            break
                        output.writeframesraw(frames)
                duration = sample_count / sample_rate
                local = json.loads(timing.read_text(encoding="utf-8"))
                sid = section.get("id", index + 1)
                measured.append({"id": sid, "start": start, "duration": duration,
                                 "caption_timing": local.get("source", "estimated")})
                for word in local.get("words", []):
                    begin = min(duration, max(0.0, float(word["start"])))
                    end = min(duration, float(word["end"]))
                    if end > begin:
                        words.append({"word": word["word"], "start": start + begin,
                                      "end": start + end, "section_id": sid})
                for cue in read_srt(str(srt), duration):
                    captions.append(((start + cue["start"]) * 1000,
                                     (start + cue["end"]) * 1000, cue["text"]))
                cursor_samples += sample_count
        check_stop()
        from video.timeline import audio_signature
        manifest = {"version": 1, "script_signature": script_signature(script),
                    "audio_sha256": audio_signature(joined),
                    "duration": cursor_samples / sample_rate, "sample_rate": sample_rate,
                    "timing_source": "measured_sections", "sections": measured, "words": words,
                    "voice_cache": {"enabled": cache_root is not None, "reused_sections": cache_hits}}
        _write_srt_blocks(captions, str(work / "narration.srt"))
        (work / "narration.timings.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        # Keep old successful files intact until every section has completed.
        for name in ("narration.wav", "narration.srt", "narration.timings.json"):
            os.replace(work / name, destination / name)
    print(f"   → Narration assembled: {len(measured)}/{len(sections)} sections, "
          f"{manifest['duration']:.2f}s measured audio "
          f"({cache_hits} reused, {len(measured) - cache_hits} recorded).")
    return str(destination / "narration.wav")


def generate_narration(script: dict, output_dir: str, *, segmented: bool = True,
                       stop_event=None, use_cache=None) -> str:
    """
    Record measured sections as one WAV and write matching SRT/timing metadata.
    The legacy single-MP3 path is available with segmented=False.
    """
    if segmented:
        return _generate_segmented(script, output_dir, stop_event=stop_event, use_cache=use_cache)
    parts = []
    for section in script["sections"]:
        narration = section.get("narration", "").strip()
        if not narration:
            continue
        if narration.startswith(("Start with", "Deep explanation", "Open with")):
            continue
        parts.append(narration)

    full_text  = "  ...  ".join(parts)
    audio_path = os.path.join(output_dir, "narration.mp3")
    srt_path   = os.path.join(output_dir, "narration.srt")

    print(f"   → Synthesising {len(full_text.split())} words via Edge TTS…")
    try:
        asyncio.run(_synthesise(full_text, audio_path, srt_path))
    except Exception as e:
        if "403" in str(e):
            raise RuntimeError(
                "Microsoft's free voice service rejected the request (403). "
                "Update the voice library:  pip install -U edge-tts"
            ) from e
        raise

    # ── Post-run report ──────────────────────────────────────────────────────
    size_kb     = os.path.getsize(audio_path) // 1024
    audio_dur_s = _get_audio_duration_ms(audio_path) / 1000

    if not os.path.exists(srt_path):
        print(f"   ⚠ ERROR: SRT file missing at {srt_path}")
        return audio_path

    content        = open(srt_path, encoding="utf-8").read()
    verified_count = content.count(" --> ")

    # Find the last timestamp in the SRT to measure caption coverage
    timestamps = re.findall(r"--> (\d+:\d+:\d+,\d+)", content)
    if timestamps:
        def _ts_to_ms(ts: str) -> int:
            h, m, rest = ts.split(":")
            s, ms = rest.split(",")
            return int(h) * 3_600_000 + int(m) * 60_000 + int(s) * 1_000 + int(ms)
        cap_end_s = max(_ts_to_ms(ts) for ts in timestamps) / 1000
    else:
        cap_end_s = 0.0

    coverage = (cap_end_s / audio_dur_s * 100) if audio_dur_s > 0 else 0

    print(f"   → Audio : {size_kb} KB  |  {audio_dur_s:.1f}s")
    print(f"   → SRT   : {verified_count} cues  |  covers 0s → {cap_end_s:.1f}s  ({coverage:.0f}%)")

    if coverage < 85:
        print(f"   ⚠ Caption coverage {coverage:.0f}% — captions will disappear before audio ends.")
        print( "      Fix: switch the voice in Settings to  en-GB-RyanNeural")
        print( "      That voice emits proper word-level timing events.")

    return audio_path
