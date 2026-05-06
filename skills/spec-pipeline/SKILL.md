---
name: spec-pipeline
description: Use when producing a detailed design spec for a non-trivial feature/initiative AND turning it into a parallel-executable task DAG. Multi-stage parallel pipeline (author → m chapter files → n reviewers → m fixers → reviewer loop → merge → t parallel DAG extractors → tasks). Run entirely inside one worktree pool, end with one PR.
---

# spec-pipeline

Standardized parallel pipeline for turning a feature seed (rough idea OR existing partial design) into:
1. A consolidated, P0/P1-clean design spec (single markdown file).
2. A task DAG seeded into TaskList with proper blockers.

**Pipeline runs entirely inside ONE worktree pool. Output is ONE PR containing the merged spec.**

Manager has full autonomy through every stage. Don't ask the user mid-pipeline; only escalate on direction-changing evidence (per `feedback_autonomous_execution`).

## When to use

- User says "design X" / "spec out X" / "write the design for X" / "把 X design 拆成 task" / "根据 0.x design 做 0.y design".
- Initiative is non-trivial: multi-component, multi-week, multi-worker downstream.
- Trivial designs (single file change, one-shot fix) → skip this skill, go direct.

## When NOT to use

- Topic is vague — invoke `superpowers:brainstorming` first to lock direction, THEN feed the brainstorm output into this skill.
- Single-author micro-spec (<1 page, no DAG needed) — write inline.

## Stages

```
0. Pick pool, set base branch
1. AUTHOR: 1 worker → m chapter files (00-overview.md, 01-arch.md, ...)
2. REVIEW: n reviewers in parallel, distinct angles, write findings to chapter-N.review.md
3. FIX: m fixers (1 per chapter) — OR fewer if reviewer flagged cross-file changes (1 fixer per cross-file set)
4. LOOP: re-review every modified chapter; iterate until 0 P0/P1 issues
5. MERGE: 1 merger worker → consolidates m chapter files into ONE final spec
6. DAG: t parallel DAG-extraction workers (split by chapter range) → emit task subjects + blocker edges
7. SEED: manager TaskCreate every emitted task with subject `[T<level>.<n>] <desc>` and TaskUpdate to set blockers
8. PR: 1 PR opened against base branch with the consolidated spec file (chapter files + .review files deleted from PR; kept in branch history for audit)
```

## Stage details

### Stage 0 — Pick pool, set base branch

- Pick a free worktree pool (`~/ccsm-worktrees/pool-{1..20}`). Reserve it for the entire pipeline.
- Decide base branch (usually `working` or `main`).
- Decide spec output path: `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`.
- Create feature branch: `spec/YYYY-MM-DD-<topic>` off base.
- All subsequent workers run in this same pool (per "single pool" constraint).

### Stage 1 — Author (1 worker)

- Dispatch ONE author worker (model: opus). Long-running. Self-contained: input = topic seed + any prior design references; output = m chapter files in `docs/superpowers/specs/<topic>-chapters/`.
- Worker decides chapter split based on topic shape. Typical chapters: overview, goals/non-goals, architecture, components, data flow, error handling, testing, release slicing, risks, references.
- Each chapter is a standalone `.md` file with consistent structure (heading depth, code-fence styles).
- Worker commits + pushes the chapter files to the feature branch.
- **Worker does NOT write the merged spec yet** — that's stage 5.
- Author worker prompt template lives in `references/author-prompt.md`.

### Stage 2 — Review (n parallel reviewers)

- Dispatch n=4-6 reviewers in parallel (single message, multiple Agent calls). Each gets a distinct angle:
  - **R1: Feature-preservation** — if this is a refactor / non-feature work, did design avoid touching feature behavior unnecessarily? (e.g. v0.3 = daemon split = pure refactor; v0.4 = +web frontend; both should NOT change product features unless required by the refactor itself.)
  - **R2: Security** — auth, sandbox, ACL, attack surface, sender validation.
  - **R3: Reliability / observability** — failure modes, crash recovery, logging, metrics, debuggability.
  - **R4: Scalability / performance** — bottlenecks, hot paths, resource caps.
  - **R5: Testability** — can each component be unit-tested? E2E story coherent?
  - **R6: Naming / consistency / clarity** — terminology, doc structure, code/file naming.
- Each reviewer reads ALL chapter files but writes findings to a single per-reviewer file: `docs/superpowers/specs/<topic>-chapters/<chapter>.R<n>.review.md`.
- Findings tagged P0 (blocker) / P1 (must-fix-before-merge) / P2 (nice-to-have follow-up).
- Reviewers DO NOT modify chapter content.
- Reviewer prompt template: `references/reviewer-prompt.md`.

### Stage 3 — Fix (m fixers, or fewer)

- Manager scans all `.review.md` files. Build per-chapter merged finding list.
- Default: m fixers, 1 per chapter, parallel.
- **Cross-file changes**: if a single finding spans multiple chapters (e.g. "rename concept X across chapters 3, 4, 5"), allocate ONE fixer to the cross-file set instead of risking inconsistency from m parallel writers. The fixer count drops accordingly.
- Each fixer reads its assigned chapter(s) + relevant `.review.md` files + any cross-file context. Writes to chapter file(s). Commits.
- Fixers run in same pool but operate on DISJOINT chapter file sets (mutex by file ownership).
- Fixer prompt template: `references/fixer-prompt.md`.

### Stage 4 — Review loop

- After fixer batch completes, re-dispatch reviewers (same n angles) on changed chapters only.
- New review findings written to next-numbered `.review.md` files (e.g. `chapter-3.R2.review.r2.md`).
- Repeat fix → review until **0 P0 + 0 P1** across all chapters. P2 deferred.
- Hard cap: 5 rounds. If still not converging, manager escalates to user with summary.

### Stage 5 — Merge (1 merger worker)

- Dispatch ONE merger worker. Input: all final chapter files. Output: ONE consolidated spec markdown at the planned `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` path.
- Merger preserves chapter content verbatim where possible; resolves cross-chapter inconsistencies (terminology, numbering, references); produces unified TOC; adds changelog entry.
- Merger commits the merged file AND deletes the chapter directory + .review files (keeps history in git, removes from PR working tree).
- Merger prompt template: `references/merger-prompt.md`.

### Stage 6 — DAG (t parallel extractors)

- Past lesson: a single worker doing full-DAG extraction blows context on large designs.
- Split work: t=2-4 extractors. Each extractor takes a chapter range (e.g. extractor 1: arch + components, extractor 2: testing + release, etc.).
- Each emits a JSON / YAML chunk: `[{ subject, level, blockedBy, notes }, ...]`.
- Subjects written in canonical form: `[T<level>.<n>] <imperative description>`. Levels: L0 = scaffold, L1-L8 = implementation phases.
- Extractors do NOT call TaskCreate themselves (manager owns ID allocation).
- Extractor prompt template: `references/extractor-prompt.md`.

### Stage 6.5 — Library / wire-up split (manager, mandatory)

Before Stage 7 (TaskCreate seeding), the manager MUST scan every extractor-emitted task and decide:

- **Pure library** (defines code, no global state effect, no startup hook): split into two tasks:
  - `[LIBRARY] <name> — implementation`
  - `[WIRE-UP] <name> — call from runStartup` (with `addBlockedBy: [<library-task-id>]`)
- **Pure wire-up** (instantiates / registers / installs / boots existing library): leave as single task, but verify the library it wires already exists. If not, create the library task first.
- **Pure refactor / cleanup / test**: leave as single task. Mark `[REFACTOR-ONLY]` in the subject.
- **Cross-concern monolith** (per `feedback_spec_task_size_discipline.md`): apply that feedback first, then re-apply 6.5 to each piece.

Why this stage exists: v0.3 audit (2026-05-03) found 8 tasks shipped "library ALIGNED + production unwired" because the original task subject bundled library + wire-up into one. Single PR review approved the library half; the wire-up half was silently dropped. Splitting at extractor time forces the wire-up to be a separately-trackable, separately-reviewable PR.

Acceptance: after Stage 6.5, every shippable task has either (a) a wire-up follow-up linked via blockedBy, or (b) `[REFACTOR-ONLY]` marker, or (c) is itself the wire-up task.

### Stage 7 — Seed (manager)

- Manager collects all extractor output.
- Resolves ID allocation: TaskCreate per task (sequential — TaskCreate hook requires no `#NNN` prefix, so plain subject; immediate TaskUpdate adds `#<realId>` prefix per `feedback_taskcreate_no_id_prefix`).
- After all tasks created, manager TaskUpdate with `addBlockedBy` per the DAG edges.
- Mark tasks `[ready]` / `[blocked]` per existing TaskList conventions (`feedback_tasklist_conventions`).

### Stage 8 — PR

- One PR against base branch. Title: `spec: <topic> design (vX.Y)`. Body:
  - Summary (3-5 bullets covering scope + slicing + key risks).
  - DAG seeded: list `Task #NNN` IDs for top-level tasks.
  - Review history: count of rounds, how many P0/P1 closed.
  - Test plan: N/A (spec PR; downstream worker PRs cover testing).
- Use `gh pr create --base <base>` (per `feedback_gh_pr_explicit_base`).
- Reviewer optional for spec PR (manager can self-merge after final visual scan); recommend ONE final reviewer if topic is high-stakes.

## Reuse existing skills

Inside this pipeline, individual workers should invoke established skills where they fit:

- `superpowers:brainstorming` — if topic seed is genuinely vague, run brainstorming FIRST (outside this skill) to lock direction.
- `elements-of-style:writing-clearly-and-concisely` (if available) — author + merger workers use it for prose quality.
- `superpowers:writing-plans` — for very complex DAGs, the t extractors can invoke it per chapter range.
- Codebase-aware skills (e.g. `frontend-design`, `react-best-practices`, `playwright-e2e-builder`) — author + reviewers reference them when chapter content touches their domain.

## Pool management

- Whole pipeline lives in ONE pool. ALL workers `cd` to that pool.
- Sequential stages (1, 5, 6→7, 8) → 1 worker at a time, no collision.
- Parallel stages (2, 3, 4) → multiple workers in the same pool. **Each writes to a DISJOINT file set** (chapter-N reviewer N writes only `chapter-N.R<reviewerNum>.review.md`; fixer for chapter-N writes only `chapter-N.md`). No two workers write the same file in the same parallel batch.
- Between stages: `git pull --rebase` to absorb sibling commits before next worker starts.

## Anti-patterns

- ❌ Single mega-worker doing author + review + fix + DAG (context blow, single-thread, no quality cross-check).
- ❌ Multiple PRs (one per chapter) — fragments review, makes "spec is locked" status ambiguous.
- ❌ Asking user mid-pipeline (manager owns dispatch / merge / fix / loop decisions).
- ❌ Letting reviewers modify chapter content directly (creates merge conflicts; reviewers FIND, fixers FIX).
- ❌ Skipping the merge stage and shipping m chapter files as the final design (downstream readers expect ONE doc).
- ❌ Single DAG extractor on a large spec (context blow — past v0.3 lesson).
- ❌ Writing the merged spec into the chapter directory and the canonical path simultaneously (duplication risk).

## Outputs

- 1 PR: spec doc + (deleted) chapter dir + (deleted) review files.
- N TaskList entries seeded with proper IDs and blocker DAG.
- Pipeline meta-notes saved to `docs/superpowers/specs/<topic>-pipeline-log.md` (rounds run, reviewer findings closed, extractor chapter splits) — optional, useful for audit.
