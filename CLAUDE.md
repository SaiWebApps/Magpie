# Magpie — CLAUDE.md

## Project
YouTube audio/video stash tool. Two components:
- `magpie.user.js` — Tampermonkey userscript (injects download buttons on YouTube)
- `magpie_server.py` — Local HTTP server (handles yt-dlp downloads, streams NDJSON progress)
- `magpie_ibroadcast.py` — iBroadcast backup module
- `magpie_tags.py` — Audio tagging from filenames

## STOP — DEPLOY CHANGES YOURSELF
After editing ANY file in this project, deploy the change. Never ask the user to do it.

- **JS changes (`magpie.user.js`):** Tampermonkey's "Track from disk" is configured to watch
  `/Applications/Magpie/magpie.user.js`. Edits are live in the browser after a page reload.
  Bump `@version` in the header on every change. Nothing else needed.

- **Python changes (`magpie_server.py`, `magpie_ibroadcast.py`, `magpie_tags.py`):** Restart
  the server yourself:
  ```
  make -C /Applications/Magpie restart
  ```
  Then verify it came back:
  ```
  curl -sf http://127.0.0.1:7865/health
  ```

- **Never say** "restart the server", "reload the script", "bump the version", or any variant.
  Do it. Report that you did it.

Violation: told user to restart the server and reload Tampermonkey after every single change.

## MEMORY LOCATION
All memories for this project live in `/Applications/Magpie/.memory/`.
The index is `/Applications/Magpie/.memory/MEMORY.md`.
Always read and write memories there — never in the default `~/.claude-work/projects/` path.

## STOP — FEATURE PARITY
When adding a new UI element (button, panel, control) that parallels an existing one:
1. List every feature the existing element has (format selector, progress display, label updates, error handling, hover states)
2. Present the feature list to the user BEFORE writing code
3. The new element MUST implement every listed feature
4. If a feature is intentionally omitted, state which one and get explicit user approval

Violation: shipped playlist button without format dropdown while the video button had one.

## STOP — BROWSER TEST REQUIRED
Before declaring any UI change "done" or "working":
1. State explicitly: "I have NOT tested this in a browser."
2. Provide a test plan with concrete steps the user can follow
3. Never use the words "working", "done", "complete", or "fixed" for UI code without browser verification
4. Label all UI claims as UNVERIFIED until browser-tested

Violation: reported code as working without ever opening a browser.

## STOP — DESIGN DEVIATION REQUIRES APPROVAL
When the user requests a specific UI pattern (e.g., "dropdown button"):
1. If you plan to implement a DIFFERENT pattern (e.g., "split button + popover"), STOP
2. Describe the deviation: "You asked for X. I plan to build Y instead because [reason]."
3. Wait for explicit approval before writing any code
4. "I think this is better" is NOT approval

Violation: redirected "dropdown button" request to "split button + popover" without asking.

## STOP — STATE ISOLATION
Every new UI component that stores state (localStorage, sessionStorage, cookies, globals):
1. Must use a unique key that includes the component's identity
2. Must NOT reuse any key from another component
3. Before writing the code, list all existing state keys and confirm no collision

Violation: shared localStorage key 'magpie-format' between video and playlist buttons.

## STOP — BATCH EDIT COMMUNICATION
When making more than 3 file edits in sequence:
1. STOP after the 3rd edit
2. Summarize what was changed and what remains
3. Continue only after presenting the summary
4. Never make more than 5 edits without a user-facing status message

Violation: 14 parallel edits with no user communication.
