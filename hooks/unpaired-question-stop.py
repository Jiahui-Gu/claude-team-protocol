#!/usr/bin/env python3
"""Stop hook: BLOCK turn end if assistant's last text ended with an unpaired
question (`?` / `？` at end of last non-empty line) AND the turn did not call
AskUserQuestion AND night-shift is OFF.

Force re-do via {"decision":"block","reason":...}. The agent must re-output
the turn using AskUserQuestion (or rephrase as statement / decision).

Why a Stop hook (not just UserPromptSubmit reminder):
  Post-hoc reminders fire AFTER the user has read the bad turn — too late.
  Stop hook blocks the turn from completing, forcing re-output before user sees it.

False-positive minimization:
  - Only trailing `?`/`？` pattern (high-trust). NO heuristic word matches like
    "要不要" / "是否" / "which one" — those false-positive on declarative
    sentences ("拿速度换稳定" ended with a 要 word once; "选项 X 比 Y 更好"
    type sentences trip "选哪").
  - Skip if last AssistantMessage has tool_use name=AskUserQuestion (the
    question already went through the proper channel).
  - Skip if night-shift flag exists (AskUserQuestion is hard-blocked anyway,
    blocking unpaired-question would create a deadlock).
  - Skip if last text < 20 chars (probably a filler like "好" / "ok").
  - Strip code blocks and quotes before scanning.
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

# Skip ccsm-probe envs.
if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)

# Skip if night-shift is ON: AskUserQuestion is blocked anyway.
night_flag = os.path.expanduser("~/.claude/hooks/state/night-shift.flag")
if os.path.exists(night_flag):
    sys.exit(0)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

transcript_path = payload.get("transcript_path")
if not transcript_path or not os.path.exists(transcript_path):
    sys.exit(0)

# Read transcript, collect records.
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

# Find last top-level assistant message (skip subagent streams).
last_assistant = None
for rec in reversed(records):
    if rec.get("type") != "assistant":
        continue
    if rec.get("agent_id") or rec.get("agent_type"):
        continue
    msg = rec.get("message") or {}
    if msg.get("role") != "assistant":
        continue
    last_assistant = msg
    break

if not last_assistant:
    sys.exit(0)

content = last_assistant.get("content")
if not isinstance(content, list):
    sys.exit(0)

# If turn called AskUserQuestion, the question already went through the
# proper channel — skip.
for block in content:
    if isinstance(block, dict) and block.get("type") == "tool_use":
        if block.get("name") == "AskUserQuestion":
            sys.exit(0)

# Gather text blocks.
text_parts = []
for block in content:
    if not isinstance(block, dict):
        continue
    if block.get("type") == "text":
        t = block.get("text") or ""
        if isinstance(t, str):
            text_parts.append(t)

full_text = "\n".join(text_parts).strip()
if len(full_text) < 20:
    sys.exit(0)


def strip_code_and_quotes(s: str) -> str:
    s = re.sub(r"```.*?```", "", s, flags=re.DOTALL)
    s = re.sub(r"`[^`]*`", "", s)
    s = "\n".join(line for line in s.splitlines() if not line.lstrip().startswith(">"))
    return s


scrub = strip_code_and_quotes(full_text).strip()
if len(scrub) < 20:
    sys.exit(0)

# Last non-empty line.
lines = [ln for ln in scrub.splitlines() if ln.strip()]
if not lines:
    sys.exit(0)

last_line = lines[-1].strip()

# ONLY trailing `?` / `？` (high-trust). Reject if last char is not `?`/`？`.
if not re.search(r"[？?]\s*$", last_line):
    sys.exit(0)

# One more guard: skip rhetorical questions inside markdown headings, e.g.
# "## 为什么这么慢" (no `?`) — already guarded by trailing `?` requirement.
# Also skip if the last line is a markdown heading (`# foo?`) — those are
# usually section markers, not asking the user.
if re.match(r"^\s*#{1,6}\s", last_line):
    sys.exit(0)

# Also skip if the last line begins with a quotation marker (>) — already
# stripped above, but defensive.
if last_line.lstrip().startswith(">"):
    sys.exit(0)

preview = last_line[:200]

reason = (
    "Your turn ended with a plain-text question to the user (last line: "
    f"`{preview}`). Per memory rule feedback_questions_via_askuserquestion.md, "
    "every clarifying question must use the AskUserQuestion tool with 2-4 "
    "concrete options — NOT a trailing `?` in chat. Re-do this turn: either "
    "(a) call AskUserQuestion with the same question + options, or "
    "(b) make an 80/20 manager decision yourself and rephrase as a statement "
    "of what you decided. Do NOT just restate the question without using the tool."
)

print(json.dumps({"decision": "block", "reason": reason}))
sys.exit(0)
