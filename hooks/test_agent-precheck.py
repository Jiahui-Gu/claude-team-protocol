"""Tests for agent-precheck.py (consolidated Agent PreToolUse checks)."""
import json
import os
import unittest
from test_helpers import run_hook


HOOK = "agent-precheck.py"
TASK_DIR = os.path.expanduser("~/.claude/tasks/c0184255-29b7-46d5-a8e9-b82543d4db87")


def _stub_task(tid, subject="task", status="pending", blocked_by=None):
    os.makedirs(TASK_DIR, exist_ok=True)
    p = os.path.join(TASK_DIR, f"{tid}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({
            "id": str(tid), "subject": subject, "status": status,
            "blockedBy": blocked_by or [],
        }, f)
    return p


def _unstub(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _dev_prompt(tid, files=True):
    files_block = "\n## Files\n- foo.ts (MODIFY)\n" if files else ""
    return (
        f"Task #{tid}: do the thing\n\n## Task spec\n"
        "git reset --hard origin/working\ngit clean -fdx\n"
        f"{files_block}"
    )


class TestModelCheck(unittest.TestCase):

    def test_blocks_sonnet(self):
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "sonnet", "prompt": "x"}},
        )
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)
        self.assertIn("opus", err)

    def test_allows_opus(self):
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": "x"}},
        )
        self.assertEqual(rc, 0, err)

    def test_blocks_missing_model(self):
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"prompt": "x"}},
        )
        self.assertEqual(rc, 2)
        self.assertIn("None", err)


class TestCleanWorktreeCheck(unittest.TestCase):

    def test_blocks_reset_without_clean(self):
        prompt = "cd ~/pool && git reset --hard origin/working && npm install"
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": prompt}},
        )
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)
        self.assertIn("git clean", err)

    def test_allows_reset_with_clean_fd(self):
        prompt = "git reset --hard origin/working && git clean -fd"
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": prompt}},
        )
        self.assertEqual(rc, 0, err)

    def test_allows_reset_with_clean_fdx(self):
        prompt = "git reset --hard origin/working\ngit clean -fdx"
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": prompt}},
        )
        self.assertEqual(rc, 0)

    def test_skips_non_reset_prompt(self):
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": "Read files."}},
        )
        self.assertEqual(rc, 0)

    def test_skips_empty_prompt(self):
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {"model": "opus", "run_in_background": True, "prompt": ""}},
        )
        self.assertEqual(rc, 0)


class TestCheckOrdering(unittest.TestCase):
    """Model is checked first; if model is wrong AND prompt is also wrong,
    the model failure takes precedence (first failure wins)."""

    def test_model_failure_short_circuits_clean_check(self):
        # Both wrong: bad model AND reset without clean. Should fail on model.
        rc, _, err = run_hook(
            HOOK,
            {"tool_name": "Agent", "tool_input": {
                "model": "haiku",
                "prompt": "git reset --hard origin/main",
            }},
        )
        self.assertEqual(rc, 2)
        self.assertIn("opus", err)
        self.assertNotIn("git clean", err)


class TestNonAgentSkip(unittest.TestCase):

    def test_skips_bash_without_stash(self):
        # Bash is now routed to BASH_CHECKS, but plain `git reset --hard`
        # only matters at the Agent dispatch layer (clean_worktree check).
        # On the Bash side it must pass.
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}},
            env={"CCSM_STASH_BYPASS": None},
        )
        self.assertEqual(rc, 0)

    def test_skips_taskcreate(self):
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "TaskCreate", "tool_input": {"subject": "x"}},
        )
        self.assertEqual(rc, 0)


class TestNoGitStashInBash(unittest.TestCase):
    """check_no_git_stash_in_bash: Bash command must not invoke mutating
    `git stash`. Mirrors prompt-side check, but operates on the Bash tool."""

    def _payload(self, cmd):
        return {"tool_name": "Bash", "tool_input": {"command": cmd}}

    def _run(self, cmd, **kw):
        kw.setdefault("env", {"CCSM_STASH_BYPASS": None})
        return run_hook(HOOK, self._payload(cmd), **kw)

    def test_blocks_git_stash_bare(self):
        rc, _, err = self._run("git stash")
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)
        self.assertIn("CCSM_STASH_BYPASS", err)

    def test_blocks_git_stash_push(self):
        rc, _, err = self._run("git stash push -u -m 'wip'")
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_blocks_git_stash_pop(self):
        rc, _, err = self._run("git stash pop")
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_blocks_git_stash_save(self):
        rc, _, _ = self._run("git stash save 'wip'")
        self.assertEqual(rc, 2)

    def test_blocks_git_stash_apply(self):
        rc, _, _ = self._run("git stash apply")
        self.assertEqual(rc, 2)

    def test_blocks_git_stash_drop(self):
        rc, _, _ = self._run("git stash drop")
        self.assertEqual(rc, 2)

    def test_blocks_git_stash_include_untracked(self):
        # Task #437's exact incident.
        rc, _, _ = self._run("git stash --include-untracked")
        self.assertEqual(rc, 2)

    def test_blocks_when_in_compound(self):
        # Stash buried inside chained command must still be caught.
        rc, _, _ = self._run("npm test && git stash push -u")
        self.assertEqual(rc, 2)

    def test_allows_git_stash_list(self):
        rc, _, err = self._run("git stash list")
        self.assertEqual(rc, 0, err)

    def test_allows_git_stash_show(self):
        rc, _, err = self._run("git stash show -p")
        self.assertEqual(rc, 0, err)

    def test_allows_git_stash_help(self):
        rc, _, err = self._run("git stash --help")
        self.assertEqual(rc, 0, err)

    def test_allows_with_bypass_env_inline(self):
        # Bypass via env var prefix on the command line itself.
        rc, _, err = self._run("CCSM_STASH_BYPASS=reverse-verify git stash push -u")
        self.assertEqual(rc, 0, err)

    def test_allows_with_bypass_env_real(self):
        # Bypass via real env var (rare, but free to honor).
        rc, _, err = run_hook(
            HOOK,
            self._payload("git stash"),
            env={"CCSM_STASH_BYPASS": "reverse-verify"},
        )
        self.assertEqual(rc, 0, err)

    def test_allows_unrelated_bash(self):
        rc, _, err = self._run("ls -la && git status")
        self.assertEqual(rc, 0, err)

    def test_allows_word_lookalike(self):
        # `gitstash` / `git-stash-helper` must not match `\bgit\s+stash\b`.
        rc, _, err = self._run("./git-stash-helper.sh")
        self.assertEqual(rc, 0, err)
        rc, _, err = self._run("echo gitstash")
        self.assertEqual(rc, 0, err)

    def test_skips_empty_command(self):
        rc, _, _ = self._run("")
        self.assertEqual(rc, 0)

    def test_skips_non_bash_non_agent(self):
        rc, _, _ = run_hook(
            HOOK,
            {"tool_name": "TaskCreate", "tool_input": {"command": "git stash"}},
        )
        self.assertEqual(rc, 0)


class TestRobustness(unittest.TestCase):

    def test_malformed_json(self):
        rc, _, _ = run_hook(HOOK, "not-json{{")
        self.assertEqual(rc, 0)

    def test_empty_payload(self):
        rc, _, _ = run_hook(HOOK, {})
        self.assertEqual(rc, 0)


class TestTaskNotBlocked(unittest.TestCase):
    """check_task_not_blocked: dev dispatch must reference unblocked task."""

    def _payload(self, prompt):
        return {"tool_name": "Agent", "tool_input": {
            "model": "opus", "run_in_background": True, "prompt": prompt,
        }}

    def test_blocks_when_blockedby_open(self):
        stubs = [
            _stub_task(999100, "child", "pending", blocked_by=["999101"]),
            _stub_task(999101, "parent", "pending"),
        ]
        try:
            rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999100)))
            self.assertEqual(rc, 2)
            self.assertIn("BLOCKED", err)
            self.assertIn("#999101", err)
        finally:
            _unstub(stubs)

    def test_allows_when_blockedby_completed(self):
        stubs = [
            _stub_task(999110, "child", "pending", blocked_by=["999111"]),
            _stub_task(999111, "parent", "completed"),
        ]
        try:
            rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999110)))
            self.assertEqual(rc, 0, err)
        finally:
            _unstub(stubs)

    def test_blocks_when_subject_has_blocked_tag(self):
        stubs = [_stub_task(999120, "do thing [blocked]", "pending")]
        try:
            rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999120)))
            self.assertEqual(rc, 2)
            self.assertIn("[blocked]", err)
        finally:
            _unstub(stubs)

    def test_allows_when_clean(self):
        stubs = [_stub_task(999130, "do thing", "pending")]
        try:
            rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999130)))
            self.assertEqual(rc, 0, err)
        finally:
            _unstub(stubs)

    def test_bypass_comment_works(self):
        stubs = [_stub_task(999140, "do thing [blocked]", "pending")]
        try:
            prompt = _dev_prompt(999140) + "\n<!-- blocked-bypass: just merged -->"
            rc, _, err = run_hook(HOOK, self._payload(prompt))
            self.assertEqual(rc, 0, err)
        finally:
            _unstub(stubs)

    def test_allows_when_blocker_json_missing(self):
        # blockedBy points at a task whose json was deleted (= completed and
        # pruned per task-prune protocol). scheduler-helper 闸 1 treats
        # missing-json as unblocked; agent-precheck must agree, otherwise
        # manager has to use blocked-bypass HTML comments to dispatch.
        stubs = [_stub_task(999160, "child", "pending", blocked_by=["999999"])]
        try:
            rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999160)))
            self.assertEqual(rc, 0, err)
        finally:
            _unstub(stubs)

    def test_skips_non_dev(self):
        # No `## Task spec` marker → reviewer / scheduler-tick → skip.
        prompt = "Task #999150: review the PR\n\n## Review spec\nlook at it"
        rc, _, err = run_hook(HOOK, self._payload(prompt))
        self.assertEqual(rc, 0, err)

    def test_skips_when_task_json_missing(self):
        # No stub — task json doesn't exist → fail-safe pass (don't block on
        # unknown tasks; manager can dispatch tasks in other sessions).
        rc, _, err = run_hook(HOOK, self._payload(_dev_prompt(999199)))
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
