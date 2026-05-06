#!/usr/bin/env python3
"""PostToolUse for TaskUpdate: clear task ID from pending on ANY status update.

Any TaskUpdate call (in_progress / completed / deleted) means the manager has
acknowledged the task — clear it from the dispatch-pending state so the next
UserPromptSubmit doesn't keep nagging.

Also writes the acknowledgment to ACKED_STATE with a timestamp so that
dispatch-track-pre.py can suppress writes for the same id when TaskUpdate
and Agent are dispatched in parallel (same-batch race: pre may fire AFTER
post, otherwise re-introducing the id we just cleared).
"""
import json
import os
import sys
import time

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

STATE_DIR = os.path.expanduser("~/.claude/hooks/state")
# Window during which a recent ack suppresses a pre-write for the same id.
# Generous because hooks may be queued by the harness; tighten if false-suppress
# becomes a problem.
ACK_TTL_SECONDS = 60


def _session_paths(session_id):
    sid = (session_id or "default").replace("/", "_").replace("\\", "_")
    return (
        f"{STATE_DIR}/pending-tasks-{sid}.txt",
        f"{STATE_DIR}/acked-tasks-{sid}.txt",
    )


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "TaskUpdate":
    sys.exit(0)

STATE, ACKED_STATE = _session_paths(data.get("session_id"))

ti = data.get("tool_input", {})
tid = str(ti.get("taskId", "")).lstrip("#")

if not tid:
    sys.exit(0)

# 1. Clear pending state.
if os.path.exists(STATE):
    with open(STATE) as f:
        lines = [line.strip() for line in f if line.strip()]
    with open(STATE, "w") as f:
        for t in lines:
            if t != tid:
                f.write(t + "\n")

# 2. Record ack with timestamp; prune stale entries.
os.makedirs(os.path.dirname(ACKED_STATE), exist_ok=True)
now = time.time()
acked = {}
if os.path.exists(ACKED_STATE):
    try:
        with open(ACKED_STATE) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) == 2:
                    try:
                        ts = float(parts[1])
                    except ValueError:
                        continue
                    if now - ts <= ACK_TTL_SECONDS:
                        acked[parts[0]] = ts
    except Exception:
        pass
acked[tid] = now
with open(ACKED_STATE, "w") as f:
    for k, ts in acked.items():
        f.write(f"{k}\t{ts}\n")

sys.exit(0)
