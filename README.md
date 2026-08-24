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

Then install the userscript:

1. Open Tampermonkey in your browser
2. Create a new script
3. Paste the contents of `magpie.user.js`
4. Save

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
```

## Troubleshooting

```bash
make logs        # watch the server log
make status      # check if server is responding
make restart     # restart if stuck
```

The server runs on `http://127.0.0.1:7865` and writes logs to `/tmp/magpie-server.log`.
