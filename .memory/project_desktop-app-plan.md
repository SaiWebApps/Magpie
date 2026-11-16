---
name: project_desktop-app-plan
description: Decisions for the planned cross-platform Magpie desktop app (2026-11-15) — Tauri + bundled Python engine, app replaces the LaunchAgent, no Safari, magpie:// fallback included
metadata:
  type: project
---

On 2026-11-15 the user approved the direction for a Magpie desktop app (macOS, Windows, Linux). Still in planning; no code written yet.

- **App replaces the startup helper.** The app runs the server on 127.0.0.1:7865 itself, lives in the menu bar / tray, and launches at login. `make install` and the LaunchAgent go away.
- **Stack: Tauri** for the shell and UI, with the existing Python engine (magpie_server, magpie_tags, magpie_ibroadcast, musicdiff matching) bundled as a PyInstaller sidecar. yt-dlp and ffmpeg bundled per OS.
- **Safari is out of scope.** Chrome, Edge, Brave, Firefox only.
- **`magpie://stash?url=…` fallback is in:** when the app isn't running, the userscript opens that link to launch the app and queue the job.

Planned features: setup wizard (detect browsers/profiles, open Tampermonkey store page, open the userscript install URL, confirm via a /hello ping), persistent job queue (POST /jobs, GET /jobs/<id>/events, resume after restart, button reattaches on page load), library with user-added folders and a starred default download folder (separate audio/video), and a side-by-side local vs iBroadcast compare built on musicdiff's buckets.

**Why:** the user wants Magpie easy to set up for others and downloads that survive leaving the YouTube page.

**How to apply:** don't re-ask these four decisions. The feature-parity rule in CLAUDE.md applies to the download queue UI vs the Stash button. Related: [[reference_ibroadcast-library-api]], [[project_skill-location]].

Real-download test: `MAGPIE_TEST_REAL=1 python3 -m unittest tests.test_jobs.RealDownloadTest -v`. It works from the Claude shell because the user approved adding `www.youtube.com` and `googlevideo.com` to `~/.claude-work/apple/dangerous_allowed_domains.csv` on 2026-11-15 and chose to keep them for later phases (2 and 7 rerun it). Don't ask to add them again; a new blocked host still needs its own approval.

Decisions on 2026-11-15 after the first phase 1 browser try:
- **✅ Stashed only once the file is confirmed in the iBroadcast library**, not when iBroadcast accepts the upload. Wait shows "⌛ Waiting for iBroadcast…"; no confirmation in 15 min → ⚠️ Not in iBroadcast. Skip-as-duplicate must rely on iBroadcast's library, not Magpie's local sent record.
- **Backup off in config → ✅ with "iBroadcast backup is off"; on but login missing/expired → ⚠️ Not backed up.**
- **Album renumbering is off** (the user sorts by title, so track numbers don't matter to them). New stashes carry no track number and no other file is rewritten. Turned off after measuring 6 s per stash into a 127-file album and ~30 stale copies in iBroadcast each time.
- **Goal: the desktop app replaces the music skills** (/musicdiff, /musicsync, /musicdedupe). Sync should eventually run automatically in the app.
