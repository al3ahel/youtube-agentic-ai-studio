"""
YouTube Uploader — the one place YAAS talks to the YouTube Data API v3.

  get_credentials()  load the saved OAuth token, refresh it if it expired,
                     or run the browser sign-in once and save the result
  upload_video()     upload the MP4 (resumable), then set the thumbnail
  fit_tags()         trim tags to YouTube's 500-character limit

The token is stored as JSON (youtube_token.json), never pickled.
Google libraries are imported lazily so importing this module stays cheap.
"""
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

import config

# Older versions pickled the token to youtube_token.pickle. That file is never
# loaded (unpickling runs code); users sign in once more and can delete it.
TOKEN_FILE = "youtube_token.json"

TITLE_MAX = 100          # characters
DESCRIPTION_MAX = 5000   # characters
TAGS_MAX_CHARS = 500     # all tags together, as YouTube counts them

CHANNEL_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")


class YouTubeAuthRequired(RuntimeError):
    """The saved sign-in cannot be used without an explicit Connect action."""


class YouTubeChannelMismatch(RuntimeError):
    """The OAuth identity does not match the channel bound to the job/profile."""


def has_token(token_file=None) -> bool:
    return Path(token_file or TOKEN_FILE).is_file()


def _save_token(creds, token_file=None) -> None:
    """Atomically save just this profile's token with restrictive permissions."""
    path = Path(token_file or TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".youtube-token-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(creds.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_token(token_file=None):
    from google.oauth2.credentials import Credentials
    path = Path(token_file or TOKEN_FILE)
    if not path.is_file():
        return None
    try:
        # Preserve the scopes actually recorded in the token. Passing new scopes
        # here would incorrectly label an old upload-only grant as read-capable.
        return Credentials.from_authorized_user_file(str(path))
    except (ValueError, OSError):
        return None


def get_credentials(log=print, *, token_file=None, client_secret_file=None,
                    interactive=True, scopes=None, force=False):
    """Load/refresh one token; only explicit interactive calls may open OAuth.

    Legacy callers keep their original default token/sign-in behaviour. Channel
    workers pass a profile token and ``interactive=False`` so a scheduled job can
    never unexpectedly open a browser or borrow another profile's credentials.
    """
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    requested_scopes = tuple(scopes or config.YOUTUBE_SCOPES)
    creds = None if force else _load_token(token_file)
    if creds:
        granted = set(creds.scopes or [])
        broad = "https://www.googleapis.com/auth/youtube" in granted
        if not broad and not set(requested_scopes).issubset(granted):
            if not interactive:
                raise YouTubeAuthRequired(
                    "This saved sign-in needs channel-read permission. Use Connect YouTube for this profile."
                )
            creds = None
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_file)
        except Exception:
            log("The saved YouTube sign-in could not be refreshed. Reconnect this channel profile.")
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            raise YouTubeAuthRequired("Connect YouTube for this channel profile before publishing or refreshing it.")
        secret = str(client_secret_file or config.YOUTUBE_CLIENT_SECRET)
        if not os.path.isfile(secret):
            raise FileNotFoundError(
                f"YouTube OAuth file not found: {secret}\n"
                "Follow SETUP.md step 4 to create it in Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            secret, list(requested_scopes))
        creds = flow.run_local_server(
            host="localhost", bind_addr="127.0.0.1", port=0, timeout_seconds=180,
            prompt="select_account consent", access_type="offline",
            authorization_prompt_message="Complete the YouTube sign-in in the browser opened for this profile.",
            success_message="YouTube sign-in received. Return to YAAS to check the selected channel.",
        )
        if not creds or not creds.valid:
            raise YouTubeAuthRequired("YouTube sign-in did not finish. Choose Connect YouTube to try again.")
        _save_token(creds, token_file)
    return creds


def _tag_cost(tag: str) -> int:
    # YouTube wraps a tag that contains a space in quotes, and those count
    return len(tag) + (2 if " " in tag else 0)


def fit_tags(tags, limit: int = TAGS_MAX_CHARS) -> list:
    """
    Clean tags and keep as many as fit in YouTube's limit, in order.
    The limit covers every tag plus the commas between them; over it, YouTube
    rejects the whole upload with `invalidTags`. Angle brackets are not allowed.
    """
    out, seen, used = [], set(), 0
    for tag in tags or []:
        tag = " ".join(str(tag).replace("<", "").replace(">", "").split())
        key = tag.lower()
        if not tag or key in seen:
            continue
        cost = _tag_cost(tag) + (1 if out else 0)       # comma before all but the first
        if used + cost > limit:
            continue                                     # a shorter tag later may still fit
        out.append(tag)
        seen.add(key)
        used += cost
    return out


def _service(creds):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    return build("youtube", "v3", http=AuthorizedHttp(creds, http=httplib2.Http(timeout=30)),
                 cache_discovery=False)


def _channel_from_service(yt, expected_channel_id=None) -> dict:
    """Fetch the OAuth account's selected channel, never a user-entered ID."""
    if expected_channel_id is not None and not _CHANNEL_ID.fullmatch(str(expected_channel_id)):
        raise YouTubeChannelMismatch("This profile has no verified YouTube channel. Connect it before publishing.")
    response = yt.channels().list(part="snippet,statistics", mine=True, maxResults=50).execute()
    items = response.get("items", [])
    if not items:
        raise YouTubeAuthRequired("This Google sign-in has no YouTube channel. Connect the intended YouTube channel.")
    if len(items) != 1 or response.get("nextPageToken"):
        raise YouTubeChannelMismatch(
            "This sign-in returned multiple channels. Reconnect and select the specific YouTube or Brand channel."
        )
    channel = items[0]
    ident = channel.get("id", "")
    if not isinstance(ident, str) or not _CHANNEL_ID.fullmatch(ident):
        raise YouTubeAuthRequired("YouTube returned an invalid channel identity. Reconnect this profile.")
    if expected_channel_id and ident != expected_channel_id:
        raise YouTubeChannelMismatch(
            "The signed-in YouTube channel does not match this profile. Reconnect the original channel, "
            "or create a new profile for a different channel."
        )
    snippet = channel.get("snippet") or {}
    statistics = channel.get("statistics") or {}
    thumbnail = (snippet.get("thumbnails", {}).get("medium") or
                 snippet.get("thumbnails", {}).get("default") or {}).get("url", "")
    parsed = urlsplit(thumbnail if isinstance(thumbnail, str) else "")
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            not any(host == domain or host.endswith("." + domain)
                    for domain in ("googleusercontent.com", "ggpht.com", "ytimg.com"))):
        thumbnail = ""

    def count(key):
        value = str(statistics.get(key, ""))
        return int(value) if value.isascii() and value.isdigit() and len(value) <= 20 else None

    return {
        "id": ident, "title": str(snippet.get("title", "YouTube channel"))[:150],
        "thumbnail": thumbnail, "url": f"https://www.youtube.com/channel/{ident}",
        "handle": str(snippet.get("customUrl", ""))[:150],
        "subscribers": None if statistics.get("hiddenSubscriberCount") else count("subscriberCount"),
        "videos": count("videoCount"), "views": count("viewCount"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_authenticated_channel(log=print, *, token_file=None, client_secret_file=None,
                                credentials=None, expected_channel_id=None) -> dict:
    """Read saved OAuth channel metadata without starting an interactive login."""
    creds = credentials or get_credentials(
        log=log, token_file=token_file, client_secret_file=client_secret_file,
        interactive=False, scopes=CHANNEL_SCOPES,
    )
    return _channel_from_service(_service(creds), expected_channel_id)


def _thumbnail_error(err: Exception) -> str:
    status = getattr(getattr(err, "resp", None), "status", None)
    if status == 403:
        return ("YouTube refused the custom thumbnail. Custom thumbnails need a verified "
                "channel (youtube.com/verify). Upload it from YouTube Studio instead.")
    return f"Thumbnail upload failed: {str(err)[:300]}"


def upload_video(video_path: str, title: str, description: str = "", tags=None,
                 privacy: str | None = None, publish_at: str = "",
                 thumb_path: str | None = None, on_progress=None, log=print, *,
                 token_file=None, client_secret_file=None, interactive=None,
                 expected_channel_id=None, category_id=None) -> dict:
    """
    Upload a video and (optionally) its thumbnail.

    publish_at: RFC 3339 time to schedule the video; it is uploaded as private
                and YouTube makes it public at that time.
    on_progress: called with an int percentage while the file uploads.
    Returns {"id", "url", "thumbnail_error"}. A failed thumbnail does not fail
    the upload — the video is already live — so it is logged and returned.
    """
    from googleapiclient.http import MediaFileUpload

    if token_file is not None or expected_channel_id is not None:
        if not expected_channel_id:
            raise YouTubeChannelMismatch("Connect and verify this channel profile before publishing.")
        creds = get_credentials(log=log, token_file=token_file,
                                client_secret_file=client_secret_file,
                                interactive=bool(interactive), scopes=CHANNEL_SCOPES)
    else:
        # Preserve the legacy entry point for scripts that do not use profiles.
        creds = get_credentials(log=log) if interactive is None else get_credentials(log=log, interactive=interactive)
    yt = _service(creds)
    if expected_channel_id is not None:
        _channel_from_service(yt, expected_channel_id)
    body = {
        "snippet": {
            "title":       (title or "")[:TITLE_MAX],
            "description": (description or "")[:DESCRIPTION_MAX],
            "tags":        fit_tags(tags),
            "categoryId":  str(category_id or config.VIDEO_CATEGORY_ID),
        },
        "status": {
            "privacyStatus":           "private" if publish_at else (privacy or config.VIDEO_PRIVACY),
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(video_path, mimetype="video/mp4",
                            resumable=True, chunksize=5 * 1024 * 1024)
    req = yt.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status and on_progress:
            on_progress(int(status.progress() * 100))

    video_id = response["id"]
    thumb_error = None
    if thumb_path and os.path.exists(thumb_path):
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg"),
            ).execute()
        except Exception as e:
            thumb_error = _thumbnail_error(e)
            log(thumb_error)

    return {"id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail_error": thumb_error}
