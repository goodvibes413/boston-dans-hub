#!/usr/bin/env python3
"""check_model_health.py — is the pinned Gemini model still there, and still free?

AGENTS.md's Model Strategy tells a human to "check whether gemini-3.1-flash-lite
itself has been deprecated or re-quota'd before assuming it's a transient spike."
That instruction is correct and nobody runs it — it only gets read *after*
production has already failed for a morning.

This is that check, on a schedule. It asks the API (not the docs) whether the
models the pipeline actually pins still exist, still support generateContent,
and still answer on this key's free tier. Model churn is fast enough to warrant
it: gemini-2.5-flash is slated for retirement 2026-10-16, and the 3.x line moved
3.1 → 3.5 → 3.6 → 3.8 inside four months.

Exit 0 = every pinned model healthy. Exit 1 = at least one is not, and
model_health.yml turns that into a pipeline-degraded issue.

Env vars:
  GEMINI_API_KEY   required
  GEMINI_MODEL     optional, overrides the generation pin being checked
  JUDGE_MODEL      optional, overrides the judge pin being checked
  EXTRA_MODELS     optional, comma-separated ids to check alongside the pins —
                   how you vet an upgrade candidate before switching to it
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import generate_rant
import safety_judge
from eval_models import check_quota


def pinned_models() -> list:
    """
    The models production would use right now, read from the same constants and
    env vars the pipeline reads — never a hardcoded copy, or this check would
    happily pass while production used something else entirely.
    """
    models = [
        os.environ.get("LLM_MODEL") or os.environ.get("GEMINI_MODEL", generate_rant.DEFAULT_MODEL),
        os.environ.get("JUDGE_MODEL", safety_judge.DEFAULT_MODEL),
    ]
    models += [m.strip() for m in os.environ.get("EXTRA_MODELS", "").split(",") if m.strip()]
    # dict.fromkeys: dedupe (generation and judge are usually the same model)
    # while keeping order, so the log reads in a predictable sequence.
    return list(dict.fromkeys(models))


def main() -> int:
    models = pinned_models()
    print(f"pinned models in effect: {', '.join(models)}")
    print(f"thinking_level: {generate_rant.DEFAULT_THINKING_LEVEL}\n")
    rc = check_quota(models)
    if rc != 0:
        print(
            "\nA pinned model is gone or no longer free on this key. Before changing\n"
            "DEFAULT_MODEL, re-read AGENTS.md Model Strategy — the 2026-07-01 outage\n"
            "was caused by moving to a model whose free tier had not been verified.\n"
            "Vet a replacement first:\n"
            "  python3 scripts/eval_models.py --check-quota --models '<candidate>'\n"
            "  python3 scripts/eval_models.py --fixture evals/fixtures/voice_rivalry.json \\\n"
            "      --n 3 --models '<incumbent>,<candidate>' --thinking-level low",
            file=sys.stderr,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
