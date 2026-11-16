#!/usr/bin/env python3
"""Run the real Magpie server for a test, isolated from the live one.

Port, log, token and every config/state file point into MAGPIE_TEST_DIR, so
a test never touches ~/.config, ~/Music or the server on port 7865. Unless
MAGPIE_TEST_REAL is set, yt-dlp and ffmpeg are the stand-ins in fake_bin/.

With MAGPIE_FAKE_UPLOAD_LOG set, iBroadcast is a stand-in too:
  - each upload's filename is appended to that file, and the upload itself is
    kept in <that file>.json ({md5: [title, size, time]}), so a restarted
    server sees what the previous one sent
  - a track is listed in the library MAGPIE_FAKE_PROCESSING seconds after it
    was uploaded (default 0.3), or never with MAGPIE_FAKE_DROP set
  - the wait for a track to be listed gives up after
    MAGPIE_FAKE_CONFIRM_TIMEOUT seconds (default 5)
"""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import magpie_ibroadcast  # noqa: E402
import magpie_server  # noqa: E402

test_dir = Path(os.environ["MAGPIE_TEST_DIR"])
magpie_server.LISTEN_PORT = int(os.environ["MAGPIE_TEST_PORT"])
magpie_server.LOG_PATH = str(test_dir / "magpie.log")
magpie_server.TOKEN_PATH = str(test_dir / "token")

if not os.environ.get("MAGPIE_TEST_REAL"):
    magpie_server.find_yt_dlp = lambda: str(HERE / "fake_bin" / "yt-dlp")
    magpie_server.FFMPEG_CANDIDATES = [str(HERE / "fake_bin" / "ffmpeg")]

upload_log = os.environ.get("MAGPIE_FAKE_UPLOAD_LOG")
if upload_log:
    store = Path(upload_log + ".json")
    processing = float(os.environ.get("MAGPIE_FAKE_PROCESSING", "0.3"))
    drop = bool(os.environ.get("MAGPIE_FAKE_DROP"))

    def uploads():
        try:
            return json.loads(store.read_text())
        except FileNotFoundError:
            return {}

    def fake_upload(path, token):
        path = Path(path)
        with open(upload_log, "a") as f:
            f.write(path.name + "\n")
        data = uploads()
        data[magpie_ibroadcast.file_md5(path)] = [path.stem, path.stat().st_size, time.time()]
        store.write_text(json.dumps(data))
        return {"result": True}

    def listed():
        if drop:
            return {}
        now = time.time()
        return {md5: row for md5, row in uploads().items() if now - row[2] >= processing}

    def fake_library(token):
        tracks = {"map": {"title": 0, "size": 1, "trashed": 2}}
        for n, (title, size, _) in enumerate(listed().values()):
            tracks[str(n)] = [title, size, False]
        return {"tracks": tracks}

    magpie_ibroadcast.usable_token = lambda log=None: {"access_token": "fake"}
    magpie_ibroadcast.library_md5s = lambda token: set(listed())
    magpie_ibroadcast.fetch_library = fake_library
    magpie_ibroadcast.upload_file = fake_upload
    magpie_ibroadcast.CONFIRM_INTERVAL = 0.2
    magpie_ibroadcast.CONFIRM_TIMEOUT = float(os.environ.get("MAGPIE_FAKE_CONFIRM_TIMEOUT", "5"))

magpie_server.main()
