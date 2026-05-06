"""Tests for question-without-askuserquestion-prompt.py.

This hook reads the live session transcript (no STATE file). Tests build a
fake transcript jsonl and pass its path via the `transcript_path` payload
field, then verify the hook detects (or skips) trailing-`?` patterns.
"""
import json
import os
import tempfile
import unittest
from test_helpers import run_hook


HOOK = "question-without-askuserquestion-prompt.py"


def make_transcript(*assistant_texts):
    """Write a jsonl transcript with given assistant text turns; return path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="transcript-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for text in assistant_texts:
            rec = {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
            f.write(json.dumps(rec) + "\n")
    return path


class TestQuestionPrompt(unittest.TestCase):

    def test_no_transcript_no_output(self):
        rc, out, _ = run_hook(HOOK, {"transcript_path": "/nonexistent/path"})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_trailing_question_emits_nudge(self):
        path = make_transcript(
            "I think the right move is option A. "
            "But before I do that, want me to also check the staging cluster?"
        )
        try:
            rc, out, _ = run_hook(HOOK, {"transcript_path": path})
            self.assertEqual(rc, 0)
            obj = json.loads(out.strip())
            ctx = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn("UNPAIRED QUESTION", ctx)
            self.assertIn("staging cluster?", ctx)
        finally:
            os.unlink(path)

    def test_no_question_no_output(self):
        path = make_transcript(
            "I finished the refactor. All tests pass and PR #42 is opened."
        )
        try:
            rc, out, _ = run_hook(HOOK, {"transcript_path": path})
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")
        finally:
            os.unlink(path)

    def test_short_text_skipped(self):
        # < 20 chars after scrub -> skip (avoids false positives on tiny acks).
        path = make_transcript("ok?")
        try:
            rc, out, _ = run_hook(HOOK, {"transcript_path": path})
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
