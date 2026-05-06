#!/usr/bin/env python3
"""dispatch-helper — generate the full Agent() call JSON for dispatching a
dev / reviewer / scheduler-tick subagent. Manager reads the JSON and feeds
the fields into the Agent tool verbatim.

Why a script: agent-precheck.py blocks dispatches missing model='opus' or
run_in_background or `git clean -fdx`. Manager has historically forgotten
each at least once. By generating the call from a template here, all three
parameters are baked in at the source — the hook becomes a backstop, not the
primary defense.

Usage:
  dispatch-helper.py --task-id 303 --pool 5 --role dev
  dispatch-helper.py --task-id 304 --pool 5 --role reviewer --pr 977
  dispatch-helper.py --role scheduler-tick

Output: a single JSON object on stdout. Example for dev:
  {
    "subagent_type": "general-purpose",
    "model": "opus",
    "name": "dev-303",
    "run_in_background": true,
    "description": "dev #303",
    "prompt": "..."
  }

Manager reads this and calls the Agent tool with these exact fields.
"""
import argparse
import json
import os
import sys
import time
from glob import glob

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def find_taskdir() -> str:
    cands = sorted(
        glob(os.path.expanduser("~/.claude/tasks/*/")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not cands:
        sys.stderr.write("no taskdir found under ~/.claude/tasks/\n")
        sys.exit(2)
    return cands[0]


def load_task(taskdir: str, tid: str) -> dict:
    p = os.path.join(taskdir, f"{tid}.json")
    if not os.path.exists(p):
        sys.stderr.write(f"task {tid}.json not found in {taskdir}\n")
        sys.exit(2)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm_pool(v) -> str | None:
    """Normalize metadata.pool (may be int 5 or str 'pool-5' or '5') to '5'."""
    if v is None:
        return None
    if isinstance(v, int):
        return str(v)
    s = str(v).strip()
    if s.startswith("pool-"):
        s = s[len("pool-"):]
    return s or None


def _find_existing_paired_reviewer(taskdir: str, dev_tid) -> str | None:
    """Return the task id of any existing reviewer task whose blockedBy
    contains dev_tid AND whose subject starts with 'review PR for'. Used to
    skip duplicate paired-reviewer creation on dev re-dispatch."""
    target = str(dev_tid)
    for path in glob(os.path.join(taskdir, "*.json")):
        tid = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8") as f:
                t = json.load(f)
        except Exception:
            continue
        subj = t.get("subject", "")
        if not subj.startswith("review PR for"):
            continue
        blocked_by = t.get("blockedBy") or []
        if any(str(b) == target for b in blocked_by):
            return tid
    return None


def check_pool_conflict(taskdir: str, pool: int, self_tid: str) -> None:
    """Scan all in_progress tasks in taskdir; if any other task already holds
    pool=<pool>, exit non-zero with a stderr report. The dispatching manager
    must then pick a different pool (or wait for the conflicting task to
    finish). This is the primary defense against multi-agent pool collisions
    (see Task #363).
    """
    target = _norm_pool(pool)
    if target is None:
        return
    conflicts = []
    for path in glob(os.path.join(taskdir, "*.json")):
        tid = os.path.splitext(os.path.basename(path))[0]
        if tid == str(self_tid):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                t = json.load(f)
        except Exception:
            continue
        if t.get("status") != "in_progress":
            continue
        md = t.get("metadata") or {}
        held = _norm_pool(md.get("pool"))
        if held == target:
            conflicts.append((tid, md.get("phase") or "NONE",
                              md.get("owner") or "NONE",
                              (t.get("subject") or "")[:60]))
    if conflicts:
        sys.stderr.write(
            f"POOL CONFLICT: pool-{target} is already held by "
            f"{len(conflicts)} in_progress task(s):\n"
        )
        for tid, phase, owner, subj in conflicts:
            sys.stderr.write(
                f"  - Task #{tid} phase={phase} owner={owner} subject={subj}\n"
            )
        sys.stderr.write(
            "Pick a different pool from scheduler-helper's AVAILABLE_POOLS, "
            "or wait for the conflicting task to release the pool. "
            "(Override only with --no-check-pool-conflict if you really know "
            "what you are doing — see Task #363 for why this exists.)\n"
        )
        sys.exit(3)


def short_branch(subject: str, tid: str) -> str:
    """Generate a short branch name from task subject. Keeps it under 50 chars,
    lowercase, kebab-case, prefixed with task id for grep-ability."""
    import re
    s = subject.lower()
    s = re.sub(r"\[[^\]]*\]", "", s)  # strip [TAGS]
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:35].rstrip("-")
    return f"task-{tid}-{s}" if s else f"task-{tid}"


def dev_prompt(task: dict, pool: int) -> str:
    tid = task["id"]
    subject = task.get("subject", "")
    body = task.get("description", "")
    branch = short_branch(subject, tid)
    # Early sanity: dev tasks must declare Files for scheduler 闸 2 (hotfile
    # mutex). agent-precheck.py also enforces this; failing here gives the
    # manager a faster error than waiting for the Agent dispatch hook.
    if "**Files**" not in body and "## Files" not in body:
        sys.stderr.write(
            f"refusing: task #{tid} description missing Files section.\n"
            "Add a `## Files` (or `**Files**:`) block listing every file the dev\n"
            "will MODIFY/CREATE/DELETE, then re-run dispatch-helper. Example:\n"
            "  ## Files\n"
            "  - packages/daemon/src/pty-host/host.ts (MODIFY)\n"
            "  - packages/daemon/src/pty-host/pty-emitter.ts (NEW)\n"
            "Why: scheduler.md §2 闸 2 grep needs concrete paths to detect\n"
            "hotfile collisions with in_progress tasks; without it the gate\n"
            "silently no-ops and mis-classifies conflicts.\n"
        )
        sys.exit(2)
    pool_path = os.path.expanduser(f"~/ccsm-worktrees/pool-{pool}")
    return (
        f"Task #{tid}: {subject}\n\n"
        f"Read ~/.claude/skills/team-protocol/references/dev.md (your role spec).\n\n"
        f"**CRITICAL — pool isolation**: You MUST work in `{pool_path}` (pool-{pool}). The manager has reserved this pool for you and verified it is free. Your initial cwd is whatever the manager was in (often a DIFFERENT pool). You MUST `cd` into the absolute path below as your VERY FIRST shell command, before any git / npm / file operation. Do NOT cd anywhere else. The cd command uses `|| exit 2` so a failed cd terminates you immediately rather than letting you silently corrupt another pool. The previous incident (Task #566 round-1, 2026-05-05) had two parallel devs both end up in pool-9 because cd was treated as optional — one wiped the other's uncommitted work twice.\n\n"
        f"Setup (run first, exact commands — do NOT skip, do NOT reorder, do NOT substitute):\n"
        f"```bash\n"
        f'cd "{pool_path}" || exit 2\n'
        f"pwd  # MUST print {pool_path}\n"
        f"git fetch origin\n"
        f"git reset --hard origin/working\n"
        f"git clean -fdx -e node_modules -e .turbo\n"
        f"git checkout -B {branch}\n"
        f"pnpm install --frozen-lockfile 2>/dev/null || npm install --no-audit --no-fund\n"
        f"```\n\n"
        f"---\n"
        f"## Task spec\n\n"
        f"{body}\n"
    )


def reviewer_prompt(task: dict, pool: int, pr: int | None) -> str:
    tid = task["id"]
    subject = task.get("subject", "")
    body = task.get("description", "")
    pr_ref = f"PR #{pr}" if pr else "the PR linked in the task spec below"
    pool_path = os.path.expanduser(f"~/ccsm-worktrees/pool-{pool}")
    return (
        f"Task #{tid}: {subject}\n\n"
        f"Read ~/.claude/skills/team-protocol/references/reviewer.md (your role spec).\n\n"
        f"You are reviewing {pr_ref} in pool-{pool} (`{pool_path}`).\n"
        f"**Pool isolation**: cd into the absolute path below as your VERY FIRST shell command. The `|| exit 2` makes a failed cd terminate you instead of corrupting another pool.\n\n"
        f"Setup (read-only — do NOT modify the worktree):\n"
        f"```bash\n"
        f'cd "{pool_path}" || exit 2\n'
        f"pwd  # MUST print {pool_path}\n"
        f"gh pr view {pr or '<PR#>'} --repo Jiahui-Gu/ccsm\n"
        f"gh pr diff {pr or '<PR#>'} --repo Jiahui-Gu/ccsm\n"
        f"```\n\n"
        f"---\n"
        f"## Review spec\n\n"
        f"{body}\n"
    )


def current_session_taskdir() -> str | None:
    """Find the current manager session's taskdir by mtime-newest jsonl in
    the projects dir. Why: scheduler-helper.py's `find_taskdir()` picks the
    largest task dir on disk, which is wrong when multiple sessions coexist
    or when a fresh session has few tasks. The manager's own session jsonl
    is being actively written this very moment, so its mtime is always the
    newest — that uuid maps 1:1 to the current taskdir.

    Returns the absolute path to ~/.claude/tasks/<session-uuid>/ if found,
    or None to let scheduler-helper fall back to its own discovery."""
    proj_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(proj_dir):
        return None
    jsonls = glob(os.path.join(proj_dir, "*", "*.jsonl"))
    if not jsonls:
        return None
    newest = max(jsonls, key=os.path.getmtime)
    uuid = os.path.splitext(os.path.basename(newest))[0]
    candidate = os.path.expanduser(f"~/.claude/tasks/{uuid}/")
    if not os.path.isdir(candidate):
        return None
    return candidate


def scheduler_prompt(taskdir: str | None) -> str:
    # If we know the current session's taskdir, hardcode it into the prompt
    # so scheduler's helper call never has to guess. Removes a whole class of
    # "helper picked wrong taskdir" bugs (see Task #9 P1 root cause).
    helper_cmd = "python ~/.claude/scripts/scheduler-helper.py"
    if taskdir:
        # Bash-quote the path safely.
        helper_cmd = (
            f"python ~/.claude/scripts/scheduler-helper.py "
            f"--taskdir '{taskdir}'"
        )
    return (
        f"Read ~/.claude/skills/team-protocol/references/scheduler.md and run "
        f"§2 硬步骤 1-7. Output the report in §2 step 7 format — your final "
        f"message becomes the next user prompt to manager, so be terse and "
        f"action-oriented.\n\n"
        f"Bash #1 use this exact command (taskdir was resolved by manager's "
        f"dispatch-helper from current session jsonl mtime, do not override): "
        f"`{helper_cmd}`"
    )


def main():
    ap = argparse.ArgumentParser(description="generate Agent() dispatch JSON")
    ap.add_argument("--role", required=True,
                    choices=["dev", "reviewer", "scheduler-tick"])
    ap.add_argument("--task-id", help="task id (required for dev/reviewer)")
    ap.add_argument("--pool", type=int, help="pool number (required for dev/reviewer)")
    ap.add_argument("--pr", type=int, help="PR number (reviewer only, optional)")
    ap.add_argument("--taskdir", help="explicit task dir path")
    ap.add_argument(
        "--no-check-pool-conflict",
        dest="check_pool_conflict",
        action="store_false",
        help="DANGEROUS: skip pool-collision check. Default ON. See Task #363.",
    )
    ap.set_defaults(check_pool_conflict=True)
    args = ap.parse_args()

    if args.role == "scheduler-tick":
        # Resolve the manager's current taskdir from session jsonl mtime so
        # scheduler-helper never has to guess (see Task #9 P1 root cause).
        td = current_session_taskdir()
        out = {
            "subagent_type": "general-purpose",
            "model": "opus",
            "name": "scheduler-tick",
            "run_in_background": True,  # all subagents fire-and-forget; manager re-enters cron payload to act on the report
            "description": "scheduler tick",
            "prompt": scheduler_prompt(td),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    # dev / reviewer require task-id + pool
    if not args.task_id or not args.pool:
        sys.stderr.write("--task-id and --pool required for dev/reviewer\n")
        sys.exit(2)

    taskdir = args.taskdir or current_session_taskdir() or find_taskdir()
    task = load_task(taskdir, args.task_id)

    # 闸: pool-conflict gate. Default ON. See Task #363 (multi-agent pool
    # contamination — pool-11/pool-6/pool-14 incidents 2026-05-04).
    if args.check_pool_conflict:
        check_pool_conflict(taskdir, args.pool, args.task_id)

    if args.role == "dev":
        agent_out = {
            "subagent_type": "general-purpose",
            "model": "opus",
            "name": f"dev-{args.task_id}",
            "run_in_background": True,
            "description": f"dev #{args.task_id}",
            "prompt": dev_prompt(task, args.pool),
        }
        # Paired reviewer task: created at the same time as the dev dispatch.
        # blockedBy=[dev_id] keeps it pending until dev pushes the PR; manager
        # then removes the blockedBy and dispatches reviewer. Pre-creating the
        # paired task is the primary defense against "manager forgets to dispatch
        # reviewer after dev finishes" (see Task #8 root cause).
        dev_subject = task.get("subject", "")
        paired = {
            "subject": f"review PR for {dev_subject}",
            "description": (
                f"Review the PR opened by paired dev task #{args.task_id} "
                f"({dev_subject}).\n\n"
                f"This task is blocked by #{args.task_id} until dev pushes its PR. "
                f"After dev pushes, manager:\n"
                f"  1. removes blockedBy=[{args.task_id}] from this task\n"
                f"  2. updates this task's description with PR# (e.g. 'PR #NNN')\n"
                f"  3. dispatches reviewer via "
                f"`python ~/.claude/scripts/dispatch-helper.py --role reviewer "
                f"--task-id <this-id> --pool <same-as-dev> --pr <PR#>`\n\n"
                f"Reviewer reads PR diff, judges APPROVE / REQUEST-CHANGES, "
                f"and on APPROVE merges via "
                f"`gh pr merge <N> --repo Jiahui-Gu/ccsm --squash --delete-branch`."
            ),
            "blockedBy": [str(args.task_id)],
        }
        # Two-section output. Manager parses each section and:
        #   1. TaskCreate the paired reviewer task (use subject/description/blockedBy)
        #   2. TaskUpdate dev task → status=in_progress + metadata
        #   3. Agent dispatch dev (use the AGENT section verbatim)
        # Round 7 G4 fix: emit a third section so manager doesn't have to
        # remember which metadata fields to set on TaskUpdate. Without this,
        # output_file/dispatched_at/phase/pool/owner often get missed →
        # JUST_DISPATCHED carve-out fails (no dispatched_at) and SUSPECT_GHOST
        # via worktree mtime is the only fallback (was the round-7 #385 near-miss).
        # `output_file: PENDING` is a sentinel; manager replaces it with the
        # real .output path once the Agent call returns the agentId.
        task_metadata = {
            "owner": f"dev-{args.task_id}",
            "phase": "coding",
            "pool": int(_norm_pool(args.pool)),
            "output_file": "PENDING",
            "dispatched_at": int(time.time()),
        }
        print("=== TASKUPDATE dev metadata ===")
        print(json.dumps({"taskId": str(args.task_id), "status": "in_progress", "metadata": task_metadata}, indent=2, ensure_ascii=False))
        # Skip TASKCREATE paired-reviewer if one already exists (re-dispatch case
        # — first dispatch already created it; creating again duplicates the
        # reviewer task list). Detect by scanning taskdir for any pending task
        # with blockedBy=[args.task_id] whose subject starts with "review PR for".
        existing_reviewer_id = _find_existing_paired_reviewer(taskdir, args.task_id)
        if existing_reviewer_id:
            print(f"=== TASKCREATE paired-reviewer (SKIPPED — #{existing_reviewer_id} already exists) ===")
        else:
            print("=== TASKCREATE paired-reviewer ===")
            print(json.dumps(paired, indent=2, ensure_ascii=False))
        print("=== AGENT dispatch dev ===")
        print(json.dumps(agent_out, indent=2, ensure_ascii=False))
        return 0
    else:  # reviewer
        out = {
            "subagent_type": "general-purpose",
            "model": "opus",
            "name": f"reviewer-{args.task_id}",
            "run_in_background": True,
            "description": f"reviewer #{args.task_id}",
            "prompt": reviewer_prompt(task, args.pool, args.pr),
        }

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
