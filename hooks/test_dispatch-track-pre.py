"""Tests for dispatch-track-pre.py."""
import os
import tempfile
import time
import unittest
from test_helpers import run_hook, StateBackup, read_state, write_state

STATE = os.path.expanduser("~/.claude/hooks/state/pending-tasks-test.txt")
UNTRACKED_STATE = os.path.expanduser("~/.claude/hooks/state/untracked-dispatch-test.txt")
ACKED_STATE = os.path.expanduser("~/.claude/hooks/state/acked-tasks-test.txt")
SID = "test"


class TestDispatchTrackPre(unittest.TestCase):
    HOOK = "dispatch-track-pre.py"

    def test_extracts_task_id_from_first_line(self):
        with StateBackup(STATE):
            prompt = "Task #123 implement feature\n\nSome details here"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "123")

    def test_extracts_multiple_ids_first_line(self):
        with StateBackup(STATE, ACKED_STATE):
            prompt = "Task #100 and Task #200 combined"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            ids = sorted(read_state(STATE).split())
            self.assertEqual(ids, ["100", "200"])

    def test_appends_to_existing_state(self):
        with StateBackup(STATE):
            write_state(STATE, "111\n")
            prompt = "Task #222 do stuff"
            run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            ids = sorted(read_state(STATE).split())
            self.assertEqual(ids, ["111", "222"])

    def test_ignores_task_id_in_body_only(self):
        with StateBackup(STATE):
            prompt = "Worker setup\n\nTask #333 referenced here"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))

    def test_ignores_bare_hash_no_task_word(self):
        with StateBackup(STATE):
            prompt = "Work on #444 now"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))

    def test_skips_non_agent(self):
        with StateBackup(STATE):
            rc, _, _ = run_hook(
                self.HOOK,
                {"tool_name": "Bash", "tool_input": {"command": "Task #555"}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))

    def test_case_insensitive_lowercase_task(self):
        # Per re.IGNORECASE on Task pattern: 'task #123' lowercase still matches.
        with StateBackup(STATE):
            prompt = "task #321 implement something\n\nbody"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "321")

    def test_probe_cwd_guard_skips(self):
        # Hook early-exits when cwd contains 'ccsm-probe' to avoid contaminating probes.
        probe_dir = os.path.join(tempfile.gettempdir(), "ccsm-probe-fake")
        os.makedirs(probe_dir, exist_ok=True)
        with StateBackup(STATE):
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": "Task #777 do work"}},
                cwd=probe_dir,
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))

    def test_malformed_json_stdin_noop(self):
        # Bad JSON on stdin must not raise; hook silently exits 0.
        with StateBackup(STATE):
            rc, _, _ = run_hook(self.HOOK, "not-json-at-all")
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))

    def test_ghost_dispatch_records_untracked(self):
        # Dev signals (e.g. 'open pr') without Task #NNN should record to
        # untracked-dispatch state, not pending-tasks state.
        with StateBackup(STATE, UNTRACKED_STATE):
            prompt = "Worker setup\n\nplease open pr for the new feature"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))
            self.assertTrue(os.path.exists(UNTRACKED_STATE))
            self.assertIn("Worker setup", read_state(UNTRACKED_STATE))

    def test_ghost_dispatch_skipped_when_no_dev_signals(self):
        # No Task #NNN, no dev signals -> nothing recorded anywhere.
        with StateBackup(STATE, UNTRACKED_STATE):
            prompt = "just look around and tell me what you find"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(STATE))
            self.assertFalse(os.path.exists(UNTRACKED_STATE))

    def test_recently_acked_id_suppressed_same_batch(self):
        # Regression: when a single message contains both
        # TaskUpdate(taskId=4, status=in_progress) and Agent(prompt="Task #4 ..."),
        # hook ordering between PostToolUse:TaskUpdate and PreToolUse:Agent is
        # non-deterministic. If post fires first (clearing state), pre would
        # otherwise re-introduce #4, producing a stale "DISPATCH DISCIPLINE"
        # alert on the next prompt. Post writes acked-tasks.txt with a fresh
        # timestamp; pre must filter ids present there.
        with StateBackup(STATE, ACKED_STATE):
            os.makedirs(os.path.dirname(ACKED_STATE), exist_ok=True)
            with open(ACKED_STATE, "w") as f:
                f.write(f"4\t{time.time()}\n")
            prompt = "Task #4 implement feature\n\nbody"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            pending = read_state(STATE).split() if os.path.exists(STATE) else []
            self.assertNotIn("4", pending)

    def test_stale_ack_does_not_suppress(self):
        # Acks older than ACK_TTL_SECONDS (60s) must NOT suppress a new pre
        # write — otherwise a real un-acked dispatch would be silently lost.
        with StateBackup(STATE, ACKED_STATE):
            os.makedirs(os.path.dirname(ACKED_STATE), exist_ok=True)
            with open(ACKED_STATE, "w") as f:
                f.write("4\t1.0\n")  # epoch 1970, very stale
            prompt = "Task #4 implement feature"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "4")

    def test_recent_ack_only_suppresses_matching_id(self):
        # Acks for one id must not suppress writes for a different id in the
        # same Agent prompt.
        with StateBackup(STATE, ACKED_STATE):
            os.makedirs(os.path.dirname(ACKED_STATE), exist_ok=True)
            with open(ACKED_STATE, "w") as f:
                f.write(f"4\t{time.time()}\n")
            prompt = "Task #4 and Task #5 combined"
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertEqual(read_state(STATE).strip(), "5")

    def test_ack_suppressed_grounded_dispatch_not_ghost(self):
        # Regression for Task #15: when TaskUpdate(taskId=13) and
        # Agent(prompt="Task #13. ... open PR ...") fire in the same batch,
        # post may run first and write ack for 13. Pre then filters 13 out
        # of `ids`, leaving an empty set. The prompt contains a DEV_SIGNAL
        # ("open pr"), so previously the hook fell into the ghost-dispatch
        # path and recorded the dispatch as untracked — even though it was
        # properly grounded in Task #13. Verify: no untracked entry written.
        with StateBackup(STATE, UNTRACKED_STATE, ACKED_STATE):
            os.makedirs(os.path.dirname(ACKED_STATE), exist_ok=True)
            with open(ACKED_STATE, "w") as f:
                f.write(f"13\t{time.time()}\n")
            prompt = (
                "Task #13. You are a dev (smoke / no PR mode). First action: "
                "Read protocol (skip \"open PR\" sections — recon only)."
            )
            rc, _, _ = run_hook(
                self.HOOK,
                {"session_id": "test", "tool_name": "Agent", "tool_input": {"prompt": prompt}},
            )
            self.assertEqual(rc, 0)
            self.assertFalse(
                os.path.exists(UNTRACKED_STATE),
                "Grounded dispatch must not be flagged as ghost just because "
                "its id was ack-suppressed by a same-batch TaskUpdate.",
            )


if __name__ == "__main__":
    unittest.main()
