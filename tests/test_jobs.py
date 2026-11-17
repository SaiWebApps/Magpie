#!/usr/bin/env python3
"""The download queue, the iBroadcast confirmation, and the yt-dlp command.

Each test starts the real server (tests/serve_fake.py) on a spare port with
all state in a temporary MAGPIE_HOME, and talks to it with http.client, which
connects directly rather than through any proxy.

  python3 -m unittest discover -s tests -v

MAGPIE_TEST_REAL=1 also runs one real YouTube download through the queue with
the bundled tools only (MAGPIE_TEST_BIN: their folder) and a PATH holding
nothing but the system directories. iBroadcast is off for it.
"""

import hashlib
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVE = HERE / "serve_fake.py"
sys.path.insert(0, str(HERE.parent))

import magpie_ibroadcast  # noqa: E402
import magpie_server  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shown(path):
    """How the server displays a folder in its messages."""
    return magpie_server.display_path(Path(path))


class Server:
    """One isolated server process. Several can share a temp dir in turn,
    which is how the restart test hands a job database to a second run.

    backup turns iBroadcast backup on in the config; it defaults to on exactly
    when upload_log gives the server a stand-in iBroadcast to talk to."""

    def __init__(self, tmp, delay="0.05", upload_log=None, real=False, tag=False,
                 backup=None, env=None):
        self.tmp = Path(tmp)
        self.port = free_port()
        config = self.tmp / "config.json"
        if not config.exists():
            config.write_text(json.dumps({
                "audio_dir": str(self.tmp / "audio"),
                "video_dir": str(self.tmp / "video"),
                "ibroadcast_upload": bool(upload_log) if backup is None else backup,
                "tag_from_filename": tag,
            }))
        full_env = dict(os.environ,
                        MAGPIE_HOME=str(self.tmp),
                        MAGPIE_TEST_PORT=str(self.port),
                        MAGPIE_FAKE_ARGV_LOG=str(self.tmp / "argv.jsonl"),
                        MAGPIE_FAKE_DELAY=delay)
        for key in ("MAGPIE_FAKE_UPLOAD_LOG", "MAGPIE_TEST_REAL", "MAGPIE_FAKE_DROP",
                    "MAGPIE_FAKE_PROCESSING", "MAGPIE_FAKE_CONFIRM_TIMEOUT"):
            full_env.pop(key, None)
        if upload_log:
            full_env["MAGPIE_FAKE_UPLOAD_LOG"] = str(upload_log)
        if real:
            full_env["MAGPIE_TEST_REAL"] = "1"
        full_env.update(env or {})
        self.proc = subprocess.Popen([sys.executable, str(SERVE)], env=full_env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while True:
            try:
                status, _ = self.call("GET", "/health")
                if status == 200:
                    break
            except OSError:
                pass
            if time.time() > deadline or self.proc.poll() is not None:
                self.kill()
                raise RuntimeError("test server did not start; see " + str(self.log_path))
            time.sleep(0.1)
        self.token = self.call("GET", "/token")[1]["token"]

    @property
    def log_path(self):
        return self.tmp / "logs" / "magpie.log"

    def connect(self, timeout=30):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def call(self, method, path, body=None, auth=True):
        conn = self.connect()
        headers = {}
        if auth and getattr(self, "token", None):
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, (json.loads(raw) if raw else None)

    def open_stream(self, method, path, body=None):
        conn = self.connect(timeout=60)
        headers = {"Authorization": "Bearer " + self.token}
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        return conn, conn.getresponse()

    def stream(self, method, path, body=None):
        """Every NDJSON message until the server ends the stream."""
        conn, resp = self.open_stream(method, path, body)
        messages = []
        while True:
            line = resp.readline()
            if not line:
                break
            if line.strip():
                messages.append(json.loads(line))
        conn.close()
        return messages

    def wait_for(self, job_id, predicate, seconds=60):
        deadline = time.time() + seconds
        job = None
        while time.time() < deadline:
            status, job = self.call("GET", f"/jobs/{job_id}")
            if status == 200 and predicate(job):
                return job
            time.sleep(0.1)
        raise AssertionError(f"job {job_id} never reached the expected state; last: {job}")

    def kill(self):
        """End the server at once, with no cleanup (TerminateProcess on Windows)."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)


def phases(messages):
    return [m.get("phase", m["type"]) for m in messages]


class ServerTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="magpie-test-"))
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, **kwargs):
        server = Server(self.tmp, **kwargs)
        self.servers.append(server)
        return server

    def create(self, server, v, name, **extra):
        body = {"url": f"https://www.youtube.com/watch?v={v}", "name": name, "format": "mp3"}
        body.update(extra)
        return server.call("POST", "/jobs", body)

    def stash(self, server, v, name):
        """Queue a stash and return every message up to its done."""
        _, job = self.create(server, v, name)
        return server.stream("GET", f"/jobs/{job['id']}/events")

    def files(self, name):
        audio = self.tmp / "audio"
        return sorted(p.name for p in audio.iterdir() if p.stem == name) if audio.exists() else []

    def fake_md5(self, name):
        """MD5 of what the stand-in yt-dlp writes for this stash name."""
        out = str(self.tmp / "audio" / f"{name}.mp3")
        return hashlib.md5(f"fake audio for {out}".encode("utf-8")).hexdigest()

    def argv_log(self):
        with open(self.tmp / "argv.jsonl", encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class JobQueueTests(ServerTestCase):

    def test_connection_is_direct(self):
        """No proxy between test and server: the response carries exactly the
        headers http.server writes, with a single Date header."""
        server = self.start()
        conn = server.connect()
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        names = [k.lower() for k, _ in resp.getheaders()]
        self.assertEqual(names.count("date"), 1)
        self.assertEqual(resp.getheader("Server").split(" ")[0], "Magpie/2.0-http")

    def test_create_job_answers_at_once_and_saves_it(self):
        # The stand-in download takes over 3 s at this delay; the answer must
        # arrive well before it finishes, on a slow runner too.
        server = self.start(delay="0.3")
        started = time.time()
        status, job = self.create(server, "abc", "Quick")
        elapsed = time.time() - started
        self.assertEqual(status, 202)
        self.assertLess(elapsed, 2.0, f"POST /jobs took {elapsed:.3f}s")
        self.assertIn(job["state"], ("queued", "running"))
        db = sqlite3.connect(str(self.tmp / "jobs.sqlite3"))
        row = db.execute("SELECT name, format, key FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        db.close()
        self.assertEqual(row, ("Quick", "mp3", "video:abc"))

    def test_events_deliver_every_stage_in_order(self):
        server = self.start(upload_log=self.tmp / "uploads.txt")
        messages = self.stash(server, "stages", "Stages")
        seen = phases(messages)
        order = [p for p in seen if p in ("starting", "downloading", "extracting",
                                          "backing_up", "confirming", "done")]
        collapsed = [p for i, p in enumerate(order) if i == 0 or order[i - 1] != p]
        self.assertEqual(collapsed, ["starting", "downloading", "extracting",
                                     "backing_up", "confirming", "done"])
        percents = [m["percent"] for m in messages if m.get("phase") == "downloading"]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 90.0)
        done = messages[-1]
        self.assertTrue(done["ok"])
        self.assertNotIn("backup_status", done)
        self.assertEqual(done["message"], f"Saved to {shown(self.tmp / 'audio')} · in iBroadcast")
        self.assertEqual(done["path"], str(self.tmp / "audio" / "Stages.mp3"))
        self.assertEqual(self.files("Stages"), ["Stages.mp3"])

    def test_yt_dlp_runs_bundled_with_no_shell(self):
        """The command is the bundled yt-dlp itself, never a shell or a
        Homebrew path, and carries every flag Magpie depends on."""
        server = self.start()
        self.stash(server, "argv", "Argv")
        argv = self.argv_log()[-1]
        self.assertEqual(argv[0], str(HERE / "fake_tools" / "fake_yt_dlp.py"))
        args = argv[1:]
        for shell in ("/bin/zsh", "/bin/sh", "/bin/bash", "cmd.exe", "-c", "-l"):
            self.assertNotIn(shell, args)
        for arg in args:
            self.assertNotIn(Path("/opt/homebrew"), Path(arg).parents)
            self.assertNotIn(Path("/usr/local/bin"), Path(arg).parents)
        for flag in ("--ignore-config", "--no-update", "--no-js-runtimes", "--newline",
                     "--progress", "--no-playlist", "-x"):
            self.assertIn(flag, args)
        fake = str(HERE / "fake_tools" / "fake_tool.py")
        self.assertEqual(args[args.index("--js-runtimes") + 1], "deno:" + fake)
        self.assertEqual(args[args.index("--ffmpeg-location") + 1], fake)
        self.assertEqual(args[args.index("--encoding") + 1], "utf-8")
        self.assertEqual(args[args.index("--print") + 1], magpie_server.FILE_TEMPLATE)
        self.assertEqual(args[-2:], ["--", "https://www.youtube.com/watch?v=argv"])
        self.assertNotIn("--extractor-args", args)

    def test_leaving_the_page_does_not_stop_the_job(self):
        server = self.start(delay="0.3")
        _, job = self.create(server, "leave", "Leave")
        conn, resp = server.open_stream("GET", f"/jobs/{job['id']}/events")
        first = json.loads(resp.readline())
        self.assertEqual(first["type"], "progress")
        conn.close()                     # the tab goes away mid-download
        finished = server.wait_for(job["id"], lambda j: j["state"] in ("done", "failed"))
        self.assertEqual(finished["state"], "done")
        self.assertEqual(self.files("Leave"), ["Leave.mp3"])

    def test_restart_resumes_and_uploads_once(self):
        uploads = self.tmp / "uploads.txt"
        first = self.start(delay="0.5", upload_log=uploads)
        _, job = self.create(first, "resume", "Resume")
        first.wait_for(job["id"], lambda j: j["message"].get("phase") == "downloading")
        first.kill()                    # crash mid-download, no cleanup

        second = self.start(delay="0.05", upload_log=uploads)
        finished = second.wait_for(job["id"], lambda j: j["state"] in ("done", "failed"))
        self.assertEqual(finished["state"], "done")
        self.assertEqual(finished["message"]["message"], f"Saved to {shown(self.tmp / 'audio')} · in iBroadcast")
        self.assertEqual(self.files("Resume"), ["Resume.mp3"])
        self.assertEqual(uploads.read_text().splitlines(), ["Resume.mp3"])
        log = second.log_path.read_text(encoding="utf-8")
        self.assertIn(f"resuming 1 job(s) left unfinished by the last run: ['{job['id']}']", log)

    def test_page_finds_its_jobs_by_video_and_playlist(self):
        server = self.start()
        _, video = self.create(server, "abc123", "Found")
        _, playlist = server.call("POST", "/jobs", {
            "url": "https://www.youtube.com/playlist?list=PLx", "name": "List",
            "format": "mp3", "playlist": True})
        page = "https://www.youtube.com/watch?v=abc123&t=42&list=PLx&index=3"
        status, found = server.call("GET", "/jobs?url=" + urllib.parse.quote(page, safe=""))
        self.assertEqual(status, 200)
        self.assertEqual(found["video"]["id"], video["id"])
        self.assertEqual(found["playlist"]["id"], playlist["id"])
        other = urllib.parse.quote("https://www.youtube.com/watch?v=zzz", safe="")
        self.assertEqual(server.call("GET", "/jobs?url=" + other)[1], {"video": None, "playlist": None})

    def test_old_stash_endpoint_still_streams_to_done(self):
        server = self.start()
        messages = server.stream("POST", "/stash", {
            "url": "https://www.youtube.com/watch?v=legacy", "name": "Legacy", "format": "mp3"})
        self.assertEqual(messages[-1]["type"], "done")
        self.assertTrue(messages[-1]["ok"])
        self.assertIn("downloading", phases(messages))
        self.assertEqual(self.files("Legacy"), ["Legacy.mp3"])

    def test_failure_is_reported_and_saved(self):
        server = self.start()
        _, job = self.create(server, "fail", "Broken")
        done = server.stream("GET", f"/jobs/{job['id']}/events")[-1]
        self.assertFalse(done["ok"])
        self.assertIn("Video unavailable", done["error"])
        self.assertEqual(server.call("GET", f"/jobs/{job['id']}")[1]["state"], "failed")

    def test_playlist_job_counts_items(self):
        server = self.start()
        _, job = server.call("POST", "/jobs", {
            "url": "https://www.youtube.com/playlist?list=PLabc", "name": "Mix",
            "format": "mp3", "playlist": True})
        messages = server.stream("GET", f"/jobs/{job['id']}/events")
        counts = [(m["current"], m["total"]) for m in messages if m.get("phase") == "playlist_item"]
        self.assertEqual(counts[-1], (3, 3))
        self.assertTrue(messages[-1]["ok"])
        self.assertEqual(messages[-1]["message"],
                         f"Stashed 3 tracks to {shown(self.tmp / 'audio' / 'Mix')} · iBroadcast backup is off")
        self.assertEqual(sorted(p.name for p in (self.tmp / "audio" / "Mix").iterdir()),
                         ["item1.mp3", "item2.mp3", "item3.mp3"])

    def test_jobs_need_the_token(self):
        server = self.start()
        _, job = self.create(server, "auth", "Auth")
        self.assertEqual(server.call("GET", f"/jobs/{job['id']}", auth=False)[0], 401)
        self.assertEqual(server.call("POST", "/jobs", {"url": "x"}, auth=False)[0], 401)


class IBroadcastConfirmationTests(ServerTestCase):
    """✅ means iBroadcast lists the file; every other outcome says why not."""

    def test_backup_off_says_so(self):
        server = self.start(backup=False)
        done = self.stash(server, "off", "Off")[-1]
        self.assertTrue(done["ok"])
        self.assertNotIn("backup_status", done)
        self.assertEqual(done["message"], f"Saved to {shown(self.tmp / 'audio')} · iBroadcast backup is off")

    def test_missing_login_warns(self):
        server = self.start(backup=True)          # on, but no token stored
        done = self.stash(server, "nologin", "NoLogin")[-1]
        self.assertTrue(done["ok"])
        self.assertEqual(done["backup_status"], "no_login")
        self.assertIn("make ibroadcast-login", done["backup_error"])
        self.assertEqual(done["message"],
                         f"Saved to {shown(self.tmp / 'audio')} · not backed up (no iBroadcast login)")

    def test_upload_never_listed_warns(self):
        uploads = self.tmp / "uploads.txt"
        server = self.start(upload_log=uploads, env={"MAGPIE_FAKE_DROP": "1",
                                                     "MAGPIE_FAKE_CONFIRM_TIMEOUT": "1.5"})
        messages = self.stash(server, "dropped", "Dropped")
        done = messages[-1]
        self.assertIn("confirming", phases(messages))
        self.assertTrue(done["ok"])
        self.assertEqual(done["backup_status"], "unconfirmed")
        self.assertIn("Dropped.mp3", done["backup_error"])
        self.assertEqual(done["message"], f"Saved to {shown(self.tmp / 'audio')} · not in iBroadcast yet")
        self.assertEqual(uploads.read_text().splitlines(), ["Dropped.mp3"])

    def test_already_listed_is_skipped(self):
        uploads = self.tmp / "uploads.txt"
        Path(str(uploads) + ".json").write_text(json.dumps({self.fake_md5("Have"): ["Have", 1, 0]}))
        server = self.start(upload_log=uploads)
        done = self.stash(server, "have", "Have")[-1]
        self.assertEqual(done["message"], f"Saved to {shown(self.tmp / 'audio')} · already in iBroadcast")
        self.assertFalse(uploads.exists(), "nothing should have been uploaded")

    def test_sent_long_ago_but_never_listed_is_sent_again(self):
        """Magpie's own record alone does not count as 'already there'."""
        uploads = self.tmp / "uploads.txt"
        (self.tmp / "ibroadcast-sent.json").write_text(json.dumps({self.fake_md5("Lost"): 0}))
        server = self.start(upload_log=uploads)
        done = self.stash(server, "lost", "Lost")[-1]
        self.assertEqual(done["message"], f"Saved to {shown(self.tmp / 'audio')} · in iBroadcast")
        self.assertEqual(uploads.read_text().splitlines(), ["Lost.mp3"])

    def test_sent_moments_ago_is_waited_for_not_sent_twice(self):
        uploads = self.tmp / "uploads.txt"
        (self.tmp / "ibroadcast-sent.json").write_text(json.dumps({self.fake_md5("Fresh"): time.time()}))
        server = self.start(upload_log=uploads, env={"MAGPIE_FAKE_CONFIRM_TIMEOUT": "60"})
        _, job = self.create(server, "fresh", "Fresh")
        server.wait_for(job["id"], lambda j: j["message"].get("phase") == "confirming")
        self.assertFalse(uploads.exists(), "a file sent moments ago must not be sent again")
        # The earlier copy finishes processing and shows up in the library.
        Path(str(uploads) + ".json").write_text(json.dumps({self.fake_md5("Fresh"): ["Fresh", 1, 0]}))
        finished = server.wait_for(job["id"], lambda j: j["state"] in ("done", "failed"))
        self.assertEqual(finished["message"]["message"], f"Saved to {shown(self.tmp / 'audio')} · in iBroadcast")
        self.assertFalse(uploads.exists())


class InLibraryTests(unittest.TestCase):
    """in_library against canned iBroadcast answers, no server or network."""

    def setUp(self):
        self.saved = (magpie_ibroadcast.library_md5s, magpie_ibroadcast.fetch_library)
        self.tmp = Path(tempfile.mkdtemp(prefix="magpie-inlib-"))
        self.song = self.tmp / "AC_Revelations_WoundedEagle.mp3"
        self.song.write_bytes(b"x" * 1234)

    def tearDown(self):
        magpie_ibroadcast.library_md5s, magpie_ibroadcast.fetch_library = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def library(self, *rows):
        tracks = {"map": {"title": 0, "size": 1, "trashed": 2, "artists_additional_map": {"x": 0}}}
        for n, row in enumerate(rows):
            tracks[str(n)] = list(row)
        return lambda token: {"tracks": tracks}

    def test_checksum_match_counts(self):
        magpie_ibroadcast.library_md5s = lambda token: {"abc"}
        magpie_ibroadcast.fetch_library = self.library()
        self.assertEqual(magpie_ibroadcast.in_library({self.song: "abc"}, {}), {self.song})

    def test_title_and_size_count_before_the_checksum_appears(self):
        magpie_ibroadcast.library_md5s = lambda token: set()
        magpie_ibroadcast.fetch_library = self.library(("AC_Revelations_WoundedEagle", 1234, False))
        self.assertEqual(magpie_ibroadcast.in_library({self.song: "abc"}, {}), {self.song})

    def test_wrong_size_or_trashed_does_not_count(self):
        magpie_ibroadcast.library_md5s = lambda token: set()
        magpie_ibroadcast.fetch_library = self.library(("AC_Revelations_WoundedEagle", 999, False),
                                                       ("AC_Revelations_WoundedEagle", 1234, True))
        self.assertEqual(magpie_ibroadcast.in_library({self.song: "abc"}, {}), set())


@unittest.skipUnless(os.environ.get("MAGPIE_TEST_REAL"), "set MAGPIE_TEST_REAL=1 to download from YouTube")
class RealDownloadTest(unittest.TestCase):
    """One real download with nothing but the bundled tools."""

    def test_real_video_through_the_queue(self):
        tmp = Path(tempfile.mkdtemp(prefix="magpie-real-"))
        system_path = os.pathsep.join(["/usr/bin", "/bin"]) if sys.platform != "win32" \
            else os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        server = Server(tmp, real=True, tag=True, env={"PATH": system_path})
        try:
            body = {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                    "name": "MagpieTest_MeAtTheZoo", "format": "mp3"}
            _, job = server.call("POST", "/jobs", body)
            messages = server.stream("GET", f"/jobs/{job['id']}/events")
            done = messages[-1]
            self.assertTrue(done["ok"], done.get("error"))
            path = tmp / "audio" / "MagpieTest_MeAtTheZoo.mp3"
            self.assertTrue(path.is_file())
            bin_dir = Path(os.environ.get("MAGPIE_TEST_BIN") or tmp / "bin")
            ffprobe = bin_dir / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
            probe = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                capture_output=True, text=True)
            tags = {k.lower(): v for k, v in json.loads(probe.stdout)["format"]["tags"].items()}
            print(f"\n  phases: {sorted(set(phases(messages)))}\n  message: {done['message']}"
                  f"\n  file: {path.name} ({path.stat().st_size} bytes)\n  tags: {tags}"
                  f"\n  server PATH: {system_path}")
            self.assertEqual(tags.get("album"), "MagpieTest")
            self.assertEqual(tags.get("title"), "MagpieTest_MeAtTheZoo")
            self.assertNotIn("track", tags)
        finally:
            server.kill()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
