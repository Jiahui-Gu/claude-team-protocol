#!/usr/bin/env python3
"""Stop hook: enforce that a liveness-tick cron prompt actually dispatched
a scheduler subagent this turn.

Cron payload (per cron-lifecycle-on-dispatch.py CANONICAL_PROMPT) tells the
manager to dispatch a scheduler subagent which then runs §liveness 硬步骤
1-7 inside its own context. This hook only verifies the dispatch happened;
the scheduler subagent's own Stop hook catches a botched scheduler run.

Detection: case-insensitive `liveness tick` AND
(`scheduler.md` OR `scheduler-tick`) in the last user prompt.
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if payload.get("agent_id") or payload.get("agent_type"):
    sys.exit(0)

if payload.get("stop_hook_active"):
    sys.exit(0)

transcript_path = payload.get("transcript_path")
if not transcript_path or not os.path.exists(transcript_path):
    sys.exit(0)

records = []
try:
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
except Exception:
    sys.exit(0)

if not records:
    sys.exit(0)

last_user_text = ""
for i in range(len(records) - 1, -1, -1):
    rec = records[i]
    if rec.get("type") != "user":
        continue
    msg = rec.get("message") or {}
    content = msg.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        has_text = False
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text += (b.get("text") or "")
                has_text = True
        if not has_text:
            continue
    if not text.strip():
        continue
    last_user_text = text
    break

text_lc = last_user_text.lower()
is_tick = "liveness tick" in text_lc and (
    "scheduler.md" in text_lc or "scheduler-tick" in text_lc
)
if not is_tick:
    sys.exit(0)

try:
    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_text = f.read()
except Exception:
    sys.exit(0)

if re.search(r'"name"\s*:\s*"Agent"[^}]*scheduler', transcript_text, re.IGNORECASE | re.DOTALL):
    sys.exit(0)

reason = (
    "LIVENESS TICK INCOMPLETE — cron payload says dispatch a scheduler "
    "subagent but no Agent tool_use mentioning 'scheduler' was found this "
    "turn. Re-do: call Agent(subagent_type=general-purpose, "
    "name=scheduler-tick, run_in_background=false, prompt='Read "
    "~/.claude/skills/team-protocol/references/scheduler.md and run §2 "
    "硬步骤 1-7. Output the report in §2 step 7 format.')"
)
print(json.dumps({"decision": "block", "reason": reason}))
sys.exit(0)
