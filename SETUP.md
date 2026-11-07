# 🛠️ Setup Guide

Complete step-by-step instructions to get YAAS (youtube-agentic-ai-studio) running from scratch.

> **Time required:** ~10 minutes

For this fork, start with the [Motion Studio quick start](README.md#-quick-start).
Install into a fresh Python 3.12 virtual environment; do not reuse the Python 3.13
environment that contains unrelated AutoGen, scientific, or background-removal packages.
The full requirements include the original MoviePy/NumPy pins for Classic mode.

---

## Prerequisites

- Python **3.10, 3.11 or 3.12** — [python.org/downloads](https://www.python.org/downloads/)
  (3.13+ is not supported yet: the pinned `numpy<2` has no prebuilt wheels for it, so `pip install` tries to compile numpy and usually fails)
- `ffmpeg` **and `ffprobe`** installed and on your PATH (see [Step 1b](#1b-install-ffmpeg))
- **Node.js 22 or later** for Motion and Hybrid rendering
- Optional provider accounts for Gemini, OpenRouter and Pexels
- A Google account only if you want Gemini or direct YouTube publishing

---

## Step 1 — Clone and install

### 1a. Clone the repository

Copy this repository's HTTPS clone URL from **Code → HTTPS**, then replace
`REPOSITORY_URL` below. The repository must contain the `feat/motion-studio` branch.

```bash
git clone --branch feat/motion-studio REPOSITORY_URL yaas-motion
cd yaas-motion
```

### 1b. Install ffmpeg

FFmpeg decodes measured narration and renders/muxes video. ffprobe verifies media durations and streams.

| OS | Command |
|---|---|
| **Windows** | Download from [ffmpeg.org](https://ffmpeg.org/download.html) → extract → add `bin/` folder to PATH |
| **Mac** | `brew install ffmpeg` |
| **Ubuntu/Debian** | `sudo apt install ffmpeg` |

Verify both `ffmpeg -version` and `ffprobe -version` print version information.

### 1c. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

> ⚠️ **Do not upgrade** `moviepy` or `numpy` — they are pinned intentionally. See [FAQ in README](README.md#-faq).

On Windows, use the exact virtual-environment interpreter shown in the README:
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`. Activating the
environment is optional; using its interpreter avoids PowerShell execution-policy problems.

### 1d. Install the motion renderer

```bash
node --version
npm ci --prefix renderers/hyperframes
node renderers/hyperframes/node_modules/hyperframes/bin/hyperframes.mjs browser ensure
```

This installs the pinned HyperFrames/GSAP/font packages and a browser. The first
installation needs network access; generated motion projects use local assets.
Click **Check setup** in YAAS before a run. A missing package is reported in the
Preparation stage with the command for the Python interpreter that is actually running.

---

## Step 2 — Get your API keys

### 2a. Google Gemini API Key (optional)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key

### 2b. Pexels API Key (optional, for Hybrid footage)

1. Go to [pexels.com/api](https://www.pexels.com/api/)
2. Create a free account and click **Your API Key**
3. Copy the key

### 2c. OpenRouter key (optional, for visual planning)

Create a key at [openrouter.ai/keys](https://openrouter.ai/keys), then save it in
Settings. Keep the planner set to `openrouter/free`, or choose an eligible free
model from the live selector. Free access has quotas and availability can change.
The renderer itself runs locally. Vision models interpret images and return
review text; they do not create a finished video. Frame review is off by default
and uploads selected rendered frames only when you enable it.

---

## Step 3 — Add your keys

All keys are optional. YAAS runs without any, but a Gemini key makes scripts much better.

### Option A — In the app (easiest)

Run `python gui.py`, click **Settings** (top right), paste your keys, and press **Save**.
They're written to a local `.env` file that git ignores.

### Option B — Edit `.env` yourself

```bash
cp .env.example .env
# open .env and fill in the keys you have
```

Real environment variables (`export GEMINI_API_KEY=...`) also work and take priority over `.env`.

> Never put keys in `config.py`. It's tracked by git, so they'd end up in your commits.

---

## Step 4 — Set up YouTube upload (optional)

Skip this step if you just want to generate videos locally without uploading.

### 4a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project → New Project**
3. Name it (e.g. "YAAS") and click **Create**

### 4b. Enable the YouTube Data API

1. In your new project, go to **APIs & Services → Library**
2. Search for **YouTube Data API v3**
3. Click **Enable**

### 4c. Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. If prompted, configure the consent screen first:
   - User Type: **External**
   - Fill in app name, support email, and developer email
   - Scopes: click **Add or remove scopes** → search "youtube" → select `youtube.upload`
   - Test users: add your own Gmail address
4. Back in Credentials → **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "YT Agent Desktop")
5. Click **Download JSON**
6. Rename the downloaded file to `client_secret.json`
7. Put it in the root of the project, next to `client_secret.example.json`

> `client_secret.json` is git-ignored, so your real credentials are never committed. Don't paste them into `client_secret.example.json`, which is tracked.

### 4d. First-time authentication

On your first run, a browser window will open asking you to sign in with Google and grant permission. This only happens once — the token is saved locally as `youtube_token.json` (git-ignored). If you used an older version, you'll sign in once more and can delete the old `youtube_token.pickle`.

> If you see a "Google hasn't verified this app" warning, click **Advanced → Go to (app name) (unsafe)**. This is expected for personal OAuth apps in testing mode.

---

## Step 5 — Customise your channel

In the app, open **Settings → Your channel** and set:

- **Channel name**, which is shown in your videos and thumbnails (switch it off or use custom text with *Show channel name in videos*)
- **What your channel is about**: the more specific, the better the AI's topics and scripts.
  Example: *"Educational science channel for curious adults, style of Veritasium."*

Everything you change in Settings is saved to `settings.json` in the project folder (git-ignored, next to `.env`). It only holds what differs from the defaults in `config.py`, and it wins over them. Delete it to go back to the defaults.

---

## Step 6 — Launch

```bash
python gui.py
```

Opens at **http://localhost:7070**. Type a topic on Home and press **Make video**.

---

## Step 7 — Add background music (optional)

Drop any `.mp3` or `.wav` files into the `music library/` folder.  
The pipeline will automatically shuffle and loop them to match the video duration.

The repo includes a few dark cinematic tracks to get you started. Replace or add to them freely.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'moviepy'` | Run `pip install -r requirements.txt` again |
| `No module named 'edge_tts'` | Run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then start YAAS with that same interpreter |
| Python 3.13 dependency conflicts | Install Python 3.12 side by side, create a fresh venv as in the README, and install only this project's requirements there |
| Missing motion renderer / Node | Install Node 22+, reopen the terminal, run `npm ci --prefix renderers/hyperframes` and the browser ensure command above |
| Windows socket access denied on 7070 | In PowerShell, set `$env:STUDIO_PORT = "8765"` before starting YAAS. If that port is also blocked, choose another available unreserved port |
| Missing narration timings / script changed | Run Narration and Visuals again; the app will not invent timing for an old voiceover |
| No matching storyboard | Select Visuals once after changing mode, dimensions or narration; subsequent draft/final renders can use Video alone |
| Free visual AI unavailable | The storyboard falls back to local animated templates. Add/check a key or try again later; there is no paid fallback |
| `ffmpeg not found` | Install ffmpeg and ensure it's on your PATH |
| `RESOURCE_EXHAUSTED` / 429 | Gemini quota hit — wait until midnight Pacific or switch starting model |
| YouTube sign-in / token error | Delete `youtube_token.json` and upload again to re-authenticate |
| Tiny/ugly video text | Install DejaVu fonts: `sudo apt install fonts-dejavu` (Linux) |
| Port 7070 already in use | Set the `STUDIO_PORT` environment variable, e.g. `STUDIO_PORT=7071 python gui.py` |
| `client_secret.json` error | Download your OAuth JSON from Google Cloud and save it as `client_secret.json` in the project root (step 4c) |
| Captions out of sync | Switch to a different voice in **Settings** (the voices with word timing are listed in `config.py`) |
| A setting won't stick | Values in `settings.json` win over `config.py`. Change it in **Settings**, or remove it from `settings.json` |

---

## Upgrading from an older version

Older versions saved the Settings page into `config.py` itself. If you changed settings there, `git pull` will refuse to overwrite your edited `config.py`. Move your settings across once:

```bash
git stash
```

```bash
git pull
```

```bash
git show "stash@{0}:config.py" > old_config.py
```

```bash
python gui.py --import-settings old_config.py
```

The import only reads plain values from the file, it never runs it. Check **Settings**, then delete `old_config.py` and drop the stash with `git stash drop`.

---

## Optional: Run as a background service

To keep the studio running continuously (e.g. on a home server):

```bash
# Using nohup (Linux/Mac)
nohup python gui.py > studio.log 2>&1 &

# Using screen
screen -S yt-studio
python gui.py
# Detach: Ctrl+A then D
```

---

If you get stuck, include the Preparation result and technical log with your error message and OS.
