# Author worker prompt template

Use this template when dispatching the Stage-1 author worker in `spec-pipeline`.

```
Task #<NNN> — Spec author for <topic>

You are the author worker in a spec-pipeline run.

**Pool**: <pool path>
**Branch**: spec/YYYY-MM-DD-<topic> (already created off <base>)
**Output dir**: docs/superpowers/specs/<topic>-chapters/
**Topic seed**: <one-paragraph or link to prior design>

## Your job
Produce a multi-file design as separate chapter markdown files. Do NOT produce a single merged spec — that's a later stage.

## Chapter conventions
- File names: `00-overview.md`, `01-goals.md`, `02-architecture.md`, ... — pick a reasonable split for the topic shape. Typical 8-12 chapters.
- Each chapter is standalone (own H1, own context paragraph, own intra-chapter TOC if >3 sections).
- Code fences use language tag.
- All cross-chapter references use relative links: `[architecture](./02-architecture.md)`.
- Diagrams in ASCII or mermaid (mermaid preferred where renderable).

## Quality bar
- No "TBD" placeholders. Pick a position; reviewers will challenge.
- No vague verbs ("should consider", "might want"). Use "MUST / SHOULD / MAY" or be definitive.
- Each architectural decision has a one-line "Why:" justification.
- Each "Non-goal" / "Deferred" call-out has a "Why deferred:" + version target.

## What NOT to write
- Implementation code (this is design, not impl).
- Task DAG (later stage).
- The merged single-file spec (later stage).

## Commit + push
- Commit each chapter separately: `docs(spec/<topic>): chapter NN <name>`.
- Push to origin.
- Do NOT open a PR yet (manager opens the final consolidated PR in stage 8).

## Verify
- Run `markdownlint` if configured.
- Re-read each chapter; fix obvious typos/contradictions.
- Report back to manager: chapter file list + LOC per chapter + any topics you couldn't decide on (they become open questions for reviewers).

Use Opus 4.7 1M context. Take your time — quality > speed.
```
