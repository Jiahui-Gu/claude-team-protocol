#!/usr/bin/env python3
"""scheduler-helper — pre-computes all structured task analytics for the
liveness-tick subagent so it doesn't have to write inline python heredocs.

Output is a flat plain-text report. The subagent reads it as-is and combines
with parallel `stat` + `gh pr checks` results to produce the final tick report.

Sections (each a single line per item, prefixed with the section tag):
  TASKDIR <path>
  COUNTS in_progress=N pending=M total=T
  IN_PROGRESS <id> phase=<phase> pool=<pool> pr=<pr#> output=<path-or-MISSING> subject=<60-char>
  PENDING <id> blockedBy=<csv-or-none> subject=<60-char>
  JUST_DISPATCHED <id> dispatched_at=<unix-ts> age=<sec> ; in_progress task whose output_file is PENDING/MISSING but was dispatched <90s ago — NOT a ghost yet, manager skips (no verifier)
  GHOST <id> reasons=<csv> ; legacy line — only emitted when both SUSPECT/CONFIRMED split is unsafe (defensive fallback)
  SUSPECT_GHOST <id> reasons=<csv> recheck=<shell-cmd> ; only output_file-related reason(s); scheduler MUST run recheck cmd before reporting
  CONFIRMED_GHOST <id> reasons=<csv> ; structural reason present (no-owner/coding-no-pool/ci-wait-no-pr/ci-wait-no-reviewer) OR mixed structural+output_file
  LOW_CONFIDENCE <reason-and-context> ; soft signal scheduler MUST escalate to "Uncertain (NEED VERIFY)" section
  UNBLOCKED <id> ; pending tasks whose every blockedBy is deleted (file missing) → ready to dispatch (闸 1)
  BLOCKED <id> by=<csv-of-still-existing-blocker-ids> ; pending tasks still waiting
  CI_WAIT_PRS <space-separated-pr-numbers> ; one line, for the parallel `gh pr checks` step
  CODING_POOLS <space-separated-pool-numbers> ; one line, for the parallel `stat` step
  CI_PASS_PR <pr#> ; every step passed
  CI_FAIL_PR <pr#> <step_name> <duration_seconds> ; one line per failing step
  CI_PENDING_PR <pr#> <pending_step_count> ; still running
  CI_UNKNOWN_PR <pr#> <reason> ; gh call failed, scheduler subagent should fall back to manual gh
  INFRA_TASK <id> owner=<owner> note=manager-self-dispatched-skip-ghost-check ; manager-dispatched infra task — ghost check intentionally skipped
  MISSING_FILES_SECTION <id> reason=description-has-no-files-block ; UNBLOCKED candidate whose description lacks **Files** / ## Files block (data debt, not a ghost)
  TASK_SIGNALS <id> tags=<csv> ; per-task aggregation of every tag the task picked up this tick (cross-reference index for scheduler evidence)

Ghost OR triggers (per scheduler.md §2.5):
  1. status=in_progress but missing owner (we treat metadata.owner OR pool as proxy)
  2. metadata.output_file missing OR file does not exist on disk
  3. phase=coding but no metadata.pool
  4. phase=ci-wait but no metadata.pr_number

Usage:
  python ~/.claude/scripts/scheduler-helper.py
  python ~/.claude/scripts/scheduler-helper.py --taskdir /explicit/path/

Designed to exit fast (<200 ms after Python cold start) on ~300 tasks.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from glob import glob

# Windows default stdout is cp1252 / cp936; task subjects contain → / 中文 / etc.
# Force utf-8 so the script doesn't crash on encode.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


FRESHNESS_WINDOW_S = 7 * 24 * 3600  # 7 天内有 .json 修改算 active


def _current_session_taskdir() -> str | None:
    """Resolve the manager's *current session* taskdir via projects/*.jsonl
    mtime. Same logic as dispatch-helper.py:current_session_taskdir().

    Why: every other heuristic broke. mtime-newest of taskdir is fooled by
    ScheduleWakeup payload writes; largest-by-count is fooled by sedimented
    historical teams (round-6 #1048/#1085-#1125 phantom: helper picked an
    old 162-task team while manager was on a 20-task team). The manager
    session's jsonl is being written this very second — its mtime is always
    newest — and basename(jsonl)==team uuid, which is also the taskdir name.
    """
    proj_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(proj_dir):
        return None
    jsonls = glob(os.path.join(proj_dir, "*", "*.jsonl"))
    if not jsonls:
        return None
    try:
        newest = max(jsonls, key=os.path.getmtime)
    except OSError:
        return None
    uuid = os.path.splitext(os.path.basename(newest))[0]
    candidate = os.path.expanduser(f"~/.claude/tasks/{uuid}/")
    if not os.path.isdir(candidate):
        return None
    return candidate


def find_taskdir() -> str:
    """Pick the *active* taskdir.

    Order:
      1. Current session jsonl mtime → uuid → taskdir (authoritative).
      2. Filter to *fresh* taskdirs (any *.json modified in last 7 days),
         pick largest by count.
      3. Fall back to largest-overall.

    Hard guard: chosen dir must have ≥ 5 *.json files; else refuse.
    """
    sess = _current_session_taskdir()
    if sess:
        return sess
    candidates = glob(os.path.expanduser("~/.claude/tasks/*/"))
    if not candidates:
        sys.stderr.write("no taskdir found under ~/.claude/tasks/\n")
        sys.exit(2)
    now = time.time()
    scored = []  # (count, freshest_json_mtime, dir)
    for d in candidates:
        jsons = glob(os.path.join(d, "*.json"))
        if not jsons:
            continue
        try:
            freshest = max(os.path.getmtime(j) for j in jsons)
        except OSError:
            continue
        scored.append((len(jsons), freshest, d))
    if not scored:
        sys.stderr.write("no readable taskdir found\n")
        sys.exit(2)
    fresh = [s for s in scored if (now - s[1]) <= FRESHNESS_WINDOW_S]
    pool = fresh if fresh else scored
    pool.sort(key=lambda s: (s[0], s[1]), reverse=True)
    count, _, taskdir = pool[0]
    if count < 5:
        sys.stderr.write(
            f"refusing: chosen taskdir has only {count} *.json files "
            f"(threshold 5). Likely a stale/empty team dir. Pass "
            f"--taskdir explicitly if you really mean this. Candidates: "
            f"{[(c, d) for c, _, d in pool[:3]]}\n"
        )
        sys.exit(2)
    return taskdir


def load_tasks(taskdir: str) -> tuple[dict, dict]:
    """Returns ({task_id: task_dict}, {task_id: file_path}). Skips unreadable / non-JSON files silently."""
    out = {}
    paths = {}
    for path in glob(os.path.join(taskdir, "*.json")):
        tid = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8") as f:
                out[tid] = json.load(f)
            paths[tid] = path
        except Exception:
            continue
    return out, paths


def trim(s: str, n: int = 60) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ")
    return s[:n]


def _normalize_path(p: str) -> str:
    """Convert Git-Bash-style path (/c/Users/...) to Windows native (C:/Users/...).
    Python on Windows only accepts the latter for os.path.lexists/exists.
    Worker subagents sometimes write Git-Bash paths into metadata.output_file
    (e.g. when bash captures `realpath` output). Without normalization, all
    such paths are mis-classified as "gone" and the dev gets ghosted alive
    (caused #357-class false positives on 2026-05-04)."""
    if len(p) >= 4 and p[0] == "/" and p[2] == "/" and p[1].isalpha():
        return p[1].upper() + ":/" + p[3:]
    return p


# ----- pool worktree freshness probe (Task #459) ----------------------------
# History: helper used to LOW_CONFIDENCE-flag pools as "stale (mtime 5000+s)"
# while a verifier subagent's `stat` + `git log` showed the pool was actually
# active <100s ago. Three incidents: #431 (5115s reported / 80s real),
# #443 (4070s reported / idle-but-alive), #428 (566s reported / accurate).
#
# Root cause: `os.path.getmtime("~/ccsm-worktrees/pool-N/")` returns ONLY the
# directory entry's mtime. On Windows that ticks when a child file or dir is
# *added/removed*, NOT when an existing file is modified. Most dev edits are
# in-place writes to existing files (.ts/.tsx/.py), so the worktree top mtime
# stays stale for hours while the pool is hot.
#
# Fix: probe a small set of paths that DO tick on common dev activity and
# take min(now - mtime) — i.e., the freshest signal wins. Paths chosen:
#   - worktree top                   (new file/dir add/remove)
#   - .git/index                     (every `git status` / `git add` / commit refreshes this)
#   - .git/HEAD, .git/logs/HEAD      (commit / checkout / reset)
#   - .git/FETCH_HEAD                (git fetch)
#   - .git/COMMIT_EDITMSG, .git/ORIG_HEAD  (commit / merge / reset)
#
# Note worktree's `.git` is a *file* containing `gitdir: <real-gitdir-path>`,
# pointing into the main repo's `.git/worktrees/<name>/`. Resolve it.
#
# Acceptance (Task #459): a verifier-style probe must agree with this within
# 5s. The recheck_cmd we hand to scheduler subagents below uses the SAME path
# set, so verifier (running in bash) and helper (running in python) read the
# same files and stay in sync.

_POOL_GIT_PROBES = (
    "index",
    "HEAD",
    "logs/HEAD",
    "FETCH_HEAD",
    "COMMIT_EDITMSG",
    "ORIG_HEAD",
)


def _resolve_worktree_gitdir(worktree: str) -> str | None:
    """Read `<worktree>/.git` (a text file in worktrees) and return the real
    gitdir path it points at. Returns None if not a worktree (e.g. main repo
    .git is a directory) or unreadable."""
    git_path = os.path.join(worktree, ".git")
    try:
        st = os.stat(git_path)
    except OSError:
        return None
    if os.path.isdir(git_path):
        # Main repo (not a linked worktree). Just return the dir itself.
        return git_path
    if not os.path.isfile(git_path):
        return None
    try:
        with open(git_path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline().strip()
    except OSError:
        return None
    m = re.match(r"gitdir:\s*(.+)", line)
    if not m:
        return None
    gd = m.group(1).strip()
    # Stored path is usually absolute Windows-style or POSIX; normalize Git-Bash form.
    gd = _normalize_path(gd)
    return gd if os.path.isdir(gd) else None


def pool_freshness_age(worktree: str, now: float | None = None) -> int | None:
    """Return seconds since the *most recent* sign of life in this worktree,
    or None if nothing readable. See _POOL_GIT_PROBES module comment for the
    path set + rationale.

    Returns the MIN age across probes (= freshest signal). A return of e.g.
    45 means "something here was touched 45s ago" — pool is hot. A return of
    5000 means "nothing has changed in this pool for 5000s" — really stale.
    """
    if now is None:
        now = time.time()
    paths = [worktree]
    gd = _resolve_worktree_gitdir(worktree)
    if gd:
        for name in _POOL_GIT_PROBES:
            paths.append(os.path.join(gd, name))
    youngest_mtime: float | None = None
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if youngest_mtime is None or mt > youngest_mtime:
            youngest_mtime = mt
    if youngest_mtime is None:
        return None
    return max(0, int(now - youngest_mtime))


def pool_freshness_recheck_cmd(pool_str: str) -> str:
    """Bash command for a verifier subagent to compute the SAME freshness age
    helper computed. Acceptance #459: helper vs verifier within 5s.

    Emits one `<age>s <path>` line per probe; verifier takes min. Plus
    `git log -1` and `git status -s` for human-readable context (those are
    secondary signals that don't go into the age calculation but help the
    scheduler write the report).
    """
    # Use $HOME (not ~) so the path expands inside double quotes / command
    # substitution. Tilde-quoted-as-literal would break stat.
    wt = f"$HOME/ccsm-worktrees/{pool_str}"
    probes = " ".join(
        [f'"{wt}"']
        + [
            f'"$(sed -n \'s/^gitdir: //p\' "{wt}/.git")/{name}"'
            for name in _POOL_GIT_PROBES
        ]
    )
    return (
        f"now=$(date +%s); for p in {probes}; do "
        f'if [ -e "$p" ]; then mt=$(stat -c %Y "$p" 2>/dev/null); '
        f'[ -n "$mt" ] && echo "$((now-mt))s $p"; fi; done | sort -n | head -3; '
        f'cd "{wt}" && git log --oneline -1 && git status -s | head -5'
    )


# Window (seconds) during which a freshly-dispatched in_progress task is
# allowed to have no output_file on disk. Background agents typically take
# 30-60s to create the .output jsonl after Agent dispatch returns. Without
# this carve-out, helper used to flag every just-dispatched task as
# GHOST/no-output_file → manager mechanically reset = abandoned the live
# agent + task ID was reused. 90s = comfortable upper bound on observed
# create latency. NOT a tunable — keep as constant.
JUST_DISPATCHED_WINDOW_SEC = 90


def dispatched_at(t: dict, path: str | None) -> float | None:
    """Returns Unix epoch seconds when the task was dispatched.

    Source of truth: `metadata.dispatched_at` (set by dispatch-helper).
    If the field is missing / unparseable, return None — DO NOT fall back
    to file mtime. Task json files get rewritten by every TaskUpdate / hook
    long after dispatch, so file mtime ≠ dispatch time. The earlier mtime
    fallback caused spurious JUST_DISPATCHED reports for hours-old tasks
    (Task #470: #463/#457 reported "age=27/35s" while in_progress for
    hours). NOT_JUST is the safe default — at worst we lose the 30-90s
    grace window for one task whose dispatcher forgot to stamp the field.
    """
    md = t.get("metadata") or {}
    ts = md.get("dispatched_at")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return float(ts)
        except ValueError:
            pass
    return None


def is_infra_task(t: dict) -> bool:
    """Manager-self-dispatched INFRA task — short-circuit ghost detection.

    INFRA tasks live outside the dev-/reviewer- naming convention (manager
    runs them itself or hand-dispatches owner=infra-*), so the standard
    ghost heuristics ("no owner = ghost", "coding-no-pool = ghost") wrongly
    report them every tick. False ghost reports here are dangerous because
    manager may reset a live infra agent (#391 incident).

    Trigger on ANY of:
      - metadata.owner starts with "infra-"
      - subject contains "[INFRA]"
      - description contains "[INFRA]"
      - metadata.pool is None AND metadata.phase not in (None, "ci-wait", "review")
        (manager self-dispatched, not in any worker pool, but actively phase-tagged)
    """
    md = t.get("metadata") or {}
    owner = md.get("owner") or ""
    if isinstance(owner, str) and owner.startswith("infra-"):
        return True
    sub = t.get("subject") or ""
    desc = t.get("description") or ""
    if "[INFRA]" in sub or "[INFRA]" in desc:
        return True
    if md.get("pool") is None and md.get("phase") not in (None, "ci-wait", "review"):
        return True
    return False


def is_reviewer_task(task: dict) -> bool:
    """Reviewer task identification:
    - subject 含 "review PR" / "review pr"
    - description 含 "Review the PR opened by paired dev task"
    Reviewer 不改源, 不需要 ## Files 段, 不应报 MISSING_FILES_SECTION.
    """
    subject = (task.get("subject") or "").lower()
    desc = task.get("description") or ""
    if "review pr" in subject:
        return True
    if "Review the PR opened by paired dev task" in desc:
        return True
    return False


# Task #521 — owner / pool resolution lives in TWO places depending on who
# wrote the task json:
#   - dispatch-helper writes metadata.owner / metadata.pool / metadata.phase
#     / metadata.output_file (the "rich" path).
#   - team-protocol skill / TaskCreate flows write **top-level** `owner` like
#     "dev-472-pool13" with NO metadata block at all (the "lean" path).
# Pre-#521 ghost_reasons() only consulted metadata.{owner,pool}, so every lean
# task got reported as no-owner CONFIRMED_GHOST every tick (#472/#501/#502/
# #510/#513/#516/#517/#518/#520/#521 all hit this). Fix: resolve owner from
# top-level OR metadata, and parse pool out of the "dev-NNN-poolM" /
# "reviewer-NNN-poolM" / "infra-NNN-poolM" owner string when metadata.pool
# is absent.
_OWNER_POOL_RE = re.compile(r"-pool(\d+)\b")


def resolve_owner(t: dict) -> str | None:
    """Returns the task owner string (from top-level or metadata), or None."""
    o = t.get("owner")
    if isinstance(o, str) and o:
        return o
    md = t.get("metadata") or {}
    o = md.get("owner")
    if isinstance(o, str) and o:
        return o
    return None


def resolve_pool(t: dict) -> str | None:
    """Returns "pool-N" if discoverable from metadata.pool or owner string."""
    md = t.get("metadata") or {}
    p = md.get("pool")
    if p:
        return str(p) if str(p).startswith("pool-") else f"pool-{p}"
    owner = resolve_owner(t) or ""
    m = _OWNER_POOL_RE.search(owner)
    if m:
        return f"pool-{m.group(1)}"
    return None


def ghost_reasons(t: dict) -> list[str]:
    """Returns list of failed-check tags; empty list = not a ghost."""
    if t.get("status") != "in_progress":
        return []
    # INFRA short-circuit (Task #392) — manager-self-dispatched infra tasks
    # don't follow dev-/reviewer- conventions; skip ghost detection entirely.
    if is_infra_task(t):
        return []
    md = t.get("metadata") or {}

    # Task #470 — ci-wait 豁免. dev push PR 后 dev subagent 早就退出, output_file
    # 是 dev 的 jsonl 早归档/旋转 — task 进入 ci-wait 是正常生命周期不是 ghost。
    # 只要 phase=ci-wait 且 PR 号存在 (兼容历史 metadata.pr 与新 metadata.pr_number
    # 两种字段名 — 多个 in-flight task 写的是裸 `pr`), 直接 NOT_GHOST。
    # 不加这条, helper 会持续把 ci-wait task 报 CONFIRMED_GHOST
    # (no-output_file + ci-wait-no-pr + ci-wait-no-reviewer) 误杀 live PR。
    if md.get("phase") == "ci-wait" and (md.get("pr_number") or md.get("pr")):
        return []

    reasons = []

    # OR-1: in_progress but no owner. Owner can live in TOP-LEVEL `owner`
    # (lean dispatch path: "dev-NNN-poolM") or metadata.{owner,pool} (rich
    # dispatch-helper path). Task #521: pre-fix this only consulted metadata,
    # so 10+ live tasks got false-positive CONFIRMED_GHOST every tick.
    if not resolve_owner(t) and not resolve_pool(t):
        reasons.append("no-owner")

    # OR-2: output_file missing or file gone. Use os.path.lexists() so broken
    # symlinks still count as "present" — Claude Code subagents return .output
    # paths that are symlinks to ~/.claude/projects/.../subagents/<id>.jsonl,
    # and on Windows symlink resolution can flake while the link itself is
    # valid. Also: a sentinel value of "PENDING" (manager-set placeholder for
    # transient ci-fail-fix etc.) is not a real path; skip ghost check for it.
    # And: stored paths can be in Git-Bash style (`/c/Users/...`) or Windows
    # native (`C:\Users\...`); Python's os.path.lexists only accepts the
    # latter on Windows, so normalize first to avoid false-positive ghosts
    # (caused #357-class incidents on 2026-05-04).
    of = md.get("output_file")
    if not of:
        reasons.append("no-output_file")
    elif of == "PENDING":
        pass  # explicit manager-set placeholder, not a ghost trigger
    elif not os.path.lexists(_normalize_path(of)):
        reasons.append("output_file-gone")

    phase = md.get("phase")

    # OR-3: phase=coding but no pool
    if phase == "coding" and not md.get("pool"):
        reasons.append("coding-no-pool")

    # OR-4: phase=ci-wait but no pr_number (accept legacy `pr` field too)
    if phase == "ci-wait" and not (md.get("pr_number") or md.get("pr")):
        reasons.append("ci-wait-no-pr")

    return reasons


# CI parse — fan out `gh pr checks <pr> --json ...` for every ci-wait PR and
# turn the structured output into CI_*_PR lines. Replaces LLM-parsing the
# default `gh pr checks` text output (Task #362 — multiple false positives /
# false negatives observed when the subagent eyeballed the table).
GH_FIELDS = "name,state,bucket,startedAt,completedAt"


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # gh emits RFC3339 like "2026-05-04T12:07:41Z"
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _duration_seconds(started: str | None, completed: str | None) -> int | None:
    a, b = _parse_ts(started), _parse_ts(completed)
    if not a or not b:
        return None
    return max(0, int((b - a).total_seconds()))


def fetch_pr_checks(pr: str) -> tuple[str, list[str]]:
    """Returns (pr, [output_lines]). Each output line is a fully-formed
    CI_*_PR line ready to print. Never raises — gh failure → CI_UNKNOWN_PR."""
    cmd = [
        "gh", "pr", "checks", str(pr),
        "--json", GH_FIELDS,
        "--repo", "Jiahui-Gu/ccsm",
    ]
    try:
        # gh exits non-zero when any check is failing/pending, but still emits
        # valid JSON on stdout — so we only treat "no JSON at all" as unknown.
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return pr, [f"CI_UNKNOWN_PR {pr} subprocess-error:{type(e).__name__}"]

    raw = (proc.stdout or "").strip()
    if not raw:
        # gh prints e.g. "no checks reported on the '<branch>' branch" to stderr
        err = (proc.stderr or "").strip().replace("\n", " ").replace(" ", "_")[:80]
        return pr, [f"CI_UNKNOWN_PR {pr} {err or 'empty-stdout'}"]

    try:
        checks = json.loads(raw)
    except Exception:
        return pr, [f"CI_UNKNOWN_PR {pr} json-parse-error"]

    if not isinstance(checks, list) or not checks:
        return pr, [f"CI_UNKNOWN_PR {pr} no-checks"]

    fails: list[str] = []
    pending = 0
    for c in checks:
        bucket = (c.get("bucket") or "").lower()
        name = (c.get("name") or "?").replace(" ", "_")
        if bucket == "fail":
            dur = _duration_seconds(c.get("startedAt"), c.get("completedAt"))
            dur_disp = str(dur) if dur is not None else "NA"
            fails.append(f"CI_FAIL_PR {pr} {name} {dur_disp}")
        elif bucket == "pending":
            pending += 1
        # pass / skipping / cancel → no line

    if fails:
        return pr, fails
    if pending:
        return pr, [f"CI_PENDING_PR {pr} {pending}"]
    return pr, [f"CI_PASS_PR {pr}"]


def main() -> int:
    ap = argparse.ArgumentParser(description="scheduler liveness-tick helper")
    ap.add_argument("--taskdir", default=None, help="explicit task dir path")
    args = ap.parse_args()

    taskdir = args.taskdir or find_taskdir()
    tasks, task_paths = load_tasks(taskdir)

    in_prog = {tid: t for tid, t in tasks.items() if t.get("status") == "in_progress"}
    pending = {tid: t for tid, t in tasks.items() if t.get("status") == "pending"}

    # Task #392 — TASK_SIGNALS aggregation. Every per-task emit line below
    # also tags `signals[tid]` so we can dump a per-task signal index at the
    # very end (scheduler uses it as evidence cross-reference).
    signals: dict[str, set[str]] = {}

    def tag(tid: str, name: str) -> None:
        signals.setdefault(tid, set()).add(name)

    print(f"TASKDIR {taskdir}")
    print(f"COUNTS in_progress={len(in_prog)} pending={len(pending)} total={len(tasks)}")

    # IN_PROGRESS lines (sorted by id numerically when possible)
    def id_key(tid: str):
        try:
            return (0, int(tid))
        except ValueError:
            return (1, tid)

    for tid in sorted(in_prog, key=id_key):
        t = in_prog[tid]
        md = t.get("metadata") or {}
        of = md.get("output_file")
        if not of:
            of_disp = "MISSING"
        elif of == "PENDING":
            of_disp = "PENDING"
        elif not os.path.lexists(_normalize_path(of)):
            of_disp = "GONE"
        else:
            of_disp = of
        print(
            f"IN_PROGRESS {tid} "
            f"phase={md.get('phase') or 'NONE'} "
            f"pool={md.get('pool') or 'NONE'} "
            f"pr={md.get('pr_number') or md.get('pr') or 'NONE'} "
            f"output={of_disp} "
            f"subject={trim(t.get('subject', ''))}"
        )
        tag(tid, "IN_PROGRESS")
        if (md.get("phase") or "") == "ci-wait":
            tag(tid, "CI_WAIT")

    # PENDING lines
    for tid in sorted(pending, key=id_key):
        t = pending[tid]
        bb = t.get("blockedBy") or []
        bb_csv = ",".join(bb) if bb else "none"
        print(f"PENDING {tid} blockedBy={bb_csv} subject={trim(t.get('subject', ''))}")
        tag(tid, "PENDING")

    # INFRA_TASK lines (Task #392) — emitted BEFORE ghost computation so the
    # scheduler subagent sees them as evidence even though ghost_reasons() has
    # already short-circuited and returned []. Manager-self-dispatched infra
    # tasks intentionally lack dev-/reviewer- conventions; the scheduler must
    # treat these as "skip ghost check, no action required".
    infra_ids: set[str] = set()
    for tid in sorted(in_prog, key=id_key):
        if is_infra_task(in_prog[tid]):
            md = in_prog[tid].get("metadata") or {}
            owner_disp = md.get("owner") or "NONE"
            print(
                f"INFRA_TASK {tid} owner={owner_disp} "
                f"note=manager-self-dispatched-skip-ghost-check"
            )
            tag(tid, "INFRA_TASK")
            infra_ids.add(tid)

    # RECENT_ABORT (Task #394) — manager reset 失败 task 时写
    # metadata.abort_reason; helper 必须看到否则下个 tick 又把它算 UNBLOCKED
    # 让 manager 再派, 易死循环。abort_at 可能缺, 兜底 emit。
    NOW = int(time.time())
    ABORT_TTL_SEC = 600  # 10 min
    for tid in sorted(tasks, key=id_key):
        md = tasks[tid].get("metadata", {}) or {}
        abort_reason = md.get("abort_reason")
        abort_at = md.get("abort_at")
        if not abort_reason:
            continue
        age_sec = (NOW - int(abort_at)) if isinstance(abort_at, (int, float)) else None
        if age_sec is None or age_sec < ABORT_TTL_SEC:
            age_str = f"age={age_sec}s" if age_sec is not None else "age=unknown"
            print(f"RECENT_ABORT {tid} reason={abort_reason!r} {age_str}")
            tag(tid, "RECENT_ABORT")

    # GHOST lines
    # OR-5 needs cross-task scan: pre-compute which ci-wait dev tasks lack
    # a paired reviewer task. paired reviewer task description contains the
    # exact substring "paired dev task #<dev_id>" (written by dispatch-helper).
    # Any ci-wait task without such a sibling = ghost ci-wait-no-reviewer.
    # Manager response: TaskCreate paired reviewer + dispatch immediately.
    ci_wait_dev_ids = {
        tid for tid in in_prog
        if (in_prog[tid].get("metadata") or {}).get("phase") == "ci-wait"
    }
    ci_wait_with_reviewer: set[str] = set()
    if ci_wait_dev_ids:
        for other_tid, other_t in tasks.items():
            # paired reviewer can be in any status (pending after create,
            # in_progress after dispatch, deleted after merge — but deleted
            # tasks aren't in `tasks` anyway since we glob *.json on disk).
            desc = other_t.get("description") or ""
            for dev_id in ci_wait_dev_ids:
                if f"paired dev task #{dev_id}" in desc:
                    ci_wait_with_reviewer.add(dev_id)

    # JUST_DISPATCHED carve-out — for in_progress tasks whose only ghost
    # reason is no-output_file / output_file-gone AND were dispatched
    # <90s ago, emit JUST_DISPATCHED instead of GHOST. Background agents
    # need 30-60s to create the .output jsonl; without this carve-out
    # manager would mechanically reset live agents (see
    # JUST_DISPATCHED_WINDOW_SEC docstring above).
    now = datetime.now().timestamp()
    just_dispatched_ids: set[str] = set()
    for tid in sorted(in_prog, key=id_key):
        rs = ghost_reasons(in_prog[tid])
        # Only the output_file-related reasons get the carve-out; structural
        # ghosts (no-owner / coding-no-pool / ci-wait-no-pr / no-reviewer)
        # are independent of dispatch latency and report immediately.
        of_reasons = {"no-output_file", "output_file-gone"}
        if rs and set(rs).issubset(of_reasons):
            da = dispatched_at(in_prog[tid], task_paths.get(tid))
            if da is not None:
                age = int(now - da)
                if age < JUST_DISPATCHED_WINDOW_SEC:
                    print(f"JUST_DISPATCHED {tid} dispatched_at={int(da)} age={age}")
                    tag(tid, "JUST_DISPATCHED")
                    just_dispatched_ids.add(tid)

    for tid in sorted(in_prog, key=id_key):
        if tid in just_dispatched_ids:
            continue
        rs = ghost_reasons(in_prog[tid])
        # Task #470 — ci-wait+pr 豁免也覆盖 ci-wait-no-reviewer。ghost_reasons()
        # 已对 phase=ci-wait + pr 存在的 task 返 [], 这里若再加 ci-wait-no-reviewer
        # 会把"PR 真在但还没派 reviewer"误报成 ghost (manager 派 reviewer 是
        # 独立闸门, 不应通过 ghost reset 触发)。只在 ghost_reasons 已认为有问题
        # 的 ci-wait task 上叠加 reviewer 缺失信号。
        md_for_cw = in_prog[tid].get("metadata") or {}
        ci_wait_exempt = (
            md_for_cw.get("phase") == "ci-wait"
            and (md_for_cw.get("pr_number") or md_for_cw.get("pr"))
        )
        if (
            tid in ci_wait_dev_ids
            and tid not in ci_wait_with_reviewer
            and not ci_wait_exempt
        ):
            rs.append("ci-wait-no-reviewer")
        if not rs:
            continue
        # Split SUSPECT vs CONFIRMED (Task #388 — scheduler reliability方案 2).
        # output_file-only reasons can race with subagent file creation; scheduler
        # must `ls -la` re-stat to catch the 30-60s window between helper run and
        # subagent report. Structural reasons (no metadata field) cannot be
        # raced — they're config-level missing data, report immediately.
        #
        # Task #528 — `no-owner` demoted from CONFIRMED to SUSPECT class.
        # Reviewer dispatch path historically writes the owner string late
        # (or via a separate TaskUpdate), so the first scheduler tick after
        # dispatch sees the task in_progress with no owner and used to
        # CONFIRM_GHOST → manager would reset a live reviewer. Now: if the
        # only structural reason is no-owner AND the task json was touched
        # recently (worktree mtime <480s as a proxy via task file mtime when
        # no pool is recoverable), demote to SUSPECT and let manager verify.
        # Only worktree-mtime ≥480s + no-owner + no-output_file CONFIRMS.
        of_only_reasons = {"no-output_file", "output_file-gone"}
        demotable_reasons = of_only_reasons | {"no-owner"}
        if set(rs).issubset(demotable_reasons):
            md = in_prog[tid].get("metadata") or {}
            # Task #528 — worktree-mtime <480s = SUSPECT (let manager verify);
            # ≥480s + no-output_file = CONFIRM. Use pool's worktree freshness
            # when discoverable; else fall back to task json file mtime as a
            # last-resort liveness signal (a freshly-dispatched reviewer's
            # task json was just written, so mtime is seconds-old).
            STALE_THRESHOLD_SEC = 480
            pool_str = resolve_pool(in_prog[tid])
            wt_age: int | None = None
            if pool_str is not None:
                wt = os.path.expanduser(f"~/ccsm-worktrees/{pool_str}/")
                wt_age = pool_freshness_age(wt, now=now)
            if wt_age is None:
                # No pool / unreadable worktree — fall back to task json mtime.
                tp = task_paths.get(tid)
                if tp:
                    try:
                        wt_age = max(0, int(now - os.path.getmtime(tp)))
                    except OSError:
                        wt_age = None

            # Sentinel for the MISSING-key case. The current `metadata` dict
            # may not contain the `output_file` key at all (e.g. manager
            # forgot to set it during dispatch); that case has no path to
            # `ls -la`, so SUSPECT recheck is meaningless. Treat as CONFIRMED
            # with explicit `no-path-recorded` reason. Same for explicit empty
            # string. (Task #391 dev only handled "" — MISSING key still went
            # through the SUSPECT path with the placeholder recheck text.)
            of_raw = md.get("output_file", "__MISSING__")
            of_missing = (of_raw == "__MISSING__" or of_raw == "")

            # CONFIRM only when no-owner is present AND worktree definitively
            # stale (≥480s) AND we have no output_file to ls. Otherwise stay
            # SUSPECT. (Pure output_file reasons without no-owner stay SUSPECT
            # regardless of mtime — preserves Task #521 behavior.)
            no_owner_present = "no-owner" in rs
            if (
                no_owner_present
                and wt_age is not None
                and wt_age >= STALE_THRESHOLD_SEC
                and of_missing
            ):
                rs2 = list(rs) + [f"worktree-stale-{wt_age}s"]
                if not pool_str:
                    rs2.append("no-pool-recorded")
                if of_missing:
                    rs2.append("no-path-recorded")
                print(f"CONFIRMED_GHOST {tid} reasons={','.join(rs2)}")
                tag(tid, "CONFIRMED_GHOST")
                continue

            # SUSPECT path — emit recheck cmd. Prefer pool freshness probe
            # (Task #459 same probe set) when pool known; else point at
            # output_file ls; else degenerate to a noop probe note.
            if pool_str is not None:
                recheck_cmd = pool_freshness_recheck_cmd(pool_str)
            elif not of_missing:
                # Quote the path defensively for the recheck cmd; spaces /
                # parens in Windows user-profile paths are common.
                recheck_cmd = f"ls -la '{of_raw}'"
            else:
                recheck_cmd = (
                    f"# no pool/output_file recorded — manager: TaskGet #{tid} "
                    f"and check owner field, or kill if abandoned"
                )
            rs2 = list(rs)
            if of_missing and "no-output_file" in rs2:
                rs2.append("no-path-recorded")
            if not pool_str and "no-owner" in rs2:
                rs2.append("no-pool-recorded")
            age_disp = f" worktree_age={wt_age}s" if wt_age is not None else ""
            print(
                f"SUSPECT_GHOST {tid} reasons={','.join(rs2)}{age_disp} "
                f"recheck={recheck_cmd}"
            )
            tag(tid, "SUSPECT_GHOST")
            continue
        else:
            # Structural reason present (with or without output_file reasons).
            # Conservative: any structural reason → CONFIRMED.
            print(f"CONFIRMED_GHOST {tid} reasons={','.join(rs)}")
            tag(tid, "CONFIRMED_GHOST")

    # LOW_CONFIDENCE soft signals (Task #388 方案 2) — emit when helper has
    # circumstantial evidence of trouble but can't confirm without extra work
    # the scheduler is better positioned to do (Bash/Read). Each line MUST
    # be escalated by scheduler to the "### Uncertain (NEED VERIFY)" section.
    # Currently emitted:
    #   - coding-phase task with stale pool mtime (>=300s) but task still
    #     marked phase=coding (Layer 1 says stale, but maybe phase changed
    #     to ci-wait already and metadata stale).
    #   - ci-wait PR with no log scan done by helper (helper only computes
    #     duration; if a fail step is >=600s, scheduler should grep logs).
    #
    # Note: pool mtime check requires stat; we do it here once for cheap
    # signal. ci-wait-duration LOW_CONFIDENCE is emitted after CI fan-out below.
    now_for_lc = datetime.now().timestamp()
    for tid in sorted(in_prog, key=id_key):
        md = in_prog[tid].get("metadata") or {}
        if md.get("phase") != "coding":
            continue
        pool = md.get("pool")
        if not pool:
            continue
        pool_str = pool if isinstance(pool, str) else f"pool-{pool}"
        wt = os.path.expanduser(f"~/ccsm-worktrees/{pool_str}/")
        # Task #459: use min-across-probes instead of bare top-dir mtime.
        # Top mtime alone falsely flagged hot pools as 5000s+ stale because
        # in-place file edits don't touch the parent dir entry on Windows.
        age = pool_freshness_age(wt, now=now_for_lc)
        if age is not None and age >= 300:
            recheck = pool_freshness_recheck_cmd(pool_str)
            print(
                f"LOW_CONFIDENCE {pool_str} freshness={age}s ago "
                f"(min across worktree+.git probes), but task #{tid} "
                f"metadata.phase=coding (may have moved to ci-wait). "
                f"recheck={recheck}"
            )
            tag(tid, "LOW_CONFIDENCE_POOL_STALE")

    # 闸 1 — UNBLOCKED vs BLOCKED for each pending task.
    # blocker "done" = task missing OR status in {completed, deleted}.
    # blocker "still blocking" = task exists with status pending/in_progress.
    # (Per feedback_task_status_field_authoritative: status field is source of truth,
    #  not file existence — completed tasks are kept on disk for history.)
    open_ids = {tid for tid, t in tasks.items()
                if t.get("status") in ("pending", "in_progress")}
    unblocked_ids: set[str] = set()
    for tid in sorted(pending, key=id_key):
        # Subject [blocked: <reason>] tag check (Task #411).
        # Manager 手动给 subject 加 [blocked: 原因] 表示 dev pushback / 下游
        # dep 才发现的 block, 此时 blockedBy 可能已 stale-clear。
        # 约定: 真 block 必带冒号 "[blocked:" — 裸 "[blocked]" 视作文字描述
        # (例如 #411 自己的 subject 里 "[blocked]" 是描述这个 bug, 非状态)。
        subject = (pending[tid].get("subject") or "").lower()
        if "[blocked:" in subject:
            print(f"BLOCKED_BY_SUBJECT_TAG {tid} subject contains [blocked: ...]")
            tag(tid, "BLOCKED_BY_SUBJECT_TAG")
            continue
        bb = pending[tid].get("blockedBy") or []
        unresolved = [b for b in bb if b in open_ids]
        if not unresolved:
            stale = [(b, tasks[b].get("status", "missing")) for b in bb
                     if b in tasks and tasks[b].get("status") in ("completed", "deleted")]
            if stale:
                tag_str = ",".join(f"{b}:{s}" for b, s in stale)
                print(f"UNBLOCKED {tid} stale_blockers={tag_str}")
                tag(tid, "UNBLOCKED")
                tag(tid, "STALE_BLOCKERS")
            else:
                print(f"UNBLOCKED {tid}")
                tag(tid, "UNBLOCKED")
            unblocked_ids.add(tid)
        else:
            print(f"BLOCKED {tid} by={','.join(unresolved)}")
            tag(tid, "BLOCKED")

    # MISSING_FILES_SECTION (Task #392) — UNBLOCKED candidates whose task
    # description has no `**Files**` / `## Files` block. Not a ghost / not a
    # real Uncertain — flagged as data-debt so manager can patch the Files
    # section in-flight before dispatching (闸 2 hotfile mutex needs it).
    for tid in sorted(unblocked_ids, key=id_key):
        if is_reviewer_task(pending[tid]):
            continue  # reviewer doesn't need Files section
        desc = (pending[tid].get("description") or "")
        if "**Files**" not in desc and "## Files" not in desc:
            print(
                f"MISSING_FILES_SECTION {tid} "
                f"reason=description-has-no-files-block"
            )
            tag(tid, "MISSING_FILES_SECTION")

    # CI_WAIT_PRS — single line for the parallel gh pr checks step
    pr_list = []
    for tid in sorted(in_prog, key=id_key):
        md = in_prog[tid].get("metadata") or {}
        if md.get("phase") == "ci-wait" and (md.get("pr_number") or md.get("pr")):
            pr_list.append(str(md.get("pr_number") or md.get("pr")))
    print(f"CI_WAIT_PRS {' '.join(pr_list) if pr_list else 'NONE'}")

    # CODING_POOLS — single line for the parallel stat step (Layer 1 only).
    # Only coding-phase tasks need stat probing.
    pool_list = []
    for tid in sorted(in_prog, key=id_key):
        md = in_prog[tid].get("metadata") or {}
        if md.get("phase") == "coding" and md.get("pool"):
            pool = md["pool"]
            n = pool.replace("pool-", "") if isinstance(pool, str) else str(pool)
            pool_list.append(n)
    print(f"CODING_POOLS {' '.join(pool_list) if pool_list else 'NONE'}")

    # BUSY_POOLS — every pool currently held by an in_progress task, regardless of phase.
    # ci-wait task still occupies the pool until PR merged (see manager.md §3.3 OCCUPANCY).
    # 闸 3 uses this — available_pools = pool-2..pool-20 − BUSY_POOLS.
    busy = set()
    for tid in in_prog:
        md = in_prog[tid].get("metadata") or {}
        pool = md.get("pool")
        if pool:
            n = pool.replace("pool-", "") if isinstance(pool, str) else str(pool)
            busy.add(n)
    busy_sorted = sorted(busy, key=lambda x: int(x) if x.isdigit() else 999)
    print(f"BUSY_POOLS {' '.join(busy_sorted) if busy_sorted else 'NONE'}")
    print("# DO NOT dispatch into BUSY_POOLS — multi-agent pool collisions"
          " wipe each other's commits / branches (see Task #363). Pick from"
          " AVAILABLE_POOLS only. dispatch-helper.py will hard-reject"
          " --pool=<busy> with exit 3.")

    # AVAILABLE_POOLS — 闸 3 result, ready to dispatch into.
    # Pool range per protocol: pool-2 .. pool-20 (pool-1 is manager's own).
    all_pools = {str(i) for i in range(2, 21)}
    available = sorted(all_pools - busy, key=lambda x: int(x))
    print(f"AVAILABLE_POOLS {' '.join(available) if available else 'NONE'}")

    # CAPACITY — 闸 4 result, max parallel dispatches this tick.
    # 19 - live_in_progress, capped at len(available_pools).
    capacity = max(0, min(19 - len(in_prog), len(available)))
    print(f"CAPACITY {capacity}")

    # CI parse — fan out gh pr checks for every ci-wait PR in parallel.
    # On Windows each gh subprocess startup is ~300ms, so 8 PRs serial = ~2.4s
    # but ThreadPoolExecutor + I/O-bound subprocess wait collapses to ~400ms.
    if pr_list:
        # Cap workers so we don't fork 50 gh's if the queue ever grows huge.
        with ThreadPoolExecutor(max_workers=min(16, len(pr_list))) as ex:
            results = list(ex.map(fetch_pr_checks, pr_list))
        # Preserve input order for deterministic output (ex.map already does,
        # but be explicit so future readers know).
        for _pr, lines in results:
            for line in lines:
                print(line)
                # Task #388 方案 2 — LOW_CONFIDENCE for long-running fail steps.
                # If a CI_FAIL_PR step duration is >=600s, helper hasn't fetched
                # job logs to confirm whether it's an infra timeout vs a real
                # 10-minute test failure. Scheduler should `gh api jobs/<id>/logs
                # | tail -50 | grep -i 'exceeded.*maximum execution'` before
                # writing the infra-timeout hint.
                if line.startswith("CI_FAIL_PR "):
                    parts = line.split()
                    # CI_FAIL_PR <pr> <step_name> <dur>
                    if len(parts) >= 4 and parts[3].isdigit() and int(parts[3]) >= 600:
                        print(
                            f"LOW_CONFIDENCE pr={parts[1]} check={parts[2]} "
                            f"duration={parts[3]}s, but no log scan done "
                            f"(scheduler must grep 'exceeded.*maximum execution' before infra-timeout hint)"
                        )

    # TASK_SIGNALS aggregation (Task #392) — one line per task that has at
    # least one signal, listing every tag the task picked up this tick.
    # Scheduler subagent uses this as the cross-reference index for its
    # "evidence" pointers in the report.
    for tid in sorted(signals, key=id_key):
        tags = sorted(signals[tid])
        if tags:
            print(f"TASK_SIGNALS {tid} tags={','.join(tags)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
