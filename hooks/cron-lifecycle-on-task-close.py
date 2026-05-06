#!/usr/bin/env python3
"""PostToolUse(TaskUpdate): advisory nudge to delete liveness cron when
TaskList drains to zero active tasks.

Per manager.md §3.2: completed task → directly TaskUpdate status=deleted.
This hook fires on status=deleted only.

Historically also cleared a per-session flag file written by
cron-lifecycle-on-dispatch.py. That flag was dropped (Task #347) because
it persisted across `/compact` — sid is stable across compaction but the
manager's visible transcript is not, so the flag silenced the dispatch
nudge even after the manager had lost the prior reminder. Dispatch hook
now reads the live transcript instead. We still best-effort delete any
leftover flag file here so old state doesn't linger.
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

if data.get("tool_name") != "TaskUpdate":
    sys.exit(0)

ti = data.get("tool_input", {}) or {}
status = (ti.get("status") or "").lower()
if status != "deleted":
    sys.exit(0)

# Best-effort: clear any legacy cron-fired flag file for this session.
# Flag mechanism is no longer used by the dispatch hook (Task #347),
# but old files may exist on disk; remove them so they don't confuse
# future debugging.
session_id = data.get("session_id") or ""
if session_id:
    flag_path = os.path.expanduser(
        f"~/.claude/hooks/state/cron-fired-{session_id}.flag"
    )
    try:
        os.remove(flag_path)
    except FileNotFoundError:
        pass
    except Exception:
        pass

ctx = (
    "LIVENESS CRON LIFECYCLE: a task was just deleted. If TaskList now "
    "has 0 in_progress + 0 pending tasks, run CronDelete on the liveness "
    "tick. Otherwise keep it running. "
    "(Hook: cron-lifecycle-on-task-close.py)"
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": ctx,
    }
}))
sys.exit(0)
