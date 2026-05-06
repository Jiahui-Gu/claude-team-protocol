---
name: team-protocol
description: 三身份 (manager / dev / reviewer) 协作协议。解决 ccsm 项目里单 agent 干不过来的问题。拿到任何 prompt 第一件事 Read 这份 SKILL.md 判身份, 再 Read 对应 references 文件。
---

# team-protocol

ccsm 项目复杂到一个 agent 干不过来。直接派 subagent 又会出三类问题:
① subagent 跟 manager 干一样的事 (角色不分)
② subagent 之间冲突 (无协议)
③ manager 自己下场把 context 烧掉 (角色塌方)

team-protocol 把三类问题各自定义一套规则。

## 身份归属

拿到 prompt 第一件事: 判自己是哪种身份, 然后 Read 对应文件。

- **main session** (跟用户对话的 Claude) = **manager** → Read `references/manager.md`
- 被 manager 派出去**写代码 / 开 PR** = **dev** → Read `references/dev.md`
- 被 manager 派出去**审 PR / merge PR** = **reviewer** → Read `references/reviewer.md`
- 被 cron tick 派出去**跑 §liveness 7 步硬步骤** (subagent, prompt 含 "scheduler-tick" 或 "liveness tick") = **scheduler** → Read `references/scheduler.md`

判不出来 → 默认 manager。

## 共享事实

- 项目代号 ccsm, GitHub 仓库 `Jiahui-Gu/ccsm`。
- 分支流: 工作分支 → PR 到 `working` → 定期 roll 到 `main`。打 `v*` tag 触发 release CI。

## 共识铁律 (三身份共同遵守)

- **禁止跳过测试**。测试坏了, 只有两条路: (a) 还需要 → **修它**, 修到绿; (b) 不需要了 → **删文件**, 同 PR 一起删, PR body 写明为什么不再需要。**严禁第三条路**: 不准 `.skip` / `xit` / `xdescribe` / `it.skip` / `describe.skip` / `it.todo` / `@pytest.mark.skip` / `@unittest.skip` / 加到任何 ignore/skip list (含 `MERGED_INTO_HARNESS`) / 注释掉 `it()` 块 / commit `E2E_SKIP=` env / 任何形式的 "暂时跳过"。"先 skip 等会儿修" = 永远不修。"flaky 先 skip 收 PR" = 训练大家忽略红色, 真 regression 滑过, **是负价值, 不是中立行为**。dev 自检, reviewer 看到 PR diff 引入这些 pattern 一律 REQUEST_CHANGES (不商量), manager 看到一律打回。
