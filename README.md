# claude-team-protocol

我自己用 Claude Code 时攒的一套**多 agent 协作协议** + 配套 hook 脚本。

把单个 Claude session 切成 **manager / dev / reviewer** 三种身份, 用 cron + 后台 subagent 跑长任务流水线 (派活 → 写码 → CI → review → merge), manager 不下场做 dev 活, 全程 ground 到 TaskList 上的具体 task。

> 本仓库只放 **我自己写的部分**: `team-protocol` skill、`spec-pipeline` skill、以及 `hooks/` 下的 27 个 PreToolUse / PostToolUse / Stop / SessionStart / UserPromptSubmit hook。其它 skills (frontend-design / shadcn / react-best-practices 等) 是社区作品, 不在这里再分发。

## 目录

```
scripts/                        team-protocol skill 调用的 helper 脚本
  ├── dispatch-helper.py        生成派发 JSON (manager / dev / reviewer / scheduler-tick)
  ├── scheduler-helper.py       scheduler tick 状态聚合 (TaskList / cron / CI)
  └── test_scheduler-helper.py  pytest

commands/                       自定义 slash commands
  └── night.md                  /night [on|off] — 切 night-shift flag, 让 hook 走自治闸

hooks/                          所有自定义 Claude Code hooks (Python + bash)
  ├── agent-precheck.py         派 dev 前的 blockedBy / defer_until / task subject 检查
  ├── askuserquestion-*.py      AskUserQuestion 的 night-shift 自治闸
  ├── bash-discipline.py        Bash 命令风格闸
  ├── cron-lifecycle-*.py       cron 派发 / session-start / task-close 三个生命周期 hook
  ├── dispatch-track-*.py       subagent dispatch 三段追踪 (pre / post / prompt)
  ├── liveness-tick-enforce.py  保证 manager 真把 cron tick 派出去 scheduler subagent
  ├── post-merge-pool-detach.py worktree pool merge 后自动 detach
  ├── unpaired-question-stop.py 纯文本问句 (没走 AskUserQuestion) 的 Stop hook 警告
  ├── test_*.py                 每个 hook 配套的 pytest
  └── README.md                 hook 总览

skills/team-protocol/           三身份协议本体 (SKILL.md + manager/dev/reviewer/scheduler references)
skills/spec-pipeline/           把 design spec 拆成可并行执行 task DAG 的多阶段 pipeline
```

## 安装

1. 把 `hooks/` 拷到 `~/.claude/hooks/`, `scripts/` 拷到 `~/.claude/scripts/`, `commands/` 拷到 `~/.claude/commands/`, 把 `skills/team-protocol` 和 `skills/spec-pipeline` 拷到 `~/.claude/skills/` 下。
2. 按 `hooks/README.md` 在 `~/.claude/settings.json` 里把每个 hook 注册到对应事件。
3. hook 脚本里的路径全部用 `os.path.expanduser("~/...")`, 跨用户不需要改源码。

## 适用范围

- Claude Code (CLI 形态), Windows / macOS / Linux 都能跑 (路径已脱敏成 `~/`)。
- 强依赖 `gh` CLI (PR / CI / log 拉取) 和 git worktree (manager 用 worktree pool 派活)。
- 如果你不打算跑多 agent 流水线, 单看 `agent-precheck.py` / `bash-discipline.py` / `unpaired-question-stop.py` 这几个独立 hook 也有用。

## 注意

- `hooks/state/` 是运行时数据 (pending tasks / acked tasks / cron flag), 不在仓库里, 第一次运行时 hook 会自己建。
- 文档里出现的 `Jiahui-Gu/ccsm` 是我自己用来跑这套协议的项目, 想用的话替换成你自己的 `<owner>/<repo>`。
- skills 里写的 cron token、TaskList 字段名等都是 Claude Code 当前 (Opus 4.7) 行为, 跟着模型 / harness 升级可能要调。

## License

MIT。Hook 脚本和协议文档都是我自己写的。`skills/spec-pipeline/references/` 下个别模板段落参考了 superpowers plugin 的工作流思路。
