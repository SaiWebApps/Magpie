---
name: feedback_never-shrink-allowlist
description: Never remove entries from ~/.claude-work/apple/dangerous_allowed_domains.csv; the user wants as much allowed as possible
metadata:
  type: feedback
---

On 2026-11-16 I removed four entries (Docker Hub and GitHub Actions log hosts) from `~/.claude-work/apple/dangerous_allowed_domains.csv` as cleanup. The user wants as much allowed as possible and never wants entries removed. I restored them.

**Why:** the allowlist is not clutter to the user; every entry saves a future block. Removing entries only causes repeat failures.

**How to apply:** only ever add to that file (one host per line with a reason column). Never remove or tidy entries, even ones a finished task no longer needs. Cleanup applies to temp files, test artifacts and scratch folders, not to permissions. Related: [[feedback_stop-asking-permission]].
