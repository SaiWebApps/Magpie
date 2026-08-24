# Magpie

One-click YouTube audio stash. Adds a **Stash** button to YouTube's action bar (next to Share/Save) that downloads the audio as MP3 to your Mac.

Two parts: a Tampermonkey userscript (the button) and a local Python server (calls yt-dlp). No browser extension, no extension store, no permissions nightmare.

## Requirements

- macOS
- Python 3
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — `brew install yt-dlp`
- [ffmpeg](https://ffmpeg.org/) — `brew install ffmpeg`
- [Tampermonkey](https://www.tampermonkey.net/) browser extension

## Install

```bash
# 1. Clone or copy to /Applications/Magpie (or anywhere you like)

# 2. Check dependencies and install the LaunchAgent
make install

# 3. Verify the server is running
make status
```

Then install the userscript by opening this URL with Tampermonkey enabled:

```
http://127.0.0.1:7865/magpie.user.js
```

Tampermonkey shows its install screen. Click **Install**.

**Install from that URL, not by pasting the file.** A pasted copy is a snapshot:
edit `magpie.user.js` afterwards and the browser keeps running the old text with
no sign anything is stale. Installed from the URL, Tampermonkey re-reads the
file from the Magpie server, so changes actually reach the browser.

After editing `magpie.user.js`, bump `@version` at the top — Tampermonkey only
applies an update when the version increases — then either wait for its update
check or force one from the Tampermonkey dashboard.

Reload any YouTube video — the **Stash** button appears in the action bar.

## Download location

Save folders live in `~/.config/magpie.json`. The server writes it with these
defaults the first time it starts:

```json
{
  "audio_dir": "/Users/you/Music",
  "video_dir": "/Users/you/Movies"
}
```

Edit either path to move where files land. The config is read fresh on every
stash, so **no restart is needed** — the next download uses the new folder.

Rules:

- Paths must be absolute. `~` is expanded; relative paths are ignored.
- The folder is created if it doesn't exist.
- A missing or malformed config falls back to the defaults rather than failing
  the download. Check `make logs` if a path seems to be ignored.
- Playlists get a subfolder named after the playlist, inside the folder above.

To point somewhere else entirely:

```json
{
  "audio_dir": "/Users/you/Downloads/Stashed",
  "video_dir": "/Volumes/External/Video"
}
```

### Auto-adding to the Music app

The default `~/Music` is a plain folder — files land there but the Music app
won't import them. To have macOS pick them up automatically, point `audio_dir`
at the Music app's watched folder instead:

```json
{
  "audio_dir": "/Users/you/Music/Music/Media.localized/Automatically Add to Music.localized"
}
```

That folder only exists once you've opened the Music app at least once.

## iBroadcast backup

Magpie can push stashed audio to your [iBroadcast](https://ibroadcast.com)
library, so a track is on your phone and in CarPlay without another step.

Authorize once:

```bash
make ibroadcast-login
```

That prints a short code and a URL. Open the URL, enter the code, done. The
token is saved to `~/.config/magpie-ibroadcast.json` with permissions `600`,
outside this repo. It refreshes itself from then on.

Check it any time:

```bash
make ibroadcast-status
```

What it does and doesn't do:

- **Upload only.** Magpie never deletes or renames anything in your library.
  If you'd rather have two-way sync, use iBroadcast's own MediaSync app —
  but be aware that removing a track there can remove it from your disk.
- **Audio only.** MP4 video stashes are never sent; iBroadcast is a music
  service.
- **Duplicates are skipped.** Magpie compares checksums against your library
  first, so re-stashing a track you already have uploads nothing.
- **A failed upload never fails a stash.** The local file is already saved by
  then. The button says so, and the reason is in `make logs`.
- **`backed up` means accepted, not yet playable.** iBroadcast processes
  uploads on its own schedule — a track took about 100 seconds to appear in a
  measured run. It will not show up in the app the instant the button says so.

The Stash button shows `Backing up…` during the upload, then one of:

| Message | Meaning |
|---|---|
| `Saved to ~/Music · backed up to iBroadcast` | uploaded |
| `Saved to ~/Music · already in iBroadcast` | you already had it |
| `Saved to ~/Music · iBroadcast backup failed` | local file is fine, upload isn't |
| `Saved to ~/Music` | backup is off, or not set up yet |

## When something fails

Failures stay on screen. The button holds its failed state — `⚠️ Failed`,
`⚠️ Backup failed`, `⚠️ Timeout` — until you start another stash, rather than
resetting itself after a few seconds and taking the reason with it.

**Hover the message to read the error.** Both the button and the line below the
video carry the full text as a tooltip; the line below is marked with a dotted
underline and `hover for details` when there's something to read. The same text
also goes to the browser console.

Errors say what to do next rather than echoing a server error page. A rejected
token reads:

```
iBroadcast rejected the credentials (HTTP 403). Re-authorize with:  make ibroadcast-login
```

The format dropdown stays usable while a failure is showing, so you can switch
between MP3 and MP4 before retrying.

A failed backup never means a failed stash — if the message starts with
`Saved to`, your local file is already on disk.

To turn it off, set this in `~/.config/magpie.json`:

```json
{
  "ibroadcast_upload": false
}
```

It's on by default but does nothing until you've run `make ibroadcast-login`,
so it stays quiet if you never set it up.

## Usage

1. Go to any YouTube video
2. Click **Stash** in the action bar
3. Edit the filename if you want, hit OK
4. Progress shows on the button (%, Converting, Finishing)
5. Path appears below the video when done — click to copy

## Makefile targets

```
make install     Install LaunchAgent and start server
make uninstall   Stop server and remove LaunchAgent
make reinstall   Full reinstall
make start       Start the server
make stop        Stop the server
make restart     Restart the server
make status      Check if server is running
make check       Verify dependencies
make logs        Tail the server log

make ibroadcast-login    Authorize iBroadcast backup (one time)
make ibroadcast-status   Check the iBroadcast connection
```

## Troubleshooting

```bash
make logs        # watch the server log
make status      # check if server is responding
make restart     # restart if stuck
```

The server runs on `http://127.0.0.1:7865` and writes logs to `/tmp/magpie-server.log`.
