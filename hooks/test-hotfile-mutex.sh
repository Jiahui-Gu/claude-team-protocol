#!/usr/bin/env bash
# Smoke test for check_hotfile_pr_in_flight in agent-precheck.py.
#
# Feeds 5 cases as stdin JSON, asserts exit code (and stderr substring for
# the BLOCK case). Prints PASS/FAIL summary.
#
# Run: bash ~/.claude/hooks/test-hotfile-mutex.sh
set -u

HOOK="$HOME/.claude/hooks/agent-precheck.py"
PASS=0
FAIL=0

# Build a JSON Agent dispatch payload.
# args: $1=prompt
mk_payload() {
  python -c "
import json, sys
prompt = sys.argv[1]
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {
        'model': 'opus',
        'run_in_background': True,
        'prompt': prompt,
    },
}))
" "$1"
}

run_case() {
  local name="$1" expected_exit="$2" expected_stderr_substr="$3" payload="$4"
  local stderr_file
  stderr_file=$(mktemp)
  echo "$payload" | python "$HOOK" 2>"$stderr_file"
  local actual_exit=$?
  local stderr_content
  stderr_content=$(cat "$stderr_file")
  rm -f "$stderr_file"

  local ok=1
  if [[ "$actual_exit" != "$expected_exit" ]]; then
    ok=0
  fi
  if [[ -n "$expected_stderr_substr" ]]; then
    while IFS= read -r needle; do
      [[ -z "$needle" ]] && continue
      if [[ "$stderr_content" != *"$needle"* ]]; then
        ok=0
      fi
    done <<< "$expected_stderr_substr"
  fi

  if [[ "$ok" == "1" ]]; then
    echo "PASS: $name (exit=$actual_exit)"
    PASS=$((PASS+1))
  else
    echo "FAIL: $name (exit=$actual_exit, expected=$expected_exit)"
    echo "----- stderr -----"
    echo "$stderr_content"
    echo "------------------"
    FAIL=$((FAIL+1))
  fi
}

# ----- case 1: dev dispatch, unique file, no conflict -----
P1="Task #9999 dev round-1
git reset --hard origin/working && git clean -fdx

## Task spec

do stuff

## Files
- some/unique/path-that-no-other-task-touches.ts (NEW)
"
run_case "1. unique file, no conflict" 0 "" "$(mk_payload "$P1")"

# ----- case 2: dev dispatch, file collides with an in_progress task -----
# Uses packages/daemon/src/rpc/pty-attach.ts which Task #355 owns (in_progress).
P2="Task #9998 dev round-1
git reset --hard origin/working && git clean -fdx

## Task spec

modify pty-attach

## Files
- packages/daemon/src/rpc/pty-attach.ts (MODIFY)
"
run_case "2. collides with in-flight task on pty-attach.ts" 2 "BLOCKED
Task #355
pty-attach.ts" "$(mk_payload "$P2")"

# ----- case 3: re-dispatch SELF (#355) on its own files → allowed -----
P3="Task #355 dev round-2
git reset --hard origin/working && git clean -fdx

## Task spec

re-dispatch round-2 to address review

## Files
- packages/daemon/src/rpc/pty-attach.ts (MODIFY)
"
run_case "3. self re-dispatch on hot file" 0 "" "$(mk_payload "$P3")"

# ----- case 4: hotfile-bypass marker -----
P4="Task #9997 dev round-1
git reset --hard origin/working && git clean -fdx

<!-- hotfile-bypass: stacked PR coordinated with #355, will rebase after -->

## Task spec

stacked on top of #355

## Files
- packages/daemon/src/rpc/pty-attach.ts (MODIFY)
"
run_case "4. hotfile-bypass marker" 0 "" "$(mk_payload "$P4")"

# ----- case 5: reviewer dispatch (no '## Task spec') -----
P5="Task #9996 reviewer round-1

## Review spec

review PR #1027 against spec.

## Files
- packages/daemon/src/pty-host/host.ts (review only)
"
run_case "5. reviewer dispatch skipped" 0 "" "$(mk_payload "$P5")"

echo
echo "Summary: $PASS passed, $FAIL failed"
exit $FAIL
