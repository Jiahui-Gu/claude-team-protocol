# Reviewer worker prompt template

Use this template when dispatching Stage-2 / Stage-4 reviewers in `spec-pipeline`.

```
Task #<NNN> — Spec reviewer (angle: <R<n>: angle name>) for <topic> round <r>

You are reviewer #<n> for the <topic> spec, focusing on the **<angle>** dimension.

**Pool**: <pool path>
**Chapter dir**: docs/superpowers/specs/<topic>-chapters/
**Round**: <r>

## Your angle
<one of:>
- **R1 Feature-preservation**: This work is <refactor | additive frontend | infra>. Verify the design does NOT change product features unless the change is REQUIRED by the refactor itself. Flag any "while we're at it" feature drift as P0. Examples of acceptable changes: data flow path moves but user-visible behavior preserved. Examples of unacceptable: "we'll also redesign the session list UI" inside a daemon-split spec.
- **R2 Security**: Auth boundaries, sandbox/ACL, sender validation, secret handling, attack surface, DoS caps, supply chain.
- **R3 Reliability / observability**: Failure modes, crash recovery, log strategy, metrics, dashboards, debuggability, idempotency, retry semantics.
- **R4 Scalability / performance**: Hot paths, resource caps, fan-out, contention, latency budgets, memory/CPU budgets per session.
- **R5 Testability**: Each component unit-testable in isolation? E2E story coherent? Test data plan? Flake risks?
- **R6 Naming / consistency / clarity**: Terminology consistent across chapters? Doc TOC navigable? Naming follows codebase convention? Acronyms defined on first use?

## Your job
Read ALL chapter files in the chapter dir. Write your findings to:
`docs/superpowers/specs/<topic>-chapters/<chapter-id>.R<n>.review.r<r>.md`

ONE review file per chapter you have findings on (so a fixer assigned to that chapter has all your notes co-located).

## Finding format
```markdown
# Review of chapter <id>: <chapter title>

Reviewer: R<n> (<angle>)
Round: <r>

## Findings

### P0-1 (BLOCKER): <one-line>
**Where**: chapter <id>, section <#>, line <N>
**Issue**: <what's wrong>
**Why this is P0**: <consequence if shipped as-is>
**Suggested fix**: <concrete change or "needs cross-chapter rename, see also chapter <X>">

### P1-1 (must-fix): <one-line>
... (same shape)

### P2-1 (nice-to-have): <one-line>
... (same shape)

## Cross-file findings (if any)
List findings that touch multiple chapters. Manager will assign these to a single fixer to avoid inconsistency.
```

## Severity rubric
- **P0** = blocker: shipping as-is causes a known correctness, security, or product-direction failure. Examples: missing auth check, contradictory chapters, undefined critical term, broken data flow.
- **P1** = must-fix-before-merge: would land tech debt or confusion immediately. Examples: vague critical decision, missing failure mode, inconsistent naming on a core concept.
- **P2** = nice-to-have: defer to follow-up. Examples: prose polish, additional examples, optional hardening.

## Anti-patterns
- ❌ Don't modify chapter files directly. You FIND, fixers FIX.
- ❌ Don't dump generic "consider X" without "where" + "why P0".
- ❌ Don't mark P0 for things that are P2 (cry-wolf inflation).
- ❌ Don't repeat findings other reviewers already covered. Read sibling `.review.md` files first; if your angle adds nothing new, write `## Findings\n\nNo P0/P1/P2 from <angle>.\n` and stop.

## Verify
- All your `.review.md` files committed.
- Push to origin.
- Report back: count of P0/P1/P2 findings + any cross-chapter findings.

Use Opus 4.7 1M context.
```
