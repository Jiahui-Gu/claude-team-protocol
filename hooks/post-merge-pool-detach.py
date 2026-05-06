#!/usr/bin/env python3
"""PostToolUse(Bash): after `gh pr merge`, detach any pool worktree still
checked out on the merged branch. Eliminates the recurring noise:

    failed to delete local branch chore/xxx: failed to run git: error:
    cannot delete branch '...' used by worktree at '~/ccsm-worktrees/pool-N'

Trigger: command contains `gh pr merge` AND tool succeeded. We detach ONLY
pools whose HEAD branch is EXACTLY equal to the merged PR's `headRefName`
(extracted from the command line or tool response). String equality only —
no substring/regex/suffix overlap (incident: PR #619 on `...-889` detached
pool-3 on unrelated `...-894` because the old code fell through to a
"detach any feature branch" path when the regex failed to extract names).

Sanity guards before detaching, even on exact match:
  - `git status --porcelain` must be empty (no uncommitted changes).
  - No file under the worktree (excluding `.git/`) modified within last 60s
    (worker may be mid-Edit/Write — silent detach drops in-flight work).

Stays advisory: prints a summary so manager sees what was detached/skipped.
Exit 0 always.
"""
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

POOL_ROOT = os.path.expanduser("~/ccsm-worktrees")
RECENT_MTIME_WINDOW_SEC = 60


def _extract_merged_branch(cmd: str, resp: str) -> str:
    """Return the exact merged branch name, or empty string if unknown.

    Strategy:
      1. Parse `gh pr merge ... --branch <name>` if present (rare).
      2. Parse `headRefName` from JSON-ish tool response if present.
      3. Match the canonical success line `Merged pull request ... (BRANCH)`.
      4. Fall back to `local branch <name>` (only one — if multiple, ambiguous,
         return empty so we no-op).
    Never returns a guess. Empty string => detach nothing.
    """
    # 2. headRefName from tool_response JSON
    m = re.search(r'"headRefName"\s*:\s*"([^"]+)"', resp)
    if m:
        return m.group(1)

    # 1. explicit --head/--branch on the cmd (rare, but exact)
    m = re.search(r"--head[= ](\S+)", cmd)
    if m:
        return m.group(1)

    # 3. canonical merge confirmation line, e.g. "✓ Merged pull request #619 (chore/foo-889)"
    m = re.search(r"Merged pull request #\d+\s*\(([^)]+)\)", resp)
    if m:
        return m.group(1).strip()

    # 4. Fall back: `local branch <name>` lines in stderr; only trust if unique.
    names = set(re.findall(r"local branch (\S+)", resp))
    if len(names) == 1:
        return next(iter(names))
    return ""


def _worktree_recently_modified(pool: str, window_sec: int) -> str:
    """Return path of first file mtime within window_sec, or empty string."""
    cutoff = time.time() - window_sec
    for root, dirs, files in os.walk(pool):
        # skip .git/ entirely
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getmtime(p) >= cutoff:
                    return p
            except OSError:
                continue
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    cmd = (data.get("tool_input", {}).get("command", "") or "")
    if "gh pr merge" not in cmd:
        sys.exit(0)

    resp = data.get("tool_response", "") or ""
    if isinstance(resp, dict):
        resp = json.dumps(resp)
    resp = str(resp)

    merged_branch = _extract_merged_branch(cmd, resp)
    if not merged_branch:
        # Nothing identifiable to clean — bail out, do NOT detach blindly.
        sys.exit(0)

    actions = []
    warnings = []
    errors = []

    for n in range(1, 21):
        pool = os.path.join(POOL_ROOT, f"pool-{n}")
        if not os.path.isdir(pool):
            continue

        def run(args):
            return subprocess.run(args, cwd=pool, capture_output=True, text=True, timeout=10)

        try:
            br = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
            current = br.stdout.strip()
            if current == "HEAD":
                continue  # already detached
            if current == "working":
                continue  # main branch, leave alone

            # EXACT string equality only — no substring / suffix / regex.
            if current != merged_branch:
                continue

            # Sanity guard 1: dirty worktree => worker mid-edit, skip.
            st = run(["git", "status", "--porcelain"])
            if st.stdout.strip():
                warnings.append(
                    f"pool-{n}: branch matches {merged_branch} but worktree dirty — skipped"
                )
                continue

            # Sanity guard 2: any file mtime within last 60s => worker active.
            recent = _worktree_recently_modified(pool, RECENT_MTIME_WINDOW_SEC)
            if recent:
                warnings.append(
                    f"pool-{n}: branch matches {merged_branch} but recent file activity "
                    f"({os.path.relpath(recent, pool)}) — skipped"
                )
                continue

            # Detach to origin/working
            run(["git", "fetch", "origin", "--quiet"])
            chk = run(["git", "checkout", "--detach", "origin/working"])
            if chk.returncode == 0:
                actions.append(f"pool-{n}: detached from {current}")
                # Now safe to delete the merged feature branch locally
                run(["git", "branch", "-D", merged_branch])
            else:
                errors.append(f"pool-{n}: detach failed: {chk.stderr.strip()}")
        except Exception as e:
            errors.append(f"pool-{n}: {e}")

    if not actions and not warnings and not errors:
        sys.exit(0)

    msg_parts = []
    if actions:
        msg_parts.append("POOL-AUTO-DETACH: " + "; ".join(actions))
    if warnings:
        msg_parts.append("WARNINGS: " + "; ".join(warnings))
    if errors:
        msg_parts.append("ERRORS: " + "; ".join(errors))

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": " | ".join(msg_parts),
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
