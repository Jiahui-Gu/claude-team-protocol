"""Tests for cron-lifecycle-on-dispatch.py.

Advisory hook: emits additionalContext when a background Agent is dispatched,
nudging the manager to verify the liveness cron tick. Always exits 0; never blocks.

Dedup is based on whether the prior nudge sentinel still appears in the
visible transcript file (Task #347): this survives compaction correctly,
unlike the old per-session flag file which silenced the hook across compact.
"""
import json
import os
import tempfile
import unittest
from test_helpers import run_hook


SENTINEL = "LIVENESS CRON CHECK (one-shot per epoch)"


def _write_transcript(lines):
    fd, path = tempfile.mkstemp(prefix="cron_xcript_", suffix=".jsonl")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line if line.endswith("\n") else line + "\n")
    return path


class TestCronLifecycleOnDispatch(unittest.TestCase):
    HOOK = "cron-lifecycle-on-dispatch.py"

    def test_background_agent_emits_additional_context(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "Agent",
                "tool_input": {"run_in_background": True, "prompt": "do thing"},
            },
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LIVENESS CRON", ctx)
        self.assertIn("CronList", ctx)
        # Sentinel must be embedded so future dispatches can dedup.
        self.assertIn(SENTINEL, ctx)

    def test_foreground_agent_silent(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "Agent",
                "tool_input": {"run_in_background": False, "prompt": "do thing"},
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_missing_run_in_background_silent(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {"tool_name": "Agent", "tool_input": {"prompt": "do thing"}},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_non_agent_tool_silent(self):
        rc, out, _ = run_hook(
            self.HOOK,
            {"tool_name": "Bash", "tool_input": {"run_in_background": True}},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_malformed_json_noop(self):
        rc, out, _ = run_hook(self.HOOK, "garbage")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_silent_when_sentinel_already_in_transcript(self):
        """If a prior nudge's sentinel is still visible, stay silent."""
        prior_ctx = (
            f"{SENTINEL}: first background Agent of this epoch dispatched..."
        )
        record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prior_ctx}],
            },
        }
        xpath = _write_transcript([json.dumps(record)])
        try:
            rc, out, _ = run_hook(
                self.HOOK,
                {
                    "tool_name": "Agent",
                    "tool_input": {"run_in_background": True, "prompt": "another"},
                    "transcript_path": xpath,
                },
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")
        finally:
            os.remove(xpath)

    def test_fires_when_transcript_lacks_sentinel(self):
        """Compact-style scenario: transcript exists but sentinel was
        compressed away. Hook MUST re-fire."""
        record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "compacted summary blah blah"}],
            },
        }
        xpath = _write_transcript([json.dumps(record)])
        try:
            rc, out, _ = run_hook(
                self.HOOK,
                {
                    "tool_name": "Agent",
                    "tool_input": {"run_in_background": True, "prompt": "post-compact"},
                    "transcript_path": xpath,
                },
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertIn(
                SENTINEL, payload["hookSpecificOutput"]["additionalContext"]
            )
        finally:
            os.remove(xpath)

    def test_fires_when_transcript_path_missing(self):
        """No transcript path → fail-open: fire (defensive)."""
        rc, out, _ = run_hook(
            self.HOOK,
            {
                "tool_name": "Agent",
                "tool_input": {"run_in_background": True, "prompt": "x"},
                "transcript_path": "/nonexistent/path/does-not-exist.jsonl",
            },
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn(SENTINEL, payload["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
