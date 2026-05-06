# Claude Code disciplinary hooks

Enforce manager-session dispatch discipline for ccsm. Source of truth for the rules: `~/.claude/skills/team-protocol/`.

## Hook reference

| Hook | Event | Behavior | State file |
| --- | --- | --- | --- |
| `bash-discipline.py` | PreToolUse(Bash) | BLOCK `gh pr create` without `--base working` (or `--base main --head working` for release); BLOCK `cd` into main repo paths (worktrees only) | (none) |
| `agent-model-enforce.py` | PreToolUse(Agent) | BLOCK Agent calls with `model != "opus"` | (none) |
| `agent-prompt-clean-worktree.py` | PreToolUse(Agent) | BLOCK Agent prompts that do `git reset --hard` without `git clean -fd[x]` | (none) |
| `taskcreate-id-discipline.py` | PreToolUse + PostToolUse(TaskCreate) | Pre: BLOCK subjects starting with `#NNN`. Post: parse the real ID from response and inject reminder to `TaskUpdate` and prepend `#<realId>` | (none) |
| `dispatch-track-pre.py` | PreToolUse(Agent) | Append `Task #NNN` IDs from prompt's first line to `pending-tasks.txt`. If prompt has dev signals (`gh pr create` etc.) but no Task #NNN, log to `untracked-dispatch.txt` | `pending-tasks.txt` (append), `untracked-dispatch.txt` (append) |
| `dispatch-track-post.py` | PostToolUse(TaskUpdate) | Remove the updated task ID from `pending-tasks.txt` | `pending-tasks.txt` (remove) |
| `dispatch-track-prompt.py` | UserPromptSubmit | Surface `pending-tasks.txt` and `untracked-dispatch.txt` as additionalContext; clear `untracked-dispatch.txt` after surfacing | reads both, clears `untracked-dispatch.txt` |
| `askuserquestion-autonomy-nudge.py` | PreToolUse(AskUserQuestion) | 00:00–12:00 local: HARD BLOCK (deny). 12:00–24:00 with pending tasks: soft additionalContext nudge | reads `pending-tasks.txt` |
| `cron-lifecycle-on-dispatch.py` | PostToolUse(Agent) | If `run_in_background=true`, inject reminder to CronList and CronCreate liveness tick (`*/5 * * * *`) | (none) |
| `cron-lifecycle-on-task-close.py` | PostToolUse(TaskUpdate) | When status ∈ {completed, deleted}, remind to CronDelete liveness tick if TaskList drains to 0 | (none) |
| `post-merge-pool-detach.py` | PostToolUse(Bash) | After `gh pr merge`, scan pool-1..20: any worktree on the exactly-merged branch (clean + no recent file mtime) → `git checkout --detach origin/working` + delete branch | (none) |
| `question-without-askuserquestion-stop.py` | Stop | Scan last manager assistant turn for question patterns; if no AskUserQuestion was used, write to `unpaired-question.txt` | `unpaired-question.txt` (write) |
| `question-without-askuserquestion-prompt.py` | UserPromptSubmit | Surface `unpaired-question.txt` as nudge to use AskUserQuestion; clear after surfacing | reads + clears `unpaired-question.txt` |

## Skill alignment

All disciplinary rules originate in `~/.claude/skills/team-protocol/references/`:
- `manager.md` §2.3 (model=opus, Task #NNN first line, setup with reset+clean), §2.4 (manager-mediated dev→reviewer), §3.1 (liveness cron), §3.2 (TaskCreate ID), §3.3 (pool-1..20)
- `dev.md` §3 (gh pr create --base working)

Hook error messages should cite the skill section, not deleted memory files.

## Tests

```bash
bash ~/.claude/hooks/run-tests.sh
```

`unittest`-based, uses `subprocess`; backs up live `state/` files first.

## State directory

`~/.claude/hooks/state/`:
- `pending-tasks.txt` — bare task IDs (no `#`) from `Task #NNN` dispatches awaiting TaskUpdate
- `untracked-dispatch.txt` — first-line previews of dev-signal Agent calls without Task #NNN
- `unpaired-question.txt` — `pattern=...` + `preview=...` of an unpaired plain-text question

## Failure modes

- Each hook adds ~50–200ms python startup per matching tool call.
- Hooks exit 0 on internal error (don't block legit calls due to hook bug); check Claude Code log for stderr.
- Editing hook scripts: no restart needed (loaded fresh per call).
- Editing `settings.json`: requires REPL restart.

## Uninstall

Delete the `hooks` key from `~/.claude/settings.json`, or `cp ~/.claude/settings.json.bak ~/.claude/settings.json`.
