#!/usr/bin/env python3
"""eval_models.py — run a fixture through several models and compare outputs.

Usage:
  python scripts/eval_models.py \
    --fixture evals/fixtures/voice_rivalry.json --n 2 \
    --models "gemini-flash-latest,gemma-3-27b-it,gemma-3-12b-it"

For each model it runs generate_rant.py N times (same subprocess + env mechanism
as eval_voice.py), writing outputs to evals/runs/<model_slug>/ so models never
overwrite each other. Then it prints a comparison table built from
eval_voice.summarize(). The table is triage only — read the JSON files yourself
to judge voice quality. That IS the eval.

All models here (the Gemini baseline + Gemma open models) are served through the
same GEMINI_API_KEY; the model is selected per run via the LLM_MODEL env var that
generate_rant.py honors. No extra dependency, no machine to keep running.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from eval_voice import split_fixture, summarize  # reuse — no duplication

RUNS_DIR = REPO / "evals" / "runs"
REQUIRED_KEYS = {"headline", "morning_brew", "trend_watch"}


def slug(model: str) -> str:
    """Filesystem-safe folder name for a model id (e.g. gemma-3-27b-it)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


def write_fixture_sections(label: str, sections) -> tuple[dict, Path]:
    """Split sections to shared tmp files and return (base_env, tmp_dir).

    Mirrors eval_voice.py's tmp-file layout so generate_rant.py reads the fixture
    via env vars. Shared across all models (the input is identical per model)."""
    rolling, season_past, season_current, recent_output, drafts, news, today, roster = sections
    tmp = RUNS_DIR / f"{label}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "rolling.json").write_text(json.dumps(rolling, indent=2))
    (tmp / "season_static.json").write_text(json.dumps(season_past, indent=2))
    (tmp / "season_current.json").write_text(json.dumps(season_current, indent=2))
    (tmp / "drafts.json").write_text(json.dumps(drafts, indent=2))
    (tmp / "news.json").write_text(json.dumps(news, indent=2))
    (tmp / "roster.json").write_text(json.dumps(roster, indent=2))
    (tmp / "schedule.json").write_text('{"games": []}')

    archive_dir = tmp / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for old in archive_dir.glob("*.json"):
        old.unlink()
    for entry in recent_output:
        date = entry.get("date")
        if not date:
            continue
        slim = {k: v for k, v in entry.items() if k != "date"}
        (archive_dir / f"{date}.json").write_text(json.dumps(slim, indent=2))

    base_env = os.environ.copy()
    base_env["ROLLING_STORE_PATH"] = str(tmp / "rolling.json")
    base_env["SCHEDULE_PATH"] = str(tmp / "schedule.json")
    base_env["NEWS_PATH"] = str(tmp / "news.json")
    base_env["SEASON_STATIC_PATH"] = str(tmp / "season_static.json")
    base_env["SEASON_CURRENT_PATH"] = str(tmp / "season_current.json")
    base_env["DRAFT_PICKS_PATH"] = str(tmp / "drafts.json")
    base_env["ROSTER_PATH"] = str(tmp / "roster.json")
    base_env["DAN_ARCHIVE_PATH"] = str(archive_dir)
    if today:
        base_env["TODAY_OVERRIDE"] = today
    return base_env, tmp


def print_comparison(models: list[str], results: dict) -> None:
    print("\n" + "=" * 78)
    print("COMPARISON (averaged across runs — read the JSON files for real judgment)")
    print("=" * 78)
    header = (f"{'model':<22}{'ok':>6}{'keys':>6}{'paras':>7}"
              f"{'words':>7}{'trends':>8}{'news':>6}{'#stats':>8}")
    print(header)
    print("-" * len(header))
    for model in models:
        rows = results[model]
        good = [r for r in rows if "error" not in r]

        def avg(key):
            return (sum(r.get(key, 0) for r in good) / len(good)) if good else 0

        keys_ok = sum(1 for r in good if REQUIRED_KEYS.issubset(set(r.get("keys", []))))
        avg_stats = (sum(len(r.get("stat_numbers", [])) for r in good) / len(good)) if good else 0
        ok_str = f"{len(good)}/{len(rows)}"
        print(f"{model:<22}{ok_str:>6}{keys_ok:>6}{avg('brew_paragraphs'):>7.1f}"
              f"{avg('brew_words'):>7.0f}{avg('trend_count'):>8.1f}"
              f"{avg('news_count'):>6.1f}{avg_stats:>8.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True, help="Path to a fixture JSON")
    ap.add_argument("--n", type=int, default=2, help="Generations per model")
    ap.add_argument("--models", required=True,
                    help="Comma-separated model ids, e.g. "
                         "'gemini-flash-latest,gemma-3-27b-it'")
    ap.add_argument("--label", help="Output filename label (default: fixture stem)")
    args = ap.parse_args()

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        sys.exit(f"error: fixture not found: {fixture}")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        sys.exit("error: no models given (use --models 'a,b')")

    label = args.label or fixture.stem
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fixture_data = json.loads(fixture.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"error: fixture not valid JSON: {e}")

    base_env, _tmp = write_fixture_sections(label, split_fixture(fixture_data))

    print(f"eval_models: fixture={fixture.name} label={label} n={args.n} "
          f"models={models}")
    results = {}
    for model in models:
        model_dir = RUNS_DIR / slug(model)
        model_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== model: {model} ===")
        rows = []
        for i in range(1, args.n + 1):
            out_path = model_dir / f"{label}_{i}.json"
            env = base_env.copy()
            env["LLM_MODEL"] = model
            env["OUTPUT_PATH"] = str(out_path)
            print(f"  run {i}/{args.n} → {slug(model)}/{out_path.name}")
            result = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "generate_rant.py")],
                env=env, capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"    FAIL: {result.stderr.strip()[:300]}", file=sys.stderr)
                rows.append({"run": i, "error": result.stderr.strip()[:200]})
                continue
            rows.append({"run": i, **summarize(out_path)})
        results[model] = rows

    print_comparison(models, results)
    print(f"\nOutputs in evals/runs/<model>/{label}_*.json — open them to judge voice.")


if __name__ == "__main__":
    main()
