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

By default, audio files are saved to:

```
~/Music/Music/Media.localized/Music/
```

To change this, edit **line 35** of `magpie_server.py`:

```python
DEST_DIR = HOME / "Music" / "Music" / "Media.localized" / "Music"
```

Change it to any directory you want, e.g.:

```python
DEST_DIR = HOME / "Downloads" / "Music"
```

Then restart the server:

```bash
make restart
```

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
