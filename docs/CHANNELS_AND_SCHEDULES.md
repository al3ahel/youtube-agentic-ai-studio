# Channel profiles and scheduled videos

YAAS can keep several channels in one local studio. Each profile has its own
voice, visual style, music, branding, YouTube connection and recurring generation
schedules. Provider API keys remain shared by the studio.

Schedules **generate drafts or local final MP4s**. They do not publish to YouTube.
Review the result and use the existing publishing controls separately.

## Create and select a channel

On desktop, use **Your channels** in the sidebar. On a small screen, use the
**Channel** selector at the top of Home. The **+** button creates a profile.

1. Give the profile a useful local name, such as **Morning Brief** or **Quiet Hours**.
2. Choose General, News, or ASMR/calm as a starting style.
3. Add the topics, tone and audience this channel is for.
4. Create the profile, then open **Profile settings** to adjust its defaults.

The initial **default** profile preserves existing settings and old projects.
You can continue using it while adding other profiles.

A profile's local name, its branding text, and its actual YouTube channel title
are separate concepts. Renaming a profile does not rename a channel on YouTube.
Only the authenticated YouTube response supplies the fetched channel identity
shown on Home.

| Preset | Voice and pace | Visuals and target duration | Intended starting point |
| --- | --- | --- | --- |
| General | Existing studio defaults; natural pitch by default | Existing studio defaults | Your own topic, style and audience |
| News | Andrew, natural pace, unchanged pitch | Hybrid, about 5 minutes | Clear explanations with sources, dates and uncertainty |
| ASMR / calm | Brian, 15% slower, unchanged pitch | Classic, about 10 minutes, gentler photo movement | Calm spoken sessions with less stimulating scripts and visuals |

News and ASMR/calm start with **music off**. Target duration guides the long-video
script; Shorts keep their shorter format. Presets can be adjusted after creation.

The News preset uses retrieved background material and trend ideas. It does not
independently verify live breaking news. Check the dates, claims and original
sources before publication. The ASMR/calm preset changes writing and voice pacing;
it does not create binaural recordings or authentic ASMR triggers.

## Choose a voice and soundtrack

In **Profile settings**, select the narrator, voice speed and, under **More channel
settings**, pitch. Click **Preview voice** to audition a short sample using those
unsaved values. It makes no speech request merely because you change a selector.
Save the profile when the sample sounds right.

Voice previews depend on the external speech service. A failed preview shows an
inline error and can be retried. Saving a voice preference changes future
recordings; it does not replace narration already saved in an open project.

Music is optional. Turn on **Background music**, choose a level and optionally
provide this profile's local music folder. The starting level is 8% (`0.08`).
The FFmpeg mixer fades and loops a stable track, lowers it beneath speech, and
limits peaks. Motion uses a lossless WAV soundtrack before final video encoding.
Existing mixed recordings keep their existing music until you deliberately rebuild
the audio. See [voice and music behavior](MOTION_STUDIO.md#voice-music-and-visual-pacing).

## Connect the correct YouTube channel

You can generate and schedule local videos without connecting YouTube.

1. Complete the [Google OAuth setup](../SETUP.md#step-4--connect-youtube-optional).
2. Select the intended profile on Home.
3. Click **Connect YouTube** and finish Google sign-in in the browser opened by YAAS.
4. Choose the intended YouTube channel, including the correct Brand Account when
   your Google account controls more than one channel.
5. Return to Home and check the fetched channel title and avatar.

Connection is explicit and asynchronous. Normal profile switching, scheduled
generation and opening the app do not start a Google sign-in flow. **Refresh**
updates the fetched identity using the saved token; it does not open authentication.

Each profile has a separate token. Additional profiles never fall back to the
default profile's legacy token. The app checks the authenticated channel again
before uploading, and blocks an unexpected identity.

After the first verified connection, a profile remains bound to that YouTube
channel. **Disconnect YouTube** removes the local credential but preserves the
binding and project history. Reconnecting the profile to a different channel is
blocked; create another profile for another channel.

**Delete profile** is available only for unused, unconnected profiles. The default
profile, a verified/bound profile, or a profile referenced by a schedule, current
project or archive cannot be deleted.

## Project identity and switching

Home, channel statistics and **My videos** follow the selected profile. Generation
requests record the profile they started with. Review displays the **Project
channel**, and render/upload requests use that saved profile rather than whichever
channel happened to be selected most recently.

Opening a library project activates its saved profile. Legacy archives without a
profile belong to the default profile. Channel switching is blocked during active
generation, upload or visual editing. Save or undo unsaved Settings changes before
switching.

If another browser tab changes the selected channel while Settings contains
unsaved edits, those edits remain attached to the original profile. The Settings
heading names that profile; saving writes to it, then loads the newly selected
channel. Home's quick voice and fact-check controls also save to the channel
shown when you change them.

This keeps a News video's recording, metadata and upload destination together when
you also work on a calm-video channel in the same studio.

## Add a recurring generation schedule

Open **Home → Scheduled videos → Add schedule** for the selected channel.

| Field | What to choose |
| --- | --- |
| Schedule name | A label such as “Morning briefing” or “Sunday wind-down” |
| Topic or recurring brief | What each new video should research or describe |
| Repeat / cron | A preset, or a five-field cron expression |
| Time zone | An IANA name such as `Asia/Dubai`, `Europe/London` or `America/New_York` |
| Format | Long video or Short |
| Visual style | Motion, Hybrid or Classic |
| Output | Draft for review, or final MP4 kept locally |
| Use current trends | Whether research should also consider timely topic ideas |
| Enabled | Whether future cron slots should generate videos |

Motion and Hybrid default to **Draft for review**. Classic makes a local final
MP4. Neither output choice publishes automatically. A schedule uses its channel's
saved voice, music and other defaults; queued jobs retain the settings captured
when they entered the queue.

Cron has **five fields**: minute, hour, day of month, month, weekday. Seconds are
not supported, and each schedule's automatic occurrences must be at least
**15 minutes apart**.

| Cron | Meaning in the selected time zone |
| --- | --- |
| `0 9 * * *` | Every day at 09:00 |
| `0 9 * * 1-5` | Weekdays at 09:00 |
| `0 9 * * 1` | Mondays at 09:00 |
| `0 */6 * * *` | At 00:00, 06:00, 12:00 and 18:00 |
| `*/15 * * * *` | Every 15 minutes, the minimum permitted interval |

Choose a frequency your machine and providers can handle. A long render can take
longer than its recurrence interval.

Time zones are stored with the schedule rather than inferred from the machine
each time. Nonexistent local times at a spring daylight-saving change are skipped;
a repeated autumn wall-clock minute runs once. Review the **Next** time shown on
the schedule card after changing cron or time zone.

**Run now** requests one immediate queued job, including for a paused schedule.
It does not enable that schedule's future automatic slots. Duplicate queued or
running requests for the same schedule are rejected. Editing or pausing a schedule
invalidates its older queued job; a running job must finish before that schedule
can be edited or deleted.

## What happens when a job runs

Schedules and their queue persist in a local SQLite database. One scheduler process
owns the queue, and generation runs **one job at a time**, alongside the manual
studio workflow.

| State or event | Behavior |
| --- | --- |
| Due and available | Queue one job for that occurrence; repeated scheduler checks do not duplicate it |
| Studio busy | Wait in the queue until the current generation/upload/edit completes |
| Automatic occurrence missed by more than 15 minutes | Mark it skipped and retain the next future occurrence |
| Automatic queued job waits more than 15 minutes | Mark it skipped; use **Run now** if a fresh video is still wanted |
| Generation completes | Archive the result with its profile and link it from the schedule |
| Generation fails or is stopped | Show the error; there is no automatic retry |
| App stops during generation | On restart, mark that job failed/interrupted; do not repeat it automatically |

A queued manual **Run now** request is distinct from an expiring automatic slot.
The queue persists across restarts, but a job already marked running is treated
as interrupted if its process disappears.

Each scheduled run uses separate working files. After archiving its result, YAAS
restores the previously open editor project. Scheduled drafts are saved with a
**Draft** badge and cannot be uploaded until a final video has been rendered.
Use the card's **Open video** link or find the result under that channel's
**My videos**.

The card shows queued/running/completed/failed/skipped status, last result or error,
and the next automatic time. It is not a guarantee that a sleeping computer will
wake up or that an external provider will be available.

## Keep the scheduler running

The ordinary app starts the scheduler:

```bash
python gui.py
```

Closing the browser tab alone does not stop that Python process. Closing its
terminal, shutting down or sleeping the machine prevents jobs from running.

For an already configured studio without the browser UI, use:

```bash
python gui.py --scheduler-only
```

Use the same virtual environment, repository directory, configuration, local media
and credentials. On Windows, the explicit environment command is:

```powershell
.\.venv\Scripts\python.exe gui.py --scheduler-only
```

Run **one studio process per project data directory**. Stop the normal app before
starting scheduler-only mode, or stop scheduler-only mode before reopening the app.
The scheduler uses an operating-system file lock to reject a second owner.
`STUDIO_SCHEDULER=0` disables automatic scheduling for the normal app; leave it
unset when using scheduler-only mode.

For unattended use, keep the process on an always-on computer or a machine you
host, with Python 3.10–3.12, Node 22+, FFmpeg/ffprobe, renderer/browser dependencies
and the needed network access. Configure profiles and schedules before switching
to scheduler-only operation. The GUI stays bound to localhost.

**A GitHub fork is source storage, not a running cloud studio.** Its Actions tests
verify code; they do not keep your personal schedules running. Pushing to main
does not install the app, copy credentials or provision a host.

The scheduling dependencies are `croniter==6.2.4` and `tzdata>=2025.2`, installed
by `requirements.txt`. The timezone package supplies IANA data on systems such as
Windows that may lack a system zone database.

## Local storage and backups

| Location | Contents |
| --- | --- |
| `settings.json` | Compatible settings for the original default profile and shared studio options |
| `.env` | Shared provider API keys |
| `.yaas/channels.json` | Profile defaults, local labels and fetched public channel metadata |
| `youtube_token.json` | Legacy/default-profile OAuth token |
| `.yaas/channels/<profile-id>/youtube_token.json` | Additional profile's isolated OAuth token |
| `.yaas/schedules.sqlite3` | Schedules, queued jobs and recent execution results |
| `.yaas/jobs/` | Isolated scheduled-job work; failed jobs may remain for inspection |
| `output/library/` | Archived videos/drafts, profile IDs, pictures, scripts, recordings and timing |

These local data files are git-ignored. Stop the studio before copying a backup so
the SQLite database and profiles stay consistent. Preserve `.yaas/`,
`settings.json`, `.env`, the OAuth client/token files, `output/library/` and any
custom music folders together when moving machines. Treat credentials as private
local data rather than repository content.

Older root-level settings and the default token continue to work. A saved token
alone does not prove the profile's channel identity: use **Refresh** or
**Connect YouTube** to fetch and verify it before publishing through the profile
workflow. Old projects can reuse their pictures and recordings through
[Animate these photos](MOTION_STUDIO.md#animate-an-existing-photo-project).

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| “Scheduler is not running” | Keep one normal app or scheduler-only process running; check whether another process owns its lock |
| Schedule marked skipped | Inspect the last-run details; the app was offline/busy too long or the minimum interval would have been exceeded |
| Schedule marked interrupted/failed after restart | Inspect its error and local files, then request **Run now** if you want a new attempt |
| Wrong YouTube channel selected at sign-in | Keep the existing binding; create a new profile for the other channel |
| Old token is present but Home says unconnected | Use **Refresh** or **Connect YouTube** to verify identity and scopes |
| Voice sample fails | Check the speech-service connection, certificates/proxy and selected voice; the UI reports the failure without saving changed settings |
| Scheduled draft has no publishing action | Open it, review the storyboard and render the final video first |
| Cron or time zone is rejected | Use five fields, an IANA name and occurrences at least 15 minutes apart; reinstall normal requirements if timezone data is missing |
