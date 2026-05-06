#!/usr/bin/env python3
"""UserPromptSubmit hook: one-shot nudge per epoch to ensure liveness cron
is registered on the main session.

Why this exists:
  cron-lifecycle-on-dispatch.py only fires on `Agent + run_in_background=true`,
  i.e. when the manager dispatches a background subagent. But the manager
  often does in-pool work for several turns (edits, PRs, ground-truth checks)
  before dispatching anything. During that window no liveness cron is
  registered, and the user can be left wondering why "the 5-min auto cron
  doesn't seem to fire". Repro on 2026-05-05 (#1092 round-2 + #1093 spec).

Behavior:
  - Fires on every UserPromptSubmit, but is idempotent across an epoch via
    a sentinel string injected into the additionalContext (mirrors the
    cron-lifecycle-on-dispatch.py pattern). If the sentinel is still
    visible in the transcript, stay silent. If it's gone (post-compact or
    fresh session), re-emit.
  - Skips subagent contexts (agent_id present) — only main session.
  - Skips ccsm-probe sandbox dirs.

The nudge tells manager: run CronList; if no `*/5 * * * *` liveness tick is
registered, CronCreate one with the canonical payload from manager.md §3.1.
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Subagent contexts have agent_id / agent_type set; skip them.
if data.get("agent_id") or data.get("agent_type"):
    sys.exit(0)

SENTINEL = "LIVENESS CRON SESSION CHECK (one-shot per epoch)"


def _already_nudged(transcript_path: str) -> bool:
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for chunk in iter(lambda: f.read(65536), ""):
                if SENTINEL in chunk:
                    return True
    except Exception:
        return False
    return False


transcript_path = data.get("transcript_path") or ""
if _already_nudged(transcript_path):
    sys.exit(0)

CANONICAL_PROMPT = (
    "liveness tick. Generate the dispatch JSON via "
    "`python ~/.claude/scripts/dispatch-helper.py --role scheduler-tick` "
    "and feed the fields verbatim into the Agent tool (subagent_type / model / "
    "name / run_in_background / description / prompt). After scheduler returns, "
    "DO NOT mechanically execute — follow manager.md §3.1.1 verifier flow: "
    "if any of Hung / CI fail / Ghost / Auto-dispatch / Unknown sections are "
    "non-empty, dispatch a verifier subagent to ground-truth-check 1-2 key "
    "claims first, then execute only the segments verifier confirms. Pure "
    "all-healthy ticks (all 4 sections empty) may skip verifier. Never ask "
    "the user; if scheduler report is internally inconsistent, dispatch a "
    "research subagent to investigate."
)

ctx = (
    f"{SENTINEL}: epoch start (fresh session OR first prompt after /compact). "
    "Run CronList now. If no `*/5 * * * *` liveness tick is registered AND "
    "TaskList has any in_progress / pending task, CronCreate one (durable=false) "
    "with EXACTLY this payload:\n\n"
    "----- BEGIN CANONICAL LIVENESS PROMPT -----\n"
    + CANONICAL_PROMPT +
    "\n----- END CANONICAL LIVENESS PROMPT -----\n\n"
    "If TaskList is empty (no in_progress / pending), skip — no background work "
    "to monitor. Subsequent prompts in this epoch will be silent — this hook "
    "won't nag again until the sentinel falls out of the visible transcript "
    "(post-compact or new session). "
    "(Hook: cron-lifecycle-on-session-start.py)"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": ctx,
    }
}))
sys.exit(0)
