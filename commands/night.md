---
description: Toggle night-shift mode (AskUserQuestion hard-block). Usage: /night [on|off]. No arg = show status.
allowed-tools: Bash(touch:*), Bash(rm:*), Bash(test:*), Bash(ls:*), Bash(sh:*)
---

Run exactly this shell pipeline and report only the final line of output to the user. Do not run any other tools.

```bash
sh -c '
FLAG=~/.claude/hooks/state/night-shift.flag
arg="$ARGUMENTS"
case "$arg" in
  on)  touch "$FLAG"; echo "night-shift: ON" ;;
  off) rm -f "$FLAG"; echo "night-shift: OFF" ;;
  "")  test -f "$FLAG" && echo "night-shift: ON" || echo "night-shift: OFF" ;;
  *)   echo "usage: /night [on|off]"; exit 2 ;;
esac
'
```
