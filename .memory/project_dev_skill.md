---
name: Development enforcement system
description: Multi-layer defense system built 2026-05-03 — hooks for mechanical enforcement, /retro skill for session-end learning capture, critical rules merged into global CLAUDE.md. Replaced the /dev skill which was redundant.
type: project
originSessionId: 06cf942c-b156-498d-bcf8-8a7a95501ae1
---
## Development Enforcement System (2026-05-03)

Built from 94+ sessions, 61 feedback files, 14 projects. The Ondoway OAuth disaster was the primary driver.

### Architecture (final, after cleanup)

**Layer 1 — Auto-loaded rules (`~/CLAUDE.md`, 379 lines):**
- OAuth/Auth/Deep Links section (FlutterDeepLinkingEnabled, lifecycle async, cold-start deep links, redirect races, end-to-end manual testing)
- Never pivot silently + STOP-when-env-blocked protocol
- Platform & Toolchain Verification (hello-world before real code)
- Mandatory `/retro` before session end

**Layer 2 — PreToolUse hooks (mechanical blocks):**
- `session-preflight.sh` — Injects pre-flight checklist on first tool call of each session
- `platform-guard.sh` — Blocks new platform code until build chain verified with hello-world
- `prevent-laziness.sh` — Blocks raw pytest, stubs, lint suppression, force push
- `mandatory-compliance.sh` — Blocks missing planner, short agent prompts
- `research-before-build.sh` — Blocks new files without researching existing solutions

**Layer 3 — PostToolUse hooks (detection after the fact):**
- `dev-patterns.sh` — Detects skipped tests, lifecycle async, FlutterDeepLinkingEnabled, navigation during build, redirect races, empty SceneDelegates
- `session-retro-reminder.sh` — Reminds to run /retro after git commit/push
- `error-diagnosis/hook.sh` — Forces diagnosis protocol on Bash failures
- `verify-after-edit.sh` — Verification reminders after every edit

**Layer 4 — `/retro` skill (manual, session-end):**
- Catalogs all mistakes against 30 anti-patterns
- Classifies as new or repeat
- Updates CLAUDE.md or LEARNINGS.md
- Creates memory entries

### What was removed
- `/dev` SKILL.md (406 lines) — redundant after merging critical rules into CLAUDE.md. The hook was preserved as `~/.claude-work/hooks/dev-patterns.sh`.
