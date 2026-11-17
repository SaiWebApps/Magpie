#!/usr/bin/env python3
"""Magpie HTTP server — the local endpoint the Tampermonkey userscript calls.

Endpoints:
  GET  /health            — liveness check, returns {"ok": true}
  GET  /token             — returns the auth token (only from 127.0.0.1)
  POST /jobs              — queues a download, returns the job at once (202)
  GET  /jobs?url=<page>   — newest video and playlist job for a YouTube page
  GET  /jobs/<id>         — one job: state and latest message
  GET  /jobs/<id>/events  — streams a job's progress as NDJSON until done
  POST /stash             — queues a download and streams it (for userscripts
                            older than 2.5.0)
  POST /check-dupe        — is this name already saved or uploaded?

Downloads run as jobs (magpie_jobs.py), so closing the page that started one
does not stop it, and jobs unfinished at shutdown resume on the next start.

yt-dlp, ffmpeg, ffprobe and deno are Magpie's own pinned copies
(magpie_tools.py), run directly with no shell. yt-dlp reports progress and
the files it wrote as JSON lines, read with json rather than parsed as text.

Files live where magpie_paths.py says for this OS: config.json (save folders
and options, re-read on every stash), the job database, the iBroadcast
token, and the log, magpie.log.

Auth: a Bearer token generated on startup, printed to stdout and written to
the server-token file in the state folder.
"""

import datetime
import json
import os
import secrets
import shlex
import socket
import socketserver
import subprocess
import sys
import traceback
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock

import magpie_ibroadcast
import magpie_jobs
import magpie_paths
import magpie_tags
import magpie_tools

# The job store and its workers. Created in main(), so importing this module
# (as the tests do) opens no database and starts no threads.
JOBS = None
RUNNER = None

VERSION = "2.0-http"

HOME = Path.home()
DIRS = magpie_paths.DIRS

# Served at /magpie.user.js so Tampermonkey can install and update the script
# from this checkout.
SCRIPT_DIR = Path(__file__).resolve().parent
USERSCRIPT_PATH = SCRIPT_DIR / "magpie.user.js"

# Destination folders. These are the fallbacks; load_config() lets the JSON
# file at CONFIG_PATH override either one without touching this file.
CONFIG_PATH = DIRS.config_file
DEFAULT_AUDIO_DIR = DIRS.music()
DEFAULT_VIDEO_DIR = DIRS.videos()

# Back up stashed audio to iBroadcast. On by default; with no login stored,
# each stash says it was not backed up and carries on.
DEFAULT_IBROADCAST_UPLOAD = True

# Empty means yt-dlp chooses YouTube's player clients itself, which tracks
# YouTube's changes better than any fixed list here. Set youtube_player_client
# in the config to force one (see `yt-dlp --extractor-args help`).
DEFAULT_YT_PLAYER_CLIENT = ""

# Write album/title tags from the {Album}_{Track} filename before uploading.
# yt-dlp leaves downloads untagged, and iBroadcast then files them under
# "Unknown Album" with the filename as the title.
DEFAULT_TAG_FROM_FILENAME = True
DEFAULT_ALBUM_ALIASES = magpie_tags.DEFAULT_ALBUM_ALIASES

LOG_PATH = DIRS.log_file
TOKEN_PATH = DIRS.server_token
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7865

ALLOWED_ORIGINS = {"https://www.youtube.com", "https://youtube.com"}

AUTH_TOKEN = secrets.token_urlsafe(32)

# yt-dlp is told to print each event as one JSON object per line: download
# progress, postprocessor progress, and the final path of every file it wrote.
DOWNLOAD_TEMPLATE = 'download:{"kind": "download", "progress": %(progress)j}'
POSTPROCESS_TEMPLATE = 'postprocess:{"kind": "postprocess", "progress": %(progress)j}'
FILE_TEMPLATE = 'after_move:{"kind": "file", "path": %(filepath)j}'

# yt-dlp postprocessor names -> the phase the button shows while it runs.
POSTPROCESS_PHASES = {
    "ExtractAudio": "extracting",
    "Merger": "merging",
    "FixupM4a": "fixing",
    "FixupM3u8": "fixing",
    "FixupTimestamp": "fixing",
    "FixupDuration": "fixing",
    "FixupDuplicateMoov": "fixing",
    "FixupStretched": "fixing",
}

# Longest stash name and playlist-item filename, in characters. Leaves room
# for the folders above them inside Windows' 260-character path limit.
MAX_NAME = 150


# ---------------------------------------------------------------------------
# Logging and configuration
# ---------------------------------------------------------------------------

def dlog(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception as exc:
        sys.stderr.write(f"dlog failed: {exc}\n")


def display_path(p: Path) -> str:
    """Render a path with the home folder collapsed to ~ for display."""
    try:
        return os.path.join("~", str(p.relative_to(HOME)))
    except ValueError:
        return str(p)


def _coerce_dir(value, default: Path) -> Path:
    """Turn one config value into an absolute Path, falling back to default.

    A relative path is rejected rather than resolved, because the server's
    working directory is whatever started it — not something the user can
    reason about when editing the config."""
    if not isinstance(value, str) or not value.strip():
        return default
    p = Path(value.strip()).expanduser()
    if not p.is_absolute():
        dlog(f"config: {value!r} is not an absolute path; using {default}")
        return default
    return p


def _coerce_bool(value, default: bool) -> bool:
    """Accept a real JSON boolean only; anything else keeps the default."""
    return value if isinstance(value, bool) else default


def _coerce_str(value, default: str) -> str:
    """Accept a non-empty string only; anything else keeps the default."""
    return value.strip() if isinstance(value, str) and value.strip() else default


def _coerce_aliases(value, default: dict) -> dict:
    """Accept a flat string->string map only; anything else keeps the default."""
    if not isinstance(value, dict):
        return default
    clean = {str(k): str(v) for k, v in value.items()
             if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()}
    return clean or default


def _config_defaults():
    return {
        "audio_dir": DEFAULT_AUDIO_DIR,
        "video_dir": DEFAULT_VIDEO_DIR,
        "ibroadcast_upload": DEFAULT_IBROADCAST_UPLOAD,
        "youtube_player_client": DEFAULT_YT_PLAYER_CLIENT,
        "tag_from_filename": DEFAULT_TAG_FROM_FILENAME,
        "album_aliases": dict(DEFAULT_ALBUM_ALIASES),
    }


def player_client_args(client):
    """yt-dlp flags selecting the YouTube player client, or none to let
    yt-dlp choose."""
    if not client or client.lower() in ("default", "none", "auto"):
        return []
    return ["--extractor-args", f"youtube:player_client={client}"]


def load_config():
    """Return the settings dict: audio_dir and video_dir as Paths, plus the
    other options.

    Called on every stash so an edit takes effect on the next download without
    a server restart. A missing, unreadable, or malformed file falls back to
    the defaults — a bad config should never block a download."""
    defaults = _config_defaults()
    if not CONFIG_PATH.exists():
        return defaults
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        dlog(f"config: cannot read {CONFIG_PATH} ({exc}); using defaults")
        return defaults
    if not isinstance(data, dict):
        dlog(f"config: {CONFIG_PATH} is not a JSON object; using defaults")
        return defaults
    return {
        "audio_dir": _coerce_dir(data.get("audio_dir"), DEFAULT_AUDIO_DIR),
        "video_dir": _coerce_dir(data.get("video_dir"), DEFAULT_VIDEO_DIR),
        "ibroadcast_upload": _coerce_bool(data.get("ibroadcast_upload"),
                                          DEFAULT_IBROADCAST_UPLOAD),
        "youtube_player_client": _coerce_str(data.get("youtube_player_client"),
                                             DEFAULT_YT_PLAYER_CLIENT),
        "tag_from_filename": _coerce_bool(data.get("tag_from_filename"),
                                          DEFAULT_TAG_FROM_FILENAME),
        "album_aliases": _coerce_aliases(data.get("album_aliases"),
                                         dict(DEFAULT_ALBUM_ALIASES)),
    }


def ensure_config_file():
    """Write CONFIG_PATH with the current defaults if it does not exist yet, so
    there is always a file to edit. Never overwrites an existing config."""
    if CONFIG_PATH.exists():
        return
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "audio_dir": str(DEFAULT_AUDIO_DIR),
                "video_dir": str(DEFAULT_VIDEO_DIR),
                "ibroadcast_upload": DEFAULT_IBROADCAST_UPLOAD,
                "youtube_player_client": DEFAULT_YT_PLAYER_CLIENT,
                "tag_from_filename": DEFAULT_TAG_FROM_FILENAME,
                "album_aliases": dict(DEFAULT_ALBUM_ALIASES),
            }, f, indent=2)
            f.write("\n")
        dlog(f"wrote default config to {CONFIG_PATH}")
    except OSError as exc:
        dlog(f"could not write default config to {CONFIG_PATH}: {exc}")


# ---------------------------------------------------------------------------
# Tagging and backup
# ---------------------------------------------------------------------------

BACKUP_SUFFIXES = {
    "confirmed": " · in iBroadcast",
    "skipped": " · already in iBroadcast",
    "unconfirmed": " · not in iBroadcast yet",
    "failed": " · iBroadcast backup failed",
    "no_login": " · not backed up (no iBroadcast login)",
    "off": " · iBroadcast backup is off",
}

# Backup outcomes where the local file is fine but iBroadcast does not have
# it. The button shows these as a warning, with the detail on hover, instead
# of a plain ✅ — a check mark means the track is in the library.
BACKUP_WARNINGS = ("unconfirmed", "failed", "no_login")


def tag_downloads(paths, config, ffmpeg, is_audio):
    """Tag finished audio from its {Album}_{Track} filename.

    Runs before the iBroadcast upload so the library gets real album and title
    values instead of falling back to the filename. A tagging failure is never
    fatal — an untagged file is still a good file."""
    if not is_audio or not config.get("tag_from_filename") or not paths:
        return
    aliases = config.get("album_aliases")
    magpie_tags.tag_all(paths, ffmpeg, aliases, dlog)


def run_backup(paths, enabled, is_audio, send_progress):
    """Back up finished files to iBroadcast and wait until it lists them.

    Returns (message_suffix, status, detail). Video is never sent, so it adds
    nothing; audio always says where it stands in iBroadcast, including when
    backup is switched off."""
    if not is_audio:
        return "", "video", ""
    if not enabled:
        return BACKUP_SUFFIXES["off"], "off", ""
    send_progress({"type": "progress", "phase": "backing_up"})

    def on_progress(phase, done, total):
        send_progress({"type": "progress", "phase": phase,
                       "current": done, "total": total})

    status, detail = magpie_ibroadcast.backup(paths, dlog, on_progress)
    dlog(f"ibroadcast backup: {status} — {detail}")
    return BACKUP_SUFFIXES.get(status, ""), status, detail


def attach_backup_error(done, status, detail):
    """Carry a backup warning into the done payload.

    The userscript shows it as a warning with the reason on hover, so the text
    has to survive the trip rather than only reaching the server log."""
    if status in BACKUP_WARNINGS:
        done["backup_status"] = status
        done["backup_error"] = detail or "iBroadcast does not have this file"
    return done


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------

MEDIA_SUFFIXES = (".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus",
                  ".mp4", ".mkv", ".webm", ".mov")

# Names Windows reserves for devices, with or without an extension.
WINDOWS_RESERVED = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                    | {f"COM{d}" for d in "123456789¹²³"}
                    | {f"LPT{d}" for d in "123456789¹²³"})


def sanitize(name: str) -> str:
    """Clean a user-supplied filename into one that is valid on macOS,
    Windows and Linux alike, so a library can move between them.

    A trailing media extension is dropped. yt-dlp appends the real extension
    itself, so "Song.mp3" typed at the prompt would otherwise be saved as
    "Song.mp3.m4a" — a name that lies about the contents."""
    bad = '/\\:*?"<>|'
    cleaned = "".join(c for c in name if c not in bad and ord(c) >= 32).strip()
    # Repeat: "Song.mp3.m4a" must not become "Song.mp3" and then "Song.mp3.mp3".
    # Each pass removes at least one character, so this always terminates.
    stripping = True
    while stripping:
        stripping = False
        lower = cleaned.lower()
        for suffix in MEDIA_SUFFIXES:
            if lower.endswith(suffix) and len(cleaned) > len(suffix):
                cleaned = cleaned[:-len(suffix)].strip()
                stripping = True
                break
    # Windows drops trailing dots and spaces, and refuses device names.
    cleaned = cleaned[:MAX_NAME].rstrip(". ")
    if cleaned.split(".")[0].rstrip(" ").upper() in WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned or "audio"


QUARANTINE_ATTR = "com.apple.quarantine"


def strip_quarantine(path):
    """On macOS, clear the Gatekeeper quarantine flag from a finished download.

    macOS can attribute a download to the browser that triggered it, and
    Music.app refuses to open media still carrying the flag. Only that one
    attribute is removed, so Finder tags survive. Other systems have no such
    flag."""
    if sys.platform != "darwin":
        return
    try:
        # Returns non-zero when the attribute is not set; that is not an error.
        subprocess.run(
            ["/usr/bin/xattr", "-d", QUARANTINE_ATTR, str(path)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception as exc:
        dlog(f"could not clear quarantine on {path}: {exc}")


# ---------------------------------------------------------------------------
# The bundled tools
# ---------------------------------------------------------------------------

_TOOLS_LOCK = Lock()


def get_tools(send_progress):
    """{"yt-dlp": argv prefix, "ffmpeg": path, "ffprobe": path, "deno": path},
    installing any missing tool first. Raises magpie_tools.ToolError."""
    with _TOOLS_LOCK:
        if magpie_tools.missing():
            def on_progress(tool, n, total):
                send_progress({"type": "progress", "phase": "installing",
                               "current": n, "total": total})
            magpie_tools.install(log=dlog, progress=on_progress)
    paths = magpie_tools.tool_paths()
    return {"yt-dlp": [str(paths["yt-dlp"])], "ffmpeg": str(paths["ffmpeg"]),
            "ffprobe": str(paths["ffprobe"]), "deno": str(paths["deno"])}


def popen_kwargs():
    """Text pipes in UTF-8 on every OS, and no console window on Windows.

    yt-dlp otherwise writes in the console code page on Windows, which mangles
    any character outside it in titles and error messages."""
    kwargs = {"encoding": "utf-8", "errors": "replace",
              "env": dict(os.environ, PYTHONIOENCODING="utf-8")}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def yt_dlp_base(tools, config):
    """The arguments every yt-dlp call shares: no config file or self-update
    can change behaviour, output is UTF-8, and the bundled deno and ffmpeg are
    the ones used."""
    return [*tools["yt-dlp"], "--ignore-config", "--no-update", "--encoding", "utf-8",
            "--no-js-runtimes", "--js-runtimes", f"deno:{tools['deno']}",
            "--ffmpeg-location", tools["ffmpeg"],
            *player_client_args(config["youtube_player_client"])]


def download_argv(tools, config, url, output_template, is_audio, extra=()):
    format_args = ["-x", "--audio-format", "mp3"] if is_audio else ["--merge-output-format", "mp4"]
    return [*yt_dlp_base(tools, config),
            "--newline", "--progress",
            "--progress-template", DOWNLOAD_TEMPLATE,
            "--progress-template", POSTPROCESS_TEMPLATE,
            "--print", FILE_TEMPLATE,
            *format_args, "--no-playlist", *extra,
            "-o", output_template, "--", url]


def run_download(argv, on_progress):
    """Run one yt-dlp download, passing its progress to on_progress.

    Returns (exit code, [paths yt-dlp wrote], [last non-JSON output lines])."""
    dlog(f"running: {argv}")
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=1, **popen_kwargs())
    files, tail = [], []
    last_percent = -1
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict) or "kind" not in event:
            tail.append(line)
            del tail[:-80]
            dlog(f"yt-dlp> {line}")
            continue
        progress = event.get("progress") or {}
        if event["kind"] == "file" and event.get("path"):
            files.append(Path(event["path"]))
        elif event["kind"] == "download" and progress.get("status") == "downloading":
            percent = progress.get("_percent")
            if isinstance(percent, (int, float)) and int(percent) != last_percent:
                last_percent = int(percent)
                on_progress({"type": "progress", "phase": "downloading", "percent": percent})
        elif event["kind"] == "postprocess" and progress.get("status") == "started":
            phase = POSTPROCESS_PHASES.get(progress.get("postprocessor"))
            if phase:
                on_progress({"type": "progress", "phase": phase})
    proc.wait()
    dlog(f"yt-dlp exited with code {proc.returncode}; wrote {[str(f) for f in files]}")
    return proc.returncode, files, tail


def describe_failure(code, tail, argv):
    text = "\n".join(tail[-12:]) if tail else f"exit code {code}"
    return text + f"\n\n[ran: {shlex.join(argv)}]"


# ---------------------------------------------------------------------------
# NDJSON helpers
# ---------------------------------------------------------------------------

def ndjson_line(data_dict):
    """Format a dict as a newline-delimited JSON line."""
    return json.dumps(data_dict) + "\n"


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class MagpieHandler(BaseHTTPRequestHandler):
    """Handles /health, /token, /jobs, /stash and /check-dupe."""

    server_version = f"Magpie/{VERSION}"
    # HTTP/1.1 so /stash and /jobs/<id>/events can stream with chunked transfer
    # encoding. All non-stream responses send Content-Length, so keep-alive
    # stays well-framed.
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        dlog(f"HTTP {format % args}")

    def _get_client_ip(self):
        """Return the client IP address."""
        return self.client_address[0]

    def _is_loopback(self):
        """Check if the request came from localhost."""
        ip = self._get_client_ip()
        return ip in ("127.0.0.1", "::1")

    def _set_cors_headers(self):
        """Set CORS headers if the Origin is allowed."""
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

    def _check_auth(self):
        """Validate the Authorization: Bearer <token> header. Returns True if valid."""
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {AUTH_TOKEN}":
            return True
        return False

    def _send_json(self, code, obj):
        """Send a JSON response."""
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._set_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._set_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        parts = path.strip("/").split("/")
        if path == "/health":
            self._handle_health()
        elif path == "/token":
            self._handle_token()
        elif path == "/magpie.user.js":
            self._handle_userscript()
        elif parts[0] == "jobs":
            self._handle_jobs_get(parts)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_userscript(self):
        """Serve magpie.user.js so Tampermonkey installs and updates from disk.

        No auth: Tampermonkey's updater sends no headers we control, and the
        listener is loopback-only anyway."""
        try:
            body = USERSCRIPT_PATH.read_bytes()
        except OSError as exc:
            dlog(f"cannot read userscript at {USERSCRIPT_PATH}: {exc}")
            self._send_json(404, {"error": f"userscript not readable: {exc}"})
            return
        self.send_response(200)
        # Tampermonkey requires this content type to treat the response as a
        # script rather than offering it as a download.
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/jobs":
            self._handle_create_job()
        elif self.path == "/stash":
            self._handle_stash()
        elif self.path == "/check-dupe":
            self._handle_check_dupe()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_health(self):
        self._send_json(200, {"ok": True})

    def _handle_token(self):
        """Return the auth token, but only to loopback clients."""
        if not self._is_loopback():
            self._send_json(403, {"error": "token endpoint is only available from localhost"})
            return
        self._send_json(200, {"token": AUTH_TOKEN})

    def _handle_check_dupe(self):
        """Check whether a name already exists locally or in iBroadcast."""
        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return

        try:
            raw = self.rfile.read(content_length)
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return

        name = sanitize(body.get("name", ""))
        format = body.get("format", "mp3")
        if not name:
            self._send_json(400, {"error": "missing name"})
            return

        is_audio = format == "mp3"
        config = load_config()
        dest_dir = config["audio_dir"] if is_audio else config["video_dir"]

        found_in = []

        local_path = dest_dir / f"{name}.{format}"
        if local_path.exists():
            found_in.append("local")

        if is_audio:
            ibc_names = magpie_ibroadcast.load_uploaded_names()
            if name in ibc_names:
                found_in.append("ibroadcast")

        self._send_json(200, {"duplicate": len(found_in) > 0, "found_in": found_in, "name": name})

    def _read_stash_request(self):
        """Authenticate and validate a stash body, then queue it.

        Returns the new job's id, or None once an error response is sent."""
        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return None

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return None

        try:
            raw = self.rfile.read(content_length)
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            dlog(f"bad request body: {exc}")
            self._send_json(400, {"error": "invalid JSON"})
            return None

        url = body.get("url")
        name = sanitize(body.get("name", "audio"))
        format = body.get("format", "mp3")
        if format not in ("mp3", "mp4"):
            self._send_json(400, {"error": "format must be mp3 or mp4"})
            return None
        playlist = bool(body.get("playlist", False))

        if not url:
            self._send_json(400, {"error": "missing url"})
            return None

        dlog(f"stash request: url={url} name={name} format={format} playlist={playlist}")
        job_id = JOBS.create(url, name, format, playlist)
        RUNNER.submit(job_id)
        dlog(f"job {job_id}: queued")
        return job_id

    def _handle_create_job(self):
        """Queue a download and answer straight away. The page then follows
        the job with GET /jobs/<id>/events, and can drop off at any time."""
        job_id = self._read_stash_request()
        if job_id:
            self._send_json(202, JOBS.get(job_id))

    def _handle_stash(self):
        """Queue a download, then stream it to the end in the same response.
        Serves userscripts older than 2.5.0, which stash this way."""
        job_id = self._read_stash_request()
        if job_id:
            self._stream_job(job_id)

    def _handle_jobs_get(self, parts):
        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return
        if len(parts) == 1:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            url = (query.get("url") or [""])[0]
            if not url:
                self._send_json(400, {"error": "missing url"})
                return
            self._send_json(200, JOBS.newest_for(url))
            return
        job = JOBS.get(parts[1])
        if job is None:
            self._send_json(404, {"error": "no such job"})
        elif len(parts) == 2:
            self._send_json(200, job)
        elif len(parts) == 3 and parts[2] == "events":
            self._stream_job(parts[1])
        else:
            self._send_json(404, {"error": "not found"})

    def _stream_job(self, job_id):
        """Stream a job's messages as NDJSON until its done message.

        A watcher leaving early only ends the stream; the job carries on."""
        # Chunked transfer encoding lets the browser hand each NDJSON line to
        # GM_xmlhttpRequest's onprogress as it arrives instead of buffering the
        # whole body until the socket closes, and the terminating 0-length
        # chunk gives an explicit end-of-stream.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self._set_cors_headers()
        self.end_headers()
        try:
            for msg in JOBS.follow(job_id):
                payload = ndjson_line(msg).encode("utf-8")
                # Chunked framing: <hex length> CRLF <payload> CRLF.
                self.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            dlog(f"job {job_id}: watcher left ({exc}); the job carries on")


class MagpieHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse DNS lookup http.server does
    while binding, which can stall for about 35 seconds on macOS 15 and later.
    On Windows the port is bound exclusively: SO_REUSEADDR there lets another
    process bind the same port and steal requests."""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


# ---------------------------------------------------------------------------
# The stash itself — run by a job worker, never by a request handler
# ---------------------------------------------------------------------------

def run_stash(job, send_progress):
    """Download, tag and back up one stash, reporting through send_progress.

    job is a magpie_jobs record (url, name, format, playlist). Every path ends
    by sending exactly one {"type": "done"} message."""
    url, name, format = job["url"], job["name"], job["format"]
    is_audio = format == "mp3"

    send_progress({"type": "progress", "phase": "starting", "percent": 0})
    try:
        tools = get_tools(send_progress)
    except (magpie_tools.ToolError, OSError) as exc:
        dlog(f"tools unavailable: {exc}\n{traceback.format_exc()}")
        send_progress({"type": "done", "ok": False,
                       "error": f"Magpie could not set up yt-dlp and ffmpeg: {exc}"})
        return

    # Config is read per job, so editing CONFIG_PATH takes effect on the next
    # stash with no restart.
    config = load_config()
    dest_dir = config["audio_dir"] if is_audio else config["video_dir"]
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        key = "audio_dir" if is_audio else "video_dir"
        send_progress({
            "type": "done",
            "ok": False,
            "error": f"Cannot use destination folder {dest_dir}: {exc}. "
                     f"Check \"{key}\" in {CONFIG_PATH}.",
        })
        return
    if job["playlist"]:
        download_playlist_parallel(url, name, is_audio, dest_dir, tools, send_progress, config)
        return

    argv = download_argv(tools, config, url, str(dest_dir / f"{name}.%(ext)s"), is_audio)
    try:
        code, produced, tail = run_download(argv, send_progress)
    except OSError as exc:
        dlog(f"could not start yt-dlp: {exc}\n{traceback.format_exc()}")
        send_progress({"type": "done", "ok": False, "error": f"failed to start yt-dlp: {exc}"})
        return

    if code != 0:
        send_progress({"type": "done", "ok": False, "error": describe_failure(code, tail, argv)})
        return
    for p in produced:
        strip_quarantine(p)
    tag_downloads(produced, config, tools["ffmpeg"], is_audio)
    suffix, backup_status, backup_detail = run_backup(
        produced, config["ibroadcast_upload"], is_audio, send_progress)
    path = str(produced[-1]) if produced else str(dest_dir / f"{name}.{format}")
    send_progress(attach_backup_error(
        {"type": "done", "ok": True, "path": path,
         "message": f"Saved to {display_path(dest_dir)}{suffix}"},
        backup_status, backup_detail))


def playlist_entries(tools, config, url):
    """The video URLs in a playlist, in order. Raises RuntimeError with
    yt-dlp's message when the playlist cannot be read."""
    argv = [*yt_dlp_base(tools, config), "--flat-playlist", "--dump-json", "--", url]
    dlog(f"extracting playlist: {argv}")
    proc = subprocess.run(argv, capture_output=True, timeout=120, **popen_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:] if proc.stderr else f"exit code {proc.returncode}")
    items = []
    for line in proc.stdout.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        video_url = entry.get("webpage_url") or entry.get("url") or ""
        if urllib.parse.urlsplit(video_url).scheme not in ("http", "https"):
            video_url = "https://www.youtube.com/watch?" + urllib.parse.urlencode(
                {"v": entry.get("id") or video_url})
        items.append(video_url)
    return items


def download_playlist_parallel(url, name, is_audio, dest_dir, tools, send_progress, config):
    from concurrent.futures import ThreadPoolExecutor

    send_progress({"type": "progress", "phase": "loading"})
    try:
        items = playlist_entries(tools, config, url)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        send_progress({"type": "done", "ok": False, "error": f"failed to extract playlist:\n{exc}"})
        return

    total = len(items)
    if total == 0:
        send_progress({"type": "done", "ok": False, "error": "no items found in playlist"})
        return

    dlog(f"playlist: {total} items, downloading 3 at a time")
    send_progress({"type": "progress", "phase": "playlist_item", "current": 0, "total": total})

    subfolder = dest_dir / name
    subfolder.mkdir(parents=True, exist_ok=True)
    template = str(subfolder / "%(title)s.%(ext)s")

    lock = Lock()
    completed = [0]
    failed = [0]
    produced = []

    def dl(video_url):
        argv = download_argv(tools, config, video_url, template, is_audio,
                             extra=("--trim-filenames", str(MAX_NAME)))
        try:
            code, files, tail = run_download(argv, lambda msg: None)
            ok = code == 0
            if not ok:
                dlog(f"playlist item failed: {video_url}: {describe_failure(code, tail, argv)}")
        except OSError as e:
            dlog(f"playlist item error: {video_url}: {e}")
            ok, files = False, []
        with lock:
            produced.extend(files)
            if ok:
                completed[0] += 1
            else:
                failed[0] += 1
            send_progress({"type": "progress", "phase": "playlist_item",
                           "current": completed[0] + failed[0], "total": total})

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(dl, items))

    for p in produced:
        strip_quarantine(p)
    tag_downloads(produced, config, tools["ffmpeg"], is_audio)

    dest_label = display_path(subfolder)
    if failed[0] == 0:
        msg = f"Stashed {total} tracks to {dest_label}"
    else:
        msg = f"Stashed {completed[0]}/{total} to {dest_label} ({failed[0]} failed)"
    suffix, backup_status, backup_detail = run_backup(
        produced, config["ibroadcast_upload"], is_audio, send_progress)
    send_progress(attach_backup_error(
        {"type": "done", "ok": failed[0] == 0 or completed[0] > 0,
         "path": str(subfolder), "message": msg + suffix},
        backup_status, backup_detail))


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def write_token():
    """Write the auth token to TOKEN_PATH for external readers."""
    try:
        TOKEN_PATH.write_text(AUTH_TOKEN, encoding="utf-8")
        if sys.platform != "win32":
            TOKEN_PATH.chmod(0o600)
        dlog(f"token written to {TOKEN_PATH}")
    except OSError as exc:
        dlog(f"failed to write token file: {exc}")
        sys.stderr.write(f"warning: could not write {TOKEN_PATH}: {exc}\n")


def main():
    DIRS.ensure()
    dlog(f"=== magpie_server.py {VERSION} starting ===")
    dlog(f"PATH={os.environ.get('PATH', '<unset>')}")
    for old, new in magpie_paths.migrate(DIRS, dlog):
        print(f"Moved {old} to {new}", flush=True)

    ensure_config_file()
    config = load_config()
    if not config["ibroadcast_upload"]:
        backup_state = "off (ibroadcast_upload is false)"
    elif magpie_ibroadcast.load_token() is None:
        backup_state = "on, but not authorized yet — run: make ibroadcast-login"
    else:
        backup_state = "on"
    dlog(f"config: {CONFIG_PATH}")
    dlog(f"audio -> {config['audio_dir']}")
    dlog(f"video -> {config['video_dir']}")
    dlog(f"tools: {DIRS.bin} (missing: {magpie_tools.missing() or 'none'})")
    dlog(f"ibroadcast backup: {backup_state}")
    print(f"Config: {CONFIG_PATH}", flush=True)
    print(f"  audio_dir: {config['audio_dir']}", flush=True)
    print(f"  video_dir: {config['video_dir']}", flush=True)
    print(f"  ibroadcast backup: {backup_state}", flush=True)
    print(f"Log: {LOG_PATH}", flush=True)

    write_token()

    # Print token to stdout so whatever started the server can capture it
    print(f"MAGPIE_TOKEN={AUTH_TOKEN}", flush=True)
    dlog(f"auth token: {AUTH_TOKEN}")

    global JOBS, RUNNER
    JOBS = magpie_jobs.JobStore()
    RUNNER = magpie_jobs.JobRunner(JOBS, run_stash, dlog)
    dlog(f"jobs: {magpie_jobs.DB_PATH}")
    resumed = JOBS.requeue_unfinished()
    for job_id in resumed:
        RUNNER.submit(job_id)
    if resumed:
        dlog(f"resuming {len(resumed)} job(s) left unfinished by the last run: {resumed}")

    server = MagpieHTTPServer((LISTEN_HOST, LISTEN_PORT), MagpieHandler)
    dlog(f"listening on http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"Magpie server {VERSION} listening on http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        dlog("shutting down (KeyboardInterrupt)")
        print("\nshutting down", flush=True)
    finally:
        server.server_close()
        dlog("server closed")


if __name__ == "__main__":
    main()
