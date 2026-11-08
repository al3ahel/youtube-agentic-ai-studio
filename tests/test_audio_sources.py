"""Automatic audio uses attributable licensed files and a pinned channel cache."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit
import wave

import pytest

import config
from apis import audio_sources
from studio import audio_library, channels, library
from studio.state import fresh_project_state, state

_REAL_PUBLIC_ADDRESS = audio_sources._public_address


@pytest.fixture(autouse=True)
def isolated_audio(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(library, "LIB_DIR", str(tmp_path / "output" / "library"))
    # These transport/parser tests exercise Commons, independently of the
    # separately verified offline licensed collection.
    monkeypatch.setattr(audio_sources, "_BUNDLED_ROOT", tmp_path / "no-bundled-catalog")
    monkeypatch.setattr(audio_sources, "_public_address", lambda *args: pytest.fail("Unmocked catalogue network access"))
    yield
    state.clear()
    state.update(previous)


def _wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 800)
    return buffer.getvalue()


def _payload(license_url="https://creativecommons.org/licenses/by/4.0/", **metadata):
    data = {"Artist": {"value": '<a href="https://example.org/author">Example artist</a>'},
            "ObjectName": {"value": "Quiet rain"}, "LicenseUrl": {"value": license_url},
            "LicenseShortName": {"value": "CC BY 4.0"}, "License": {"value": "cc-by-4.0"}, **metadata}
    return {"query": {"pages": {"123": {"pageid": 123, "index": 1, "title": "File:Quiet rain.wav",
        "imageinfo": [{"url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Quiet_rain.wav",
                       "mime": "audio/wav", "mediatype": "AUDIO", "size": len(_wav()), "extmetadata": data}]}}}}


def _music_payload(**metadata):
    payload = _payload(**{"ObjectName": {"value": "Quiet piano"},
        "ImageDescription": {"value": "An instrumental piano performance."}, **metadata})
    page = payload["query"]["pages"]["123"]
    page["title"] = "File:Quiet piano.wav"
    page["imageinfo"][0]["url"] = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Quiet_piano.wav"
    return payload


class _Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.status = status
        self.stream = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
    def getheader(self, name):
        return self.headers.get(name)
    def read(self, count):
        return self.stream.read(count)


def _network(monkeypatch, payload=None, data=None, status=200):
    calls = []
    @contextmanager
    def response(url, **kwargs):
        calls.append((url, kwargs))
        if kwargs["host"] == "commons.wikimedia.org":
            yield _Response(json.dumps(_payload() if payload is None else payload).encode(), status)
        else:
            yield _Response(_wav() if data is None else data, headers={"Content-Type": "audio/wav"})
    monkeypatch.setattr(audio_sources, "_response", response)
    return calls


def _acquire(**values):
    return audio_sources.acquire_audio("rain", kind="sfx", profile_id="default", **values)


def _cached_files():
    return sorted(path for path in (Path(config.DATA_DIR) / "audio-auto").rglob("*") if path.is_file())


def test_real_audio_is_cached_with_exact_license_and_attribution_without_changing_settings(monkeypatch):
    if not shutil.which("ffprobe"):
        pytest.skip("Real ffprobe required")
    calls = _network(monkeypatch)
    before = channels.get_profile("default")["defaults"]
    previous_state = deepcopy(state)
    asset = _acquire(min_duration=.1, max_duration=1)
    assert asset is not None, audio_sources.get_status()
    assert Path(asset["path"]).read_bytes() == _wav()
    assert asset["duration"] == pytest.approx(.1) and asset["bytes"] == len(_wav())
    assert asset["provider"] == "wikimedia" and asset["provider_id"] == "123"
    assert asset["license"] == "CC BY 4.0"
    assert asset["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert asset["creator"] == "Example artist" and "<a" not in asset["attribution"]
    assert all(value in asset["attribution"] for value in (asset["creator"], asset["title"], asset["license_url"], asset["source_url"]))
    assert asset["content_id"] == "unknown"
    assert Path(asset["path"]).parent == Path(config.DATA_DIR).resolve() / "audio-auto" / "default" / "sfx"
    metadata = next(path for path in _cached_files() if path.suffix == ".json")
    assert "path" not in json.loads(metadata.read_text())["asset"]
    assert not (Path(config.DATA_DIR) / "audio-library").exists()
    assert channels.get_profile("default")["defaults"] == before and state == previous_state
    assert len(calls) == 2
    params = parse_qs(urlsplit(calls[0][0]).query)
    assert params["gsrnamespace"] == ["6"] and params["gsrsearch"] == ["rain filetype:audio"]
    assert _acquire(min_duration=.1, max_duration=1) == asset
    assert len(calls) == 2 and audio_sources.get_status()["reason"] == "cached"


@pytest.mark.parametrize("url,name", [
    ("http://creativecommons.org/publicdomain/zero/1.0/deed.en", "CC0 1.0"),
    ("https://creativecommons.org/publicdomain/mark/1.0/", "Public domain"),
    ("https://creativecommons.org/licenses/by/2.5/", "CC BY 2.5"),
    ("https://creativecommons.org/licenses/by/3.0/legalcode", "CC BY 3.0"),
])
def test_only_canonical_supported_free_license_metadata_is_accepted(url, name):
    candidates = audio_sources._candidates(_payload(url), "sfx")
    assert len(candidates) == 1
    assert candidates[0]["license"] == name
    assert candidates[0]["license_url"].startswith("https://creativecommons.org/")
    assert "deed" not in candidates[0]["license_url"] and "legalcode" not in candidates[0]["license_url"]


def test_public_domain_declaration_uses_actual_file_page_without_inventing_a_cc_license():
    payload = _payload("", LicenseShortName={"value": "Public domain"}, License={"value": "pd"}, Copyrighted={"value": "False"})
    candidate = audio_sources._candidates(payload, "sfx")[0]
    assert candidate["license"] == "Public domain" and candidate["license_url"] == candidate["source_url"]


@pytest.mark.parametrize("url", [
    "https://creativecommons.org/licenses/by-sa/4.0/", "https://creativecommons.org/licenses/by-nc/4.0/",
    "https://creativecommons.org/licenses/by-nd/4.0/", "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "https://creativecommons.org.evil.example/licenses/by/4.0/", "https://@creativecommons.org/licenses/by/4.0/",
    "https://example.org/free-license", "https://creativecommons.org/licenses/by/4.0/?custom=true", "",
])
def test_noncommercial_noderivatives_sharealike_unknown_and_ambiguous_licenses_are_skipped(url):
    assert audio_sources._candidates(_payload(url), "sfx") == []


def test_conflicting_license_label_or_missing_required_author_is_skipped():
    assert audio_sources._candidates(_payload(LicenseShortName={"value": "CC BY-SA 4.0"}), "sfx") == []
    assert audio_sources._candidates(_payload(Artist={"value": ""}), "sfx") == []
    assert audio_sources._candidates(_payload("https://creativecommons.org/publicdomain/zero/1.0/", Artist={"value": ""}), "sfx")


@pytest.mark.parametrize("url", [
    "http://upload.wikimedia.org/wikipedia/commons/a.wav", "https://127.0.0.1/wikipedia/commons/a.wav",
    "https://upload.wikimedia.org.evil.example/wikipedia/commons/a.wav",
    "https://upload.wikimedia.org@evil.example/wikipedia/commons/a.wav",
    "https://user:secret@upload.wikimedia.org/wikipedia/commons/a.wav",
    "https://upload.wikimedia.org:444/wikipedia/commons/a.wav",
    "https://upload.wikimedia.org/private/a.wav", "https://upload.wikimedia.org/wikipedia/commons/../../private.wav",
    "https://upload.wikimedia.org/wikipedia/commons/%2e%2e/private.wav", "https://upload.wikimedia.org/wikipedia/commons/a.wav#other",
])
def test_catalogue_cannot_supply_an_arbitrary_download_target(url):
    payload = _payload()
    payload["query"]["pages"]["123"]["imageinfo"][0]["url"] = url
    assert audio_sources._candidates(payload, "sfx") == []


@pytest.mark.parametrize("field,value", [("size", audio_sources.MAX_AUDIO_BYTES + 1), ("size", True), ("mime", "text/html"), ("mediatype", "VIDEO")])
def test_oversized_or_nonaudio_catalogue_entries_are_skipped(field, value):
    payload = _payload()
    payload["query"]["pages"]["123"]["imageinfo"][0][field] = value
    assert audio_sources._candidates(payload, "sfx") == []


def test_catalogue_rate_limit_is_visible_and_does_not_retry_or_write_files(monkeypatch, capsys):
    calls = _network(monkeypatch, status=429)
    assert _acquire() is None
    assert len(calls) == 1 and _cached_files() == []
    assert audio_sources.get_status()["reason"] == "rate_limited"
    assert "temporarily unavailable" in capsys.readouterr().out


def test_no_licensed_match_returns_safe_diagnostic_without_download(monkeypatch):
    calls = _network(monkeypatch, _payload("https://creativecommons.org/licenses/by-nc/4.0/"))
    assert _acquire() is None
    assert len(calls) == 1 and audio_sources.get_status()["reason"] == "no_licensed_match"


@pytest.mark.parametrize("bad_bytes,duration", [(b"not audio", None), (_wav(), 7201), (_wav(), .05)])
def test_unreadable_or_unsuitable_audio_leaves_no_partial_cache(monkeypatch, bad_bytes, duration):
    payload = _payload()
    payload["query"]["pages"]["123"]["imageinfo"][0]["size"] = len(bad_bytes)
    _network(monkeypatch, payload, data=bad_bytes)
    if duration is not None:
        monkeypatch.setattr(audio_library, "_probe_audio", lambda path: duration)
    elif not shutil.which("ffprobe"):
        pytest.skip("Real ffprobe required")
    assert _acquire(min_duration=.1, max_duration=100) is None
    assert _cached_files() == []


def test_byte_cap_is_enforced_for_chunked_download_and_temporary_file_is_removed(monkeypatch):
    calls = []
    @contextmanager
    def response(url, **kwargs):
        calls.append(url)
        if kwargs["host"] == "commons.wikimedia.org":
            yield _Response(json.dumps(_payload()).encode())
        else:
            value = _Response(b"x" * 100, headers={"Content-Type": "audio/wav"})
            value.headers.pop("Content-Length")
            yield value
    monkeypatch.setattr(audio_sources, "_response", response)
    # The candidate fits; a dishonest/chunked body does not.
    payload = _payload()
    candidate = audio_sources._candidates(payload, "sfx")[0]
    candidate["expected_bytes"] = 10
    monkeypatch.setattr(audio_sources, "_search", lambda *args: [candidate])
    monkeypatch.setattr(audio_sources, "MAX_AUDIO_BYTES", 32)
    assert _acquire() is None
    assert audio_sources.get_status()["reason"] == "file_too_large" and _cached_files() == []


def test_every_redirect_rechecks_the_media_allowlist_before_network_access(monkeypatch, tmp_path):
    original = audio_sources._response
    calls = []
    @contextmanager
    def redirect_once(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            yield _Response(status=302, headers={"Location": "http://127.0.0.1/private"})
        else:
            with original(url, **kwargs) as response:
                yield response
    monkeypatch.setattr(audio_sources, "_response", redirect_once)
    candidate = audio_sources._candidates(_payload(), "sfx")[0]
    with pytest.raises(audio_sources.AudioSourceError) as error:
        audio_sources._download(candidate, tmp_path / "out.wav", None, time.monotonic() + 5)
    assert error.value.code == "unsafe_url" and len(calls) == 2
    assert not (tmp_path / "out.wav").exists()


def test_dns_private_address_is_rejected_before_connecting(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(audio_sources.AudioSourceError) as error:
        _REAL_PUBLIC_ADDRESS("upload.wikimedia.org", None, time.monotonic() + 2)
    assert error.value.code == "unsafe_address"


def test_explicit_channel_cache_is_not_redirected_by_active_switch(monkeypatch):
    _network(monkeypatch)
    owner = channels.create_profile("News")
    other = channels.create_profile("ASMR", "asmr")
    def probe(path):
        channels.activate_profile(other["id"])
        return .1
    monkeypatch.setattr(audio_library, "_probe_audio", probe)
    asset = audio_sources.acquire_audio("rain", kind="sfx", profile_id=owner["id"])
    assert asset is not None
    assert owner["id"] in Path(asset["path"]).parts and other["id"] not in Path(asset["path"]).parts
    assert not (Path(config.DATA_DIR) / "audio-auto" / other["id"]).exists()
    assert channels.active_profile_id() == other["id"]


def test_deleted_profile_cannot_commit_or_read_automatic_cache(monkeypatch):
    _network(monkeypatch)
    owner = channels.create_profile("Temporary")
    def delete_profile(path):
        channels.delete_profile(owner["id"])
        return .1
    monkeypatch.setattr(audio_library, "_probe_audio", delete_profile)
    assert audio_sources.acquire_audio("rain", kind="sfx", profile_id=owner["id"]) is None
    assert audio_sources.get_status()["reason"] == "profile_unavailable" and _cached_files() == []


def test_corrupt_cached_file_is_not_played_as_a_trusted_asset(monkeypatch):
    _network(monkeypatch)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    asset = _acquire()
    Path(asset["path"]).write_bytes(b"x" * asset["bytes"])
    monkeypatch.setattr(audio_sources, "_search", lambda *args: [])
    assert _acquire() is None and audio_sources.get_status()["reason"] == "no_licensed_match"


def test_cache_commit_failure_preserves_previous_files_and_removes_new_download(monkeypatch):
    _network(monkeypatch)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    _acquire()
    before = {path: path.read_bytes() for path in _cached_files()}
    def failure(*args):
        raise OSError("No disk space")
    monkeypatch.setattr(audio_sources, "_save_cache", failure)
    payload = _payload()
    payload["query"]["pages"]["123"]["pageid"] = 124
    _network(monkeypatch, payload)
    assert audio_sources.acquire_audio("different query", kind="sfx", profile_id="default") is None
    assert {path: path.read_bytes() for path in _cached_files()} == before


def test_stop_before_search_does_not_access_network_or_write_cache(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(audio_sources, "_search", lambda *args: pytest.fail("Stopped job queried catalogue"))
    assert _acquire(stop_event=stop) is None
    assert audio_sources.get_status()["reason"] == "cancelled" and _cached_files() == []


def test_stop_during_download_removes_partial_file_and_never_commits(monkeypatch):
    _network(monkeypatch)
    stop = threading.Event()
    def download(candidate, path, stop_event, deadline):
        path.write_bytes(_wav())
        stop_event.set()
    monkeypatch.setattr(audio_sources, "_download", download)
    assert _acquire(stop_event=stop) is None
    assert audio_sources.get_status()["reason"] == "cancelled" and _cached_files() == []


def test_diagnostics_do_not_leak_between_parallel_workers(monkeypatch):
    barrier = threading.Barrier(2)
    def search(query, *args):
        barrier.wait(timeout=2)
        code = "rate_limited" if query == "rain" else "catalogue_unavailable"
        raise audio_sources.AudioSourceError(code, code)
    monkeypatch.setattr(audio_sources, "_search", search)
    def worker(query):
        assert audio_sources.acquire_audio(query, kind="sfx", profile_id="default") is None
        return audio_sources.get_status()["reason"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["rain", "wind"]))
    assert results == ["rate_limited", "catalogue_unavailable"]


@pytest.mark.parametrize("payload", [None, [], {"query": []}, {"query": "invalid"}, {"query": {"pages": "invalid"}}])
def test_malformed_catalogue_structure_is_a_safe_empty_candidate_list(payload):
    assert audio_sources._candidates(payload, "sfx") == []


def test_candidate_download_attempts_are_bounded(monkeypatch):
    candidate = audio_sources._candidates(_payload(), "sfx")[0]
    candidates = [{**candidate, "provider_id": str(index)} for index in range(6)]
    monkeypatch.setattr(audio_sources, "_search", lambda *args: candidates)
    attempts = []
    def failed_download(candidate, path, *args):
        attempts.append(candidate["provider_id"])
        raise audio_sources.AudioSourceError("download_unavailable", "No usable file")
    monkeypatch.setattr(audio_sources, "_download", failed_download)
    assert _acquire() is None
    assert len(attempts) == audio_sources.MAX_DOWNLOADS and _cached_files() == []


def test_other_channel_gets_its_own_cache_and_deleted_channel_cannot_reuse_it(monkeypatch):
    calls = _network(monkeypatch)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    first = channels.create_profile("First")
    second = channels.create_profile("Second")
    first_asset = audio_sources.acquire_audio("rain", kind="sfx", profile_id=first["id"])
    second_asset = audio_sources.acquire_audio("rain", kind="sfx", profile_id=second["id"])
    assert first_asset["path"] != second_asset["path"] and len(calls) == 4
    assert Path(first_asset["path"]).read_bytes() == Path(second_asset["path"]).read_bytes()
    channels.delete_profile(first["id"])
    assert audio_sources.acquire_audio("rain", kind="sfx", profile_id=first["id"]) is None
    assert audio_sources.get_status()["reason"] == "profile_unavailable" and len(calls) == 4
    assert audio_sources.acquire_audio("rain", kind="sfx", profile_id=second["id"]) == second_asset
    assert len(calls) == 4


@pytest.mark.parametrize("field,value", [
    ("ImageDescription", "English pronunciation of rain"),
    ("Categories", "Sounds|Kataf pronunciation|Lingua Libre"),
    ("ImageDescription", "Spoken Wikipedia article"),
])
def test_pronunciation_or_spoken_articles_are_not_selected_as_background_audio(field, value):
    payload = _music_payload(**{field: {"value": value}})
    assert audio_sources._candidates(payload, "music") == []
    assert audio_sources._candidates(payload, "sfx") == []


def test_a_licensed_rain_recording_cannot_be_relabelled_as_music():
    payload = _payload("https://creativecommons.org/publicdomain/zero/1.0/",
                       ImageDescription={"value": "A field recording of rain."})
    assert audio_sources._candidates(payload, "sfx")
    assert audio_sources._candidates(payload, "music") == []


@pytest.mark.parametrize("description", [
    "An instrumental piano piece.", "Solo guitar performance.", "A piano sonata.",
    "An ambient music composition.", "Electronic music without vocals or speech.",
    "Instrumental music with no vocals, narration or lyrics.", "Instrumental vocal-free piano music.",
])
def test_positive_musical_evidence_is_eligible(description):
    candidates = audio_sources._candidates(_music_payload(ImageDescription={"value": description}), "music")
    assert len(candidates) == 1 and candidates[0]["kind"] == "music"


@pytest.mark.parametrize("description", [
    "Instrumental music with a spoken introduction.", "A choir sings over piano music.",
    "Instrumental accompaniment with lead vocals.", "Instrumental music featuring a vocalist.",
    "Piano music with singing.", "Instrumental music and speech.",
    "A vocalization over instrumental music.", "Instrumental music with rap.",
    "An a cappella version of an instrumental melody.",
    "No vocals, but a spoken introduction over instrumental piano music.",
    "An instrumental sample accompanies an interview.",
    "Instrumental music with a field recording of rain.", "This is not instrumental.",
    "A background music recording.",  # A generic label does not establish an instrumental bed.
])
def test_vocal_spoken_environmental_or_ambiguous_recordings_are_omitted_from_music(description):
    assert audio_sources._candidates(_music_payload(ImageDescription={"value": description}), "music") == []


@pytest.mark.parametrize("field", ["ObjectName", "Categories"])
def test_vocal_evidence_in_other_metadata_overrides_instrumental_description(field):
    payload = _music_payload(**{field: {"value": "Piano music with singing"}})
    assert audio_sources._candidates(payload, "music") == []


@pytest.mark.parametrize("query,kind,expected", [
    ("gentle instrumental background music subtle", "music", "instrumental music"),
    ("gentle rain ambient recording calm", "sfx", "rain"),
    ("gentle sea waves ambient recording calm", "sfx", "sea waves"),
    ("quiet forest birds ambient recording subtle", "sfx", "forest birds"),
    ("quiet fireplace crackling recording subtle", "sfx", "fireplace crackling"),
    ("soft paper turn sound effect", "sfx", "paper turn"),
    ("soft footsteps on gravel sound effect", "sfx", "footsteps on gravel"),
    ("quiet ambient music subtle", "music", "ambient music"),  # Keep the music genre.
    ("gentle rain without thunder recording", "sfx", "rain without thunder"),
    ("calm background music", "music", None), ("quiet ambient recording", "sfx", None),
    ("soft background noise", "sfx", None), ("rain", "sfx", None),
])
def test_single_broadening_removes_only_known_filler_and_preserves_the_subject(query, kind, expected):
    assert audio_sources._broader_query(query, kind) == expected


def test_one_empty_search_uses_same_genre_variant_then_caches_the_original_intent(monkeypatch):
    searches, downloads = [], []
    @contextmanager
    def response(url, **kwargs):
        if kwargs["host"] == "commons.wikimedia.org":
            searches.append(parse_qs(urlsplit(url).query)["gsrsearch"][0])
            payload = {"query": {"pages": {}}} if len(searches) == 1 else _music_payload()
            yield _Response(json.dumps(payload).encode())
        else:
            downloads.append(url)
            yield _Response(_wav(), headers={"Content-Type": "audio/wav"})
    monkeypatch.setattr(audio_sources, "_response", response)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    options = {"kind": "music", "profile_id": "default", "mood": "subtle"}
    query = "calm ambient instrumental music without vocals, low tempo, suitable for documentary narration"
    asset = audio_sources.acquire_audio(query, **options)
    assert asset is not None and asset["title"] == "Quiet piano"
    assert searches == ["ambient instrumental music filetype:audio", "ambient music filetype:audio"]
    assert len(downloads) == 1
    assert audio_sources.acquire_audio(query, **options) == asset
    assert len(searches) == 2 and len(downloads) == 1
    assert audio_sources.get_status()["reason"] == "cached"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_long_query_is_not_broadened_after_http_failure(monkeypatch, status):
    calls = _network(monkeypatch, status=status)
    assert audio_sources.acquire_audio("gentle rain ambient recording", kind="sfx",
                                      profile_id="default", mood="calm") is None
    assert len(calls) == 1 and not _cached_files()
    assert "omitted" in audio_sources.get_status()["message"]
    assert "fallback" not in audio_sources.get_status()["message"]


def test_transport_failure_does_not_trigger_a_broader_query(monkeypatch):
    calls = []
    def fail(query, *args):
        calls.append(query)
        raise OSError("Connection failed")
    monkeypatch.setattr(audio_sources, "_search", fail)
    assert audio_sources.acquire_audio("quiet forest birds recording", kind="sfx", profile_id="default") is None
    assert len(calls) == 1 and not _cached_files()


def test_broadened_search_shares_deadline_and_total_download_budget(monkeypatch):
    candidate = audio_sources._candidates(_payload(), "sfx")[0]
    candidates = [{**candidate, "provider_id": str(index)} for index in range(8)]
    searches, downloads = [], []
    def search(query, kind, stop_event, deadline):
        searches.append((query, deadline))
        return [] if len(searches) == 1 else candidates
    def download(candidate, path, stop_event, deadline):
        downloads.append(deadline)
        raise audio_sources.AudioSourceError("unusable_audio", "Unusable recording; omitted.")
    monkeypatch.setattr(audio_sources, "_search", search)
    monkeypatch.setattr(audio_sources, "_download", download)
    assert audio_sources.acquire_audio("soft paper turn transition sound", kind="sfx",
                                      profile_id="default", mood="calm") is None
    assert [row[0] for row in searches] == ["paper turn", "page turn", "paper rustle"]
    assert len(downloads) == audio_sources.MAX_DOWNLOADS
    assert len({row[1] for row in searches} | set(downloads)) == 1
    assert not _cached_files()


def test_cancellation_after_empty_search_prevents_broadening(monkeypatch):
    stop, calls = threading.Event(), []
    def search(query, *args):
        calls.append(query)
        stop.set()
        return []
    monkeypatch.setattr(audio_sources, "_search", search)
    assert audio_sources.acquire_audio("gentle rain ambient recording", kind="sfx",
                                      profile_id="default", stop_event=stop) is None
    assert len(calls) == 1 and audio_sources.get_status()["reason"] == "cancelled"


def test_music_cached_before_suitability_checks_is_not_reused(monkeypatch):
    _network(monkeypatch, _music_payload())
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    query = "gentle instrumental background music"
    options = {"kind": "music", "profile_id": "default", "mood": "subtle"}
    assert audio_sources.acquire_audio(query, **options) is not None
    metadata = next(path for path in _cached_files() if path.suffix == ".json")
    saved = json.loads(metadata.read_text())
    saved.pop("music_selection_version")
    saved["asset"]["title"] = "Rain recording previously labelled as music"
    metadata.write_text(json.dumps(saved))
    searches = []
    monkeypatch.setattr(audio_sources, "_search", lambda query, *args: searches.append(query) or [])
    assert audio_sources.acquire_audio(query, **options) is None
    assert searches == ["instrumental music"]
    assert audio_sources.get_status()["reason"] == "no_licensed_match"


@pytest.mark.parametrize("query,kind,expected", [
    ("calm ambient instrumental music without vocals, low tempo, suitable for documentary narration", "music",
     ["ambient instrumental music", "ambient music"]),
    ("gentle instrumental piano music without vocals or speech", "music", ["instrumental piano music", "piano music"]),
    ("soft paper turn sound effect", "sfx", ["paper turn", "page turn", "paper rustle"]),
    ("small quiet whoosh for narration", "sfx", ["small whoosh", "small swish"]),
    ("gentle swoosh for a scene transition", "sfx", ["whoosh", "swish"]),
    ("soft footsteps on gravel sound effect", "sfx", ["footsteps on gravel"]),
    ("gentle rain without thunder recording", "sfx", ["rain without thunder"]),
    ("fast energetic guitar music", "music", ["fast energetic guitar music"]),
])
def test_literal_queries_preserve_subject_and_do_not_search_usage_or_vocal_exclusions(query, kind, expected):
    assert audio_sources._search_queries(query, kind) == expected


def test_next_literal_query_is_tried_after_licensed_candidate_has_unsuitable_duration(monkeypatch):
    first = audio_sources._candidates(_payload(), "sfx")[0]
    second = {**first, "provider_id": "456", "title": "Turning a page"}
    searches, downloads = [], []
    def search(query, *args):
        searches.append(query)
        return [first] if query == "paper turn" else [second]
    def download(candidate, path, *args):
        downloads.append(candidate["provider_id"])
        path.write_bytes(_wav())
    monkeypatch.setattr(audio_sources, "_search", search)
    monkeypatch.setattr(audio_sources, "_download", download)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .01 if len(downloads) == 1 else .1)
    asset = audio_sources.acquire_audio("soft paper turn", kind="sfx", profile_id="default", min_duration=.05)
    assert asset["provider_id"] == "456"
    assert searches == ["paper turn", "page turn"] and downloads == ["123", "456"]


def test_repeated_catalogue_candidate_is_not_downloaded_again_for_every_query(monkeypatch):
    candidate = audio_sources._candidates(_payload(), "sfx")[0]
    searches, downloads = [], []
    monkeypatch.setattr(audio_sources, "_search", lambda query, *args: searches.append(query) or [candidate] * 4)
    def download(candidate, *args):
        downloads.append(candidate["provider_id"])
        raise audio_sources.AudioSourceError("unusable_audio", "Unusable sound")
    monkeypatch.setattr(audio_sources, "_download", download)
    assert audio_sources.acquire_audio("soft paper turn", kind="sfx", profile_id="default") is None
    assert searches == ["paper turn", "page turn", "paper rustle"] and downloads == ["123"]
