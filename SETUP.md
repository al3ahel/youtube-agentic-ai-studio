# Setup guide

Use this guide to install YAAS, configure channel profiles, and keep scheduled
generation running. The [README quick start](README.md#-quick-start) contains the
shortest installation path.

Use a **fresh Python 3.12 virtual environment** for this project. An existing
Python 3.13 environment with unrelated AutoGen, scientific or background-removal
packages is not compatible with the pinned Classic renderer dependencies.

## Prerequisites

- **Python 3.10, 3.11 or 3.12** — [Python downloads](https://www.python.org/downloads/).
  Python 3.13+ is not supported by the pinned MoviePy 1.x / NumPy <2 stack.
- **FFmpeg and ffprobe** on `PATH`.
- **Node.js 22 or later** for Motion and Hybrid.
- Optional Gemini, OpenRouter or Pexels keys.
- A Google account and OAuth client only for direct YouTube connection/publishing.

## Step 1 — Clone and install

### 1a. Clone the repository

Copy this fork's HTTPS URL from **Code → HTTPS** and replace `REPOSITORY_URL`.
These enhancements are on the fork's default **main** branch.

```bash
git clone REPOSITORY_URL yaas-motion
cd yaas-motion
```

No feature-branch argument or personal username is embedded in the command.
The original upstream repository has its own feature set.

### 1b. Install FFmpeg

| OS | Installation |
| --- | --- |
| Windows | Download from [ffmpeg.org](https://ffmpeg.org/download.html), extract it, and add its `bin` directory to `PATH` |
| macOS | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |

Open a new terminal if you changed `PATH`, then verify:

```bash
ffmpeg -version
ffprobe -version
```

FFmpeg decodes recordings, mixes the optional music bed and handles encoded media.
ffprobe measures duration and verifies streams.

### 1c. Create an isolated Python environment

**Windows PowerShell**, from the repository directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

**macOS / Linux:**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Use that same interpreter to start the app. Activation is optional; explicit
interpreter paths avoid PowerShell execution-policy issues and installing into the
wrong environment. The shorter `python` commands later in this guide assume that
`python` resolves to this environment.

Keep the MoviePy and NumPy pins in `requirements.txt`. Installing individual missing
packages into a shared environment can leave incompatible packages behind even
when pip reports “Successfully installed”. Recreate the project environment with
a supported interpreter instead of changing unrelated applications' dependencies.

The normal requirements also install **`croniter==6.2.4`** and **`tzdata>=2025.2`**
for cron scheduling and IANA time zones, including on Windows.

### 1d. Install the motion renderer

```bash
node --version
npm ci --prefix renderers/hyperframes
node renderers/hyperframes/node_modules/hyperframes/bin/hyperframes.mjs browser ensure
```

This installs the pinned renderer, animation/font packages and a browser. The
first installation needs network access; generated motion projects use local
assets. A missing dependency is reported during Preparation with a command for
the Python interpreter actually running the app.

## Step 2 — Start the studio

**Windows PowerShell:**

```powershell
$env:STUDIO_PORT = "8765"
.\.venv\Scripts\python.exe gui.py
```

Open **http://localhost:8765**. Port 8765 is an example alternate port for systems
where Windows forbids binding the default 7070.

**macOS / Linux:**

```bash
.venv/bin/python gui.py
```

The default address is **http://localhost:7070**. Open **Check local setup** on Home
before generating a video. On any platform, `STUDIO_PORT` can select another
available, unreserved port.

The app binds to localhost. The process running `gui.py` also starts its scheduler;
the browser is just its interface.

## Step 3 — Choose local or free-tier AI

New scripts need an available text model. In **Settings → Writing models**, choose
an installed Ollama model or add a hosted key below. **Find local models** lists
installed models from your loopback Ollama server; it does not download weights
or start inference. **Local only** applies to writing, sound direction and text
visual planning and never falls through to a hosted model. Remote frame review
is unavailable in that mode. Local model choices and routing belong to the channel;
provider keys remain shared by the studio.

Use a model your machine can run and whose license permits your intended use.
The local address normally is `http://127.0.0.1:11434`. The app rejects remote
hosts, cloud aliases and automatic model pulls. Ollama must already be installed
and running. You can instead use a free-tier hosted account; check its tier and
quota, because a key alone does not guarantee that billing is disabled.

| Provider | Where to obtain a key | Purpose |
| --- | --- | --- |
| Gemini | [Google AI Studio](https://aistudio.google.com/apikey) | Research and script generation |
| Groq | [Groq console](https://console.groq.com/keys) | Free-tier text fallback |
| Pexels | [Pexels API](https://www.pexels.com/api/) | Stock pictures and Hybrid video footage |
| OpenRouter | [OpenRouter keys](https://openrouter.ai/keys) | Optional free visual planning, text fallback and frame review |

Open **Settings**, paste keys and save. They are written to a git-ignored local
`.env` file. Alternatively, copy `.env.example` to `.env` and fill in the keys.
Real process environment variables take priority over `.env`.

Provider API keys are **shared by all profiles in this local studio**. YouTube
tokens are separate per profile. Never put credentials into tracked `config.py`
or `client_secret.example.json`.

OpenRouter planning defaults to `openrouter/free`; explicit choices are checked
against the live free-model catalogue. Free providers can have quotas and changing
availability. A vision model returns review text, not a finished generated video.
Frame review is off until explicitly enabled, and sends selected rendered frames
when enabled in a hosted route. Text visual planning can use the configured local,
Gemini or Groq route without a separate OpenRouter key. Local motion templates can
still run on a saved script/recording without any inference.

The old keyless Pollinations generation fallback is retired.
Gemini defaults to `gemini-3.8-flash`, with `gemini-3.1-flash-lite` and the 2.5
Flash models in its fallback chain. Google restricts 2.5 access to previously
active projects, so a new key should use a supported 3.x choice. See Google's
[models](https://ai.google.dev/gemini-api/docs/models) and
[free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing).
The Groq default is `openai/gpt-oss-120b`. Saved retired Groq Llama defaults migrate at runtime.
These identifiers do not bypass account quotas or turn a paid account into a free one.

## Step 4 — Connect YouTube (optional)

You can create and schedule local videos without connecting YouTube. Direct
publishing needs a verified profile connection.

### 4a. Create Google OAuth credentials

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com).
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen for your app. For a personal app in testing,
   add the Google account you will use as a test user.
4. Include the YouTube upload and read-only scopes:
   `https://www.googleapis.com/auth/youtube.upload` and
   `https://www.googleapis.com/auth/youtube.readonly`.
5. Create an OAuth client with application type **Desktop app**.
6. Download its JSON file and save it as `client_secret.json` in the repository
   root, beside `client_secret.example.json`.

The real JSON file is git-ignored. A profile may use another local OAuth client
JSON path through **Profile settings → More channel settings**. Leaving that
field empty preserves the existing setting.

### 4b. Connect each profile explicitly

1. Select the intended profile in the sidebar or mobile channel selector.
2. On Home, click **Connect YouTube**.
3. Complete Google sign-in in the browser opened by YAAS. Choose the correct
   YouTube channel/Brand Account.
4. Return to Home and verify the **fetched channel title and avatar**.

The connection runs asynchronously, so Home shows its progress and any failure.
It requests read-only access to verify the authenticated channel as well as upload
access. An older upload-only token may need explicit reconnection for those scopes.

A token's presence alone does not mark a profile verified. **Refresh** uses the
saved token to fetch identity without opening a new sign-in window. Opening the
app, changing profiles or running scheduled generation does not start authentication.

Once verified, a profile stays bound to that YouTube channel, even after
disconnecting. Reconnecting it to another channel is blocked; add another profile
for that channel. Publishing checks the authenticated identity again.

The default profile can retain the legacy `youtube_token.json`. Other profiles
store isolated tokens under `.yaas/channels/<profile-id>/`. Do not delete or copy
another profile's token as a way to change its destination.

## Step 5 — Create channel profiles and tune quality

Use the sidebar **+**, or the **+** beside the mobile selector, to create a profile.
Choose **General**, **News**, or **ASMR / calm**, then describe its topics, tone
and audience.

Open **Profile settings** to adjust the narrator, speed, visual mode, format,
duration, music and publishing defaults. **Preview voice** uses the currently
selected voice, speed and pitch without saving them; save after choosing a voice.
The default pitch is natural (`+0Hz`). New profiles use Automatic sound design;
the agent decides whether a suitable licensed bed, ambience or sparse effect fits.

News starts with natural narration and Hybrid visuals. It uses retrieved background
sources and trends; it does not verify live breaking news. ASMR/calm starts with
slower narration, calmer writing and gentler photo movement. It does not synthesize
binaural recordings or authentic ASMR triggers.

The advanced **Settings** page edits the selected profile's channel defaults.
Provider keys remain shared. The initial default profile preserves compatible
`settings.json` behavior; additional profiles and fetched identity live in
`.yaas/channels.json`.

For more detail, see [Channels and schedules](docs/CHANNELS_AND_SCHEDULES.md).

## Step 6 — Make or reopen a video

For a new video, select a channel, enter a topic, choose Motion/Hybrid/Classic,
and press **Make video**. Use **Draft preview first** with Motion or Hybrid.

Motion creates graphics without automatically attaching pictures or footage.
Hybrid uses the original current-project photo pipeline and can add stock video;
Classic retains the original photo renderer. Review's storyboard is the same
composition used by Hyperframes during generation. New photo associations are
bound to the channel, script and file contents, so an old filename cannot be
assumed to belong to a new video.

For an existing photo project, open **My videos → Open → Animate these photos**.
This reuses its pictures and recording, imports saved SRT timing when available,
or labels a duration-based estimate. If the separate recording is missing but
an old MP4 exists, YAAS can recover its soundtrack, including music already mixed
into it. It cannot separate that music from the speech.

You do not have to regenerate unchanged old narration just to try Motion.
Regenerate it if the spoken script changes or if you want fresh word events
from the speech service, when available. See [the full Motion workflow](docs/MOTION_STUDIO.md).

## Step 7 — Let the agent direct sound

Choose **Automatic** under sound design and save the profile. The agent reads the
script and measured narration, chooses appropriate search terms or an owned
recording, and can fetch declared CC0/Public Domain/CC BY audio from Wikimedia
Commons without a key. You do not need to import tracks for this mode. An available
free/local text model supplies AI choices; otherwise conservative local choices
are explicitly identified. If no suitable licensed recording can be obtained,
the draft retains narration and Review explains why.

On an existing Motion/Hybrid draft, click **Regenerate sound design** in Review.
It rebuilds only the sound and MP4, retaining voice, storyboard and publishing
prose. Review shows actual source credits, cue placement and measured loudness.
Normal/news finishing targets −16 LUFS and ASMR −20 LUFS with bounded gain and
true-peak headroom; targets are not forced when doing so would violate a limit.

**Manual** supports exact imported tracks and the existing local music folder.
Old enabled imported selections stay Manual on upgrade. **Off** removes added
music/effects. Pixabay audio can still be downloaded/imported as an optional
Manual source; its documented public API does not provide audio search.

Video-only rendering reuses its committed soundtrack. An interrupted new sound
pass cannot overwrite it. No synthesized noise/test-tone fallback is added to
normal videos. Complete applied credits are preserved, including after archive
reopening, and refreshed before publication.

## Step 8 — Schedule recurring videos

On the selected channel's Home page, open **Scheduled videos → Add schedule**.
Choose a name, recurring brief, five-field cron expression, IANA time zone, format,
visual mode and draft/final output.

For example, `0 9 * * 1-5` with `Asia/Dubai` means weekdays at 09:00 Dubai time.
Each schedule's automatic occurrences must be at least **15 minutes apart**. Motion and Hybrid
default to drafts; Classic generates a local final MP4. Nothing is published
automatically.

The persistent queue runs one generation job at a time. A scheduled run uses
separate working files, archives its result and restores the open editor.
Automatic slots missed or queued for more than 15 minutes are skipped, and
interrupted jobs are not retried automatically. Use **Run now** for a fresh attempt.

Keep the normal app process running, or stop it and start:

```bash
python gui.py --scheduler-only
```

On Windows, use `.\.venv\Scripts\python.exe gui.py --scheduler-only`.
Run one studio process per project data directory. A GitHub fork and its CI do
not host your running app or personal scheduled jobs. Full lifecycle, storage and
hosting details are in [Channels and schedules](docs/CHANNELS_AND_SCHEDULES.md).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `No module named 'moviepy'` or `No module named 'edge_tts'` | Install `requirements.txt` using the same venv interpreter that starts the app; on Windows use `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| Python 3.13 or unrelated dependency conflicts | Install Python 3.12, create a fresh project venv, install its requirements, then run `python -m pip check` there |
| Missing motion renderer / Node | Install Node 22+, reopen the terminal, run `npm ci --prefix renderers/hyperframes` and the browser ensure command |
| Windows socket access denied on 7070 | Set `$env:STUDIO_PORT = "8765"` before starting YAAS; choose another available unreserved port if that is blocked too |
| Old recording has no timing manifest | Open the archive and choose **Animate these photos**; Review labels imported caption timing or estimated timing |
| Spoken script no longer matches recording | Regenerate Narration and Visuals; old audio cannot speak newly edited text |
| Old MP4 still contains loud music | Recovered audio includes the old mix; use clean original narration or record it again |
| Old photos are available but their section map is missing | Inspect the image reuse note and check scene/photo matches in Review |
| No matching storyboard | Select Visuals once after changing mode, dimensions or narration; later draft/final renders can use Video alone |
| Free visual AI unavailable | Planning falls back to local templates; check the provider/key or try later |
| Speech preview fails with a certificate error | Check system certificates and network/proxy trust, then retry; keep secure certificate verification enabled |
| `ffmpeg` or `ffprobe` not found | Install both and reopen the terminal after updating `PATH` |
| Provider quota / 429 | Wait for that provider's quota to recover or choose another supported provider; free-tier availability is external |
| YouTube sign-in / token error | Select the correct profile and **Reconnect YouTube**; verify the test user, scopes and bound channel |
| Wrong-channel connection blocked | Create a new profile for the other channel; existing projects keep their verified binding |
| Scheduler unavailable or not running | Keep one app/scheduler-only process alive; check for another process owning its lock |
| Job skipped or interrupted | Read the card's last-run details; use **Run now** when you want a new attempt |
| Time zone rejected | Use an IANA name such as `Asia/Dubai`; install the current project requirements including `tzdata` |
| Tiny/ugly Classic text | Install DejaVu fonts on Linux, for example `sudo apt install fonts-dejavu` |
| A profile setting appears unchanged | Check the selected channel, save its settings, and regenerate the relevant stage; existing recordings are not changed by a preference edit |

## Upgrading from an earlier checkout

Back up local data before changing machines or replacing an environment. Keep
`settings.json`, `.env`, `.yaas/`, the OAuth client/token files,
`output/library/` and custom music together. Stop the app while backing up the
schedule database. See [storage details](docs/CHANNELS_AND_SCHEDULES.md#local-storage-and-backups).

If an earlier checkout used the feature branch, preserve any local code edits
before switching to this fork's main branch:

```bash
git fetch origin
git switch main
git pull --ff-only
```

Reinstall `requirements.txt` in the project environment and run `pip check`, then
run `npm ci --prefix renderers/hyperframes` again. The supported Python range
remains 3.10–3.12.

For an existing Windows checkout using `.venv-yaas312` and port 8765, stop YAAS
with **Ctrl+C**, then run the following in the same PowerShell window. Keep the
existing environment and local project data; an untracked `.venv-yaas312/`
directory is not a code conflict.

```powershell
cd C:\Users\Admin\youtube-agentic-ai-studio
git switch main
git pull --ff-only origin main
.\.venv-yaas312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-yaas312\Scripts\python.exe -m pip check
npm ci --prefix renderers/hyperframes
ffmpeg -version
ffprobe -version
$env:STUDIO_PORT = "8765"
.\.venv-yaas312\Scripts\python.exe gui.py
```

Open `http://127.0.0.1:8765` and refresh the page after updating. In the selected
channel's settings, choose **Automatic** sound for agent-selected music/ambience
and effects. A previously enabled, explicitly selected track remains **Manual**
on upgrade. Setting changes affect the next relevant generation step; an old
render is not silently rebuilt just by changing a preference.

Very old YAAS versions wrote settings into tracked `config.py`. Export that old
file to `old_config.py`, then use the migration command:

```bash
python gui.py --import-settings old_config.py
```

The importer reads plain values without executing the file. Check the imported
default-profile Settings before removing your backup. An existing default token
may need **Refresh** or **Connect YouTube** to fetch verified identity.

## Optional: keep a configured scheduler in the background

For an already configured studio on an always-on Linux/macOS machine:

```bash
nohup .venv/bin/python gui.py --scheduler-only > studio.log 2>&1 &
```

Use your platform's service manager if you need process supervision after reboot.
Do not start a second GUI process over the same project while scheduler-only mode
is running. A host needs the installed renderer/browser, FFmpeg, local data and
network access as well as the source checkout.

If you get stuck, include the Preparation result, last schedule error or technical
log, the Python version and your OS. Do not include API keys or OAuth tokens.
