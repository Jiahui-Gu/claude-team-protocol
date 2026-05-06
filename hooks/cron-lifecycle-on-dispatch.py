#!/usr/bin/env python3
"""PostToolUse(Agent): one-shot nudge to ensure liveness cron is alive
when a background Agent is dispatched.

Per manager.md §3.1, every background worker requires a liveness cron tick.
This hook fires on background-mode Agent dispatch — but only if the manager
hasn't already been nudged in the *currently visible* transcript.

Why we read transcript instead of a session-keyed flag file:
  - `/compact` does NOT change session_id; the old flag file
    (cron-fired-<sid>.flag) would persist across compact and silence the
    hook, but the manager's actual context (the visible transcript) has
    been compressed and lost the prior nudge. Result: 5+ background
    workers dispatched, manager never reminded, CronList stays empty.
    Repro on 2026-05-04 (Task #347).
  - The fix: use the transcript itself as the source of truth. If the
    sentinel string from a prior nudge is still visible in the transcript,
    the manager has the context — stay silent. If it's gone (post-compact
    or never fired), re-emit. Naturally idempotent across compact.

Falls back to firing every time if transcript_path missing (defensive).
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

if data.get("tool_name") != "Agent":
    sys.exit(0)

ti = data.get("tool_input", {}) or {}
if not ti.get("run_in_background"):
    sys.exit(0)

# Sentinel that previous nudges leave behind in the transcript via the
# additionalContext field. Searching for it in the current transcript file
# is our test for "manager already has the reminder in visible context".
SENTINEL = "LIVENESS CRON CHECK (one-shot per epoch)"


def _already_nudged(transcript_path: str) -> bool:
    """Return True if a prior nudge sentinel is still visible in the
    transcript (i.e. survived any compaction). False on any read error or
    when the sentinel cannot be found."""
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    try:
        # Transcripts can be large; do a streaming substring scan rather
        # than parsing every JSONL record. The sentinel is unique enough
        # that a raw substring match is safe.
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
    f"{SENTINEL}: first background Agent of this "
    "epoch dispatched (or first dispatch since /compact). Run CronList now; "
    "if no liveness tick is registered, CronCreate one with schedule "
    "`*/5 * * * *` (durable=false) using EXACTLY this payload:\n\n"
    "----- BEGIN CANONICAL LIVENESS PROMPT -----\n"
    + CANONICAL_PROMPT +
    "\n----- END CANONICAL LIVENESS PROMPT -----\n\n"
    "Subsequent background dispatches in this epoch will be silent — this hook "
    "won't nag again until the sentinel above falls out of the visible transcript "
    "(e.g. post-compact). "
    "(Hook: cron-lifecycle-on-dispatch.py)"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": ctx,
    }
}))
sys.exit(0)
