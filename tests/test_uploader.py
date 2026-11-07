"""YouTube uploader: tag limits, JSON token storage, refresh, thumbnail failures."""
import json
import os

import pytest

import config
import gui
from studio import state as studio_state, upload as studio_upload
from uploader import youtube


def _cost(tags):
    """Length as YouTube counts it: quotes around spaced tags, commas between."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


# ── fit_tags ──────────────────────────────────────────────────────────────────

def test_fit_tags_keeps_short_lists_unchanged():
    assert youtube.fit_tags(["moon", "space", "black holes"]) == ["moon", "space", "black holes"]


def test_fit_tags_stays_under_500_counting_quotes_and_commas():
    tags = [f"tag with spaces {i:03d}" for i in range(60)]
    out = youtube.fit_tags(tags)
    assert out == tags[:len(out)]                 # order kept
    assert 0 < len(out) < len(tags)
    assert _cost(out) <= youtube.TAGS_MAX_CHARS
    assert _cost(tags[:len(out) + 1]) > youtube.TAGS_MAX_CHARS


def test_fit_tags_skips_a_long_tag_but_keeps_later_short_ones():
    out = youtube.fit_tags(["a" * 300, "b" * 250, "short"])
    assert out == ["a" * 300, "short"]


def test_fit_tags_cleans_duplicates_blanks_and_angle_brackets():
    out = youtube.fit_tags(["Space", "space", "  ", None, "<b>bold</b>", "  two   words "])
    assert out == ["Space", "None", "bbold/b", "two words"]


# ── token storage ─────────────────────────────────────────────────────────────

TOKEN = {
    "token": "access", "refresh_token": "refresh", "client_id": "id.apps.googleusercontent.com",
    "client_secret": "secret", "token_uri": "https://oauth2.googleapis.com/token",
    "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
}


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_token_round_trips_as_json(in_tmp):
    from google.oauth2.credentials import Credentials
    youtube._save_token(Credentials.from_authorized_user_info(TOKEN))
    assert youtube.has_token()
    saved = json.loads((in_tmp / youtube.TOKEN_FILE).read_text())
    assert saved["refresh_token"] == "refresh"
    if os.name == "posix":
        assert (os.stat(youtube.TOKEN_FILE).st_mode & 0o777) == 0o600
    assert youtube._load_token().refresh_token == "refresh"


def test_unreadable_token_means_sign_in_again(in_tmp):
    (in_tmp / youtube.TOKEN_FILE).write_text('{"token": "x"}')   # no refresh fields
    assert youtube._load_token() is None


def test_legacy_pickle_is_never_loaded(in_tmp):
    (in_tmp / "youtube_token.pickle").write_bytes(b"not a pickle")
    assert not youtube.has_token()
    assert youtube._load_token() is None


def test_expired_token_is_refreshed_and_saved(in_tmp, monkeypatch):
    from google.oauth2.credentials import Credentials
    (in_tmp / youtube.TOKEN_FILE).write_text(json.dumps({**TOKEN, "expiry": "2000-01-01T00:00:00Z"}))

    def fake_refresh(self, request):
        self.token = "fresh"
        self.expiry = None
    monkeypatch.setattr(Credentials, "refresh", fake_refresh)

    creds = youtube.get_credentials()
    assert creds.token == "fresh"
    assert json.loads((in_tmp / youtube.TOKEN_FILE).read_text())["token"] == "fresh"


def test_missing_client_secret_is_a_clear_error(in_tmp, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_CLIENT_SECRET", "client_secret.json")
    with pytest.raises(FileNotFoundError, match="SETUP.md"):
        youtube.get_credentials()


# ── upload_video ──────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status): self.status = status


class _ThumbError(Exception):
    resp = _Resp(403)


class _FakeYouTube:
    def __init__(self, thumb_error=None):
        self.thumb_error, self.body = thumb_error, None

    def videos(self):
        yt = self

        class _Videos:
            def insert(self, part, body, media_body):
                yt.body = body

                class _Req:
                    def next_chunk(self): return None, {"id": "abc123"}
                return _Req()
        return _Videos()

    def thumbnails(self):
        yt = self

        class _Thumbs:
            def set(self, videoId, media_body):
                class _Exec:
                    def execute(self):
                        if yt.thumb_error:
                            raise yt.thumb_error
                return _Exec()
        return _Thumbs()


@pytest.fixture
def fake_yt(in_tmp, monkeypatch):
    (in_tmp / "v.mp4").write_bytes(b"\0" * 16)
    (in_tmp / "t.jpg").write_bytes(b"\xff\xd8\xff")
    yt = _FakeYouTube()
    monkeypatch.setattr(youtube, "get_credentials", lambda log=print: object())
    monkeypatch.setattr(youtube, "_service", lambda creds: yt)
    return yt


def test_upload_caps_title_description_and_tags(fake_yt):
    r = youtube.upload_video("v.mp4", "T" * 150, "D" * 6000,
                             tags=[f"tag number {i}" for i in range(100)], privacy="unlisted")
    snip = fake_yt.body["snippet"]
    assert r["url"] == "https://www.youtube.com/watch?v=abc123"
    assert len(snip["title"]) == 100 and len(snip["description"]) == 5000
    assert _cost(snip["tags"]) <= 500
    assert fake_yt.body["status"]["privacyStatus"] == "unlisted"


def test_scheduled_upload_is_private_until_publish_time(fake_yt):
    youtube.upload_video("v.mp4", "T", publish_at="2030-01-01T10:00:00Z", privacy="public")
    assert fake_yt.body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                      "publishAt": "2030-01-01T10:00:00Z"}


def test_thumbnail_failure_is_reported_not_swallowed(fake_yt):
    fake_yt.thumb_error = _ThumbError("forbidden")
    logged = []
    r = youtube.upload_video("v.mp4", "T", thumb_path="t.jpg", log=logged.append)
    assert r["id"] == "abc123"                               # the video still uploaded
    assert "verified channel" in r["thumbnail_error"]
    assert logged == [r["thumbnail_error"]]


def test_gui_upload_uses_the_uploader(fake_yt, monkeypatch):
    from studio import channels
    ident = "UC" + "a" * 22
    service = _ProfileYouTube(ident)
    service.thumb_error = _ThumbError("forbidden")
    monkeypatch.setattr(youtube, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(youtube, "_service", lambda creds: service)
    channels.get_profile("default")
    channels._save_channel("default", {"id": ident, "title": "Verified test channel"})
    channels.token_path("default").write_text("fixture-token")
    fake_yt_dir = os.getcwd()
    monkeypatch.setattr(studio_upload, "secret_ok", lambda profile_id=None: True)
    monkeypatch.setattr(studio_upload, "sync_library", lambda: None)
    monkeypatch.setitem(studio_state.state, "profile_id", "default")
    monkeypatch.setitem(studio_state.state, "preview_only", False)
    monkeypatch.setitem(studio_state.state, "visual_plan", None)
    monkeypatch.setitem(studio_state.state, "video_path", os.path.join(fake_yt_dir, "v.mp4"))
    monkeypatch.setitem(studio_state.state, "thumb_path", os.path.join(fake_yt_dir, "t.jpg"))
    monkeypatch.setitem(studio_state.state, "script", {"title": "Hi", "tags": ["a"]})
    monkeypatch.setitem(studio_state.state, "description", "desc")

    class _NowThread:                                        # run the upload inline
        def __init__(self, target, daemon=None): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(studio_upload.threading, "Thread", _NowThread)

    r = gui.app.test_client().post("/api/upload", json={"privacy": "private"},
                                   headers={"Host": "localhost:7070"})
    assert r.get_json() == {"ok": True}
    status = gui.app.test_client().get("/api/status", headers={"Host": "localhost:7070"}).get_json()
    assert status["yt_url"] == "https://www.youtube.com/watch?v=abc123"
    assert "verified channel" in status["upload_warning"]
    assert service.body["snippet"]["description"] == "desc"


def test_noninteractive_missing_token_never_opens_browser(in_tmp, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file",
                        lambda *args, **kwargs: pytest.fail("Unattended jobs must not start OAuth"))
    with pytest.raises(youtube.YouTubeAuthRequired, match="Connect YouTube"):
        youtube.get_credentials(token_file=in_tmp / "profile.json", interactive=False,
                                scopes=youtube.CHANNEL_SCOPES)


def test_old_upload_only_scope_is_not_relabelled_as_channel_read(in_tmp):
    path = in_tmp / "profile.json"
    path.write_text(json.dumps(TOKEN))
    assert youtube._load_token(path).scopes == TOKEN["scopes"]
    with pytest.raises(youtube.YouTubeAuthRequired, match="channel-read"):
        youtube.get_credentials(token_file=path, interactive=False, scopes=youtube.CHANNEL_SCOPES)


def test_profile_refresh_writes_only_its_own_token(in_tmp, monkeypatch):
    from google.oauth2.credentials import Credentials
    profile = in_tmp / "channels" / "token.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({**TOKEN, "scopes": list(youtube.CHANNEL_SCOPES), "expiry": "2000-01-01T00:00:00Z"}))
    legacy = in_tmp / youtube.TOKEN_FILE
    legacy.write_text("leave this token alone")

    def refresh(self, request):
        self.token, self.expiry = "fresh-profile-token", None

    monkeypatch.setattr(Credentials, "refresh", refresh)
    credentials = youtube.get_credentials(token_file=profile, interactive=False, scopes=youtube.CHANNEL_SCOPES)
    assert credentials.token == "fresh-profile-token"
    assert json.loads(profile.read_text())["token"] == "fresh-profile-token"
    assert legacy.read_text() == "leave this token alone"
    if os.name == "posix":
        assert (profile.stat().st_mode & 0o777) == 0o600


def test_revoked_profile_token_fails_without_interactive_fallback(in_tmp, monkeypatch):
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    path = in_tmp / "profile.json"
    path.write_text(json.dumps({**TOKEN, "scopes": list(youtube.CHANNEL_SCOPES), "expiry": "2000-01-01T00:00:00Z"}))

    def revoked(self, request):
        raise RuntimeError("refresh_token=private-value")

    monkeypatch.setattr(Credentials, "refresh", revoked)
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda *a, **kw: pytest.fail("No OAuth fallback"))
    messages = []
    with pytest.raises(youtube.YouTubeAuthRequired):
        youtube.get_credentials(token_file=path, interactive=False, scopes=youtube.CHANNEL_SCOPES, log=messages.append)
    assert "private-value" not in " ".join(messages)


class _ProfileYouTube(_FakeYouTube):
    def __init__(self, channel_id):
        super().__init__()
        self.channel_id, self.identity_query = channel_id, None

    def channels(self):
        yt = self

        class _Channels:
            def list(self, **kwargs):
                yt.identity_query = kwargs

                class _Request:
                    def execute(self):
                        return {"items": [{"id": yt.channel_id,
                            "snippet": {"title": "Fetched channel", "thumbnails": {"default": {"url": "https://example.com/untrusted.png"}}},
                            "statistics": {"subscriberCount": "123", "videoCount": "4", "viewCount": "567"}}]}
                return _Request()
        return _Channels()


def test_profile_upload_checks_authenticated_channel_before_upload(fake_yt, monkeypatch):
    expected = "UC" + "a" * 22
    service = _ProfileYouTube("UC" + "b" * 22)
    calls = []
    monkeypatch.setattr(youtube, "get_credentials", lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr(youtube, "_service", lambda creds: service)
    with pytest.raises(youtube.YouTubeChannelMismatch, match="does not match"):
        youtube.upload_video("v.mp4", "Do not publish", token_file="profile.json", expected_channel_id=expected)
    assert service.body is None
    assert service.identity_query["mine"] is True
    assert calls[0]["interactive"] is False
    assert calls[0]["token_file"] == "profile.json"


def test_profile_upload_uses_verified_identity_and_category(fake_yt, monkeypatch):
    expected = "UC" + "a" * 22
    service = _ProfileYouTube(expected)
    monkeypatch.setattr(youtube, "get_credentials", lambda **kwargs: object())
    monkeypatch.setattr(youtube, "_service", lambda creds: service)
    result = youtube.upload_video("v.mp4", "Right channel", token_file="profile.json",
                                  expected_channel_id=expected, category_id="25", privacy="private")
    assert result["id"] == "abc123"
    assert service.body["snippet"]["categoryId"] == "25"
    assert service.body["status"]["privacyStatus"] == "private"


def test_profile_upload_requires_verified_channel_even_with_token(fake_yt):
    with pytest.raises(youtube.YouTubeChannelMismatch, match="verify"):
        youtube.upload_video("v.mp4", "Unverified", token_file="profile.json")
    assert fake_yt.body is None


def test_fetched_metadata_contains_no_oauth_or_arbitrary_image_url(in_tmp, monkeypatch):
    ident = "UC" + "a" * 22
    service = _ProfileYouTube(ident)
    monkeypatch.setattr(youtube, "_service", lambda creds: service)
    channel = youtube.fetch_authenticated_channel(credentials=object())
    assert channel["id"] == ident and channel["title"] == "Fetched channel"
    assert channel["subscribers"] == 123 and channel["videos"] == 4
    assert channel["thumbnail"] == ""
    assert service.identity_query == {"part": "snippet,statistics", "mine": True, "maxResults": 50}
