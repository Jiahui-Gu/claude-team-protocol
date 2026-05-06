#!/usr/bin/env python3
"""PreToolUse for Agent and Bash: precheck list. Each check returns a block
reason string (rejecting the call) or None (passing). On the first failure
the hook exits 2 with that reason on stderr; if all checks pass it exits 0.

Routing (by tool_name):
- Agent: runs AGENT_CHECKS (model, clean_worktree, run_in_background,
  files_section, hotfile_pr_in_flight, task_not_blocked,
  no_git_stash_in_prompt).
- Bash: runs BASH_CHECKS (no_git_stash_in_bash). PreToolUse:Bash already
  has bash-discipline.py wired separately; this entry adds stash-specific
  enforcement that needs the same bypass / messaging vocabulary as the
  Agent-side stash check.
- everything else: pass-through.

Adding a new precheck = write a `check_xxx(data) -> str | None` and append
to the relevant CHECKS list.
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass  # py < 3.7

if "ccsm-probe" in os.getcwd().replace("\\", "/").lower():
    sys.exit(0)


# ---------- checks ----------

def check_model(data):
    """Agent must use model='opus'."""
    model = data.get("tool_input", {}).get("model")
    if model != "opus":
        return (
            f"BLOCKED: Agent must use model='opus' (got {model!r}). "
            "See ~/.claude/skills/team-protocol/references/manager.md §2.3."
        )
    return None


def check_clean_worktree(data):
    """Any prompt with `git reset --hard` must also include `git clean -fd[x]?`.

    Rationale: workers in worktree pools must start from a known-clean state.
    `git reset --hard` does NOT remove untracked files left over from a prior
    worker that started but didn't commit. This caused #553's worker to
    inherit #552's app-effects/.
    """
    prompt = data.get("tool_input", {}).get("prompt", "") or ""
    if not prompt:
        return None
    if not re.search(r"git\s+reset\s+--hard", prompt):
        return None
    if re.search(r"git\s+clean\s+-fd[x]?", prompt):
        return None
    return (
        "BLOCKED: Agent prompt does 'git reset --hard' without 'git clean -fdx'. "
        "Untracked files (from prior worker's incomplete commits) survive reset --hard "
        "and pollute the worktree, causing cross-PR file leaks (e.g. #553 inherited #552's "
        "app-effects/). Add 'git clean -fdx' (or '-fd' to keep node_modules) right after "
        "'git reset --hard origin/working'."
    )


def check_run_in_background(data):
    """Agent must be dispatched with run_in_background=True.

    Foreground agents block the manager session: while the agent runs (often
    minutes to hours), the user cannot send new messages because the REPL is
    busy waiting on the tool result. The manager pattern is fire-and-forget
    parallel dispatch — agents notify on completion, manager stays responsive.

    Two narrow exceptions allow foreground:
      - verifier subagent (name starts with "verifier" OR description contains
        "verify scheduler report"): one-shot read-only probe whose verdict
        manager MUST consume in the same turn to decide §3.1.1 actions.
        Background would force a turn-break before manager can act.
      - explore/research one-shot subagents whose name starts with
        "verify-" or "explore-" follow the same pattern.

    scheduler-tick is NO LONGER an exception (was historically foreground for
    "report becomes next user prompt"; now both modes work and background
    keeps the manager REPL responsive while the tick runs).
    """
    ti = data.get("tool_input", {}) or {}
    name = (ti.get("name") or "").lower()
    desc = (ti.get("description") or "").lower()
    rib = ti.get("run_in_background")

    is_verifier = (
        name.startswith("verifier")
        or name.startswith("verify-")
        or "verify scheduler report" in desc
    )
    if is_verifier:
        return None  # foreground OR background both allowed

    if rib is True:
        return None
    return (
        "BLOCKED: Agent must be dispatched with run_in_background=true. "
        "Foreground agents block the manager REPL until they finish, preventing "
        "the user from sending new messages and other agents from being dispatched "
        "in parallel. Re-call the Agent tool with run_in_background: true. "
        "(Manager pattern: fire-and-forget; agents notify on completion. "
        "Exception: verifier subagents may run foreground — name them `verifier-*` "
        "or set description to `verify scheduler report`.)"
    )


def check_files_section(data):
    """dev dispatch prompt must contain a Files section so scheduler 闸 2
    (hotfile mutex) can grep concrete file paths and decide whether the new
    task collides with in_progress tasks.

    Detection: dispatch-helper.py writes the literal marker `## Task spec`
    at the top of every dev-role prompt (line 150). Reviewer prompts use
    `## Review spec` instead, so they're auto-exempt; scheduler-tick prompts
    have neither, also exempt. This means we never need a per-prefix
    whitelist — the marker IS the role signal.

    Why required: helper-aided survey shows ~0/15 recent tasks ship with
    `**Files**` or `## Files` segment. scheduler.md §2 闸 2 grep was
    silently no-op'ing the entire fleet, falling back to subject string
    guessing — which mis-classified hotfile collisions repeatedly.
    """
    prompt = (data.get("tool_input", {}) or {}).get("prompt", "") or ""
    if not re.search(r"(?:^|\n)## Task spec(?:$|\n)", prompt):
        return None  # not a dev dispatch, skip
    if "**Files**" in prompt or "## Files" in prompt:
        return None
    return (
        "BLOCKED: dev dispatch prompt missing Files section. scheduler 闸 2 "
        "(hotfile mutex) needs concrete paths to detect collisions with in_progress "
        "tasks; without it, scheduler falls back to subject grep and mis-judges. "
        "Add a `## Files` (or `**Files**:`) section to the task description listing "
        "every file the dev will MODIFY/CREATE/DELETE, then re-dispatch via "
        "`python ~/.claude/scripts/dispatch-helper.py --role dev --task-id <id> "
        "--pool <N>`. Example:\n"
        "  ## Files\n"
        "  - packages/daemon/src/pty-host/host.ts (MODIFY)\n"
        "  - packages/daemon/src/pty-host/pty-emitter.ts (NEW)\n"
        "See ~/.claude/skills/team-protocol/references/scheduler.md §2 闸 2."
    )


TASK_DIR = os.path.expanduser("~/.claude/tasks/c0184255-29b7-46d5-a8e9-b82543d4db87")


def _extract_files_section(text):
    """Return set of file paths from a `## Files` or `**Files**` section.

    Matches `- path/to/file (NOTE)` lines until the next `##` heading, the
    next bold header `**Foo**`, or end of text. Returns empty set if no
    section found.
    """
    if not text:
        return set()
    m = re.search(
        r"(?:## Files|\*\*Files\*\*:?)(.*?)(?:\n##|\n\*\*[A-Z]|\Z)",
        text,
        re.S,
    )
    if not m:
        return set()
    files = set()
    for line in m.group(1).splitlines():
        lm = re.match(r"^\s*-\s*([^\s(]+)", line)
        if lm:
            files.add(lm.group(1))
    return files


def check_hotfile_pr_in_flight(data):
    """dev dispatch must not collide with files of any in_progress task.

    Rationale: manager often dispatches the next dev task before the prior
    one's PR has merged. If the new task's `## Files` overlap with an
    in-flight task's files, the second PR will rebase-conflict the first.
    scheduler 闸 2 catches this on its next tick — too late, the dispatch
    already happened. This check enforces the mutex at dispatch time.

    Skipped for reviewer / scheduler-tick (no `## Task spec` marker).
    Bypass via HTML comment `<!-- hotfile-bypass: <reason> -->` for the
    rare legitimate case (e.g. coordinated stacked PRs).
    """
    prompt = (data.get("tool_input", {}) or {}).get("prompt", "") or ""
    if not re.search(r"(?:^|\n)## Task spec(?:$|\n)", prompt):
        return None  # not a dev dispatch
    if "hotfile-bypass" in prompt:
        return None  # human escape hatch

    self_id = None
    sm = re.search(r"^Task #(\d+)\b", prompt, re.MULTILINE)
    if sm:
        self_id = sm.group(1)

    own_files = _extract_files_section(prompt)
    if not own_files:
        return None  # check_files_section will block separately

    if not os.path.isdir(TASK_DIR):
        return None  # no task dir, nothing to compare

    conflicts = []  # list of (task_id, pr_num_or_None, [overlap_files])
    for fname in os.listdir(TASK_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(TASK_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                t = json.load(fh)
        except Exception:
            continue
        if t.get("status") != "in_progress":
            continue
        tid = str(t.get("id") or "")
        if not tid or tid == self_id:
            continue
        other_files = _extract_files_section(t.get("description", "") or "")
        overlap = own_files & other_files
        if overlap:
            md = t.get("metadata") or {}
            pr = md.get("pr") or md.get("pr_number")
            conflicts.append((tid, pr, sorted(overlap)))

    if not conflicts:
        return None

    lines = [
        "BLOCKED: dev dispatch collides with in-flight task(s) on hot file(s).",
        "Overlapping files would cause PR rebase conflicts. Wait for the "
        "in-flight PR(s) to merge, or coordinate stacked PRs.",
        "",
        "Conflicts:",
    ]
    for tid, pr, files in conflicts:
        pr_str = f"PR #{pr}" if pr else "PR NONE"
        lines.append(f"  - Task #{tid} ({pr_str}): {', '.join(files)}")
    lines += [
        "",
        "Bypass (rare, e.g. coordinated stacked PRs): add an HTML comment "
        "`<!-- hotfile-bypass: <reason> -->` anywhere in the dispatch prompt.",
    ]
    return "\n".join(lines)


def check_task_not_blocked(data):
    """dev dispatch must reference a task that is NOT blocked.

    Rationale: manager repeatedly dispatches tasks whose subject contains
    `[blocked]` or whose blockedBy list is non-empty (4-of-4 mis-dispatches
    on 2026-05-05 wave). dev then has to push back, wasting a precious
    Layer-1 round-trip + pool reset.

    Detection: parse first `Task #NNN` from prompt, load
    `~/.claude/tasks/<session>/<NNN>.json`, reject if:
      - `blockedBy` is non-empty AND any blocker is not status=completed/deleted
      - `subject` contains `[blocked]`

    Skipped for non-dev (no `## Task spec` marker). Bypass: add
    `<!-- blocked-bypass: <reason> -->` (e.g. when manager has just confirmed
    the blocker is satisfied but task json hasn't been refreshed yet).
    """
    prompt = (data.get("tool_input", {}) or {}).get("prompt", "") or ""
    if not re.search(r"(?:^|\n)## Task spec(?:$|\n)", prompt):
        return None
    if "blocked-bypass" in prompt:
        return None

    sm = re.search(r"^Task #(\d+)\b", prompt, re.MULTILINE)
    if not sm:
        return None
    tid = sm.group(1)
    path = os.path.join(TASK_DIR, f"{tid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            t = json.load(fh)
    except Exception:
        return None

    subject = t.get("subject", "") or ""
    blocked_by = t.get("blockedBy") or []

    open_blockers = []
    for bid in blocked_by:
        bid_s = str(bid)
        bpath = os.path.join(TASK_DIR, f"{bid_s}.json")
        if not os.path.isfile(bpath):
            # missing json = task deleted (= completed-pruned per task-prune protocol).
            # scheduler-helper 闸 1 视 missing-json 为 unblocked, agent-precheck 必须对齐.
            continue
        try:
            with open(bpath, encoding="utf-8") as bfh:
                bt = json.load(bfh)
        except Exception:
            open_blockers.append((bid_s, "unreadable"))
            continue
        bstatus = (bt.get("status") or "").lower()
        if bstatus not in ("completed", "deleted", "cancelled"):
            open_blockers.append((bid_s, bstatus or "unknown"))

    has_blocked_tag = "[blocked]" in subject.lower()
    if not open_blockers and not has_blocked_tag:
        return None

    lines = [
        f"BLOCKED: Task #{tid} cannot be dispatched — it is still blocked.",
        f"  subject: {subject}",
    ]
    if has_blocked_tag:
        lines.append("  reason: subject contains '[blocked]' tag")
    if open_blockers:
        lines.append("  open blockers (not completed/deleted):")
        for bid, st in open_blockers:
            lines.append(f"    - #{bid} status={st}")
    lines += [
        "",
        "Action: ship the upstream blocker(s) first, then re-run TaskUpdate "
        "to remove the [blocked] tag and refresh blockedBy. Re-dispatch only "
        "after `TaskGet` shows blockedBy empty.",
        "Bypass (rare, e.g. blocker just merged but task json not refreshed): "
        "add `<!-- blocked-bypass: <reason> -->` to the dispatch prompt.",
    ]
    return "\n".join(lines)


def check_no_git_stash_in_prompt(data):
    """Agent dispatch prompt must not instruct the worker to run `git stash`.

    Rationale: `git stash` is repo-level (refs/stash lives in shared .git),
    NOT worktree-level. Across pool-N worktrees, one worker's stash leaks
    into another's `git stash pop`, swallowing untracked dirs. Three
    incidents on record: Task #363 (pool-6 dev #50), Task #429, Task #430.

    Replacement (per dev.md §2):
      git diff > /tmp/task-NNN.patch && git checkout -- .
      # ...later...
      git apply /tmp/task-NNN.patch

    Bypass: add `<!-- stash-bypass: <reason> -->` (e.g. the rare reverse-verify
    4-line sequence dev.md §3 step 5 explicitly grandfathers).
    """
    prompt = (data.get("tool_input", {}) or {}).get("prompt", "") or ""
    if not prompt:
        return None
    if "stash-bypass" in prompt:
        return None
    if not re.search(r"\bgit\s+stash\b", prompt, re.IGNORECASE):
        return None
    return (
        "BLOCKED: dispatch prompt contains 'git stash'. `git stash` is "
        "repo-level (refs/stash shared across worktrees), NOT worktree-level — "
        "pool-A's stash leaks into pool-B's `git stash pop` and swallows "
        "untracked dirs. 3 incidents on record (Task #363 pool-6 dev #50, "
        "Task #429, Task #430).\n"
        "Replace with patch-file flow (per dev.md §2):\n"
        "  git diff > /tmp/task-NNN.patch && git checkout -- .\n"
        "  # ...later...\n"
        "  git apply /tmp/task-NNN.patch\n"
        "Bypass (rare, e.g. reverse-verify 4-line sequence): add "
        "`<!-- stash-bypass: <reason> -->` to the prompt."
    )


def check_no_git_stash_in_bash(data):
    """Bash command must not invoke `git stash` (mutating subcommands).

    Same rationale as check_no_git_stash_in_prompt, but enforced at the
    Bash tool layer. Prompt-side text-grep is necessary-but-insufficient:
    dev workers in pool-N can ALSO run `git stash` directly via Bash
    without it ever appearing in any Agent prompt (Tasks #437, #363, #429,
    #430 all involved direct Bash invocations after dispatch).

    Read-only subcommands are allowed (don't touch refs/stash):
      git stash list, git stash show, git stash --help

    Bypass: set CCSM_STASH_BYPASS=<reason> on the same command line, e.g.
      CCSM_STASH_BYPASS=reverse-verify git stash push -u

    The env-var form is preferred over a flag file because it is strongly
    coupled to the single command and cannot leak to a later invocation.
    """
    cmd = (data.get("tool_input", {}) or {}).get("command", "") or ""
    if not cmd:
        return None
    # Bypass via env var on the command line OR via real shell env (the
    # latter is unusual for hooks but free to honor).
    if "CCSM_STASH_BYPASS=" in cmd or os.environ.get("CCSM_STASH_BYPASS"):
        return None
    # Find any `git stash ...` invocation. Use word boundaries so things
    # like `git-stash-helper` or `gitstash` don't match accidentally.
    matches = list(re.finditer(r"\bgit\s+stash\b([^\n;&|]*)", cmd, re.IGNORECASE))
    if not matches:
        return None
    READ_ONLY_SUBCOMMANDS = {"list", "show"}
    for m in matches:
        rest = m.group(1).strip()
        # `git stash --help` / `git stash -h` is read-only.
        if re.match(r"^(?:--help|-h)\b", rest):
            continue
        # Take the first token after `git stash` as the subcommand.
        first = rest.split()[0] if rest else ""
        # Strip a leading `--` (e.g. `git stash -- list` is technically odd
        # but be permissive).
        first_norm = first.lstrip("-").lower()
        if first_norm in READ_ONLY_SUBCOMMANDS:
            continue
        # Anything else (bare `git stash`, push, save, pop, apply, drop,
        # clear, branch, create, store, ...) is mutating — block.
        return (
            "BLOCKED: Bash command invokes `git stash` (mutating). "
            "`git stash` is repo-level (refs/stash shared across worktrees), "
            "NOT worktree-level — pool-A's stash leaks into pool-B's "
            "`git stash pop` and swallows untracked dirs. 4 incidents on "
            "record (Tasks #363, #429, #430, #437).\n"
            "Replace with patch-file flow (per dev.md §2):\n"
            "  git diff > /tmp/task-NNN.patch && git checkout -- .\n"
            "  # ...later...\n"
            "  git apply /tmp/task-NNN.patch\n"
            "Read-only subcommands are allowed: `git stash list`, "
            "`git stash show`, `git stash --help`.\n"
            "Bypass (rare, e.g. reverse-verify 4-line sequence per dev.md "
            "§3 step 5): prefix with CCSM_STASH_BYPASS=<reason>, e.g.\n"
            "  CCSM_STASH_BYPASS=reverse-verify git stash push -u"
        )
    return None


AGENT_CHECKS = [
    check_model,
    check_clean_worktree,
    check_run_in_background,
    check_files_section,
    check_hotfile_pr_in_flight,
    check_task_not_blocked,
    check_no_git_stash_in_prompt,
]

BASH_CHECKS = [
    check_no_git_stash_in_bash,
]


# ---------- dispatch ----------

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name")
    if tool == "Agent":
        checks = AGENT_CHECKS
    elif tool == "Bash":
        checks = BASH_CHECKS
    else:
        sys.exit(0)

    for check in checks:
        reason = check(data)
        if reason:
            print(reason, file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
