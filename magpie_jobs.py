#!/usr/bin/env python3
"""Download queue for Magpie.

Every stash is a job saved to SQLite before any work starts. Workers run it
whether or not anyone is watching, anyone can follow its progress, and jobs
still unfinished when the server stops are picked up again on the next start.
Closing the page that started a stash therefore never stops or loses it.

The work itself is not in this module. The server hands JobRunner a function
run(job, send_progress) whose every path ends with exactly one
{"type": "done", ...} message.
"""

import json
import queue
import secrets
import sqlite3
import time
import traceback
import urllib.parse
from pathlib import Path
from threading import Condition, RLock, Thread

import magpie_paths

DB_PATH = magpie_paths.DIRS.jobs_db

WORKERS = 3

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
FINISHED = (DONE, FAILED)

# A finished job is offered back to a page for this long. Long enough to close
# a tab and come back later; short enough that revisiting a video weeks later
# doesn't greet you with an old result.
REATTACH_WINDOW_SECONDS = 24 * 60 * 60


def job_key(url, playlist):
    """What identifies 'the same stash' across URLs that differ in &t=, &index=
    and so on: the video id, or the playlist id for a playlist stash."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url or "").query)
    field, prefix = ("list", "list:") if playlist else ("v", "video:")
    values = query.get(field)
    return prefix + values[0] if values else "url:" + (url or "")


class JobStore:
    """Jobs on disk plus the live message log of each unfinished job.

    One lock guards both the SQLite connection and the logs, and its condition
    wakes followers when a message arrives."""

    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id         TEXT PRIMARY KEY,
                key        TEXT NOT NULL,
                url        TEXT NOT NULL,
                name       TEXT NOT NULL,
                format     TEXT NOT NULL,
                playlist   INTEGER NOT NULL,
                state      TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS jobs_key ON jobs (key, created_at)")
        self._db.commit()
        self._cond = Condition(RLock())
        # id -> every message since the job was (re)queued. Followers hold a
        # reference to the list, so dropping it from here once the job is done
        # does not cut off anyone still reading it.
        self._live = {}

    def _row(self, job_id):
        return self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    @staticmethod
    def _public(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "url": row["url"],
            "name": row["name"],
            "format": row["format"],
            "playlist": bool(row["playlist"]),
            "state": row["state"],
            "message": json.loads(row["message"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create(self, url, name, format, playlist):
        job_id = secrets.token_hex(8)
        now = time.time()
        first = {"type": "progress", "phase": "queued"}
        with self._cond:
            self._db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, job_key(url, playlist), url, name, format, int(bool(playlist)),
                 QUEUED, json.dumps(first), now, now))
            self._db.commit()
            self._live[job_id] = [first]
        return job_id

    def get(self, job_id):
        with self._cond:
            return self._public(self._row(job_id))

    def mark_running(self, job_id):
        with self._cond:
            self._db.execute("UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?",
                             (RUNNING, time.time(), job_id))
            self._db.commit()

    def add_message(self, job_id, msg):
        """Record one progress or done message and wake every follower."""
        with self._cond:
            done = msg.get("type") == "done"
            if done:
                state = DONE if msg.get("ok") else FAILED
                self._db.execute(
                    "UPDATE jobs SET state = ?, message = ?, updated_at = ? WHERE id = ?",
                    (state, json.dumps(msg), time.time(), job_id))
            else:
                self._db.execute("UPDATE jobs SET message = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(msg), time.time(), job_id))
            self._db.commit()
            log = self._live.setdefault(job_id, [])
            log.append(msg)
            if done:
                del self._live[job_id]
            self._cond.notify_all()

    def follow(self, job_id):
        """Yield the job's latest message, then each new one, ending with done.

        A late follower only needs where the job is now, not its history, so
        it starts from the newest message rather than replaying every percent."""
        with self._cond:
            row = self._row(job_id)
            if row is None:
                return
            log = self._live.get(job_id)
            if log is None:
                latest = json.loads(row["message"])
        if log is None:
            # Finished, or unfinished from before a restart and not requeued
            # yet; either way the saved message is all there is.
            yield latest
            return
        index = max(len(log) - 1, 0)
        while True:
            with self._cond:
                while index >= len(log):
                    self._cond.wait()
                msg = log[index]
            index += 1
            yield msg
            if msg.get("type") == "done":
                return

    def newest_for(self, url):
        """The newest single-video job and the newest playlist job for the page
        at url, each only if it is unfinished or finished recently."""
        cutoff = time.time() - REATTACH_WINDOW_SECONDS
        found = {}
        with self._cond:
            for label, playlist in (("video", False), ("playlist", True)):
                row = self._db.execute(
                    "SELECT * FROM jobs WHERE key = ? AND playlist = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (job_key(url, playlist), int(playlist))).fetchone()
                if row is not None and (row["state"] not in FINISHED or row["updated_at"] >= cutoff):
                    found[label] = self._public(row)
                else:
                    found[label] = None
        return found

    def requeue_unfinished(self):
        """Put jobs left queued or running by a previous run back in the queue.
        Returns their ids, oldest first."""
        first = {"type": "progress", "phase": "queued"}
        with self._cond:
            rows = self._db.execute(
                "SELECT id FROM jobs WHERE state IN (?, ?) ORDER BY created_at",
                (QUEUED, RUNNING)).fetchall()
            ids = [row["id"] for row in rows]
            for job_id in ids:
                self._db.execute(
                    "UPDATE jobs SET state = ?, message = ?, updated_at = ? WHERE id = ?",
                    (QUEUED, json.dumps(first), time.time(), job_id))
                self._live[job_id] = [first]
            self._db.commit()
        return ids


class JobRunner:
    """A fixed pool of worker threads running jobs in the order they arrive."""

    def __init__(self, store, run, log=lambda m: None, workers=WORKERS):
        self._store = store
        self._run = run
        self._log = log
        self._queue = queue.Queue()
        for n in range(workers):
            Thread(target=self._work, name=f"magpie-job-{n}", daemon=True).start()

    def submit(self, job_id):
        self._queue.put(job_id)

    def _work(self):
        while True:
            job_id = self._queue.get()
            job = self._store.get(job_id)
            if job is None or job["state"] in FINISHED:
                continue
            self._store.mark_running(job_id)
            self._log(f"job {job_id}: running {job['url']} as {job['name']}.{job['format']}")
            sent_done = [False]

            def send_progress(msg, job_id=job_id, sent_done=sent_done):
                if sent_done[0]:
                    return
                if msg.get("type") == "done":
                    sent_done[0] = True
                self._store.add_message(job_id, msg)

            try:
                self._run(job, send_progress)
            except Exception as exc:
                self._log(f"job {job_id}: crashed: {exc}\n{traceback.format_exc()}")
                send_progress({"type": "done", "ok": False,
                               "error": f"Magpie hit an internal error: {exc}"})
            if not sent_done[0]:
                send_progress({"type": "done", "ok": False,
                               "error": "The download ended without reporting a result."})
            final = self._store.get(job_id)
            self._log(f"job {job_id}: {final['state'] if final else 'gone'}")
