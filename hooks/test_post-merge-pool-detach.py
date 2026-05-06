"""Tests for post-merge-pool-detach.py.

Strategy: build real git repos in a temp dir as fake pool-N worktrees, then
spawn the hook with USERPROFILE/HOME pointing at the temp dir so the hook's
`os.path.expanduser("~/ccsm-worktrees")` resolves there. No mocking of
subprocess — uses real git.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "post-merge-pool-detach.py")


def _git(repo, *args, env=None):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, timeout=15, env=env,
    )


def _make_pool(root, n, branch, dirty=False):
    """Create ~/ccsm-worktrees/pool-N as a real git repo on `branch`."""
    pool = os.path.join(root, "ccsm-worktrees", f"pool-{n}")
    os.makedirs(pool, exist_ok=True)
    # init + minimal config so commit works
    _git(pool, "init", "-q", "-b", "working")
    _git(pool, "config", "user.email", "t@t")
    _git(pool, "config", "user.name", "t")
    # initial commit on `working`
    with open(os.path.join(pool, "seed.txt"), "w") as f:
        f.write("seed\n")
    _git(pool, "add", ".")
    _git(pool, "commit", "-q", "-m", "seed")
    # create + checkout target branch (unless caller wants to stay on working)
    if branch != "working":
        _git(pool, "checkout", "-q", "-b", branch)
        with open(os.path.join(pool, "feat.txt"), "w") as f:
            f.write("feat\n")
        _git(pool, "add", ".")
        _git(pool, "commit", "-q", "-m", "feat")
    if dirty:
        with open(os.path.join(pool, "dirty.txt"), "w") as f:
            f.write("uncommitted\n")
    # Backdate every file so the recent-mtime guard does not fire spuriously
    old = time.time() - 3600
    for r, _, files in os.walk(pool):
        for fname in files:
            try:
                os.utime(os.path.join(r, fname), (old, old))
            except OSError:
                pass
    return pool


def _run_hook(home_dir, payload):
    env = os.environ.copy()
    env["USERPROFILE"] = home_dir
    env["HOME"] = home_dir
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=tempfile.gettempdir(),
        timeout=30,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def _current_branch(pool):
    return _git(pool, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _make_payload(merged_branch, response_template=None):
    resp = response_template if response_template is not None else (
        f"Merged pull request #619 ({merged_branch})"
    )
    return {
        "tool_name": "Bash",
        "tool_input": {"command": f"gh pr merge {merged_branch} --squash --delete-branch"},
        "tool_response": resp,
    }


class TestPostMergePoolDetach(unittest.TestCase):

    def setUp(self):
        # Need an upstream `origin` because the hook does
        # `git checkout --detach origin/working`. Build a bare upstream once
        # per test, share across all pools by adding it as `origin` remote.
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # bare upstream repo with `working` branch + one commit
        self.upstream = os.path.join(self.root, "upstream.git")
        os.makedirs(self.upstream)
        _git(self.upstream, "init", "-q", "--bare", "-b", "working")

    def tearDown(self):
        # Windows may hold file locks briefly after subprocess exits; retry.
        for _ in range(5):
            try:
                self.tmp.cleanup()
                break
            except (OSError, PermissionError):
                time.sleep(0.2)

    def _wire_origin(self, pool):
        """Push the pool's `working` to upstream and set origin."""
        _git(pool, "remote", "add", "origin", self.upstream)
        # push working so origin/working exists
        cur = _current_branch(pool)
        _git(pool, "push", "-q", "origin", "working")
        _git(pool, "fetch", "-q", "origin")
        # restore branch (push doesn't switch but be safe)
        if _current_branch(pool) != cur:
            _git(pool, "checkout", "-q", cur)

    # ---- 1. exact branch match -> detached --------------------------------

    def test_exact_match_detaches(self):
        merged = "chore/foo-889"
        pool = _make_pool(self.root, 1, merged)
        # need origin/working for detach target. Push from working first.
        _git(pool, "checkout", "-q", "working")
        self._wire_origin(pool)
        _git(pool, "checkout", "-q", merged)

        # backdate again after wire-up
        old = time.time() - 3600
        for r, _, files in os.walk(pool):
            for fn in files:
                try:
                    os.utime(os.path.join(r, fn), (old, old))
                except OSError:
                    pass

        rc, out, _err = _run_hook(self.root, _make_payload(merged))
        self.assertEqual(rc, 0)
        self.assertIn("POOL-AUTO-DETACH", out)
        self.assertIn("pool-1", out)
        self.assertEqual(_current_branch(pool), "HEAD")  # detached

    # ---- 2. suffix overlap -> NOT detached (regression for #619) ----------

    def test_suffix_overlap_not_detached(self):
        merged = "chore/settings-remove-connection-889"
        pool_branch = "chore/cleanup-entry-points-and-collapse-894"
        pool = _make_pool(self.root, 3, pool_branch)
        _git(pool, "checkout", "-q", "working")
        self._wire_origin(pool)
        _git(pool, "checkout", "-q", pool_branch)

        rc, out, _err = _run_hook(self.root, _make_payload(merged))
        self.assertEqual(rc, 0)
        # Not detached: still on its own branch.
        self.assertEqual(_current_branch(pool), pool_branch)
        self.assertNotIn("pool-3: detached", out)

    # ---- 3. no pool matches -> no-op --------------------------------------

    def test_no_pool_matches_noop(self):
        merged = "chore/nobody-cares-777"
        pool = _make_pool(self.root, 2, "feature/something-else-555")
        _git(pool, "checkout", "-q", "working")
        self._wire_origin(pool)
        _git(pool, "checkout", "-q", "feature/something-else-555")

        rc, out, _err = _run_hook(self.root, _make_payload(merged))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")  # silent no-op
        self.assertEqual(_current_branch(pool), "feature/something-else-555")

    # ---- 4. exact match BUT dirty -> SKIP + warn --------------------------

    def test_exact_match_but_dirty_skips_with_warning(self):
        merged = "chore/foo-889"
        pool = _make_pool(self.root, 4, merged)
        _git(pool, "checkout", "-q", "working")
        self._wire_origin(pool)
        _git(pool, "checkout", "-q", merged)
        # introduce uncommitted change
        with open(os.path.join(pool, "dirty.txt"), "w") as f:
            f.write("uncommitted\n")
        # backdate so mtime guard doesn't preempt the dirty guard
        old = time.time() - 3600
        for r, _, files in os.walk(pool):
            for fn in files:
                try:
                    os.utime(os.path.join(r, fn), (old, old))
                except OSError:
                    pass

        rc, out, _err = _run_hook(self.root, _make_payload(merged))
        self.assertEqual(rc, 0)
        self.assertIn("WARNINGS", out)
        self.assertIn("worktree dirty", out)
        self.assertEqual(_current_branch(pool), merged)  # NOT detached

    # ---- 5. exact match BUT recent file mtime -> SKIP + warn --------------

    def test_exact_match_but_recent_mtime_skips_with_warning(self):
        merged = "chore/foo-889"
        pool = _make_pool(self.root, 5, merged)
        _git(pool, "checkout", "-q", "working")
        self._wire_origin(pool)
        _git(pool, "checkout", "-q", merged)

        # backdate everything to old, then touch ONE tracked file fresh to
        # simulate an in-flight worker write that has been saved+committed
        # (or is about to be). File must be tracked & clean so the dirty
        # guard does NOT fire — we want the mtime guard to be the one
        # that catches it.
        fresh = os.path.join(pool, "in-flight.tsx")
        with open(fresh, "w") as f:
            f.write("// worker is editing me")
        _git(pool, "add", "in-flight.tsx")
        _git(pool, "commit", "-q", "-m", "in-flight")

        old = time.time() - 3600
        for r, _, files in os.walk(pool):
            for fn in files:
                try:
                    os.utime(os.path.join(r, fn), (old, old))
                except OSError:
                    pass
        # now touch the one tracked file fresh
        now = time.time()
        os.utime(fresh, (now, now))

        rc, out, _err = _run_hook(self.root, _make_payload(merged))
        self.assertEqual(rc, 0)
        self.assertIn("WARNINGS", out)
        self.assertIn("recent file activity", out)
        self.assertEqual(_current_branch(pool), merged)  # NOT detached


if __name__ == "__main__":
    unittest.main()
