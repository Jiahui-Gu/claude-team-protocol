"""Tests for dispatch-track-prompt.py."""
import json
import os
import unittest
from test_helpers import run_hook, StateBackup, read_state, write_state

STATE = os.path.expanduser("~/.claude/hooks/state/pending-tasks-test.txt")
SID_PAYLOAD = {"session_id": "test"}
TASKS_DIR = os.path.expanduser("~/.claude/tasks")
STUB_SESSION = "phantom-test-session"


def _stub_task(tid):
    d = f"{TASKS_DIR}/{STUB_SESSION}"
    os.makedirs(d, exist_ok=True)
    p = f"{d}/{tid}.json"
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"id":"' + str(tid) + '","status":"pending"}')
    return p


def _unstub(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(f"{TASKS_DIR}/{STUB_SESSION}")
    except OSError:
        pass


class TestDispatchTrackPrompt(unittest.TestCase):
    HOOK = "dispatch-track-prompt.py"

    def test_no_state_file_no_output(self):
        with StateBackup(STATE):
            rc, out, _ = run_hook(self.HOOK, SID_PAYLOAD)
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")

    def test_with_pending_emits_json_context(self):
        # Stub real task json files so the new phantom-prune doesn't drop them.
        stubs = [_stub_task(999998), _stub_task(999999)]
        try:
            with StateBackup(STATE):
                write_state(STATE, "999998\n999999\n")
                rc, out, _ = run_hook(self.HOOK, SID_PAYLOAD)
                self.assertEqual(rc, 0)
                obj = json.loads(out.strip())
                ctx = obj["hookSpecificOutput"]["additionalContext"]
                self.assertIn("#999998", ctx)
                self.assertIn("#999999", ctx)
                self.assertIn("DISPATCH DISCIPLINE", ctx)
        finally:
            _unstub(stubs)

    def test_empty_state_no_output(self):
        with StateBackup(STATE):
            write_state(STATE, "\n  \n")
            rc, out, _ = run_hook(self.HOOK, SID_PAYLOAD)
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")

    def test_self_heal_drops_resolved_ids(self):
        # #11 is completed in real session jsonl history → should be pruned.
        # #999997 is stubbed as a real-but-pending task → should survive.
        stubs = [_stub_task(999997)]
        try:
            with StateBackup(STATE):
                write_state(STATE, "11\n999997\n")
                rc, out, _ = run_hook(self.HOOK, SID_PAYLOAD)
                self.assertEqual(rc, 0)
                remaining = read_state(STATE).split()
                self.assertNotIn("11", remaining)
                self.assertIn("999997", remaining)
                if out.strip():
                    obj = json.loads(out.strip())
                    ctx = obj["hookSpecificOutput"]["additionalContext"]
                    self.assertNotIn("#11", ctx)
                    self.assertIn("#999997", ctx)
        finally:
            _unstub(stubs)

    def test_phantom_id_is_pruned(self):
        # Bug fix: an ID that was never TaskCreated (typo / lost dispatch)
        # has no status_map entry AND no task json file anywhere — it must
        # be pruned, not eternally re-warned.
        with StateBackup(STATE):
            write_state(STATE, "987654321\n")
            rc, out, _ = run_hook(self.HOOK, SID_PAYLOAD)
            self.assertEqual(rc, 0)
            remaining = read_state(STATE).split()
            self.assertNotIn("987654321", remaining)
            self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
