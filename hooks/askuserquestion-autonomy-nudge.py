#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion): autonomy nudge + night-shift hard block.

Two behaviors:

1. **Night shift (manual switch)**: HARD BLOCK when
   `state/night-shift.flag` exists. Deny message tells the model to either
   self-decide (confident) or dispatch Explore/Plan to gather facts
   (not confident) — never to ask the user. Toggle via `/night on|off`.

2. **Worker(s) dispatched**: soft nudge — if `pending-tasks.txt` is
   non-empty, surface a reminder to consider whether the question is
   genuinely big/uncertain or a small judgment call.

Failing safe: if neither condition fires, hook is a no-op.
"""
import json
import os
import sys

try:
    sys.stdin.reconfigure(encoding="utf-8")
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

if data.get("tool_name") != "AskUserQuestion":
    sys.exit(0)

# --- Night-shift hard block (manual switch via flag file) ---
NIGHT_FLAG = os.path.expanduser("~/.claude/hooks/state/night-shift.flag")
if os.path.exists(NIGHT_FLAG):
    qs = data.get("tool_input", {}).get("questions", []) or []
    preview = ""
    if qs:
        first = qs[0].get("question", "") if isinstance(qs[0], dict) else ""
        preview = (first[:140] + "…") if len(first) > 140 else first

    msg = (
        "NIGHT SHIFT BLOCK (manual switch ON): AskUserQuestion is denied. "
        "User is asleep / not available. "
        f"Cancelled question: {preview!r}.\n"
        "DECISION LADDER (do NOT ask the user):\n"
        "  1. CONFIDENT → pick the option you'd recommend, execute, and tell "
        "the user what you did in your next text response.\n"
        "  2. NOT CONFIDENT → dispatch an Explore/Plan agent to gather the "
        "missing facts, then decide on the returned evidence. Still no "
        "AskUserQuestion. Report decision + evidence to the user.\n"
        "Per feedback_questions_via_askuserquestion.md + "
        "feedback_night_shift_switch.md."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }))
    sys.exit(0)

# --- Soft nudge when workers are pending ---
PENDING = os.path.expanduser("~/.claude/hooks/state/pending-tasks.txt")

active = []
if os.path.exists(PENDING):
    try:
        with open(PENDING, "r", encoding="utf-8") as f:
            active = [l.strip() for l in f if l.strip()]
    except Exception:
        active = []

if not active:
    sys.exit(0)

qs = data.get("tool_input", {}).get("questions", []) or []
preview = ""
if qs:
    first = qs[0].get("question", "") if isinstance(qs[0], dict) else ""
    preview = (first[:140] + "…") if len(first) > 140 else first

ids = ", ".join(f"#{t}" for t in active[:10])
more = f" (+{len(active) - 10} more)" if len(active) > 10 else ""

ctx = (
    "AUTONOMY NUDGE: Worker(s) just dispatched "
    f"({len(active)} pending: {ids}{more}) and you're about to ask the user.\n"
    f"Question: {preview!r}\n"
    "Per feedback_questions_via_askuserquestion.md: manager 自治范围: "
    "派/不派 task / 实现细节 / 80/20 明显的小决策 / 有信心的决策 → 自己拍, 不问。\n"
    "ONLY ask via AskUserQuestion when: 大方向 / 架构 lock / 真正取舍模糊 / 用户偏好.\n"
    "If this question is a small judgment call (e.g. 'should I dispatch X follow-up?', "
    "'merge now?'), CANCEL the AskUserQuestion and just decide. List 清空才停。"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": ctx,
    }
}))
sys.exit(0)
