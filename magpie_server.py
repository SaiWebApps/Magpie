#!/usr/bin/env python3
"""Magpie HTTP server — replaces the native messaging host with an SSE-based
HTTP endpoint that a Tampermonkey userscript can call directly.

Endpoints:
  GET  /health  — liveness check, returns {"ok": true}
  GET  /token   — returns the auth token (only from 127.0.0.1)
  POST /stash   — starts a download, streams progress via SSE

Auth: Bearer token generated on startup, printed to stdout and written to
/tmp/magpie-token.

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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

VERSION = "2.0-http"

HOME = Path.home()
DEST_DIR = HOME / "Music" / "Music" / "Media.localized" / "Automatically Add to Music.localized"

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

        if not url:
            self._send_json(400, {"error": "missing url"})
            return

        dlog(f"stash request: url={url} name={name}")

        # Begin SSE response
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._set_cors_headers()
        self.end_headers()

        def send_progress(data_dict):
            try:
                self.wfile.write(ndjson_line(data_dict).encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError) as exc:
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

        # Prepare output path
        if not DEST_DIR.exists():
            send_progress({
                "type": "done",
                "ok": False,
                "error": (
                    f"Destination folder not found: {DEST_DIR}\n"
                    "Open the Music app at least once to create this folder."
                ),
            })
            return
        output_template = str(DEST_DIR / f"{name}.%(ext)s")
        expected_path = str(DEST_DIR / f"{name}.mp3")

        # Build yt-dlp command through a login shell (resolves dylib paths)
        yt_dlp_cmd = " ".join([
            shlex.quote(yt_dlp),
            "-x",
            "--audio-format", "mp3",
            "--no-playlist",
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
            send_progress({"type": "done", "ok": True, "path": expected_path, "message": "Added to Music library"})
        else:
            err_text = "\n".join(tail[-12:]) if tail else f"exit code {proc.returncode}"
            err_text += f"\n\n[ran: {' '.join(cmd)}]"
            send_progress({"type": "done", "ok": False, "error": err_text})


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

    write_token()

    # Print token to stdout so the LaunchAgent can capture it
    print(f"MAGPIE_TOKEN={AUTH_TOKEN}", flush=True)
    dlog(f"auth token: {AUTH_TOKEN}")

    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), MagpieHandler)
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
