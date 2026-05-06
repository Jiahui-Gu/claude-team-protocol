#!/usr/bin/env python3
"""UserPromptSubmit: inject reminders for dispatch-tracking state.

Two state files:
- pending-tasks.txt: dispatched tasks that never got TaskUpdate.
- untracked-dispatch.txt: dev-signal Agent calls with no Task #NNN ref
  (ghost dispatches; per feedback_ground_task_before_dispatch.md).

Both surface as additionalContext. Untracked-dispatch state clears after
surfacing (one-shot nag); pending-tasks state clears via dispatch-track-post.py.

Self-heal (Task #43): before emitting DISPATCH DISCIPLINE, scan recent session
jsonl files for the most recent TaskList tool_result and any TaskUpdate calls,
build a {tid: status} map, and drop ids whose authoritative status is
in_progress / completed / deleted from pending-tasks.txt (atomic rewrite).
This kills the false-alarm loop where a TaskUpdate happened in another session
(or was missed by post-hook ordering) and pending state went stale.
"""
import glob
import json
import os
import re
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass  # py < 3.7
if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

STATE_DIR = os.path.expanduser("~/.claude/hooks/state")
PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/*/*.jsonl")
TASKS_DIR = os.path.expanduser("~/.claude/tasks")


def _session_paths(session_id):
    sid = (session_id or "default").replace("/", "_").replace("\\", "_")
    return (
        f"{STATE_DIR}/pending-tasks-{sid}.txt",
        f"{STATE_DIR}/untracked-dispatch-{sid}.txt",
    )


# Read payload up front so we can scope state files to this session.
try:
    _payload = json.load(sys.stdin)
except Exception:
    _payload = {}
STATE, UNTRACKED_STATE = _session_paths(_payload.get("session_id"))

# Statuses that mean "task is no longer pending dispatch — clear from state".
RESOLVED_STATUSES = {"in_progress", "completed", "deleted", "cancelled"}

# How many recent session jsonl files to scan for TaskList / TaskUpdate.
# Limit so we don't read hundreds of MB of transcripts on every prompt.
SCAN_RECENT_N = 8

TASKLIST_LINE_RE = re.compile(r"^#(\d+)\s+\[(\w+)\]")


def _build_status_map():
    """Return {tid (str): status (str)} from recent session jsonl files.

    Strategy: walk the SCAN_RECENT_N most-recently-modified jsonl files. For
    each, collect TaskList tool_use ids, then any tool_result matching those
    ids — parse `#N [status]` lines. Also fold in TaskUpdate tool_input calls
    (taskId + status). Later observations override earlier ones (we walk
    oldest-first within each file, files newest-first overall, so newest
    observation wins by overwriting last).
    """
    status = {}
    try:
        files = glob.glob(PROJECTS_GLOB)
    except Exception:
        return status
    if not files:
        return status
    try:
        files.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        pass
    files = files[:SCAN_RECENT_N]
    # Walk oldest-of-the-recent first so newest writes win.
    for fp in reversed(files):
        try:
            with open(fp, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue
        tl_ids = set()
        for line in lines:
            if "TaskList" not in line and "TaskUpdate" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            content = obj.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                ctype = c.get("type")
                if ctype == "tool_use":
                    name = c.get("name")
                    if name == "TaskList":
                        tid = c.get("id")
                        if tid:
                            tl_ids.add(tid)
                    elif name == "TaskUpdate":
                        ti = c.get("input", {}) or {}
                        tid = str(ti.get("taskId", "")).lstrip("#").strip()
                        st = str(ti.get("status", "")).lower().strip()
                        if tid and st:
                            status[tid] = st
                elif ctype == "tool_result" and c.get("tool_use_id") in tl_ids:
                    txt = c.get("content", "")
                    if not isinstance(txt, str):
                        try:
                            txt = json.dumps(txt)
                        except Exception:
                            continue
                    for tline in txt.split("\n"):
                        m = TASKLIST_LINE_RE.match(tline.strip())
                        if m:
                            status[m.group(1)] = m.group(2).lower()
    return status


def _atomic_rewrite(path, lines):
    """Write `lines` (list[str], no trailing \\n needed) atomically to path."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _task_exists_anywhere(tid):
    """Return True if tid has a task json file in any session's tasks dir.

    Phantom IDs (typos / never-TaskCreated dispatches) won't show up in
    status_map AND won't have a json file — those should be pruned, not
    eternally re-warned.
    """
    try:
        sessions = os.listdir(TASKS_DIR)
    except OSError:
        return True  # Can't tell — assume real, fail safe (keep warning).
    for sid in sessions:
        if os.path.isfile(f"{TASKS_DIR}/{sid}/{tid}.json"):
            return True
    return False


def _self_heal_pending():
    """Drop resolved task ids from pending-tasks.txt. Returns surviving list."""
    if not os.path.exists(STATE):
        return []
    try:
        with open(STATE, encoding="utf-8") as f:
            pending = [l.strip() for l in f if l.strip()]
    except Exception:
        return []
    if not pending:
        return []

    status_map = _build_status_map()

    survivors = []
    for tid in pending:
        st = status_map.get(tid)
        if st in RESOLVED_STATUSES:
            continue  # Resolved — drop.
        if st is None and not _task_exists_anywhere(tid):
            continue  # Phantom — drop (typo / never TaskCreated).
        survivors.append(tid)

    if survivors != pending:
        try:
            _atomic_rewrite(STATE, survivors)
        except Exception:
            pass
    return survivors


messages = []

pending = _self_heal_pending()
if pending:
    ids = ", ".join(f"#{t}" for t in pending)
    messages.append(
        f"DISPATCH DISCIPLINE: tasks {ids} were dispatched via Agent but never marked "
        f"in_progress via TaskUpdate. Update them now (or mark completed if done). "
        f"State auto-clears on TaskUpdate. File: {STATE}"
    )

if os.path.exists(UNTRACKED_STATE):
    try:
        with open(UNTRACKED_STATE, encoding="utf-8") as f:
            ghosts = [l.strip() for l in f if l.strip()]
    except Exception:
        ghosts = []
    if ghosts:
        sample = "; ".join(ghosts[:3])
        more = f" (+{len(ghosts) - 3} more)" if len(ghosts) > 3 else ""
        messages.append(
            f"GHOST DISPATCH: {len(ghosts)} dev-signal Agent call(s) had no 'Task #NNN' in "
            f"first line. Ground every dispatch in a real task (per "
            f"feedback_ground_task_before_dispatch.md). Sample: {sample}{more}. "
            f"State auto-clears now. File: ~/.claude/hooks/state/untracked-dispatch.txt"
        )
    # One-shot: clear after surfacing.
    try:
        os.remove(UNTRACKED_STATE)
    except Exception:
        pass

if not messages:
    sys.exit(0)

ctx = "\n\n".join(messages)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}))
sys.exit(0)
