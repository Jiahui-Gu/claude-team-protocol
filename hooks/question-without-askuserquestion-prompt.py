#!/usr/bin/env python3
"""UserPromptSubmit: detect if the assistant's last turn (in this same session's
transcript) asked a plain-text question without using AskUserQuestion, and
inject a nudge in the next prompt's context.

This is a single-hook design (no Stop-hook companion, no state file). Reading
the transcript live on each UserPromptSubmit guarantees:
  - no cross-session leakage (we read this session's transcript only)
  - no stale state (no file to clean up)
  - no race between Stop and UserPromptSubmit firing on different sessions

Replaces the prior 2-hook design (question-without-askuserquestion-stop.py
wrote a state file, this hook read+cleared it) which leaked across sessions
because the state file was global, not session-keyed.
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

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

transcript_path = payload.get("transcript_path")
if not transcript_path or not os.path.exists(transcript_path):
    sys.exit(0)

# Walk transcript backwards to find the last assistant message AFTER the
# previous user message (so we look at the assistant's most recent turn that
# the user is now responding to).
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

# Find last assistant message; bail if it's a subagent stream (we only care
# about top-level manager turns).
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

question_patterns = [
    r"[？?]\s*$",
    r"[？?]\s*\n\s*$",
]

last_line = scrub.splitlines()[-1].strip() if scrub.splitlines() else scrub

hit = None
if re.search(r"[？?]\s*$", last_line):
    hit = "trailing-?"
else:
    for pat in question_patterns:
        if re.search(pat, scrub, flags=re.IGNORECASE):
            hit = pat
            break

if not hit:
    sys.exit(0)

preview = (last_line[:200] if last_line else scrub[:200]).strip()

ctx = (
    "UNPAIRED QUESTION: last assistant turn asked the user a question in plain text "
    "instead of using the AskUserQuestion tool. Per memory rule "
    "feedback_questions_via_askuserquestion.md, every clarifying question must go "
    "through AskUserQuestion with 2-4 concrete options. Re-ask via AskUserQuestion now, "
    "or proceed with a manager 80/20 decision if the question is non-blocking.\n"
    f"Detected: pattern={hit}\n"
    f"preview={preview}"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": ctx,
    }
}))

sys.exit(0)
