"""Shared helpers for hook tests.

Hooks have hardcoded absolute state paths under
~/.claude/hooks/state/. We back up + restore those state
files around each test so we don't trash live manager state.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOOKS_DIR, "state")

# Path to hook script.
def hook(name: str) -> str:
    return os.path.join(HOOKS_DIR, name)


def run_hook(script_name: str, payload, cwd: str = None, timeout: int = 10, env: dict = None):
    """Spawn a hook via python, feed payload as stdin JSON.

    Returns (returncode, stdout_str, stderr_str).

    `env`, if provided, is merged on top of os.environ for the child
    process. Pass {"VAR": None} to unset a var inherited from the parent.
    """
    if isinstance(payload, (dict, list)):
        stdin_bytes = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, str):
        stdin_bytes = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        stdin_bytes = payload
    else:
        stdin_bytes = b""

    # Force cwd OUTSIDE ccsm-probe heuristic so hooks don't early-exit.
    # Use a neutral temp dir for cwd by default.
    cwd = cwd or tempfile.gettempdir()
    child_env = None
    if env is not None:
        child_env = os.environ.copy()
        for k, v in env.items():
            if v is None:
                child_env.pop(k, None)
            else:
                child_env[k] = v
    proc = subprocess.run(
        [sys.executable, hook(script_name)],
        input=stdin_bytes,
        capture_output=True,
        cwd=cwd,
        timeout=timeout,
        env=child_env,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


class StateBackup:
    """Context manager: back up a state file, ensure clean slate, restore on exit."""

    def __init__(self, *paths):
        self.paths = paths
        self._backups = {}

    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        for p in self.paths:
            if os.path.exists(p):
                fd, tmp = tempfile.mkstemp(prefix="hookbak_")
                os.close(fd)
                shutil.copy2(p, tmp)
                self._backups[p] = tmp
                os.remove(p)
            else:
                self._backups[p] = None
        return self

    def __exit__(self, exc_type, exc, tb):
        for p, tmp in self._backups.items():
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
            if tmp is not None:
                shutil.move(tmp, p)
        return False


def write_state(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_state(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
