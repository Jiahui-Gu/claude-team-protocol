"""Tests for dispatch-track-post.py."""
import os
import unittest
from test_helpers import run_hook, StateBackup, read_state, write_state

STATE = os.path.expanduser("~/.claude/hooks/state/pending-tasks-test.txt")


class TestDispatchTrackPost(unittest.TestCase):
    HOOK = "dispatch-track-post.py"

    def test_removes_acknowledged_id(self):
        with StateBackup(STATE):
            write_state(STATE, "123\n456\n789\n")
            rc, _, _ = run_hook(
                self.HOOK,
                {
                    "session_id": "test",
                    "tool_name": "TaskUpdate",
                    "tool_input": {"taskId": "456", "status": "in_progress"},
                },
            )
            self.assertEqual(rc, 0)
            remaining = read_state(STATE).split()
            self.assertEqual(remaining, ["123", "789"])

    def test_removes_with_hash_prefix(self):
        with StateBackup(STATE):
            write_state(STATE, "100\n200\n")
            rc, _, _ = run_hook(
                self.HOOK,
                {
                    "session_id": "test",
                    "tool_name": "TaskUpdate",
                    "tool_input": {"taskId": "#100", "status": "completed"},
                },
            )
            self.assertEqual(rc, 0)
            remaining = read_state(STATE).split()
            self.assertEqual(remaining, ["200"])

    def test_skips_non_taskupdate(self):
        with StateBackup(STATE):
            write_state(STATE, "111\n")
            rc, _, _ = run_hook(
                self.HOOK,
                {"tool_name": "TaskCreate", "tool_input": {"taskId": "111"}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "111")

    def test_no_state_file_noop(self):
        with StateBackup(STATE):
            rc, _, _ = run_hook(
                self.HOOK,
                {
                    "session_id": "test",
                    "tool_name": "TaskUpdate",
                    "tool_input": {"taskId": "999", "status": "in_progress"},
                },
            )
            self.assertEqual(rc, 0)

    def test_missing_task_id_noop(self):
        with StateBackup(STATE):
            write_state(STATE, "111\n")
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "TaskUpdate", "tool_input": {}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "111")

    def test_malformed_json(self):
        with StateBackup(STATE):
            write_state(STATE, "111\n")
            rc, _, _ = run_hook(self.HOOK, "garbage")
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "111")


if __name__ == "__main__":
    unittest.main()
