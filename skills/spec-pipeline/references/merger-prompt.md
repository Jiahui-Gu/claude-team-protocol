# Merger worker prompt template

Use this template when dispatching the Stage-5 merger in `spec-pipeline`.

```
Task #<NNN> — Spec merger for <topic>

You are the single merger worker.

**Pool**: <pool path>
**Chapter dir**: docs/superpowers/specs/<topic>-chapters/
**Output path**: docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md

## Your job
Consolidate all chapter files into ONE final design markdown. Then delete the chapter dir + .review files from the working tree.

## Process
1. Read every chapter in numeric order.
2. Build a unified TOC at the top.
3. Promote chapter H1s to H2s under the unified spec H1.
4. Renumber inline section refs to use unified numbering (e.g. chapter 02 § 2 becomes § 2.2).
5. Resolve any remaining inconsistencies you spot (terminology drift, numeric mismatches, broken cross-links). Document any resolutions in a "Merger notes" appendix.
6. Add a top-level changelog entry: `vN — initial spec from spec-pipeline run YYYY-MM-DD`.
7. Add status field at top: `**Status:** locked (pipeline rounds: <count>, P0/P1 closed: <count>)`.
8. Add author + tracks fields per project convention.

## Required header (paste verbatim, fill placeholders)
```markdown
# <Topic Title>

**Status:** locked (spec-pipeline run YYYY-MM-DD, <rounds>r, <P0count> P0 + <P1count> P1 closed, <P2count> P2 deferred)
**Author:** ccsm
**Tracks:** #<task-list>

## Changelog
- **v1 (YYYY-MM-DD)**: initial spec from spec-pipeline. See chapter history in branch.

## Table of contents
<auto-generate from H2s>
```

## Cleanup
After writing the merged spec:
```bash
git rm -r docs/superpowers/specs/<topic>-chapters/
```
The chapter files + review files stay in git history (audit trail) but are removed from PR working tree.

## Anti-patterns
- ❌ Don't add new content beyond what chapters contain. You MERGE; you don't AUTHOR.
- ❌ Don't drop chapter content silently. If a chapter section is redundant with another, MOVE it (consolidate) and note the move in Merger notes.
- ❌ Don't leave the chapter dir in the PR. The final spec is the single file.

## Commit + push
- One commit: `spec: <topic> consolidate chapters → final design`
- Push to origin.

## Verify
- Open the merged file, render mentally. TOC links resolve? Numbering consistent? No orphaned `(see chapter X)` refs?
- Report back to manager: final LOC, count of inconsistencies resolved, any unresolved you flagged in Merger notes.

Use Opus 4.7 1M context.
```
