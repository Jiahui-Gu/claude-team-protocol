#!/usr/bin/env python3
"""PreToolUse for Agent: record `Task #NNN` task IDs from prompt to state file.

Also: if the prompt looks like dev-worker work (DEV_SIGNALS) but lacks a
`Task #NNN` reference in the first line, record it as an untracked dispatch
to a separate state file so the manager gets nudged on next UserPromptSubmit.

Race-safety: when TaskUpdate(in_progress) + Agent(Task #N ...) ship in the
same message, hook ordering between PostToolUse:TaskUpdate and PreToolUse:Agent
is non-deterministic. If post fires first and clears the id, pre would then
re-introduce it — producing a stale "DISPATCH DISCIPLINE" alert on the next
prompt. To prevent that, post writes an ACKED_STATE entry (id + ts), and pre
filters any id seen there within ACK_TTL_SECONDS before writing pending state.
"""
import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass  # py < 3.7

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

STATE_DIR = os.path.expanduser("~/.claude/hooks/state")
ACK_TTL_SECONDS = 60


def _session_paths(session_id):
    sid = (session_id or "default").replace("/", "_").replace("\\", "_")
    return (
        f"{STATE_DIR}/pending-tasks-{sid}.txt",
        f"{STATE_DIR}/untracked-dispatch-{sid}.txt",
        f"{STATE_DIR}/acked-tasks-{sid}.txt",
    )

# DEV_SIGNALS — strong PR-creation intent only. Anything broader (refactor,
# implement, merge, fix pr) over-fires on read-only audits / spec rounds /
# reviewer prompts. See team-protocol skill manager.md §2.3 (every dispatch
# must reference Task #NNN in first line).
DEV_SIGNALS = (
    "gh pr create",
    "open pr",
    "open a pr",
    "push branch",
    "push the branch",
)


def _recently_acked_ids(acked_state):
    """Return set of task ids acked within ACK_TTL_SECONDS."""
    if not os.path.exists(acked_state):
        return set()
    now = time.time()
    out = set()
    try:
        with open(acked_state) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) != 2:
                    continue
                try:
                    ts = float(parts[1])
                except ValueError:
                    continue
                if now - ts <= ACK_TTL_SECONDS:
                    out.add(parts[0])
    except Exception:
        return set()
    return out


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Agent":
    sys.exit(0)

STATE, UNTRACKED_STATE, ACKED_STATE = _session_paths(data.get("session_id"))

prompt = data.get("tool_input", {}).get("prompt", "")
first_line = prompt.split("\n", 1)[0] if prompt else ""
ids = set(re.findall(r"\bTask\s+#(\d+)\b", first_line, re.IGNORECASE))
# Track whether the dispatch was *grounded* in a Task #NNN, independent of
# whether the ack filter later empties `ids`. Otherwise ack-suppression
# (parallel TaskUpdate + Agent in same batch) would mis-route a properly
# grounded dispatch into the ghost-dispatch path.
had_task_ref = bool(ids)

if ids:
    # Filter out ids that were just acknowledged in the same tool batch
    # (parallel TaskUpdate + Agent dispatch). See module docstring.
    acked = _recently_acked_ids(ACKED_STATE)
    ids = {i for i in ids if i not in acked}

if ids:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)

    existing = set()
    if os.path.exists(STATE):
        with open(STATE) as f:
            existing = {line.strip() for line in f if line.strip()}

    with open(STATE, "w") as f:
        for tid in sorted(existing | ids):
            f.write(tid + "\n")
    sys.exit(0)

# No Task #NNN in first line (or all suppressed by recent ack). Check
# ghost-dispatch path: dev signals present but no task reference -> log to
# untracked state (advisory, not blocking).
# But: if the dispatch *was* grounded in Task #NNN and ids only became empty
# via ack-suppression, it is NOT a ghost — skip the whole path.
if had_task_ref:
    sys.exit(0)
plow = (prompt or "").lower()
# Whitelist: dispatches that legitimately have no task ref (read-only audits,
# spec rounds, exploratory subagents). Match anywhere in prompt, not just
# first line. See audit-2026-05-01-persistence.md L-1.
WHITELIST_PHRASES = (
    "daily audit subagent",
    "read-only audit",
    "audit subagent",
    "spec round",
    "exploratory subagent",
)
if any(w in plow for w in WHITELIST_PHRASES):
    sys.exit(0)
if any(s in plow for s in DEV_SIGNALS):
    os.makedirs(os.path.dirname(UNTRACKED_STATE), exist_ok=True)
    fp = first_line.strip()[:120] or "(empty first line)"
    try:
        with open(UNTRACKED_STATE, "a", encoding="utf-8") as f:
            f.write(fp + "\n")
    except Exception:
        pass

sys.exit(0)
