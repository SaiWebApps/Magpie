---
name: feedback_isolate-tests-from-real-files
description: Any code that moves, renames or deletes user files must be proven unable to touch the real home folder from tests or isolated runs before the first test run
metadata:
  type: feedback
---

On 2026-11-16 the phase 2 file-move code (magpie_paths.migrate) took its source folder from the real home (~/.config) even when MAGPIE_HOME pointed a test server at a temp folder. The first test run moved the user's real magpie-ibroadcast.json (iBroadcast login), magpie-ibroadcast-sent.json, -names.json and magpie-jobs.sqlite3 into a test folder, and teardown deleted it. No Time Machine or snapshot existed, so they were unrecoverable; the user had to log in to iBroadcast again.

**Why:** isolation was applied to where files go, not to where they come from. Tests looked isolated but the source side of a move still pointed at real data.

**How to apply:** before running any test of code that moves, renames or deletes files: (1) trace every source and destination path under the test's environment, not just destinations; (2) add a test proving an isolated run touches nothing outside its folder; (3) checksum or list the real files before and after the first run; (4) back up the user's real files before any first run or deploy that migrates data. Related: [[project_desktop-app-plan]].
