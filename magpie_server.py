#!/usr/bin/env python3
"""Magpie HTTP server — replaces the native messaging host with an SSE-based
HTTP endpoint that a Tampermonkey userscript can call directly.

Endpoints:
  GET  /health  — liveness check, returns {"ok": true}
  GET  /token   — returns the auth token (only from 127.0.0.1)
  POST /stash   — starts a download, streams progress via SSE

Auth: Bearer token generated on startup, printed to stdout and written to
/tmp/magpie-token.

Save locations live in ~/.config/magpie.json ("audio_dir" / "video_dir"),
written with defaults on first run and re-read on every stash.

Debug log lives at /tmp/magpie.log — tail it to see what's happening.
"""

import datetime
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import zipfile
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock, Thread

VERSION = "2.0-http"

HOME = Path.home()

# Destination folders. These are the fallbacks; load_config() lets a JSON file
# at CONFIG_PATH override either one without touching this file.
CONFIG_PATH = Path(os.environ.get("MAGPIE_CONFIG") or HOME / ".config" / "magpie.json")
DEFAULT_AUDIO_DIR = HOME / "Music"
DEFAULT_VIDEO_DIR = HOME / "Movies"

YT_DLP_CANDIDATES = [
    "/opt/homebrew/bin/yt-dlp",
    "/usr/local/bin/yt-dlp",
    "/usr/bin/yt-dlp",
]

STATIC_BIN_DIR = HOME / "Library" / "Application Support" / "Magpie" / "bin"

FFMPEG_CANDIDATES = [
    str(STATIC_BIN_DIR / "ffmpeg"),
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]

LOG_PATH = "/tmp/magpie.log"
TOKEN_PATH = "/tmp/magpie-token"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7865

EVERMEET_FFMPEG = "https://evermeet.cx/ffmpeg/getrelease/zip"
EVERMEET_FFPROBE = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

ALLOWED_ORIGINS = {"https://www.youtube.com", "https://youtube.com"}

PROGRESS_RE = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%")
PLAYLIST_ITEM_RE = re.compile(r"^\[download\]\s+Downloading item (\d+) of (\d+)")

AUTH_TOKEN = secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Utility functions reused from magpie_stash.py
# ---------------------------------------------------------------------------

def dlog(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception as exc:
        sys.stderr.write(f"dlog failed: {exc}\n")


def display_path(p: Path) -> str:
    """Render a path with the home folder collapsed to ~ for display."""
    try:
        return "~/" + str(p.relative_to(HOME))
    except ValueError:
        return str(p)


def _coerce_dir(value, default: Path) -> Path:
    """Turn one config value into an absolute Path, falling back to default.

    A relative path is rejected rather than resolved, because the server's
    working directory is whatever launchd handed it — not something the user
    can reason about when editing the config."""
    if not isinstance(value, str) or not value.strip():
        return default
    p = Path(value.strip()).expanduser()
    if not p.is_absolute():
        dlog(f"config: {value!r} is not an absolute path; using {default}")
        return default
    return p


def load_config():
    """Return (audio_dir, video_dir) from CONFIG_PATH.

    Called on every stash so an edit takes effect on the next download without
    a server restart. A missing, unreadable, or malformed file falls back to
    the defaults — a bad config should never block a download."""
    if not CONFIG_PATH.exists():
        return DEFAULT_AUDIO_DIR, DEFAULT_VIDEO_DIR
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        dlog(f"config: cannot read {CONFIG_PATH} ({exc}); using defaults")
        return DEFAULT_AUDIO_DIR, DEFAULT_VIDEO_DIR
    if not isinstance(data, dict):
        dlog(f"config: {CONFIG_PATH} is not a JSON object; using defaults")
        return DEFAULT_AUDIO_DIR, DEFAULT_VIDEO_DIR
    return (
        _coerce_dir(data.get("audio_dir"), DEFAULT_AUDIO_DIR),
        _coerce_dir(data.get("video_dir"), DEFAULT_VIDEO_DIR),
    )


def ensure_config_file():
    """Write CONFIG_PATH with the current defaults if it does not exist yet, so
    there is always a file to edit. Never overwrites an existing config."""
    if CONFIG_PATH.exists():
        return
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({
                "audio_dir": str(DEFAULT_AUDIO_DIR),
                "video_dir": str(DEFAULT_VIDEO_DIR),
            }, f, indent=2)
            f.write("\n")
        dlog(f"wrote default config to {CONFIG_PATH}")
    except OSError as exc:
        dlog(f"could not write default config to {CONFIG_PATH}: {exc}")


def sanitize(name: str) -> str:
    bad = '/\\:*?"<>|'
    cleaned = "".join(c for c in name if c not in bad).strip()
    return cleaned or "audio"


def _find(candidates, binary):
    for p in candidates:
        if os.path.exists(p):
            return p
    from shutil import which
    return which(binary)


def find_yt_dlp():
    return _find(YT_DLP_CANDIDATES, "yt-dlp")


def find_ffmpeg():
    return _find(FFMPEG_CANDIDATES, "ffmpeg")


def strip_xattrs(path):
    """Remove all extended attributes from path, especially com.apple.quarantine."""
    try:
        for attr in os.listxattr(path):
            try:
                os.removexattr(path, attr)
            except OSError as exc:
                dlog(f"removexattr {attr} on {path}: {exc}")
    except (OSError, AttributeError) as exc:
        dlog(f"listxattr on {path}: {exc}")
    try:
        subprocess.run(["/usr/bin/xattr", "-c", path], check=False, timeout=5)
    except Exception as exc:
        dlog(f"xattr -c on {path}: {exc}")


def ffmpeg_runs(path):
    """Returns True iff path actually executes successfully (all dylibs resolve
    and Gatekeeper allows it)."""
    if not path or not os.path.exists(path):
        return False
    try:
        if str(STATIC_BIN_DIR) in path:
            strip_xattrs(path)
    except Exception as exc:
        dlog(f"strip_xattrs during ffmpeg_runs: {exc}")
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception as exc:
        dlog(f"ffmpeg_runs check failed for {path}: {exc}")
        return False


def download_static_ffmpeg(progress_cb=None):
    """Download evermeet.cx static ffmpeg + ffprobe into STATIC_BIN_DIR.
    Returns the directory on success, or None on failure."""
    STATIC_BIN_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("ffmpeg", EVERMEET_FFMPEG),
        ("ffprobe", EVERMEET_FFPROBE),
    ]
    for name, url in targets:
        out = STATIC_BIN_DIR / name
        if out.exists() and ffmpeg_runs(str(out)):
            continue
        if progress_cb:
            progress_cb(f"downloading {name}")
        dlog(f"downloading static {name} from {url}")
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                data = resp.read()
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp.write(data)
                zip_path = tmp.name
            extracted = False
            with zipfile.ZipFile(zip_path) as z:
                for member in z.namelist():
                    base = os.path.basename(member)
                    if base == name:
                        with z.open(member) as src, open(out, "wb") as dst:
                            dst.write(src.read())
                        out.chmod(0o755)
                        strip_xattrs(str(out))
                        extracted = True
                        break
            os.unlink(zip_path)
            if not extracted:
                dlog(f"zip didn't contain {name}")
                return None
            dlog(f"installed static {name} at {out} (quarantine stripped)")
        except Exception as e:
            dlog(f"failed to fetch {name}: {e}\n{traceback.format_exc()}")
            return None

    if all((STATIC_BIN_DIR / n).exists() for n, _ in targets):
        return str(STATIC_BIN_DIR)
    return None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def ndjson_line(data_dict):
    """Format a dict as a newline-delimited JSON line."""
    return json.dumps(data_dict) + "\n"


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class MagpieHandler(BaseHTTPRequestHandler):
    """Handles /health, /token, and /stash endpoints."""

    server_version = f"Magpie/{VERSION}"
    # HTTP/1.1 so /stash can stream with chunked transfer encoding (the browser
    # hands each chunk to GM_xmlhttpRequest's onprogress as it arrives). All
    # non-stream responses send Content-Length, so keep-alive stays well-framed.
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
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/token":
            self._handle_token()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/stash":
            self._handle_stash()
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

    def _handle_stash(self):
        """Accept a download request and stream progress via SSE."""
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            dlog(f"bad request body: {exc}")
            self._send_json(400, {"error": "invalid JSON"})
            return

        url = body.get("url")
        name = sanitize(body.get("name", "audio"))
        format = body.get("format", "mp3")
        if format not in ("mp3", "mp4"):
            self._send_json(400, {"error": "format must be mp3 or mp4"})
            return
        is_audio = format == "mp3"
        playlist = body.get("playlist", False)

        if not url:
            self._send_json(400, {"error": "missing url"})
            return

        dlog(f"stash request: url={url} name={name} format={format} playlist={playlist}")

        # Begin the streaming response. Chunked transfer encoding lets the
        # browser hand each NDJSON line to GM_xmlhttpRequest's onprogress as it
        # arrives instead of buffering the whole body until the socket closes,
        # and the terminating 0-length chunk gives an explicit end-of-stream.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self._set_cors_headers()
        self.end_headers()

        stream_ended = [False]

        def send_progress(data_dict):
            if stream_ended[0]:
                return
            try:
                payload = ndjson_line(data_dict).encode("utf-8")
                # Chunked framing: <hex length> CRLF <payload> CRLF, written as a
                # single atomic write so concurrent playlist progress updates
                # (serialized under a lock) can't interleave within a frame.
                self.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
                # Every _handle_stash code path ends by sending exactly one
                # {"type": "done"} message; emit the terminating 0-length chunk
                # right after it so the browser fires onload promptly.
                if data_dict.get("type") == "done":
                    self.wfile.write(b"0\r\n\r\n")
                    stream_ended[0] = True
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError) as exc:
                stream_ended[0] = True
                dlog(f"client disconnected during stream: {exc}")

        # Find yt-dlp
        yt_dlp = find_yt_dlp()
        if not yt_dlp:
            send_progress({"type": "done", "ok": False, "error": "yt-dlp not found. brew install yt-dlp"})
            return

        # Find a working ffmpeg
        ffmpeg = None
        for candidate in FFMPEG_CANDIDATES:
            if ffmpeg_runs(candidate):
                ffmpeg = candidate
                dlog(f"using ffmpeg: {candidate}")
                break
            elif os.path.exists(candidate):
                dlog(f"ffmpeg at {candidate} exists but won't run (likely broken dylibs)")

        if not ffmpeg:
            dlog("no working ffmpeg found; downloading static fallback")
            send_progress({"type": "progress", "phase": "starting", "percent": 0})
            send_progress({"type": "progress", "phase": "downloading", "percent": 0})

            def _progress(msg):
                dlog(f"static-install: {msg}")
                send_progress({"type": "progress", "phase": "downloading", "percent": 0})

            installed = download_static_ffmpeg(progress_cb=_progress)
            if installed:
                candidate = str(STATIC_BIN_DIR / "ffmpeg")
                if ffmpeg_runs(candidate):
                    ffmpeg = candidate
                    dlog(f"using static ffmpeg: {candidate}")

        if not ffmpeg:
            send_progress({
                "type": "done",
                "ok": False,
                "error": (
                    "ffmpeg not found or all candidates fail to run. "
                    "Try: brew reinstall ffmpeg"
                ),
            })
            return

        # Prepare output path. Config is read per request, so editing
        # CONFIG_PATH takes effect on the next stash with no restart.
        audio_dir, video_dir = load_config()
        dest_dir = audio_dir if is_audio else video_dir
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
        if playlist:
            self._download_playlist_parallel(url, name, is_audio, dest_dir, ffmpeg, yt_dlp, send_progress)
            return

        output_template = str(dest_dir / f"{name}.%(ext)s")
        expected_path = str(dest_dir / f"{name}.{format}")

        # Build yt-dlp command through a login shell (resolves dylib paths)
        if is_audio:
            format_args = ["-x", "--audio-format", "mp3"]
        else:
            format_args = ["--merge-output-format", "mp4"]
        playlist_args = [] if playlist else ["--no-playlist"]
        yt_dlp_cmd = " ".join([
            shlex.quote(yt_dlp),
            *format_args,
            *playlist_args,
            "--newline",
            "--ffmpeg-location", shlex.quote(os.path.dirname(ffmpeg)),
            "-o", shlex.quote(output_template),
            shlex.quote(url),
        ])
        cmd = ["/bin/zsh", "-l", "-c", yt_dlp_cmd]
        dlog(f"running shell cmd: {cmd}")

        send_progress({"type": "progress", "phase": "starting", "percent": 0})

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            dlog(f"Popen failed: {e}\n{traceback.format_exc()}")
            send_progress({"type": "done", "ok": False, "error": f"failed to start yt-dlp: {e}"})
            return

        last_pct_int = -1
        tail = []

        if proc.stdout is None:
            send_progress({"type": "done", "ok": False, "error": "failed to capture yt-dlp output"})
            proc.wait()
            return

        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 80:
                tail.pop(0)
            dlog(f"yt-dlp> {line}")

            pm = PLAYLIST_ITEM_RE.match(line)
            if pm:
                send_progress({"type": "progress", "phase": "playlist_item", "current": int(pm.group(1)), "total": int(pm.group(2))})
                last_pct_int = -1
                continue

            m = PROGRESS_RE.match(line)
            if m:
                pct = float(m.group(1))
                cur_int = int(pct)
                if cur_int != last_pct_int:
                    last_pct_int = cur_int
                    send_progress({"type": "progress", "phase": "downloading", "percent": pct})
                continue

            lower = line.lower()
            if "[extractaudio]" in lower or ("destination:" in lower and ".mp3" in lower):
                send_progress({"type": "progress", "phase": "extracting"})
            elif "[merger]" in lower or "merging formats" in lower:
                send_progress({"type": "progress", "phase": "merging"})
            elif "[fixup" in lower:
                send_progress({"type": "progress", "phase": "fixing"})

        proc.wait()
        dlog(f"yt-dlp exited with code {proc.returncode}")

        if proc.returncode == 0:
            # Playlists return early via _download_playlist_parallel, so this
            # path is always a single file.
            message = f"Saved to {display_path(dest_dir)}"
            send_progress({"type": "done", "ok": True, "path": expected_path, "message": message})
        else:
            err_text = "\n".join(tail[-12:]) if tail else f"exit code {proc.returncode}"
            err_text += f"\n\n[ran: {' '.join(cmd)}]"
            send_progress({"type": "done", "ok": False, "error": err_text})

    def _download_playlist_parallel(self, url, name, is_audio, dest_dir, ffmpeg, yt_dlp, send_progress):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        send_progress({"type": "progress", "phase": "loading"})

        flat_cmd = ["/bin/zsh", "-l", "-c", " ".join([
            shlex.quote(yt_dlp),
            "--flat-playlist", "--dump-json",
            shlex.quote(url),
        ])]
        dlog(f"extracting playlist: {flat_cmd}")

        try:
            proc = subprocess.run(flat_cmd, capture_output=True, text=True, timeout=120)
        except Exception as e:
            send_progress({"type": "done", "ok": False, "error": f"failed to extract playlist: {e}"})
            return

        if proc.returncode != 0:
            err = proc.stderr[-500:] if proc.stderr else f"exit code {proc.returncode}"
            send_progress({"type": "done", "ok": False, "error": f"failed to extract playlist:\n{err}"})
            return

        items = []
        for line in proc.stdout.strip().split('\n'):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                video_url = entry.get("webpage_url") or entry.get("url", "")
                if not video_url.startswith("http"):
                    video_url = f"https://www.youtube.com/watch?v={video_url}"
                items.append(video_url)
            except json.JSONDecodeError:
                continue

        total = len(items)
        if total == 0:
            send_progress({"type": "done", "ok": False, "error": "no items found in playlist"})
            return

        dlog(f"playlist: {total} items, downloading 3 at a time")
        send_progress({"type": "progress", "phase": "playlist_item", "current": 0, "total": total})

        subfolder = dest_dir / name
        subfolder.mkdir(parents=True, exist_ok=True)
        template = str(subfolder / "%(title)s.%(ext)s")

        if is_audio:
            fmt_args = ["-x", "--audio-format", "mp3"]
        else:
            fmt_args = ["--merge-output-format", "mp4"]

        lock = Lock()
        completed = [0]
        failed = [0]

        def dl(video_url):
            yt_cmd = " ".join([
                shlex.quote(yt_dlp),
                *fmt_args,
                "--no-playlist",
                "--ffmpeg-location", shlex.quote(os.path.dirname(ffmpeg)),
                "-o", shlex.quote(template),
                shlex.quote(video_url),
            ])
            cmd = ["/bin/zsh", "-l", "-c", yt_cmd]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                ok = r.returncode == 0
                if not ok:
                    dlog(f"playlist item failed: {video_url}: {r.stderr[-200:]}")
            except Exception as e:
                dlog(f"playlist item error: {video_url}: {e}")
                ok = False
            with lock:
                if ok:
                    completed[0] += 1
                else:
                    failed[0] += 1
                done = completed[0] + failed[0]
                try:
                    send_progress({"type": "progress", "phase": "playlist_item", "current": done, "total": total})
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(dl, v) for v in items]
            for f in as_completed(futures):
                pass

        dest_label = display_path(subfolder)
        if failed[0] == 0:
            msg = f"Stashed {total} tracks to {dest_label}"
            send_progress({"type": "done", "ok": True, "path": str(subfolder), "message": msg})
        else:
            msg = f"Stashed {completed[0]}/{total} to {dest_label} ({failed[0]} failed)"
            send_progress({"type": "done", "ok": completed[0] > 0, "path": str(subfolder), "message": msg})


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def write_token():
    """Write the auth token to TOKEN_PATH for external readers."""
    try:
        with open(TOKEN_PATH, "w") as f:
            f.write(AUTH_TOKEN)
        os.chmod(TOKEN_PATH, 0o600)
        dlog(f"token written to {TOKEN_PATH}")
    except Exception as exc:
        dlog(f"failed to write token file: {exc}")
        sys.stderr.write(f"warning: could not write {TOKEN_PATH}: {exc}\n")


def main():
    dlog(f"=== magpie_server.py {VERSION} starting ===")
    dlog(f"PATH={os.environ.get('PATH', '<unset>')}")

    ensure_config_file()
    audio_dir, video_dir = load_config()
    dlog(f"config: {CONFIG_PATH}")
    dlog(f"audio -> {audio_dir}")
    dlog(f"video -> {video_dir}")
    print(f"Config: {CONFIG_PATH}", flush=True)
    print(f"  audio_dir: {audio_dir}", flush=True)
    print(f"  video_dir: {video_dir}", flush=True)

    write_token()

    # Print token to stdout so the LaunchAgent can capture it
    print(f"MAGPIE_TOKEN={AUTH_TOKEN}", flush=True)
    dlog(f"auth token: {AUTH_TOKEN}")

    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), MagpieHandler)
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
