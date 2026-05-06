"""Tests for bash-discipline.py."""
import unittest
from test_helpers import run_hook


class TestBashDiscipline(unittest.TestCase):
    HOOK = "bash-discipline.py"

    def test_blocks_gh_pr_create_without_base(self):
        rc, _, err = run_hook(
            self.HOOK, {"tool_input": {"command": "gh pr create --title x"}}
        )
        self.assertEqual(rc, 2)
        self.assertIn("--base working", err)

    def test_allows_gh_pr_create_with_base(self):
        rc, _, _ = run_hook(
            self.HOOK,
            {"tool_input": {"command": "gh pr create --base working --title x"}},
        )
        self.assertEqual(rc, 0)

    def test_allows_gh_pr_create_with_B_alias(self):
        rc, _, _ = run_hook(
            self.HOOK,
            {"tool_input": {"command": "gh pr create -B working --title x"}},
        )
        self.assertEqual(rc, 0)

    def test_blocks_cd_into_main_repo_tilde(self):
        rc, _, err = run_hook(
            self.HOOK, {"tool_input": {"command": "cd ~/ccsm && ls"}}
        )
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_allows_cd_into_worktree_pool(self):
        rc, _, _ = run_hook(
            self.HOOK,
            {"tool_input": {"command": "cd ~/ccsm-worktrees/pool-1 && ls"}},
        )
        self.assertEqual(rc, 0)

    def test_blocks_cd_into_agentory(self):
        rc, _, err = run_hook(
            self.HOOK, {"tool_input": {"command": "cd ~/agentory; ls"}}
        )
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_allows_unrelated_command(self):
        rc, _, _ = run_hook(self.HOOK, {"tool_input": {"command": "ls -la"}})
        self.assertEqual(rc, 0)

    def test_malformed_json(self):
        rc, _, _ = run_hook(self.HOOK, "garbage")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
