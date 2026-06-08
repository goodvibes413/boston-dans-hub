# Provenance — the vocabulary the brain enforces

Every load-bearing claim in the brain wears a provenance tag. A claim is load-bearing
when it drives downstream work: hypothesis evidence rows, decision evidence rows,
promoted user insights, strategy tensions, stakeholder concerns.

The system enforces the **vocabulary, not the workflow.** Real PM work is messy. You
have intuitions, you hear things off-the-record from an exec, you inherit claims with
no clear pedigree. Those are legitimate inputs. The brain just makes them wear their
actual provenance instead of laundering them through a fake `ingestion/` record.

The auditability promise is "every claim wears its source," not "every claim was
synthesized." A missing tag is the only bug.

## The enum

| Tag | Means | Trust |
|---|---|---|
| `[ingestion/<path>](<relative-path>)` | Went through synthesis. The ingestion file itself links back to a `source/` artifact. | Highest |
| `[source/<path>](<relative-path>)` | Direct citation to a raw artifact. Use when the source is self-explanatory and synthesis would be ceremony. | High |
| `(stakeholder-verbal, <name>, <YYYY-MM-DD>)` | Heard from a person, no recording or doc. | Medium — depends on the person |
| `(intuition, PM, <YYYY-MM-DD>)` | Your own read, no external evidence yet. Still a legitimate hypothesis input. | Low externally, useful internally |
| `(industry-knowledge)` | Accepted background, not specific to this product ("checkout friction reduces conversion"). | Low — flag for replacement |
| `(chat, no artifact)` | Synthesized in this conversation, nothing written down. Often a precursor to a future ingestion record. | Low |

## Rules the hook enforces

1. **Path-typed tags** (`[ingestion/...]`, `[source/...]`) must be working markdown links
   to files that exist, under the **top-level pipeline** `source/` or `ingestion/` (one `..`
   from a decisions/ or hypotheses/ file). A nested look-alike like `research/source/…` is a
   warning, not a trusted citation. A link that doesn't resolve yet is also a warning, not a
   block — write the source file or downgrade the tag.
2. **Non-path-typed tags** must match one of the parenthetical forms exactly. Don't invent
   new categories silently. If a new provenance type recurs, propose adding it here.
3. **A row with no tag is an orphan claim** and is rejected at write time by the PostToolUse
   hook (`.claude/hooks/validate_memory_file.py`).

### What "row" means, exactly

The unit the hook audits is the **logical list item** under an Evidence / `Explicitly NOT
doing` / `Evidence for|against:` section: a bullet (or numbered) row *plus* its soft-wrapped
continuation lines and any nested elaboration sub-bullets. The provenance tag may sit
anywhere in that item — the wrap or a child bullet counts — so a sourced multi-line claim is
never a false orphan, and an elaboration sub-bullet inherits its parent's source.

Two deliberate boundaries of this deterministic check:
- **It audits list rows, not free prose.** A claim written as a bare paragraph under
  `## Evidence` is not scanned. Write claims as bullets so each one wears its source — that's
  what the schema template does.
- **It enforces the provenance vocabulary, not the rest of the schema.** `## Status` being a
  valid enum value and `## What would reverse this` being present, specific, and observable
  are **advisory pre-save self-checks** (the schema says "run mentally"), not hook-enforced.
  Per this layer's design, provenance is the deterministic floor; schema completeness stays
  the writer's judgment.

## Evidence rows are CLAIMS, not commentary

A row under `Evidence for:` / `Evidence against:` (hypotheses) or `## Evidence` (decisions)
must assert something the world told us — "Acme's ops lead said weekly batches are unusable."

If you are noting what we *don't* know, what we're *inferring*, or what a signal *doesn't*
establish, that belongs under `Open questions / caveats:` (hypotheses) or
`## Remaining ambiguities` (decisions). Those rows do not need tags — by construction
they are things not yet established. Mixing them into Evidence is the fastest way to make
the trail unfalsifiable.

**Aggregation/meta rows are not evidence.** "N=3 customers, mixed sentiment" is a claim
*about* the evidence, not a claim someone made. It has no single source path. Put it under
caveats, or split it into one tagged Evidence row per artifact.
