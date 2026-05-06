# DAG extractor worker prompt template

Use this template when dispatching Stage-6 parallel DAG extractors in `spec-pipeline`.

```
Task #<NNN> — DAG extractor (chapter range <range>) for <topic>

You are extractor #<n> of t.

**Pool**: <pool path>
**Spec path**: docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
**Your assigned chapter range**: <e.g. § 2 + § 3 + § 4>
**Sibling extractors are covering**: <other ranges, so you don't duplicate>

## Your job
Read your chapter range. Emit a YAML list of implementation tasks needed to realize that part of the spec.

## Output format
Write to: `docs/superpowers/specs/<topic>-dag-chunks/extractor-<n>.yaml`

```yaml
# Extractor <n>: chapters <range>
tasks:
  - subject: "[T<level>.<seq>] <imperative description>"
    notes: |
      <1-3 lines: what this task delivers, key files, success criteria>
    blocked_by_subject:  # use SUBJECT strings of prerequisite tasks (manager resolves to IDs at seed time)
      - "[T<level>.<seq>] <prereq subject>"
    estimate_minutes: <rough number>
  ...
```

## Naming conventions
- Subject MUST start with `[T<level>.<seq>]`. Levels:
  - L0 = scaffold (tsconfig, dirs, deps, plumbing)
  - L1 = primitives (utils, base types)
  - L2 = transports (sockets, files, queues)
  - L3 = handlers (RPC handlers, decoders)
  - L4 = persistence (schema, migrations)
  - L5 = lifecycle / runtime (FSMs, schedulers, supervisors)
  - L6 = build / packaging
  - L7 = harness / dev
  - L8 = e2e probes
- Sequence (`<seq>`) starts at 1 within your level. Manager renumbers globally at seed time.
- Subject is imperative ("Add X", "Wire Y", "Migrate Z") — not noun phrase.

## Atomicity rule
Each task MUST be:
- Single-PR-sized (≤2 files, ≤300 LOC, ≤90 min worker time).
- Independently testable.
- Has clear "done" criteria.

If a chapter section requires more than that, SPLIT it into multiple tasks with explicit blockers.

## Blocker rules
- Use `blocked_by_subject` (string) not numeric ID — manager resolves at seed time.
- Only declare a blocker if the dependency is REAL (one task literally cannot start before the other completes). False blockers serialize the team unnecessarily.
- If unsure, leave it unblocked; manager review catches over-eager parallelization at seed time.

## Anti-patterns
- ❌ Don't emit tasks outside your assigned chapter range (sibling extractors own those).
- ❌ Don't speculate beyond the spec — if the spec is silent on something, mark it as `notes: needs spec clarification` and emit no task.
- ❌ Don't bundle 5 sub-features into one mega-task. Atomicity rule above.
- ❌ Don't call TaskCreate yourself. Manager owns ID allocation + the actual seed.

## Commit + push
- Commit: `chore(spec/<topic>): DAG chunk extractor <n>`
- Push to origin.

## Verify
- Self-read your YAML: every task has subject + notes + blocked_by_subject (possibly empty) + estimate.
- No duplicate subjects across your tasks.
- Report back to manager: count of tasks per level + total estimated minutes for your chunk + any "needs spec clarification" gaps.

Use Opus 4.7 1M context.
```
