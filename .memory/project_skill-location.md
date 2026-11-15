---
name: project_skill-location
description: Magpie's skills live in the project at /Applications/Magpie/.claude/skills, not in the user-level ~/.claude-work/skills
metadata:
  type: project
---

Skills built for Magpie (musicdiff, musicsync, musicdedupe, and any new ones) live in `/Applications/Magpie/.claude/skills/<name>/`. The user moved them there on 2026-11-10 after they were first created under `~/.claude-work/skills/` ("Why did you place these music skills in claude-work? Move them into this project." / "Move. Not copy").

**Why:** the user wants project tooling inside the project; user-level skills are for things they want in every project.

**How to apply:** create and edit Magpie skills under `.claude/skills/` in this repo. `.claude/` is git-ignored here, so they aren't committed. Related: [[reference_ibroadcast-library-api]].
