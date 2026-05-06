# Fixer worker prompt template

Use this template when dispatching Stage-3 fixers in `spec-pipeline`.

```
Task #<NNN> — Spec fixer (chapter <id>) for <topic> round <r>

You are the fixer for chapter <id>: <chapter title>.

**Pool**: <pool path>
**Chapter file**: docs/superpowers/specs/<topic>-chapters/<id>-<name>.md
**Review files** (yours to address):
  - <list every .R<n>.review.r<r>.md for this chapter>
**Cross-file context**: <if cross-file fixer, list every chapter you own + links to all relevant review files>

## Your job
Address every P0 + P1 finding in your assigned review files. P2 deferred unless trivial.

## Process
1. Read your chapter file fully.
2. Read each review file fully.
3. Build a per-finding plan: which line / section changes, what new content needed.
4. Apply edits.
5. Re-read your chapter end-to-end. Self-check: do the fixes introduce internal contradictions?
6. If a finding requires content you can't write (insufficient domain knowledge), leave a `<!-- FIXER-BLOCKED: <reason> -->` marker and report the gap to manager.

## Edit conventions
- Preserve chapter structure (H1, section order). Add subsections if a finding requires new content.
- Match chapter prose style (terse / "MUST/SHOULD/MAY" / "Why:" lines).
- For numeric/timing/path constants: cite the spec source (`per frag-X.Y line Z`) so future readers see the lock.
- For deferred items: write "Deferred to vX.Y. Why: <reason>." rather than deleting.

## Cross-file fixer special rules
If you own a cross-file finding (e.g. "rename term X across chapters 3, 4, 5"):
- Make the rename consistently in EVERY listed chapter in ONE commit.
- Verify with grep that no stale name remains.
- Check chapter cross-links still resolve.

## Anti-patterns
- ❌ Don't address P2 unless trivial (manager controls scope).
- ❌ Don't introduce content beyond what findings demand.
- ❌ Don't argue with reviewer findings in the file. If you disagree with a finding, write a counter-note in your manager report; the manager decides.
- ❌ Don't touch chapters you weren't assigned (mutex by file ownership during parallel fix batch).

## Commit + push
- One commit per chapter file: `fix(spec/<topic>): chapter <id> address R<list> r<round>`
- Push to origin.

## Verify
- Markdownlint clean (if configured).
- Self-read chapter, confirm no broken cross-links.
- Report back to manager: list of finding IDs addressed (P0-1, P1-2, ...) + any you deferred + counter-notes if any.

Use Opus 4.7 1M context.
```
