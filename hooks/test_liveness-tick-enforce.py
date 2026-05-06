"""Tests for liveness-tick-enforce.py (Stop hook).

This hook reads the live session transcript and blocks the assistant turn
when a cron liveness tick was the trigger but the assistant didn't execute
the §liveness hard steps from manager.md.

Tests build fake transcripts that include:
  - a user message with the canonical liveness tick prompt
  - (optional) a TaskList tool_result showing tasks in various phases
  - assistant tool_use records (TaskList, Bash, Agent) demonstrating which
    hard steps were performed
  - assistant final text (for step 7 output check)

Then verify the hook either accepts (rc=0) or blocks (rc=0 with decision=block
JSON, which is exit 0 by Claude Code Stop-hook convention — the `decision`
field is what blocks).
"""
import json
import os
import tempfile
import unittest
from test_helpers import run_hook


HOOK = "liveness-tick-enforce.py"

CANONICAL_TICK_PROMPT = (
    "liveness tick. Read ~/.claude/skills/team-protocol/references/manager.md "
    "§liveness 严格按\"硬步骤 1-7\"执行, 每步报产出物。"
)


def make_transcript(
    user_text=CANONICAL_TICK_PROMPT,
    tasklist_text=None,
    tool_uses=None,
    final_text="",
):
    """Build a transcript jsonl. Returns path. Caller cleans up.

    user_text: text of the last user prompt (the cron tick)
    tasklist_text: optional text injected into the user message as a
                   tool_result (the harness-injected TaskList block)
    tool_uses: list of dicts like {"name": "Bash", "input": {"command": "..."}}
               that the assistant performs in the reply
    final_text: assistant final text reply (for step 7 output check)
    """
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="transcript-tick-")
    user_content = []
    if tasklist_text:
        user_content.append({
            "type": "tool_result",
            "content": [{"type": "text", "text": tasklist_text}],
        })
    user_content.append({"type": "text", "text": user_text})

    user_rec = {
        "type": "user",
        "message": {"role": "user", "content": user_content},
    }
    asst_content = []
    for tu in (tool_uses or []):
        asst_content.append({
            "type": "tool_use",
            "name": tu["name"],
            "input": tu.get("input", {}),
        })
    if final_text:
        asst_content.append({"type": "text", "text": final_text})
    asst_rec = {
        "type": "assistant",
        "message": {"role": "assistant", "content": asst_content},
    }

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(user_rec) + "\n")
        f.write(json.dumps(asst_rec) + "\n")
    return path


def run(transcript_path):
    return run_hook(HOOK, {"transcript_path": transcript_path})


def is_blocked(stdout):
    """Return True if hook output indicates a block decision."""
    if not stdout.strip():
        return False
    try:
        obj = json.loads(stdout.strip())
        return obj.get("decision") == "block"
    except json.JSONDecodeError:
        return False


def block_reason(stdout):
    try:
        return json.loads(stdout.strip()).get("reason", "")
    except Exception:
        return ""


class TestNotATick(unittest.TestCase):
    """When the user prompt isn't a liveness tick, hook is no-op."""

    def test_normal_user_prompt_skipped(self):
        path = make_transcript(user_text="hey claude, what's the weather?")
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
            self.assertFalse(is_blocked(out))
        finally:
            os.unlink(path)

    def test_no_liveness_keyword_skipped(self):
        path = make_transcript(user_text="please run TaskList and report")
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
            self.assertFalse(is_blocked(out))
        finally:
            os.unlink(path)


class TestStep1TaskList(unittest.TestCase):
    """Step 1: TaskList must be called every tick, even when no tasks."""

    def test_no_tasklist_call_blocks_even_empty(self):
        path = make_transcript(
            tasklist_text=None,
            tool_uses=[],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            self.assertIn("step 1", block_reason(out))
            self.assertIn("TaskList", block_reason(out))
        finally:
            os.unlink(path)

    def test_tasklist_called_no_tasks_passes(self):
        path = make_transcript(
            tasklist_text="Here are the existing tasks:\n(none)",
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
            self.assertFalse(is_blocked(out), out)
        finally:
            os.unlink(path)


class TestStep34LivenessCheck(unittest.TestCase):
    """Steps 3-4: when in_progress > 0, must perform liveness check."""

    TASKLIST_ONE_INPROG = (
        "Here are the existing tasks:\n"
        "#100 [in_progress] dev: foo\n"
    )

    def test_inprog_no_check_blocks(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_INPROG,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            self.assertIn("steps 3-4", block_reason(out))
        finally:
            os.unlink(path)

    def test_layer1_stat_pool_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_INPROG,
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Bash", "input": {
                    "command": "stat -c %Y ~/ccsm-worktrees/pool-7/"
                }},
            ],
            final_text="#100 healthy (mtime 30s)",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
            self.assertFalse(is_blocked(out), out)
        finally:
            os.unlink(path)

    def test_layer2_stat_output_file_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_INPROG,
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Bash", "input": {
                    "command": "stat -c %Y /tmp/claude/.../tasks/abc123.output"
                }},
            ],
            final_text="#100 healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_tail_jsonl_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_INPROG,
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Bash", "input": {
                    "command": "tail -3 ~/.claude/projects/foo/subagents/agent-xyz.jsonl"
                }},
            ],
            final_text="#100 long-wait monitoring",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_gh_pr_checks_passes(self):
        path = make_transcript(
            tasklist_text=(
                "Here are the existing tasks:\n"
                "#200 [in_progress] ci: bar\n"
            ),
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Bash", "input": {
                    "command": "gh pr checks 800 --repo Jiahui-Gu/ccsm"
                }},
            ],
            final_text="#200 CI in progress",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_gh_pr_view_alone_does_not_count(self):
        # Per §liveness step 4: `gh pr view` alone doesn't count, must be
        # `gh pr checks` or `gh run *` so step elapsed is visible.
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_INPROG,
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Bash", "input": {
                    "command": "gh pr view 800 --repo Jiahui-Gu/ccsm"
                }},
            ],
            final_text="#100 healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            self.assertIn("steps 3-4", block_reason(out))
        finally:
            os.unlink(path)

    def test_under_review_counts_as_inprog(self):
        # Audit #8 fix: [under_review] is status=in_progress per §2.4 lifecycle.
        path = make_transcript(
            tasklist_text=(
                "Here are the existing tasks:\n"
                "#300 [under_review] dev: pr opened\n"
            ),
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            self.assertIn("steps 3-4", block_reason(out))
        finally:
            os.unlink(path)


class TestStep6AutoDispatch(unittest.TestCase):
    """Step 6: when pending > 0, must dispatch or give explicit skip rationale."""

    TASKLIST_ONE_PENDING = (
        "Here are the existing tasks:\n"
        "#400 [ready] dev: implement X\n"
    )

    def test_pending_no_action_blocks(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_PENDING,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="#400 noted",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            self.assertIn("step 6", block_reason(out))
        finally:
            os.unlink(path)

    def test_dispatch_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_PENDING,
            tool_uses=[
                {"name": "TaskList", "input": {}},
                {"name": "Agent", "input": {
                    "model": "opus",
                    "run_in_background": True,
                    "prompt": "Task #400 ...",
                }},
            ],
            final_text="#400 dispatched pool-5",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_skip_rationale_hotfile_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_PENDING,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="#400 SKIP this round (hotfile package.json busy on PR #390)",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_skip_rationale_blockedby_passes(self):
        path = make_transcript(
            tasklist_text=self.TASKLIST_ONE_PENDING,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="#400 not eligible (blockedBy #399 still in_progress)",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_blocked_pending_also_counts(self):
        # [blocked] is also pending status; must demonstrate consideration.
        path = make_transcript(
            tasklist_text=(
                "Here are the existing tasks:\n"
                "#401 [blocked] dev: depends on #400\n"
            ),
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            # blocked task = blockedBy explanation IS the rationale; the word
            # "blocked" matches the SKIP_PATTERNS regex via "blockedby" / "blocked by"
            # — but bare "blocked" alone should also fold via "skip"? No, not "skip".
            # Actually the final_text "all healthy" doesn't contain any skip
            # pattern, so this should block.
            self.assertTrue(is_blocked(out), out)
        finally:
            os.unlink(path)


class TestStep7Output(unittest.TestCase):
    """Step 7: reply must end with `all healthy` or per-task short line(s)."""

    def test_no_output_blocks(self):
        path = make_transcript(
            tasklist_text=None,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
            # Either step 1 (no tasklist data) or step 7 should fire; here
            # step 7 should fire since no text output.
            self.assertIn("step 7", block_reason(out))
        finally:
            os.unlink(path)

    def test_per_task_line_passes(self):
        path = make_transcript(
            tasklist_text=None,
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="#160 dev hung (mtime 7min) -> re-dispatched pool-3",
        )
        try:
            rc, out, _ = run(path)
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)


class TestRobustness(unittest.TestCase):

    def test_no_transcript_path_skips(self):
        rc, out, _ = run_hook(HOOK, {})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_subagent_stop_skipped(self):
        # Subagent stops have agent_id; hook must skip.
        rc, out, _ = run_hook(HOOK, {
            "transcript_path": "/nonexistent",
            "agent_id": "abc123",
        })
        self.assertEqual(rc, 0)

    def test_stop_hook_active_skipped(self):
        # Recursive stop-hook prevention.
        path = make_transcript(
            tasklist_text=None,
            tool_uses=[],
            final_text="",
        )
        try:
            rc, out, _ = run_hook(HOOK, {
                "transcript_path": path,
                "stop_hook_active": True,
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")
        finally:
            os.unlink(path)

    def test_malformed_json_stdin(self):
        rc, out, _ = run_hook(HOOK, "not-json{{")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_old_payload_signature_still_enforced(self):
        # Transition: old long payload still triggers hook.
        old_prompt = (
            "liveness tick — GitHub-based + auto-dispatch.\n\n"
            "1. `gh pr list --repo Jiahui-Gu/ccsm --state open --json ...`\n"
        )
        path = make_transcript(
            user_text=old_prompt,
            tasklist_text=(
                "Here are the existing tasks:\n"
                "#500 [in_progress] dev: foo\n"
            ),
            tool_uses=[{"name": "TaskList", "input": {}}],
            final_text="all healthy",
        )
        try:
            rc, out, _ = run(path)
            self.assertTrue(is_blocked(out), out)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
