"""Tests for scheduler-helper.py — Task #470 regression suite.

Covers two bugs the helper used to have on the live ccsm task pool:

1. ci-wait task with PR was being CONFIRMED_GHOST. Live PR threads
   #1063/#1064/#1061 (tasks #431/#436/#437) carried `metadata.pr` (NOT
   `metadata.pr_number`) and got reported as
   `no-output_file,ci-wait-no-pr,ci-wait-no-reviewer`. helper now exempts
   `phase=ci-wait + (pr_number OR pr)` from ghost reporting entirely.

2. dispatched_at fell back to file mtime when metadata.dispatched_at was
   missing, so any task json touched by an unrelated TaskUpdate looked
   freshly dispatched. Tasks #463 (phase=coding, hours old) and #457
   (phase=blocked-on-fix round=3, days old) got reported JUST_DISPATCHED
   age=27/35s. helper now returns None when the field is absent.

Fixtures replicate the exact metadata observed in
`~/.claude/tasks/c0184255-29b7-46d5-a8e9-b82543d4db87/` on 2026-05-05.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path.home() / ".claude" / "scripts" / "scheduler-helper.py"
assert SCRIPT.is_file(), f"helper script not found at {SCRIPT}"


def _load_helper():
    """Import scheduler-helper.py as a module despite the dash in name."""
    spec = importlib.util.spec_from_file_location("scheduler_helper", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


helper = _load_helper()


# --- Real metadata captured from live tasks on 2026-05-05 -------------------
FIXTURE_431 = {
    "id": "431",
    "subject": "[#230-5] P14b reconcile spec vs test: SETTINGS_SCOPE_PRINCIPAL rejection code",
    "description": "...",
    "status": "in_progress",
    "blockedBy": [],
    "owner": "dev-431-pool6",
    "metadata": {
        "phase": "coding",
        "pool": "pool-6",
        "head": "e458d8d",
        "pr": 1063,
        "round": 2,
        "ci_red": "ch15-§3-enforcement",
    },
}

FIXTURE_436 = {
    "id": "436",
    "subject": "[T8.14b-6] pty-host/sqlite/agent/importScanner sweep + threshold 80",
    "description": "...",
    "status": "in_progress",
    "blockedBy": [],
    "owner": "dev-436-pool7",
    "metadata": {"phase": "ci-wait", "pool": "pool-7", "pr": 1064},
}

FIXTURE_437 = {
    "id": "437",
    "subject": "[T8.14b-5] rpc/ coverage push (≤8 spec) — streaming + error mapping",
    "description": "...",
    "status": "in_progress",
    "blockedBy": [],
    "owner": "dev-437-pool8",
    "metadata": {"phase": "ci-wait", "pool": "pool-8", "head": "e8a6faa", "pr": 1061},
}

FIXTURE_463 = {
    "id": "463",
    "subject": "[P0-SHIP-BLOCKER] daemon SEA exe ...",
    "description": "...",
    "status": "in_progress",
    "blockedBy": [],
    "owner": "dev-463-pool11",
    "metadata": {"phase": "coding", "pool": "pool-11", "ship_blocker": "P0"},
}

FIXTURE_457 = {
    "id": "457",
    "subject": "[DOGFOOD-RESTART] win-msi 重打包 + 装 + 跑 4 项 dogfood 指标",
    "description": "...",
    "status": "in_progress",
    "blockedBy": ["463"],
    "owner": "dev-457-pool9",
    "metadata": {
        "phase": "blocked-on-fix",
        "pool": "pool-9",
        "round": 3,
        "approach": "daemon-exe-direct, skip msi (T7.4-FIX separate)",
    },
}

ALL_FIXTURES = {
    "431": FIXTURE_431,
    "436": FIXTURE_436,
    "437": FIXTURE_437,
    "463": FIXTURE_463,
    "457": FIXTURE_457,
}


# --- ghost_reasons() unit tests ---------------------------------------------

def test_ci_wait_with_pr_field_is_not_ghost():
    """#436 — phase=ci-wait + metadata.pr (legacy field name) → NOT_GHOST."""
    assert helper.ghost_reasons(FIXTURE_436) == []
    assert helper.ghost_reasons(FIXTURE_437) == []


def test_ci_wait_with_pr_number_field_is_not_ghost():
    """Same exemption with the canonical `pr_number` field."""
    t = dict(FIXTURE_436)
    t["metadata"] = dict(t["metadata"])
    del t["metadata"]["pr"]
    t["metadata"]["pr_number"] = 1064
    assert helper.ghost_reasons(t) == []


def test_ci_wait_without_any_pr_is_still_ghost():
    """If neither `pr` nor `pr_number` exists, ci-wait-no-pr still fires."""
    t = dict(FIXTURE_436)
    t["metadata"] = {"phase": "ci-wait", "pool": "pool-7"}  # no pr field at all
    rs = helper.ghost_reasons(t)
    assert "ci-wait-no-pr" in rs


def test_coding_task_with_pool_no_ghost_fields():
    """#431 — phase=coding + pool, has metadata.pr legacy field. The OR-2
    output_file check still fires (no metadata.output_file) but no structural
    ghost — caller path will SUSPECT, not CONFIRM."""
    rs = helper.ghost_reasons(FIXTURE_431)
    # No structural ghosts (no-owner / coding-no-pool / ci-wait-no-pr) —
    # only the soft output_file signal which becomes SUSPECT_GHOST + recheck.
    structural = {"no-owner", "coding-no-pool", "ci-wait-no-pr"}
    assert not (set(rs) & structural), f"unexpected structural ghost reasons: {rs}"


# --- dispatched_at() unit tests ---------------------------------------------

def test_dispatched_at_missing_field_returns_none(tmp_path):
    """#463 / #457 — no metadata.dispatched_at → return None.
    Critical: must NOT fall back to file mtime."""
    t = FIXTURE_463
    f = tmp_path / "463.json"
    f.write_text(json.dumps(t), encoding="utf-8")
    # Touch the file so mtime is "now-ish" — old code would have returned this.
    assert helper.dispatched_at(t, str(f)) is None
    assert helper.dispatched_at(FIXTURE_457, str(f)) is None


def test_dispatched_at_present_field_used():
    """When the field IS set, it is honored (numeric and string)."""
    t = {"metadata": {"dispatched_at": 1777950000}}
    assert helper.dispatched_at(t, None) == 1777950000.0
    t2 = {"metadata": {"dispatched_at": "1777950000.5"}}
    assert helper.dispatched_at(t2, None) == 1777950000.5


def test_dispatched_at_unparseable_returns_none():
    t = {"metadata": {"dispatched_at": "not-a-number"}}
    assert helper.dispatched_at(t, None) is None


# --- end-to-end: run helper as a subprocess on the fixture taskdir ----------

def _write_fixtures(taskdir: Path) -> None:
    for tid, t in ALL_FIXTURES.items():
        (taskdir / f"{tid}.json").write_text(json.dumps(t), encoding="utf-8")


def _run_helper(taskdir: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--taskdir", str(taskdir) + os.sep],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return proc.stdout


def test_e2e_no_confirmed_ghost_on_live_ci_wait(tmp_path):
    """#436/#437 used to be CONFIRMED_GHOST. Now must not appear in any
    CONFIRMED_GHOST line."""
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    for tid in ("436", "437"):
        for line in out.splitlines():
            assert not line.startswith(f"CONFIRMED_GHOST {tid} "), (
                f"task #{tid} still reported CONFIRMED_GHOST: {line}"
            )


def test_e2e_no_just_dispatched_on_old_tasks(tmp_path):
    """#463/#457/#431 lack metadata.dispatched_at — must not be JUST_DISPATCHED.
    Old behavior fell back to file mtime; we just wrote them so mtime is
    seconds-old, but helper must report nothing."""
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    for tid in ("431", "463", "457", "436", "437"):
        for line in out.splitlines():
            assert not line.startswith(f"JUST_DISPATCHED {tid} "), (
                f"task #{tid} still reported JUST_DISPATCHED: {line}"
            )


def test_e2e_ci_wait_prs_emitted(tmp_path):
    """CI_WAIT_PRS line must include 1064 and 1061 (from #436/#437 `pr` field)."""
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    ci_lines = [l for l in out.splitlines() if l.startswith("CI_WAIT_PRS ")]
    assert ci_lines, "CI_WAIT_PRS line missing"
    body = ci_lines[0].removeprefix("CI_WAIT_PRS ")
    prs = set(body.split())
    assert "1064" in prs, f"pr 1064 (task #436) missing from CI_WAIT_PRS: {body}"
    assert "1061" in prs, f"pr 1061 (task #437) missing from CI_WAIT_PRS: {body}"


def test_e2e_in_progress_pr_field_displayed(tmp_path):
    """IN_PROGRESS line must show pr=<num> not pr=NONE for tasks with legacy
    `pr` metadata field."""
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    for tid, want_pr in [("431", "1063"), ("436", "1064"), ("437", "1061")]:
        line = next(
            (l for l in out.splitlines() if l.startswith(f"IN_PROGRESS {tid} ")),
            None,
        )
        assert line, f"IN_PROGRESS {tid} line missing"
        assert f"pr={want_pr}" in line, (
            f"task #{tid} IN_PROGRESS line missing pr={want_pr}: {line}"
        )


# --- Task #521 regression: top-level owner field ---------------------------
#
# The lean dispatch path (team-protocol skill / TaskCreate flows) writes the
# task with a top-level `owner` like "dev-472-pool13" and NO metadata block.
# Pre-#521 helper only consulted metadata.{owner,pool}, so every such task
# was reported `no-owner` CONFIRMED_GHOST every tick. False positives observed
# on live tasks #472/#501/#502/#510/#513/#516/#517/#518/#520/#521.

# Real shape of #472.json on disk on 2026-05-05 (no metadata key at all):
FIXTURE_472 = {
    "id": "472",
    "subject": "[T8.14b-7c] daemon coverage: src/rpc/ ...",
    "description": "## Files\n- packages/daemon/src/rpc/*.ts (READ)\n",
    "status": "in_progress",
    "blocks": [],
    "blockedBy": [],
    "owner": "dev-472-pool13",
}


def test_resolve_owner_top_level():
    assert helper.resolve_owner(FIXTURE_472) == "dev-472-pool13"


def test_resolve_owner_metadata_fallback():
    t = {"owner": None, "metadata": {"owner": "dev-99-pool3"}}
    assert helper.resolve_owner(t) == "dev-99-pool3"


def test_resolve_owner_none():
    assert helper.resolve_owner({"metadata": {}}) is None
    assert helper.resolve_owner({}) is None


def test_resolve_pool_from_owner_string():
    """Lean tasks: pool extracted from "dev-NNN-poolM" owner."""
    assert helper.resolve_pool(FIXTURE_472) == "pool-13"
    assert helper.resolve_pool({"owner": "reviewer-518-pool15"}) == "pool-15"
    assert helper.resolve_pool({"owner": "infra-521-pool16"}) == "pool-16"


def test_resolve_pool_from_metadata_preferred():
    """metadata.pool wins if both present (dispatch-helper authoritative)."""
    t = {"owner": "dev-1-pool99", "metadata": {"pool": "pool-7"}}
    assert helper.resolve_pool(t) == "pool-7"


def test_resolve_pool_none():
    assert helper.resolve_pool({"owner": "manager"}) is None
    assert helper.resolve_pool({}) is None


def test_top_level_owner_does_not_trigger_no_owner_ghost():
    """Task #521 — the core fix. top-level owner alone (no metadata) must
    NOT produce `no-owner` reason. (output_file-related reasons may still
    appear; the SUSPECT vs CONFIRMED split is verified separately.)"""
    rs = helper.ghost_reasons(FIXTURE_472)
    assert "no-owner" not in rs, f"top-level owner should suppress no-owner: {rs}"


def test_genuinely_no_owner_still_ghost():
    """Negative control — truly ownerless in_progress task IS still a ghost."""
    t = {"id": "999", "status": "in_progress", "owner": None, "metadata": {}}
    rs = helper.ghost_reasons(t)
    assert "no-owner" in rs, f"genuine no-owner should still ghost: {rs}"


def test_owner_empty_string_still_ghost():
    """Empty-string owner = no owner."""
    t = {"id": "999", "status": "in_progress", "owner": "", "metadata": {}}
    assert "no-owner" in helper.ghost_reasons(t)


def test_e2e_lean_owner_not_confirmed_ghost(tmp_path):
    """End-to-end: write #472 fixture, run helper, must NOT be CONFIRMED_GHOST.
    (SUSPECT_GHOST with recheck pointing at pool-13 is OK — that's the
    correct conservative behavior when metadata.output_file is missing.)"""
    (tmp_path / "472.json").write_text(json.dumps(FIXTURE_472), encoding="utf-8")
    # Need a few more files to clear find_taskdir's >=5 *.json hard guard.
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    for line in out.splitlines():
        assert not line.startswith("CONFIRMED_GHOST 472 "), (
            f"task #472 (lean owner) still CONFIRMED_GHOST: {line}"
        )
        assert not line.startswith("GHOST 472 "), (
            f"task #472 (lean owner) reported as legacy GHOST: {line}"
        )
    # Positive: helper must include 472 in IN_PROGRESS section.
    assert any(l.startswith("IN_PROGRESS 472 ") for l in out.splitlines())


def test_e2e_lean_owner_suspect_recheck_uses_correct_pool(tmp_path):
    """SUSPECT_GHOST recheck for #472 must reference pool-13 (extracted from
    the top-level owner string). Pre-#521 fallback would have CONFIRMED with
    no-pool-recorded because metadata.pool was missing."""
    (tmp_path / "472.json").write_text(json.dumps(FIXTURE_472), encoding="utf-8")
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    suspect = [l for l in out.splitlines() if l.startswith("SUSPECT_GHOST 472 ")]
    if suspect:  # SUSPECT is the expected verdict but tolerate NOT_GHOST too
        assert "pool-13" in suspect[0], (
            f"SUSPECT recheck must point at pool-13: {suspect[0]}"
        )
        assert "no-pool-recorded" not in suspect[0], (
            f"pool extracted from owner — should not flag no-pool-recorded: {suspect[0]}"
        )


# --- Task #528 regression: no-owner demoted to SUSPECT ----------------------
#
# Pre-#528 helper: an in_progress task whose ghost reasons include `no-owner`
# was treated as a structural ghost → CONFIRMED_GHOST → manager mechanically
# reset. Reviewer dispatches frequently land in the task json before the
# owner field is written (separate TaskUpdate), so the first scheduler tick
# after dispatch could falsely CONFIRM a live reviewer.
#
# Fix: no-owner is now demote-able. If only reasons are no-owner +/- output_file
# AND the task json mtime (proxy for worktree mtime) is fresh (<480s), emit
# SUSPECT_GHOST. Only worktree-mtime ≥480s + no-owner + no-output_file CONFIRMS.

# A truly ownerless in_progress task — no top-level owner, no metadata.owner,
# no metadata.pool, no metadata.output_file. Mimics a reviewer just dispatched.
FIXTURE_NO_OWNER_FRESH = {
    "id": "9001",
    "subject": "[reviewer] review PR #9999 (paired dev task #8888)",
    "description": "Review the PR opened by paired dev task #8888.",
    "status": "in_progress",
    "blockedBy": [],
    "metadata": {},
}


def test_no_owner_fresh_task_is_suspect_not_confirmed(tmp_path):
    """Task #528 — no-owner + fresh task json mtime (<480s) → SUSPECT_GHOST.
    Must NOT be CONFIRMED_GHOST so manager doesn't mechanically reset a live
    reviewer that was just dispatched."""
    (tmp_path / "9001.json").write_text(
        json.dumps(FIXTURE_NO_OWNER_FRESH), encoding="utf-8"
    )
    # Need ≥5 *.json files to clear find_taskdir's hard guard.
    _write_fixtures(tmp_path)
    out = _run_helper(tmp_path)
    confirmed = [l for l in out.splitlines() if l.startswith("CONFIRMED_GHOST 9001 ")]
    suspect = [l for l in out.splitlines() if l.startswith("SUSPECT_GHOST 9001 ")]
    assert not confirmed, (
        f"#528 regression — fresh no-owner task must NOT CONFIRM: {confirmed}"
    )
    assert suspect, (
        f"#528 regression — fresh no-owner task must SUSPECT: out={out!r}"
    )
    line = suspect[0]
    assert "no-owner" in line, f"reasons must include no-owner: {line}"


def test_no_owner_stale_task_still_confirmed(tmp_path):
    """Negative control — no-owner + worktree-stale (≥480s) + no-output_file
    still CONFIRMS. We backdate the task json mtime to simulate a long-dead
    no-owner task."""
    import time as _time
    p = tmp_path / "9002.json"
    fixture = dict(FIXTURE_NO_OWNER_FRESH)
    fixture["id"] = "9002"
    p.write_text(json.dumps(fixture), encoding="utf-8")
    _write_fixtures(tmp_path)
    # Backdate to 600s ago — well past the 480s STALE_THRESHOLD_SEC.
    old_mtime = _time.time() - 600
    os.utime(p, (old_mtime, old_mtime))
    out = _run_helper(tmp_path)
    confirmed = [l for l in out.splitlines() if l.startswith("CONFIRMED_GHOST 9002 ")]
    assert confirmed, (
        f"stale no-owner + no-output_file must CONFIRM: out={out!r}"
    )
