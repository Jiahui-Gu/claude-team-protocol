"""Tests for cron-lifecycle-on-task-close.py.

Advisory hook: when TaskUpdate closes a task (status=completed/deleted),
emits additionalContext nudging manager to CronDelete liveness tick if
TaskList is now empty. Never blocks.
"""
import json
import unittest
from test_helpers import run_hook


class TestCronLifecycleOnTaskClose(unittest.TestCase):
    HOOK = "cron-lifecycle-on-task-close.py"

    def test_completed_emits_context(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "TaskUpdate",
                "tool_input": {"taskId": "42", "status": "completed"},
            },
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CronDelete", ctx)
        self.assertIn("completed", ctx)

    def test_deleted_emits_context(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "TaskUpdate",
                "tool_input": {"taskId": "42", "status": "deleted"},
            },
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("CronDelete", payload["hookSpecificOutput"]["additionalContext"])

    def test_in_progress_silent(self):
        # Going to in_progress should not emit (cron should still be alive).
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "TaskUpdate",
                "tool_input": {"taskId": "42", "status": "in_progress"},
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_non_taskupdate_silent(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {"tool_name": "TaskCreate", "tool_input": {"status": "completed"}},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_malformed_json_noop(self):
        rc, out, _ = run_hook(self.HOOK, "garbage")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
