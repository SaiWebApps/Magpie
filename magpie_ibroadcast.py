#!/usr/bin/env python3
"""iBroadcast backup for Magpie.

Pushes a finished stash into the user's iBroadcast library. Upload only — this
module never deletes or renames anything, locally or remotely.

Auth is OAuth 2.0 device code: run `make ibroadcast-login` once, approve in a
browser, and the refresh token keeps the server authorized from then on.

The API shape here follows iBroadcast's official Python uploader:
https://github.com/iBroadcastMediaServices/ibroadcast-uploaders

Standard library only, deliberately — `make install` should not require pip.
"""

import hashlib
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OAUTH_DEVICE_URL = "https://oauth.ibroadcast.com/device/code"
OAUTH_TOKEN_URL = "https://oauth.ibroadcast.com/token"
UPLOAD_URL = "https://upload.ibroadcast.com"

# The client id published in iBroadcast's own uploader script. There is no
# developer registration step; every community uploader uses this value.
CLIENT_ID = "de4ce836a9fb11f0bc7fb49691aa2236"
SCOPES = "user.account:read user.upload"

USER_AGENT = "Magpie stash uploader"
UPLOAD_METHOD = "magpie"

HOME = Path.home()
TOKEN_PATH = Path(os.environ.get("MAGPIE_IBROADCAST_TOKEN")
                  or HOME / ".config" / "magpie-ibroadcast.json")

# iBroadcast is a music service; video stashes are never sent.
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aiff"}

# Refresh a little early so a long upload cannot start on a token that expires
# mid-flight.
EXPIRY_SKEW_SECONDS = 120

NETWORK_TIMEOUT = 60
UPLOAD_TIMEOUT = 600


class IBroadcastError(Exception):
    """Any failure talking to iBroadcast. Never fatal to a stash."""


# ---------------------------------------------------------------------------
# Low-level HTTP (stdlib only)
# ---------------------------------------------------------------------------

def _request(url, data=None, headers=None, method=None, timeout=NETWORK_TIMEOUT):
    """Perform one HTTP request. Returns (status_code, body_bytes).

    An HTTP error status is returned rather than raised, because iBroadcast
    puts the useful error text in the body of a 4xx."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _plain_text(detail, limit=200):
    """Reduce a response body to one short line.

    Auth failures come back as a full HTML error page; the markup is noise in a
    tooltip, so tags are dropped and whitespace collapsed."""
    if detail is None:
        return ""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail)
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    text = str(detail)
    if "<" in text and ">" in text:
        kept, depth = [], 0
        for char in text:
            if char == "<":
                depth += 1
            elif char == ">":
                depth = max(depth - 1, 0)
            elif depth == 0:
                kept.append(char)
        text = "".join(kept)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _describe_failure(url, status, detail=None):
    """Turn an HTTP failure into one line the user can act on.

    This text ends up in the Stash button's tooltip, so it has to say what to
    do next rather than echo a server error page."""
    if status in (401, 403):
        return (f"iBroadcast rejected the credentials (HTTP {status}). "
                "Re-authorize with:  make ibroadcast-login")
    if status == 413:
        return "iBroadcast rejected the file as too large (HTTP 413)."
    if status == 429:
        return "iBroadcast is rate-limiting uploads (HTTP 429). Try again shortly."
    if 500 <= status < 600:
        return f"iBroadcast server error (HTTP {status}). Try again later."
    text = _plain_text(detail)
    return f"HTTP {status} from {url}" + (f": {text}" if text else "")


def _json_request(url, data=None, headers=None, method=None, timeout=NETWORK_TIMEOUT):
    """As _request, but decodes the body as JSON. Returns (status, obj)."""
    status, body = _request(url, data=data, headers=headers, method=method, timeout=timeout)
    try:
        return status, json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise IBroadcastError(_describe_failure(url, status, body))


def _form(fields):
    return urllib.parse.urlencode(fields).encode("utf-8")


def _escape_quotes(value):
    """Escape a value for a multipart Content-Disposition header."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _multipart(fields, file_field, filename, payload):
    """Build a multipart/form-data body. Returns (body_bytes, content_type)."""
    boundary = "----magpie" + secrets.token_hex(16)
    chunks = []
    for key, value in fields.items():
        chunks.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{_escape_quotes(key)}"\r\n\r\n'
            f'{value}\r\n'.encode("utf-8")
        )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{_escape_quotes(file_field)}"; '
        f'filename="{_escape_quotes(filename)}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode("utf-8")
    )
    chunks.append(payload)
    chunks.append(f'\r\n--{boundary}--\r\n'.encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def load_token():
    """Return the stored token dict, or None if absent or unreadable."""
    try:
        with open(TOKEN_PATH) as f:
            token = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(token, dict) or "access_token" not in token:
        return None
    return token


def save_token(token):
    """Persist the token dict with owner-only permissions."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start rather than chmod-ing after writing, so
    # the secret is never briefly world-readable.
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token, f, indent=2)
        f.write("\n")


def _stamp_expiry(token):
    token["expires_at"] = time.time() + float(token.get("expires_in", 0))
    return token


def _auth_header(token):
    return f"{token.get('token_type', 'Bearer')} {token['access_token']}"


# ---------------------------------------------------------------------------
# OAuth device-code flow
# ---------------------------------------------------------------------------

def request_device_code():
    """Start the device flow. Returns the device-code dict."""
    url = OAUTH_DEVICE_URL + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "scope": SCOPES,
    })
    status, obj = _json_request(url, headers={"User-Agent": USER_AGENT})
    if status >= 400:
        raise IBroadcastError(
            f"could not get a device code: {obj.get('error_description') or obj}")
    return obj


def exchange_device_code(device_code):
    """Exchange a device code for a token.

    Returns the token dict, or the string 'pending' while the user has not yet
    approved in their browser."""
    body = _form({
        "client_id": CLIENT_ID,
        "grant_type": "device_code",
        "device_code": device_code,
    })
    status, obj = _json_request(
        OAUTH_TOKEN_URL, data=body,
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    if status >= 400:
        if obj.get("error") == "authorization_pending":
            return "pending"
        raise IBroadcastError(
            f"authorization failed: {obj.get('error_description') or obj}")
    return _stamp_expiry(obj)


def refresh_token(token):
    """Trade a refresh token for a fresh access token."""
    if not token.get("refresh_token"):
        raise IBroadcastError("stored token has no refresh_token; log in again")
    body = _form({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    })
    status, obj = _json_request(
        OAUTH_TOKEN_URL, data=body,
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    if status >= 400:
        raise IBroadcastError(
            f"token refresh failed: {obj.get('error_description') or obj}")
    # Some providers omit refresh_token on refresh; keep the one we have.
    obj.setdefault("refresh_token", token["refresh_token"])
    return _stamp_expiry(obj)


def usable_token(log=lambda m: None):
    """Return a non-expired token dict, refreshing if needed, or None.

    None means 'not set up' — the caller should skip the upload, not fail."""
    token = load_token()
    if token is None:
        return None
    if token.get("expires_at", 0) - EXPIRY_SKEW_SECONDS > time.time():
        return token
    log("ibroadcast: access token expired, refreshing")
    token = refresh_token(token)
    save_token(token)
    return token


# ---------------------------------------------------------------------------
# Library + upload
# ---------------------------------------------------------------------------

def library_md5s(token):
    """Return the set of MD5s already in the library, for duplicate skipping."""
    status, obj = _json_request(
        UPLOAD_URL, data=b"", headers={"Authorization": _auth_header(token),
                                       "User-Agent": USER_AGENT})
    if status >= 400:
        raise IBroadcastError(_describe_failure(UPLOAD_URL, status, obj))
    return set(obj.get("md5") or [])


def file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def upload_file(path, token):
    """Upload one file. Raises IBroadcastError on failure."""
    path = Path(path)
    payload = path.read_bytes()
    body, content_type = _multipart(
        {"file_path": path.name, "method": UPLOAD_METHOD},
        "file", path.name, payload)
    headers = {
        "Authorization": _auth_header(token),
        "User-Agent": USER_AGENT,
        "Content-Type": content_type,
    }
    status, obj = _json_request(UPLOAD_URL, data=body, headers=headers,
                                timeout=UPLOAD_TIMEOUT)
    if status >= 400:
        raise IBroadcastError(f"{path.name}: {_describe_failure(UPLOAD_URL, status, obj)}")
    if obj.get("result") is False:
        reason = _plain_text(obj.get("message") or obj)
        raise IBroadcastError(f"{path.name}: iBroadcast refused the upload — {reason}")
    return obj


# ---------------------------------------------------------------------------
# The entry point the server calls
# ---------------------------------------------------------------------------

def backup(paths, log=lambda m: None, progress=lambda done, total: None):
    """Back up finished audio files to iBroadcast.

    progress(done, total) is called before each file so a caller can report
    which one is in flight; a playlist upload otherwise looks frozen.

    Returns (status, detail) where status is one of:
      'disabled'   — no token stored; nothing was attempted
      'uploaded'   — at least one file was sent
      'skipped'    — everything was already in the library, or none was audio
      'failed'     — something went wrong; the local files are untouched

    Never raises: a backup problem must not turn a successful stash into a
    failed one."""
    audio = [Path(p) for p in paths
             if Path(p).suffix.lower() in AUDIO_SUFFIXES and Path(p).is_file()]
    if not audio:
        return "skipped", "nothing to back up (not audio)"

    try:
        token = usable_token(log)
        if token is None:
            return "disabled", "iBroadcast not set up; run: make ibroadcast-login"

        known = library_md5s(token)
        total = len(audio)
        uploaded = skipped = 0
        for index, path in enumerate(audio, 1):
            progress(index, total)
            if file_md5(path) in known:
                log(f"ibroadcast: already in library, skipping {path.name}")
                skipped += 1
                continue
            log(f"ibroadcast: uploading {path.name}")
            upload_file(path, token)
            uploaded += 1

        if uploaded:
            return "uploaded", f"{uploaded} uploaded, {skipped} already there"
        return "skipped", f"already in library ({skipped})"
    except IBroadcastError as exc:
        log(f"ibroadcast: {exc}")
        return "failed", str(exc)
    except Exception as exc:  # network stack, disk, anything
        log(f"ibroadcast: unexpected failure: {exc}")
        return "failed", str(exc)


# ---------------------------------------------------------------------------
# CLI — `make ibroadcast-login` / `make ibroadcast-status`
# ---------------------------------------------------------------------------

def _with_scheme(url):
    """iBroadcast returns verification_uri without a scheme, which most
    terminals will not turn into a clickable link."""
    if not url:
        return url
    return url if "://" in url else "https://" + url


def login_cli(total_seconds=900):
    """Run the device-code flow interactively and store the token.

    iBroadcast device codes expire in about three minutes, which is not long
    enough to find the page and sign in. Rather than failing, this requests a
    fresh code and reprints it, so the window that matters is total_seconds."""
    if load_token():
        print(f"A token already exists at {TOKEN_PATH}. Continuing replaces it.\n")

    give_up_at = time.time() + total_seconds
    attempt = 0

    while time.time() < give_up_at:
        attempt += 1
        device = request_device_code()
        interval = max(int(device.get("interval", 5)), 1)
        code_dies_at = time.time() + int(device.get("expires_in", 180))
        complete = _with_scheme(device.get("verification_uri_complete"))

        if attempt == 1:
            print("To authorize Magpie with iBroadcast:\n")
        else:
            print("\nThat code expired. Here is a fresh one:\n")
        if complete:
            print(f"  Open this (code already filled in):\n    {complete}\n")
        print(f"  Or go to {_with_scheme(device.get('verification_uri'))} "
              f"and enter:  {device.get('user_code')}")
        print(f"\n  Valid for {int(code_dies_at - time.time())}s. "
              f"Waiting for approval (Ctrl-C to cancel)...")

        while time.time() < code_dies_at and time.time() < give_up_at:
            result = exchange_device_code(device["device_code"])
            if result != "pending":
                save_token(result)
                print(f"\nAuthorized. Token saved to {TOKEN_PATH} (permissions 600).")
                print("Stashed audio will now be backed up to iBroadcast.")
                return 0
            time.sleep(interval)

    print(f"\nGave up after {total_seconds}s without an approval. Run this again.")
    return 1


def status_cli():
    """Report whether backup is configured and the token still works."""
    token = load_token()
    if token is None:
        print(f"Not set up. No token at {TOKEN_PATH}.")
        print("Run: make ibroadcast-login")
        return 1
    print(f"Token file: {TOKEN_PATH}")
    expires_at = token.get("expires_at", 0)
    remaining = int(expires_at - time.time())
    print(f"Access token expires in: {remaining}s"
          f"{' (expired, will refresh)' if remaining <= 0 else ''}")
    try:
        live = usable_token(lambda m: print(f"  {m}"))
        count = len(library_md5s(live))
        print(f"Connected. Library currently holds {count} tracks.")
        return 0
    except IBroadcastError as exc:
        print(f"Token present but not working: {exc}")
        print("Run: make ibroadcast-login")
        return 1


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "login"
    if command == "login":
        sys.exit(login_cli())
    if command == "status":
        sys.exit(status_cli())
    print(f"usage: {sys.argv[0]} [login|status]")
    sys.exit(2)
