#!/usr/bin/env python3
"""
PostToolUse hook — validates a just-written memory file before the agent claims success.

This is the thing a phone-side capture bot with a nightly cron CANNOT do: it runs
*in the loop*, the moment a brain file is written, and blocks the agent's turn until
the violation is fixed. Schema enforcement at write time, not at 3am after the fact.

Runs after Write/Edit. Reads the Claude Code PostToolUse JSON payload from stdin
(`tool_input.file_path` holds the path). Only validates files under
`<...>/hypotheses/` or `<...>/decisions/` — where orphan-evidence and broken
provenance links cause the most damage. Other writes pass through silently.

Two severity tiers:

  BLOCKING (exit 2 — stderr is fed back to the model, which fixes and retries):
    - Evidence row with ZERO provenance attempt: no enum tag AND no
      [ingestion/...] / [source/...] link. Always fixable in-turn (add an
      (intuition, PM, <date>) tag or a link), so it blocks.

  WARNING (exit 0 + stderr — informational, agent fixes when the dependency lands):
    - Path-typed provenance link whose target doesn't resolve yet.
    - Any other broken internal markdown link.

Designed for a PM-OS-style layout where the cognition pipeline lives under
`context-library/`:  source/ -> ingestion/ -> {hypotheses,decisions,...}. The brain
root is auto-discovered, so it also works in a flat brain repo.

Adapted from Pawel Huryn's pm-brain validator (github.com/phuryn/pm-brain),
re-tuned for the PM-OS context-library and the Claude Code harness. Standalone,
no third-party imports.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote


# ----- provenance enum (the vocabulary the brain enforces) -----

# Inner whitespace around the tokens is tolerated ("( industry-knowledge )" is the same tag),
# but the stakeholder-verbal / intuition NAME slot must hold a real, non-blank name
# ([^,\s][^,]* = at least one non-space char), so "(stakeholder-verbal, , 2026-05-13)" is NOT a
# valid tag.
_PROVENANCE_NON_PATH_RES = (
    re.compile(r"\(\s*stakeholder-verbal\s*,\s*[^,\s][^,]*,\s*\d{4}-\d{2}-\d{2}\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*intuition\s*,\s*[^,\s][^,]*,\s*\d{4}-\d{2}-\d{2}\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*industry-knowledge\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*chat\s*,\s*no\s+artifact\s*\)", re.IGNORECASE),
)

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
# CommonMark reference-style links: a definition `[label]: target` plus a use `[text][label]`,
# collapsed `[label][]`, or shortcut `[label]`. A citation written this way is still a working
# link to a real file, so it must count as provenance, not false-block.
_REF_DEF_RE = re.compile(r"^[ \t]*\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)
_REF_FULL_RE = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
_REF_SHORTCUT_RE = re.compile(r"\[([^\]]+)\](?!\s*[\(\[])")
# Evidence rows can be unordered (-, *) OR ordered (1. / 1)) — an ordered list under
# Evidence is still a list of claims, each of which must wear a source.
_ROW_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$")

# A header opens an audited section only when it *is* an Evidence section ("## Evidence",
# "### Evidence detail", "Evidence (addendum)") or a decision's "Explicitly NOT doing"
# section. A naive `"evidence" in header` substring match wrongly fires on words like
# "Notevidence" or instructional headers ("How to write evidence (docs)"), turning ordinary
# commentary into a false block — exactly the 3am-noise failure this layer promises to avoid.
# An Evidence section is any header that STARTS WITH the standalone word "evidence":
# "Evidence", "Evidence so far", "Evidence summary", "Evidence detail", "Evidence (addendum)",
# "Evidence for|against …". The one exclusion is a HYPHEN right after it ("Evidence-based
# rollout plan", "Evidence-quality concerns") — there "evidence" is a compound adjective for a
# different topic, not a list of sourced claims, so auditing it would false-block plan rows.
# (?![\w-]) = the char after "evidence" must be a space, "(", ":", or end — not a letter/hyphen.
_AUDITED_HEADER_RE = re.compile(r"^evidence(?![\w-])", re.IGNORECASE)


def _is_audited_header(header: str) -> bool:
    # Normalize markdown emphasis so a styled heading ("## **Evidence**", "## _Evidence_") is
    # recognized exactly like the plain "## Evidence".
    h = re.sub(r"[*`]", "", header).strip().strip("_").strip()
    # "Explicitly NOT doing" must be the section heading, not merely contained in it — a
    # rationale header like "Why we are explicitly not doing alerts" is commentary, not a list.
    return bool(_AUDITED_HEADER_RE.match(h)) or h.lower().startswith("explicitly not doing")

_BARE_PLACEHOLDER_RE = re.compile(
    r"^\s*[*_`]*\s*"
    r"\(?\s*(none(\s+yet)?|n/?a|tbd|todo|"
    r"nothing\s+yet|no\s+evidence(\s+yet)?|"
    r"not\s+yet|pending|open|[—–-])\s*\)?"
    r"\s*[*_`]*\s*[.!]?\s*$",
    re.IGNORECASE,
)

# A parenthetical absence note ("(none yet)", "(no evidence found in Q1)", "(nothing here)")
# is exempt. A load-bearing claim that merely STARTS with an absence word is NOT — it must
# still block. Two discriminators separate them:
#   - a comma introduces a second, substantive clause: "(no evidence the redesign helped, but
#     support tickets dropped 30%)" — so the exempt tail is comma-free ([^,)]*).
#   - "none/nothing OF <noun>" is a quantifier claim about real things ("(none of our top
#     accounts use the monthly export)"), not an absence note — excluded via (?!\s+of).
_PAREN_ABSENCE_RE = re.compile(
    r"^\s*[*_`]*\s*\(\s*"
    r"(?:none(?!\s+of)|nothing(?!\s+of)|no\s+evidence|n/?a|tbd|not\s+yet)"
    r"[^,)]*\)\s*[*_`]*\s*[.!]?\s*$",
    re.IGNORECASE,
)

# "Evidence for|against:" label: leading list marker optional, asterisk- OR underscore-bold,
# any inline claim after the colon captured. All of these render identically in markdown:
#   - **Evidence for:**     **Evidence for:**     - __Evidence for:__
_BOLD_EVIDENCE_LABEL_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*|__)\s*Evidence\s+(?:for|against)\b[^*_:]*"  # allow annotation: "for the redesign"
    r"(?::\s*(?:\*\*|__)|(?:\*\*|__)\s*:)"  # colon INSIDE (**…:**) or OUTSIDE (**…**:) the emphasis
    r"\s*(.*)$",
    re.IGNORECASE,
)

# Strip BOTH backtick and tilde fences (CommonMark treats them equivalently), allowing
# leading indentation and a matching closing fence of the same kind/length (\1 backref).
_FENCED_CODE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# An HTML comment renders as nothing, so a provenance tag parked inside one is invisible to a
# reader — it must not satisfy the audit (same laundering we strip code spans to prevent).
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_code_spans(text: str) -> str:
    text = _HTML_COMMENT_RE.sub("", text)
    text = _FENCED_CODE_RE.sub("", text)
    # Strip inline code per line, but PRESERVE header lines so a backtick-styled heading
    # ("## `Evidence`") survives for the header normalizer to unwrap — stripping the inline
    # code here would erase the section name and silently stop auditing it.
    out = [ln if _HEADER_RE.match(ln) else _INLINE_CODE_RE.sub("", ln) for ln in text.split("\n")]
    return "\n".join(out)


def _is_empty_evidence_placeholder(row: str) -> bool:
    stripped = row.strip()
    return bool(_BARE_PLACEHOLDER_RE.match(stripped) or _PAREN_ABSENCE_RE.match(stripped))


# A schema template row — the `<provenance-tag>` literal, or a lone angle-bracket token like
# `<claim>` / `<not-doing>` copied straight from _SCHEMA — is a placeholder, not a real claim.
# (Checked AFTER code-stripping, which removes a `<provenance-tag>` written in backticks, so we
# also recognize the bare `<claim>` token that's left behind.)
_TEMPLATE_TOKEN_RE = re.compile(r"^<[^>]+>$")


def _is_template_placeholder(row: str) -> bool:
    stripped = row.strip()
    return "<provenance-tag>" in stripped or bool(_TEMPLATE_TOKEN_RE.match(stripped))


def _resolve_pipeline_link(raw_target: str, file_parent: Path, work_dir: Path):
    """Classify ONE link target. Returns (verdict, reason):
      None    — not a pipeline-citation attempt at all (http/mailto, or no source/ingestion
                path SEGMENT — a look-alike like datasource/ or opensource/ does NOT count)
      "warn"  — looks like a pipeline citation but doesn't resolve / is outside root / isn't the
                top-level source|ingestion
      "ok"    — resolves to a file under the top-level pipeline source/ or ingestion/
    """
    target = raw_target.split("#", 1)[0].strip()
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None, ""
    target = unquote(target)  # CommonMark percent-encoding: %20 -> space, etc.
    # Whole-segment match, not substring: "datasource/", "opensource/", "resource/" are NOT
    # the pipeline; a row whose only "link" is one of those has no provenance attempt.
    segs = [s for s in target.split("/") if s and s != ".."]
    if "ingestion" not in segs and "source" not in segs:
        return None, ""
    if target.endswith("/"):
        # A trailing slash is a directory request, not a file artifact.
        return "warn", f"path-typed tag ends in '/', not a file: {target}"
    resolved = (file_parent / target).resolve()
    if not resolved.exists():
        return "warn", f"path-typed tag doesn't resolve yet: {target}"
    if not resolved.is_file():
        return "warn", f"path-typed tag points at a directory, not a file artifact: {target}"
    try:
        rel = resolved.relative_to(work_dir.resolve())
    except ValueError:
        return "warn", f"path-typed tag outside brain root: {target}"
    parts = rel.parts
    # Must be the TOP-LEVEL pipeline source/ or ingestion/ (PROVENANCE.md enum + decisions/
    # _SCHEMA rule 3: `../source/<rest>`). A nested look-alike (research/source/) is a warning.
    if not parts or parts[0] not in ("source", "ingestion"):
        return "warn", f"path-typed tag not under the top-level source/ or ingestion/: {target}"
    return "ok", ""


def _reference_targets(row_text: str, ref_defs: dict):
    """Resolve CommonMark reference-style links in the row to their defined targets."""
    out = []
    for m in _REF_FULL_RE.finditer(row_text):       # [text][label] / [text][]
        label = (m.group(2).strip() or m.group(1).strip()).lower()
        if label in ref_defs:
            out.append(ref_defs[label])
    for m in _REF_SHORTCUT_RE.finditer(row_text):   # [label] not followed by ( or [
        label = m.group(1).strip().lower()
        if label in ref_defs:
            out.append(ref_defs[label])
    return out


def _classify_provenance(row_text: str, file_parent: Path, work_dir: Path,
                         ref_defs: dict | None = None) -> tuple[str, str]:
    """
    Returns (verdict, reason). Verdict:
      "ok"      — valid enum tag or a resolvable path-typed link
      "warn"    — path-typed link attempt, target doesn't resolve yet (ordering issue)
      "orphan"  — no provenance attempt at all (blocking; always fixable in-turn)
    """
    # Match the enum on link-STRIPPED text: an enum-shaped string sitting in a markdown link
    # label ("[(industry-knowledge)](https://competitor.com)") is decoration on an external
    # URL, not a real tag, and must not launder the claim.
    enum_text = LINK_RE.sub(" ", row_text)
    for rx in _PROVENANCE_NON_PATH_RES:
        if rx.search(enum_text):
            return "ok", ""
    has_attempt = False
    warn_reason = ""
    targets = [lm.group(2) for lm in LINK_RE.finditer(row_text)]
    if ref_defs:
        targets += _reference_targets(row_text, ref_defs)
    for raw in targets:
        verdict, reason = _resolve_pipeline_link(raw, file_parent, work_dir)
        if verdict is None:
            continue
        if verdict == "ok":
            return "ok", ""
        has_attempt = True
        warn_reason = reason
    if has_attempt:
        return "warn", warn_reason
    return "orphan", "no provenance tag (must be path-typed or match the enum)"


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
_BOLD_LABEL_ONLY_RE = re.compile(r"^(?:\*\*|__)[^*_]+:(?:\*\*|__)\s*$")

# The named structural fields of a hypothesis (and decision) record. When one of these appears
# — bulleted or not, with or without inline content — it ENDS the current Evidence block: it is
# a field, not an evidence claim, so its row must not be audited as orphan evidence.
_FIELD_LABEL_RE = re.compile(
    r"^(?:[-*]\s+|\d+[.)]\s+)?(?:\*\*|__)\s*"
    r"(?:origin|confidence|open\s+questions|caveats|test|decision\s+trigger|status|"
    r"resolution|meta|evidence\s+(?:for|against)|blocker\s+impact|deadline|owner|"
    r"missing\s+evidence)\b[^*_]*:(?:\*\*|__)",
    re.IGNORECASE,
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _collect_item(lines: list[str], start: int) -> tuple[str, int]:
    """A logical Evidence item = the list bullet at `start` PLUS its continuation lines:
    soft-wrapped prose and nested elaboration sub-bullets (anything blank or indented MORE
    than the bullet). It ends at a header, EOF, or a line at indent <= the bullet's indent
    (a sibling claim). The provenance tag may live anywhere in the item — on the bullet line,
    a wrapped continuation, or a child bullet — so a sourced-but-multi-line claim is not a
    false orphan, and an elaboration sub-bullet inherits its parent's source instead of being
    demanded its own. Returns (joined-text, index-after-item)."""
    base = _indent(lines[start])
    parts = [_MARKER_RE.sub("", lines[start]).strip()]
    j = start + 1
    seen_blank = False
    while j < len(lines):
        l = lines[j]
        if not l.strip():
            seen_blank = True
            j += 1
            continue
        if _HEADER_RE.match(l):
            break
        if _FIELD_LABEL_RE.match(l):
            break  # a new structural field (Test, Status, Open questions…) — not part of this claim
        ind = _indent(l)
        if ind > base:
            # nested elaboration sub-bullet or an indented wrap
            parts.append(_MARKER_RE.sub("", l).strip())
            j += 1
            seen_blank = False
            continue
        # ind <= base
        if _ROW_RE.match(l):
            break  # a sibling list item is its own claim
        if seen_blank:
            break  # flush-left text after a blank line is a new block, not this item
        # CommonMark lazy continuation: a flush-left wrap line immediately under the bullet
        # (e.g. the provenance tag on its own line at indent 0) is part of THIS claim.
        parts.append(l.strip())
        j += 1
    return " ".join(p for p in parts if p), j


def _iter_evidence_items(text: str):
    """Yield each logical Evidence / 'Explicitly NOT doing' / 'Evidence for|against' item,
    as a single joined string. The unit of audit is the logical list item, not the physical
    line."""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    in_section = False
    section_depth = 0
    while i < n:
        line = lines[i]
        hm = _HEADER_RE.match(line)
        if hm:
            d = len(hm.group(1))
            if _is_audited_header(hm.group(2)):
                in_section = True
                section_depth = d
            elif in_section and d <= section_depth:
                in_section = False
            i += 1
            continue
        # A bold "**Evidence for|against:**" label opens its own sub-block; its claims are the
        # bullets indented under it. Handled before the section-bullet path so the label line
        # itself is never mistaken for a claim.
        bm = _BOLD_EVIDENCE_LABEL_RE.match(line)
        if bm:
            label_indent = _indent(line)
            inline_claim = bm.group(1).strip()  # a claim written ON the label line
            if inline_claim:
                yield inline_claim
            i += 1
            # Collect the claim bullets under this label. They may be nested deeper (the
            # canonical `- **Evidence for:**` form) OR a flat list at the same indent (an
            # unbulleted `**Evidence for:**` label). The block ends at a header, the next
            # Evidence-for/against label, another bold sub-label (Open questions, Evidence
            # against…), or dedented non-list prose.
            while i < n:
                l2 = lines[i]
                if not l2.strip():
                    i += 1
                    continue
                if (_HEADER_RE.match(l2) or _BOLD_EVIDENCE_LABEL_RE.match(l2)
                        or _FIELD_LABEL_RE.match(l2)):
                    break  # header, next Evidence label, or a sibling field (Test/Status/Open questions…)
                if _ROW_RE.match(l2):
                    if _BOLD_LABEL_ONLY_RE.match(_MARKER_RE.sub("", l2).strip()):
                        break  # a sibling bold sub-label ends this label's claims
                    item, i = _collect_item(lines, i)
                    yield item
                    continue
                if _indent(l2) <= label_indent:
                    break  # dedented prose / new block
                i += 1
            continue
        if in_section and _ROW_RE.match(line):
            item, i = _collect_item(lines, i)
            yield item
            continue
        i += 1


# ----- memory-root discovery -----

# Host-agnostic. The install drops a `.memory-root` marker file at the chosen memory root
# (PM-OS / Job Search OS use context-library/; Team OS or a bare project use memory/).
# We also fall back to detecting OUR OWN pipeline folders, so the hook works even if the
# marker is missing — these four are created together at install regardless of host OS.
_MEMORY_ROOT_MARKER = ".memory-root"
_PIPELINE_DIRS = ("source", "ingestion", "hypotheses", "decisions")


def _find_work_dir(file_path: Path) -> Path | None:
    cur = file_path.parent.resolve()
    while True:
        if (cur / _MEMORY_ROOT_MARKER).is_file():
            return cur
        sub_count = sum(1 for d in _PIPELINE_DIRS if (cur / d).is_dir())
        if sub_count >= 2:
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


# ----- payload parsing -----

def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _extract_file_paths(payload: dict) -> list[Path]:
    out: list[Path] = []
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "filePath", "path"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            out.append(Path(v))
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                fp = e.get("file_path") or e.get("filePath")
                if isinstance(fp, str) and fp:
                    out.append(Path(fp))
    seen = set()
    result: list[Path] = []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _is_brain_file(rel: Path) -> bool:
    """Evidence-audited if it lives under hypotheses/ or decisions/ anywhere in the path
    (excludes the schema/index templates themselves)."""
    parts = rel.parts
    if "hypotheses" not in parts and "decisions" not in parts:
        return False
    if rel.name in {"_SCHEMA.md", "INDEX.md"}:
        return False
    return rel.suffix == ".md"


def _validate_evidence(file_path: Path, work_dir: Path) -> tuple[list[str], list[str]]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as e:
        return ([f"  - read failed: {e}"], [])
    # Illustrative code (a ``` fenced schema example, an inline `(intuition, PM, <date>)`
    # showing the tag format) is documentation, not a load-bearing claim. Strip code spans
    # before scanning so examples never false-block and a tag hidden in `backticks` never
    # launders an orphan claim into passing.
    text = _strip_code_spans(text)
    ref_defs = {m.group(1).strip().lower(): m.group(2).strip()
                for m in _REF_DEF_RE.finditer(text)}
    orphans: list[str] = []
    warns: list[str] = []
    for row in _iter_evidence_items(text):
        if (not row
                or _is_template_placeholder(row)
                or _is_empty_evidence_placeholder(row)
                or _BOLD_LABEL_ONLY_RE.match(row)):
            continue
        verdict, reason = _classify_provenance(row, file_path.parent, work_dir, ref_defs)
        snippet = row[:90] + ("…" if len(row) > 90 else "")
        if verdict == "orphan":
            orphans.append(f"  - {reason} :: {snippet}")
        elif verdict == "warn":
            warns.append(f"  - {reason} :: {snippet}")
    return (orphans, warns)


def _validate_links(file_path: Path) -> list[str]:
    if file_path.name == "_SCHEMA.md":
        return []
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return []
    text = _strip_code_spans(text)
    broken = []
    for m in LINK_RE.finditer(text):
        target = m.group(2).split("#", 1)[0].strip()
        if not target:
            continue
        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue
        if "{{" in target or ("<" in target and ">" in target):
            continue
        target = unquote(target)  # CommonMark percent-encoding: %20 -> space, etc.
        resolved = (file_path.parent / target).resolve()
        if not resolved.exists():
            broken.append(f"  - {target}")
    return broken


def main() -> int:
    payload = _read_payload()
    file_paths = _extract_file_paths(payload)
    if not file_paths:
        return 0

    blocking: list[str] = []
    warnings: list[str] = []

    for fp in file_paths:
        if not fp.is_absolute():
            fp = fp.resolve()
        if not fp.exists() or fp.suffix != ".md":
            continue
        work_dir = _find_work_dir(fp)
        if work_dir is None:
            continue
        try:
            rel = fp.resolve().relative_to(work_dir.resolve())
        except ValueError:
            continue

        link_problems = _validate_links(fp)
        if link_problems:
            warnings.append(
                f"{rel.as_posix()} — internal links don't resolve yet "
                "(may be an ordering issue — fix when the target is written):"
            )
            warnings.extend(link_problems)

        if _is_brain_file(rel):
            orphans, warns = _validate_evidence(fp, work_dir)
            if orphans:
                blocking.append(
                    f"{rel.as_posix()} — Evidence / 'Explicitly NOT doing' rows with NO "
                    "provenance attempt (add an enum tag or a path-typed link):"
                )
                blocking.extend(orphans)
            if warns:
                warnings.append(
                    f"{rel.as_posix()} — provenance links don't resolve yet "
                    "(probably written before the source/ingestion file — fix when it lands):"
                )
                warnings.extend(warns)

    if warnings:
        print(
            "[pm-memory hook] warnings — non-blocking, fix when dependencies land:\n\n"
            + "\n".join(warnings),
            file=sys.stderr,
        )

    if blocking:
        msg = (
            "[pm-memory hook] BLOCKING schema violation — fix in THIS turn before continuing:\n\n"
            + "\n".join(blocking)
            + "\n\nEvery Evidence row needs one provenance tag:\n"
            "  - [ingestion/...](<relative-path>) or [source/...](<relative-path>)\n"
            "  - (stakeholder-verbal, <name>, <YYYY-MM-DD>)\n"
            "  - (intuition, PM, <YYYY-MM-DD>)\n"
            "  - (industry-knowledge)\n"
            "  - (chat, no artifact)\n"
            "Empty placeholders like '(none yet)' or 'TBD' are exempt.\n"
            "Caveats and inferences belong under 'Open questions / caveats:', NOT under Evidence.\n"
            "If the path-typed file doesn't exist yet, write it first or use an enum tag "
            "like (intuition, PM, <date>) and upgrade the tag when the artifact lands."
        )
        print(msg, file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
