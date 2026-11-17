# Magpie

One-click YouTube audio stash. Adds a **Stash** button to YouTube's action bar (next to Share/Save) that downloads the audio as MP3 to your Mac.

Two parts: a Tampermonkey userscript (the button) and a local Python server (calls yt-dlp). No browser extension, no extension store, no permissions nightmare.

## Requirements

- macOS (the server itself also runs on Windows and Linux)
- Python 3.9 or later
- [Tampermonkey](https://www.tampermonkey.net/) browser extension

Magpie brings its own pinned copies of [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[ffmpeg/ffprobe](https://ffmpeg.org/) and [deno](https://deno.com) (the
JavaScript runtime yt-dlp needs for YouTube). They download on first use, or
with `make check`, each verified against its published SHA-256, into Magpie's
own folder. Nothing on your PATH is used, so a Homebrew or system install
never changes which version runs.

## Install

```bash
# 1. Clone or copy to /Applications/Magpie (or anywhere you like)

# 2. Install the bundled tools and the LaunchAgent
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

## Where Magpie keeps its files

| | macOS | Windows | Linux |
|---|---|---|---|
| config, job database, iBroadcast login | `~/Library/Application Support/Magpie/` | `%LOCALAPPDATA%\Magpie\` | `~/.config/magpie/` (config), `~/.local/share/magpie/` (data) |
| log (`magpie.log`) | `~/Library/Logs/Magpie/` | `%LOCALAPPDATA%\Magpie\Logs\` | `~/.local/state/magpie/log/` |
| bundled tools | `~/Library/Application Support/Magpie/bin/` | `%LOCALAPPDATA%\Magpie\bin\` | `~/.local/share/magpie/bin/` |

On Linux, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` move these as
usual. On macOS, files from older versions in `~/.config` (`magpie.json`,
`magpie-jobs.sqlite3`, `magpie-ibroadcast*.json`) move here on the first start;
a file already in the new place is never overwritten.

## Download location

Save folders live in `config.json` in the folder above. The server writes it
with these defaults the first time it starts — your Music and Movies folders on
macOS, Music and Videos elsewhere (Windows follows folder redirection, such as
OneDrive; Linux follows `~/.config/user-dirs.dirs`):

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
token is saved as `ibroadcast-token.json` in Magpie's data folder (see the
table above): permissions `600` on macOS and Linux, encrypted for your
Windows account on Windows. It refreshes itself from then on.

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
- **Duplicates are skipped.** A file counts as already there only when your
  iBroadcast library lists it (same checksum, or same title and exact size),
  so re-stashing a track you already have uploads nothing. Magpie's own
  record of what it sent is not enough on its own: iBroadcast sometimes
  accepts an upload and never lists it, and that file gets sent again.
- **A failed upload never fails a stash.** The local file is already saved by
  then. The button says so, and the reason is one hover away.
- **✅ means iBroadcast lists the track.** iBroadcast accepts an upload before
  it processes it — a track took about 100 seconds to appear in a measured
  run. Magpie waits until your library shows it, checking every 2 seconds,
  and gives up after 15 minutes with a warning instead of a ✅.

The Stash button shows `Backing up…` during the upload and `Waiting for
iBroadcast…` until the library lists it, then one of:

| Button | Message | Meaning |
|---|---|---|
| ✅ Stashed | `Saved to ~/Music · in iBroadcast` | uploaded, and your library lists it |
| ✅ Stashed | `Saved to ~/Music · already in iBroadcast` | you already had it |
| ✅ Stashed | `Saved to ~/Music · iBroadcast backup is off` | you turned backup off |
| ⚠️ Not in iBroadcast | `Saved to ~/Music · not in iBroadcast yet` | accepted, but not listed after 15 minutes |
| ⚠️ Backup failed | `Saved to ~/Music · iBroadcast backup failed` | local file is fine, upload isn't |
| ⚠️ Not backed up | `Saved to ~/Music · not backed up (no iBroadcast login)` | run `make ibroadcast-login` |

## Track names and tags

Name a stash `{Album}_{Track}` and Magpie writes real tags into the file before
it uploads:

```
AC3_AgainstAllOdds.mp3      album = AC        title = AC3_AgainstAllOdds
AC_Mirage_Escape.mp3        album = AC        title = AC_Mirage_Escape
WetLeg_NotFun.mp3           album = WetLeg    title = WetLeg_NotFun
Espresso.mp3                (no album)        title = Espresso
```

The leading token up to the **first** underscore becomes the album. The title
is the **whole filename** — the album is a grouping key, not something to strip
out. Removing it would turn `AC2_FlightOverVenice`, `AC3_ManInWolfHood` and
`AC4_Pirates` into titles that no longer say which game they came from, and
since all three share the album `AC`, that information would be gone for good.

A name with no underscore gets a title only — inventing an album from a single
word is worse than leaving it unset.

**Track numbers** are left alone. A stash carries no track number, and Magpie
never rewrites other files to make room for a new one. Sort by title to see an
album in filename order; an album view that orders by track number lists new
songs outside that order.

This matters because yt-dlp leaves downloads with no tags at all. Untagged
files show up in iBroadcast as "Unknown Album" with the filename as the title,
so a stash saved as `Foo.mp3.m4a` would appear as `Music/Foo.mp3`.

**Album aliases** fold related prefixes into one album. Every Assassin's Creed
numbering lands under `AC`:

```json
{
  "album_aliases": {
    "AC": "AC", "AC2": "AC", "AC3": "AC", "AC4": "AC", "ACB": "AC"
  }
}
```

Add your own — `"Bond2": "Bond"` and so on. Set `"tag_from_filename": false` to
turn tagging off entirely.

Tagging is a stream copy, so the audio is never re-encoded. Embedded cover art
is preserved where the container allows it. A tagging failure never fails a
stash: an untagged file is still a good file.

## When something fails

Failures stay on screen. The button holds its failed state — `⚠️ Failed`,
`⚠️ Backup failed`, `⚠️ Not in iBroadcast`, `⚠️ Not backed up`, `⚠️ Timeout` —
until you start another stash, rather than
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

To turn it off, set this in `config.json`:

```json
{
  "ibroadcast_upload": false
}
```

It's on by default. Until you've run `make ibroadcast-login`, each stash says
`⚠️ Not backed up` so a missing backup never passes for a finished one.

## Usage

1. Go to any YouTube video
2. Click **Stash** in the action bar
3. Edit the filename if you want, hit OK
4. Progress shows on the button (%, Converting, Finishing)
5. Path appears below the video when done — click to copy

### Leaving the page

A stash keeps going after you leave. Each one is a job the server saves
before it starts (`jobs.sqlite3` in Magpie's data folder), so closing the tab, moving
to another video or even restarting the server doesn't stop it. Jobs left
unfinished when the server stops pick up again when it starts.

Come back to the video and the button shows where the job is — `⌛ 64%`,
`✅ Stashed` with the saved path, or the failure with its reason on hover.
Finished jobs are shown this way for 24 hours. The server runs up to three
jobs at once; any more wait their turn as `⌛ Queued…`.

## Makefile targets

```
make install     Install LaunchAgent and start server
make uninstall   Stop server and remove LaunchAgent
make reinstall   Full reinstall
make start       Start the server
make stop        Stop the server
make restart     Restart the server
make status      Check if server is running
make check       Install any missing bundled tool and check each one runs
make logs        Tail the server log
make test        Run the server tests (no network; never touches the live server)

make ibroadcast-login    Authorize iBroadcast backup (one time)
make ibroadcast-status   Check the iBroadcast connection
```

## Troubleshooting

```bash
make logs        # watch the server log
make status      # check if server is responding
make restart     # restart if stuck
```

The server runs on `http://127.0.0.1:7865` and writes its log to `magpie.log`
in the log folder above (`~/Library/Logs/Magpie/` on macOS).
