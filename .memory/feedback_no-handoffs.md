---
name: feedback-no-handoffs
description: "Never ask the user to perform deploy steps — do them autonomously (restart servers, bump versions, reload scripts)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 322a350e-5109-4521-9806-54b6ecbef9f9
  modified: 2026-08-27T02:06:29.782Z
---

Never tell the user to do something you can do yourself. Deploy steps (server restarts, version bumps, script reloads) are your job.

**Why:** The user explicitly said "I should not have to do anything. Ever." Repeated violations where Claude told the user to restart the server, reload Tampermonkey, or bump the version after every change.

**How to apply:** After editing project files, perform all deploy steps in the same turn. For Magpie specifically: JS changes are auto-tracked by Tampermonkey (just bump @version); Python changes need `make -C /Applications/Magpie restart` + health check. Report what you did, not what the user needs to do.
