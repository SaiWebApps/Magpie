---
name: reference_ibroadcast-library-api
description: iBroadcast library API facts verified 2026-11-10 — works without user.library:read, size is exact upload bytes, path is empty, edits keep original MD5, re-upload replaces by path match
metadata:
  type: reference
---

Verified against the live account on 2026-11-10 (via `fetch_library` in magpie_ibroadcast.py). Used by the project skills in `/Applications/Magpie/.claude/skills/`: `/musicdiff` (read-only diff), `/musicsync` (applies fixes), `/musicdedupe` (deletes duplicate copies):

- `POST https://library.ibroadcast.com` with `mode: library` **works with Magpie's token even though its scope is only `user.account:read user.upload`** (no `user.library:read`). If it ever starts failing with 401/`authenticated: false`, the likely fix is adding `user.library:read` to `SCOPES` and re-running `make ibroadcast-login`.
- Track `size` = exact byte count of the uploaded file. Usable to pair an MD5 with a track.
- Track `path` is empty; Magpie's `file_path` upload field is not stored.
- The checksum index (`POST upload.ibroadcast.com`) is a bare MD5 list with no track ids.
- Metadata edited in iBroadcast's web player does **not** change the MD5 (iBroadcast keeps the original file). Same bytes can therefore show different title/album; e.g. `ByeByeBye.mp3` vs `Marvel_ByeByeBye`.
- Re-uploading a file with the same filename does **not** replace the old copy, despite iBroadcast's docs describing a path-match duplicate check. Seen 2026-11-10: 139 re-uploads left 139 old copies still there 13+ minutes later. `/musicdedupe` deletes them with API `mode: trash` (tracks: [ids]) and then `mode: empty_trash` **with the same `tracks` ids**. Without the ids, `empty_trash` reports "No songs removed". Both work with the upload-scoped token.

**Why ~139 files differ by MD5:** `magpie_server._handle_stash` runs `tag_downloads` (which renumbers the whole album) and then `run_backup(produced)`, which uploads only the new file. The renumbered siblings are never re-uploaded, so iBroadcast keeps stale track numbers.

Related: [[reference_shell-proxy-localhost]].
