---
name: feedback_no-process-residue
description: Never leave process residue in code, docs, or CLAUDE.md — no history of my behavior, reasoning, mistakes, dated observations, or "used to / no longer / now" changelog comments
metadata:
  type: feedback
---

Everything written into the repo (code, comments, docstrings, README, CLAUDE.md, tests) must serve only the product and the humans who read it. Never write the story of how the work happened.

Banned, with real examples I wrote on 2026-11-15:
- Violation/incident notes: "Violation (2026-11-15): reported 2.5.0 as deployed while Tampermonkey still ran 2.4.0…"
- Dated observations: "seen 2026-11-15: listed by 11:33, checksum only later"
- Before/after narration: "A stash used to run inside the HTTP request…", "no longer counts", "now survives the page too", "pre-queue interface"
- Anecdotes of past failures: "Trusting the library alone once put 25 duplicates in it", "(a proxy here duplicated Date before)"

Allowed: what the code does and why, stated in the present tense ("The library lists a new upload minutes before the checksum index does, so the index alone would keep the button waiting").

**Why:** the user considers this a recurring problem: anything in the code that is useless to humans, or that records my behavior, thinking or mistakes, is litter. History belongs in git commits and memory, not in files.

**How to apply:** before every Edit/Write, reread new comments and docstrings and cut any dates, "used to", "now", "no longer", "once", "before", incident stories, or references to my own mistakes. Lessons about my own behavior go in memory files like this one, never in the repo. Related: [[project_desktop-app-plan]].
