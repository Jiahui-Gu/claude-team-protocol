"""Tests for task-subject-discipline.py (consolidated prefix + suffix hook)."""
import json
import unittest
from test_helpers import run_hook


HOOK = "task-subject-discipline.py"


# ---------- PreToolUse(TaskCreate): block pre-baked id prefix ----------

class TestPreToolUse(unittest.TestCase):

    def test_blocks_hash_id_prefix(self):
        rc, _, err = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "#123 do thing"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_blocks_hash_id_with_leading_whitespace(self):
        rc, _, err = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "  #99 thing"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_allows_plain_subject(self):
        rc, out, err = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "do thing"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "")

    def test_allows_hash_in_middle(self):
        rc, _, _ = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "fix bug related to #123"},
        })
        self.assertEqual(rc, 0)

    def test_skips_pretooluse_taskupdate(self):
        rc, _, _ = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"subject": "#123 stuff"},
        })
        self.assertEqual(rc, 0)

    def test_empty_subject_noop(self):
        rc, _, _ = run_hook(HOOK, {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": ""},
        })
        self.assertEqual(rc, 0)


# ---------- PostToolUse(TaskCreate): nudge for #id prefix ----------

class TestPostToolUseTaskCreate(unittest.TestCase):

    def _assert_reminder(self, out, tid, subject_fragment):
        self.assertTrue(out.strip(), "expected JSON on stdout")
        payload = json.loads(out)
        spec = payload["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PostToolUse")
        ctx = spec["additionalContext"]
        self.assertIn(f"#{tid}", ctx)
        self.assertIn("TaskUpdate", ctx)
        self.assertIn(f'taskId="{tid}"', ctx)
        self.assertIn(subject_fragment, ctx)

    def test_string_response_with_id(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "do the thing"},
            "tool_response": "Task #42 created successfully: do the thing",
        })
        self.assertEqual(rc, 0)
        self._assert_reminder(out, "42", "do the thing")

    def test_structured_response(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "another task"},
            "tool_response": {"task": {"id": "777", "subject": "another task"}},
        })
        self.assertEqual(rc, 0)
        self._assert_reminder(out, "777", "another task")

    def test_content_blocks_response(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "blocky"},
            "tool_response": [
                {"type": "text", "text": "Task #314 created successfully: blocky"},
            ],
        })
        self.assertEqual(rc, 0)
        self._assert_reminder(out, "314", "blocky")

    def test_failure_silent(self):
        rc, out, err = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "x"},
            "tool_response": "error: something went wrong",
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "")

    def test_subject_already_has_id_silent(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "#9 already prefixed"},
            "tool_response": "Task #10 created successfully: #9 already prefixed",
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_taskcreate_does_not_emit_suffix_nudge(self):
        # Per design: TaskCreate's prefix nudge already covers the next
        # TaskUpdate where suffix gets added — separate suffix nudge is noise.
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "raw subject no suffix"},
            "tool_response": "Task #5 created successfully: raw subject no suffix",
        })
        self.assertEqual(rc, 0)
        # Should be exactly one nudge (the prefix one), not a second suffix one.
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("TASK SUBJECT SUFFIX", ctx)


# ---------- PostToolUse(TaskUpdate): check both prefix + suffix ----------

class TestPostToolUseTaskUpdate(unittest.TestCase):

    def test_subject_not_changed_silent(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "status": "completed"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_well_formed_subject_silent(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "#5 do thing [ready]"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_missing_suffix_nudges(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "#5 do thing"},
        })
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("TASK SUBJECT SUFFIX", ctx)
        self.assertNotIn("TASK SUBJECT PREFIX", ctx)

    def test_missing_prefix_nudges(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "do thing [ready]"},
        })
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("TASK SUBJECT PREFIX", ctx)
        self.assertNotIn("TASK SUBJECT SUFFIX", ctx)

    def test_missing_both_nudges_both(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "do thing"},
        })
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("TASK SUBJECT PREFIX", ctx)
        self.assertIn("TASK SUBJECT SUFFIX", ctx)

    def test_completed_suffix_accepted(self):
        # Per memory: completed/deleted now also carry a [...] suffix.
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "#5 done [completed]"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_deleted_suffix_accepted(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "5", "subject": "#5 gone [deleted]"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")


# ---------- routing fallback ----------

class TestRoutingFallback(unittest.TestCase):

    def test_unknown_event_taskcreate_blocks_id_prefix(self):
        rc, _, err = run_hook(HOOK, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "#7 oops"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", err)

    def test_unknown_event_taskcreate_allows_plain(self):
        rc, _, _ = run_hook(HOOK, {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "plain"},
        })
        self.assertEqual(rc, 0)

    def test_malformed_json_silent(self):
        rc, out, err = run_hook(HOOK, "garbage")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "")

    def test_unrelated_tool_silent(self):
        rc, out, _ = run_hook(HOOK, {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
