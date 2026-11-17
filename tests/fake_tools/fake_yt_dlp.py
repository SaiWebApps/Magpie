#!/usr/bin/env python3
"""Stand-in for yt-dlp in Magpie's tests. Run as [python, fake_yt_dlp.py, ...].

It honours the parts of yt-dlp's command line Magpie relies on: -o, -x,
--flat-playlist, and the --progress-template / --print templates, printing
the JSON lines the real yt-dlp prints for them (progress on stdout, postprocessor
progress on stderr, the final path on stdout). It writes a small file instead
of downloading anything.

Every invocation's argv is appended as one JSON line to MAGPIE_FAKE_ARGV_LOG
when that is set, so tests can check exactly what Magpie ran.

MAGPIE_FAKE_DELAY sets the pause between progress lines. A video id of
"fail" exits non-zero like a real failure.
"""

import json
import os
import sys
import time
import urllib.parse

args = sys.argv[1:]
log = os.environ.get("MAGPIE_FAKE_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv) + "\n")

delay = float(os.environ.get("MAGPIE_FAKE_DELAY", "0.05"))
url = args[args.index("--") + 1] if "--" in args else args[-1]

if "--flat-playlist" in args:
    for n in range(1, 4):
        print(json.dumps({"url": f"item{n}", "id": f"item{n}", "title": f"Item {n}"}), flush=True)
    sys.exit(0)

video = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("v") or ["video"])[0]
if video == "fail":
    print("ERROR: [youtube] fail: Video unavailable", file=sys.stderr, flush=True)
    sys.exit(1)


def option_values(flag):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def templates(flag):
    """{type: template} from yt-dlp's TYPE:TEMPLATE option values."""
    found = {}
    for value in option_values(flag):
        kind, _, template = value.partition(":")
        found[kind] = template
    return found


progress = templates("--progress-template")
printed = templates("--print")
ext = "mp3" if "-x" in args else "mp4"
out = option_values("-o")[0].replace("%(title)s", video).replace("%(ext)s", ext)

print(f"[youtube] Extracting URL: {url}", file=sys.stderr, flush=True)
for pct in range(0, 101, 10):
    status = "finished" if pct == 100 else "downloading"
    tick = {"status": status, "_percent": float(pct), "downloaded_bytes": pct, "total_bytes": 100,
            "filename": out}
    if "download" in progress:
        print(progress["download"].replace("%(progress)j", json.dumps(tick)), flush=True)
    time.sleep(delay)
if ext == "mp3" and "postprocess" in progress:
    for status in ("started", "finished"):
        tick = {"status": status, "postprocessor": "ExtractAudio"}
        print(progress["postprocess"].replace("%(progress)j", json.dumps(tick)),
              file=sys.stderr, flush=True)
with open(out, "wb") as f:
    f.write(f"fake audio for {out}".encode("utf-8"))
if "after_move" in printed:
    print(printed["after_move"].replace("%(filepath)j", json.dumps(out)), flush=True)
