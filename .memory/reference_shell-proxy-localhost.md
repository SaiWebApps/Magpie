---
name: reference_shell-proxy-localhost
description: "Why over-the-wire curl tests against 127.0.0.1 can't be trusted from the Claude shell (forced corporate proxy)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 2be4b1f4-994a-44ac-86bb-ed1fe2244209
---

The Claude Code Bash shell on this machine forces curl through a corporate proxy and
**blocks** both `--noproxy` and any reference to proxy-related env vars (PreToolUse security
hooks reject the command). That proxy buffers and rewrites localhost HTTP responses — e.g. a
plain `curl -i http://127.0.0.1:7865/health` came back with **duplicate `Date:` headers,
reordered `Content-Length`/`Content-Type`, and a lowercase `server:`**, none of which the
Python `http.server` actually emits.

Consequence for debugging Magpie's local server: you **cannot trust shell-curl timing or
streaming observations** against `127.0.0.1` (the proxy may buffer a chunked/SSE stream and
deliver it all at once). My early "curl got nothing for 20–60s" observations were partly this.

The user's browser path (Tampermonkey `GM_xmlhttpRequest` → `127.0.0.1`) does NOT traverse this
proxy — the server log proves browser requests arrive and complete. So **verify streaming/chunked
behavior in the browser**, not via shell curl. Pure-logic unit checks (framing bytes, parsing)
and `make status`/health are still fine. Related: [[project_dev_skill]].
