#!/usr/bin/env python3
"""
Stop hook — the "night cycle," reframed as a session-end pass that runs IN the loop.

GBrain runs consolidation as a 3am cron on a host that has to be awake. The Claude
Code harness doesn't need a sleeping-laptop cron: the natural consolidation boundary
is the end of a working session. When the agent tries to finish a turn, this hook
checks whether the session left uncommitted changes in the brain. If it did, it
blocks the stop ONCE and hands the agent a consolidation checklist (promote durable
knowledge, complete audit trails, update INDEXes, then commit). Human-in-loop, in the
same context that did the work — no scheduler, no always-on host, no single-writer
database to corrupt.

Contract (Claude Code Stop hook):
  - stdin carries `stop_hook_active`. When true, the agent is ALREADY continuing
    because of a prior Stop block — we exit 0 to avoid an infinite loop.
  - To force the agent to keep working, print JSON to stdout:
        {"decision": "block", "reason": "<the consolidation instruction>"}
    The `reason` becomes the agent's next instruction.
  - Print nothing (exit 0) to let the agent stop.

Safe by construction: if this isn't a git repo, or git is unavailable, or nothing in
the brain changed, the hook stays silent and the session ends normally.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# What counts as "the brain" is ANCHORED to the install, not guessed from generic folder
# names. Both hooks key off the `.memory-root` marker the installer drops, plus the
# promotion-map in `.memory-config.md` that records where THIS host's durable knowledge lands.
# That's what makes a change to `context-library/decisions/x.md` consolidate while a C
# project's unrelated `firmware/source/main.c` (or `tests/fixtures/decisions/`, or
# `benchmarks/memory/`) does NOT false-fire the stop every session.
_PIPELINE_DIRS = ("source", "ingestion", "hypotheses", "decisions")
_MEMORY_ROOT_MARKER = ".memory-root"
_MEMORY_CONFIG = ".memory-config.md"


def _memory_roots(cwd: Path) -> set[str]:
    """Repo-relative dirs (posix; "" == repo root) that hold a .memory-root marker."""
    roots: set[str] = set()
    for pat in (_MEMORY_ROOT_MARKER, "*/" + _MEMORY_ROOT_MARKER, "*/*/" + _MEMORY_ROOT_MARKER):
        try:
            for marker in cwd.glob(pat):
                rel = marker.parent.relative_to(cwd).as_posix()
                roots.add("" if rel == "." else rel)
        except OSError:
            pass
    return roots


def _promotion_homes(cwd: Path, roots: set[str]) -> set[str]:
    """Leading path segments of the promotion targets in <root>/.memory-config.md — the
    host-specific durable homes (Team OS `team/`, `product-development/`; PM-OS `context-library/`).
    These can live OUTSIDE the memory root, so they're tracked separately."""
    homes: set[str] = set()
    for r in roots:
        cfg = cwd / (f"{r}/{_MEMORY_CONFIG}" if r else _MEMORY_CONFIG)
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            continue
        # Only harvest from promotion-map TABLE rows (lines with a pipe). A path mentioned in
        # prose backticks ("see `blog/posts/`") is documentation, not a promotion target, and
        # must not become a brain home that false-fires the stop on `blog/`.
        for line in text.splitlines():
            # A real markdown table row STARTS with a pipe. Prose that merely contains a pipe
            # ("stay in `drafts/scratch` | `notes/inbox`") is not a promotion target.
            if not line.lstrip().startswith("|"):
                continue
            for m in re.finditer(r"`([^`]+)`", line):
                tok = m.group(1).strip()
                if "/" not in tok:
                    continue
                seg = tok.lstrip("./").split("/")[0]
                if seg and "<" not in seg:
                    homes.add(seg)
    return homes

# Raw-capture areas. Uncommitted changes ONLY here still warrant a consolidation pass
# (raw capture that never got promoted is exactly what rots), so they count too.
_CONSOLIDATION_CHECKLIST = (
    "Session-end consolidation (run before stopping):\n"
    "1. Promote durable knowledge. Any signal seen across 2+ independent sources, or "
    "that informed a decision/hypothesis, gets promoted from ingestion/ into its "
    "canonical home (knowledge/research, stakeholders, hypotheses). One-offs stay in "
    "ingestion/. Promotion is judgment — do not auto-promote noise.\n"
    "2. Complete audit trails. Every promoted insight names each supporter by source "
    "slug and preserves dissent under Contradictions. Every Evidence row carries a "
    "provenance tag.\n"
    "3. Update the INDEX of any area where you added a file (hypotheses, decisions, "
    "stakeholders).\n"
    "4. Flag, don't resolve, any contradiction with existing knowledge. Surface it; "
    "let the PM decide.\n"
    "5. Then commit the brain: `git add -A && git commit -m \"memory: consolidate <session topic>\"`. "
    "Never push.\n"
    "If nothing meets the promotion bar, say so in one line and commit the raw capture anyway "
    "so it isn't lost. Then you may stop."
)


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


def _cwd(payload: dict) -> Path:
    c = payload.get("cwd")
    if isinstance(c, str) and c:
        return Path(c)
    return Path.cwd()


def _git_porcelain(cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            # --untracked-files=all lists every untracked FILE individually. Without it, a
            # brand-new untracked directory collapses to one line ("?? research/"), hiding the
            # brain segment inside (research/stakeholders/<slug>.md) and letting the first
            # capture of a fresh session escape consolidation.
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _path_touches_brain(path: str, roots: set[str], homes: set[str], cwd: Path) -> bool:
    path = path.strip().strip('"')
    segs = [s for s in path.split("/") if s]
    if not segs:
        return False
    # 1) Under a DEDICATED (non-root) memory-root dir — the pipeline + the store live here.
    for r in roots:
        if r and (path == r or path.startswith(r + "/")):
            return True
    # 2) A repo-root install (.memory-root at the top) treats the repo as the brain, but only a
    #    TOP-LEVEL pipeline folder or promotion home should consolidate — not a README, and not a
    #    nested coincidence like tests/fixtures/decisions/ or vendor/.../source/ deeper in the tree.
    if "" in roots and (segs[0] in _PIPELINE_DIRS or segs[0] in homes):
        return True
    # 3) A promotion home declared in .memory-config.md, wherever it lives (Team OS `team/`).
    if segs[0] in homes:
        return True
    # 4) No marker at all (misconfigured install): only trust a pipeline folder that actually
    #    sits beside its siblings on disk (≥2 pipeline dirs), the way the write-time hook
    #    auto-discovers a root. This rejects a lone coincidental `source/` or `decisions/`.
    if not roots:
        for idx, s in enumerate(segs):
            if s in _PIPELINE_DIRS:
                parent = cwd / Path(*segs[:idx]) if idx else cwd
                try:
                    if sum(1 for d in _PIPELINE_DIRS if (parent / d).is_dir()) >= 2:
                        return True
                except OSError:
                    pass
    return False


def _brain_dirty(porcelain: str, cwd: Path) -> bool:
    roots = _memory_roots(cwd)
    homes = _promotion_homes(cwd, roots)
    for line in porcelain.splitlines():
        # porcelain format: "XY <path>" (renames use "old -> new"). For a rename we check
        # BOTH ends: moving a file OUT of the brain (decisions/x.md -> archive/x.md) deletes
        # a durable file and is just as much a brain mutation as moving one in.
        path = line[3:].strip()
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            if (_path_touches_brain(old, roots, homes, cwd)
                    or _path_touches_brain(new, roots, homes, cwd)):
                return True
        elif _path_touches_brain(path, roots, homes, cwd):
            return True
    return False


def main() -> int:
    payload = _read_payload()

    # Loop guard: if we already blocked once this stop-cycle, let the agent finish.
    if payload.get("stop_hook_active") is True:
        return 0

    cwd = _cwd(payload)
    porcelain = _git_porcelain(cwd)
    if porcelain is None:
        return 0  # not a git repo / git unavailable — never break the session
    if not porcelain.strip() or not _brain_dirty(porcelain, cwd):
        return 0  # brain untouched — nothing to consolidate

    print(json.dumps({"decision": "block", "reason": _CONSOLIDATION_CHECKLIST}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
