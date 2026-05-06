#!/usr/bin/env python3
"""PreToolUse hook for Bash: enforce ccsm dispatch discipline.

Blocks:
- gh pr create without --base working
- cd into ~/ccsm or ~/agentory main repo (worktrees only)

Reads tool input JSON on stdin. Exit 2 + stderr to block.
"""
import json
import os
import re
import sys

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "")
    if not cmd:
        sys.exit(0)

    # Rule 1: gh pr create base/head policy.
    #   ALLOWED: --base working (dev path, default flow per feedback_gh_pr_explicit_base.md)
    #   ALLOWED: --base main --head working (release path: working -> main merge-up,
    #            e.g. PR #632 v0.2.0 release. Without this exception, release worker
    #            falls back to raw `gh api repos/.../pulls` POST. Re-tightening this
    #            will break the release flow - leave the main+working pair allowed.)
    #   BLOCKED: anything else (no --base, --base != working/main, --base main without
    #            --head working, etc.)
    if re.search(r"\bgh\s+pr\s+create\b", cmd):
        has_base_working = bool(re.search(r"(?:--base|-B)\s+working\b", cmd))
        has_base_main = bool(re.search(r"(?:--base|-B)\s+main\b", cmd))
        has_head_working = bool(re.search(r"(?:--head|-H)\s+working\b", cmd))
        release_pair = has_base_main and has_head_working
        if not (has_base_working or release_pair):
            print(
                "BLOCKED: gh pr create must include --base working "
                "(dev path), OR --base main --head working (release merge-up). "
                "See ~/.claude/skills/team-protocol/references/dev.md §3.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Rule 2: there is no main local repo — only worktree pools allowed.
    # Block any cd into ~/ccsm or ~/agentory (legacy paths) or ~/ccsm-research/ccsm.
    # Worktree pool paths (~/ccsm-worktrees/...) and (~/agentory-worktrees/...) are allowed.
    main_repo_re = re.compile(
        r"\bcd\s+[\"']?(?:~|/c/Users/[^/\s]+|\$HOME)/(ccsm|agentory|ccsm-research/ccsm)(?:/[^\s\"']*)?[\"']?(\s|$|;|&|\|)"
    )
    for m in main_repo_re.finditer(cmd):
        full_match = m.group(0)
        if "ccsm-worktrees" in full_match or "agentory-worktrees" in full_match:
            continue
        print(
            f"BLOCKED: refused cd into '{m.group(1)}' (no local main repo exists). "
            "Use ~/ccsm-worktrees/pool-{1..20} instead "
            "(see ~/.claude/skills/team-protocol/references/manager.md §3.3).",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
