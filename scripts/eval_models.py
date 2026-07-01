#!/usr/bin/env python3
"""eval_models.py — run a fixture (or today's real data) through several models
and compare outputs.

Fixture mode:
  python scripts/eval_models.py \
    --fixture evals/fixtures/voice_rivalry.json --n 2 \
    --models "gemini-3.1-flash-lite,gemma-3-27b-it,gemma-3-12b-it"

Live mode (run against today's REAL fetched data — run the fetchers first):
  python scripts/eval_models.py --live --n 2 \
    --models "gemini-3.1-flash-lite,gemma-3-27b-it,gemma-3-12b-it"
  In live mode the Gemini output that actually shipped today
  (docs/data/daily_output.json) is included as a read-only "shipped-gemini"
  reference row, so you get a true side-by-side against what really published.

For each model it runs generate_rant.py N times (same subprocess + env mechanism
as eval_voice.py), writing outputs to evals/runs/<model_slug>/ so models never
overwrite each other. Then it prints a comparison table and a voice preview
(headline + first paragraph) per model. The table/preview are triage — read the
full JSON to judge voice. That IS the eval.

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
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from eval_voice import split_fixture, summarize  # reuse — no duplication

RUNS_DIR = REPO / "evals" / "runs"
SHIPPED_OUTPUT = REPO / "docs" / "data" / "daily_output.json"
SHIPPED_LABEL = "shipped-gemini"
REQUIRED_KEYS = {"headline", "morning_brew", "trend_watch"}


def slug(model: str) -> str:
    """Filesystem-safe folder name for a model id (e.g. gemma-3-27b-it)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


def preview_lines(path: str) -> tuple[str, str]:
    """Return (headline, first morning_brew paragraph) for the voice preview."""
    try:
        d = json.loads(Path(path).read_text())
    except Exception:
        return "", ""
    brew = d.get("morning_brew") or []
    first = brew[0] if isinstance(brew, list) and brew else ""
    return d.get("headline", ""), first


def write_fixture_sections(label: str, sections) -> dict:
    """Split sections to shared tmp files and return base_env (real-data env vars
    pointed at the tmp fixture). Mirrors eval_voice.py's tmp-file layout."""
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
        entry_date = entry.get("date")
        if not entry_date:
            continue
        slim = {k: v for k, v in entry.items() if k != "date"}
        (archive_dir / f"{entry_date}.json").write_text(json.dumps(slim, indent=2))

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
    return base_env


def run_model(model: str, base_env: dict, label: str, n: int) -> list:
    """Run generate_rant.py n times for one model; return list of summary rows."""
    model_dir = RUNS_DIR / slug(model)
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== model: {model} ===")
    rows = []
    for i in range(1, n + 1):
        out_path = model_dir / f"{label}_{i}.json"
        env = base_env.copy()
        env["LLM_MODEL"] = model
        env["OUTPUT_PATH"] = str(out_path)
        print(f"  run {i}/{n} → {slug(model)}/{out_path.name}")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "generate_rant.py")],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"    FAIL: {result.stderr.strip()[:300]}", file=sys.stderr)
            rows.append({"run": i, "error": result.stderr.strip()[:200]})
            continue
        s = summarize(out_path)
        keys = set(s.get("keys", []))
        # generate_rant exits 0 even when it writes a _generation_failed sentinel
        # or otherwise produces output without the required keys. Treat that as a
        # failure (not a silent "ok") and surface the subprocess stderr tail so
        # the reason (parse error, quota, safety block, …) is visible.
        if "_generation_failed" in keys or not REQUIRED_KEYS.issubset(keys):
            print(f"    EMPTY/INVALID output (keys={sorted(keys)}). stderr tail:", file=sys.stderr)
            for ln in result.stderr.strip().splitlines()[-12:]:
                print(f"      {ln}", file=sys.stderr)
            rows.append({"run": i, "error": "empty/invalid output", "path": str(out_path), **s})
            continue
        rows.append({"run": i, "path": str(out_path), **summarize(out_path)})
    return rows


def print_comparison(order: list, results: dict, refs: set) -> None:
    print("\n" + "=" * 78)
    print("COMPARISON (averaged across runs — read the JSON files for real judgment)")
    print("=" * 78)
    header = (f"{'model':<22}{'ok':>6}{'keys':>6}{'paras':>7}"
              f"{'words':>7}{'trends':>8}{'news':>6}{'#stats':>8}")
    print(header)
    print("-" * len(header))
    for label in order:
        rows = results[label]
        good = [r for r in rows if "error" not in r]

        def avg(key):
            return (sum(r.get(key, 0) for r in good) / len(good)) if good else 0

        keys_ok = sum(1 for r in good if REQUIRED_KEYS.issubset(set(r.get("keys", []))))
        avg_stats = (sum(len(r.get("stat_numbers", [])) for r in good) / len(good)) if good else 0
        ok_str = "ref" if label in refs else f"{len(good)}/{len(rows)}"
        print(f"{label:<22}{ok_str:>6}{keys_ok:>6}{avg('brew_paragraphs'):>7.1f}"
              f"{avg('brew_words'):>7.0f}{avg('trend_count'):>8.1f}"
              f"{avg('news_count'):>6.1f}{avg_stats:>8.1f}")


def print_previews(order: list, results: dict) -> None:
    print("\n" + "=" * 78)
    print("VOICE PREVIEW (headline + first paragraph from the first good run)")
    print("=" * 78)
    for label in order:
        good = [r for r in results[label] if "error" not in r and r.get("path")]
        print(f"\n### {label}")
        if not good:
            print("  (no successful run)")
            continue
        head, first = preview_lines(good[0]["path"])
        print(f"  HEADLINE: {head}")
        print(f"  PARA 1:   {first}")


def list_models() -> None:
    """Print every model this GEMINI_API_KEY can call with generateContent, so we
    use real model ids instead of guessing (the API's 404 says to ListModels)."""
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("error: GEMINI_API_KEY not set")
    client = genai.Client(api_key=api_key)
    rows = []
    for m in client.models.list():
        actions = list(getattr(m, "supported_actions", None) or [])
        if "generateContent" in actions:
            rows.append(m.name)
    rows.sort()
    gemma = [n for n in rows if "gemma" in n.lower()]
    print(f"generateContent-capable models for this key: {len(rows)}")
    print("\n=== GEMMA models ===")
    print("\n".join(gemma) if gemma else "  (none available to this key)")
    print("\n=== all generateContent models ===")
    print("\n".join(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="Path to a fixture JSON (omit with --live)")
    ap.add_argument("--live", action="store_true",
                    help="Run against today's REAL data (the data/*.json the "
                         "fetchers produced) instead of a fixture")
    ap.add_argument("--list-models", action="store_true",
                    help="List model ids this key can call, then exit")
    ap.add_argument("--n", type=int, default=2, help="Generations per model")
    ap.add_argument("--models",
                    help="Comma-separated model ids, e.g. "
                         "'gemini-3.1-flash-lite,gemma-3-27b-it'")
    ap.add_argument("--label", help="Output filename label")
    args = ap.parse_args()

    if args.list_models:
        list_models()
        return
    if not args.models:
        sys.exit("error: --models is required (or use --list-models)")

    if not args.live and not args.fixture:
        sys.exit("error: pass --fixture <path> or --live")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        sys.exit("error: no models given (use --models 'a,b')")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    refs: set = set()
    order: list = []
    results: dict = {}

    if args.live:
        # Real-data env: leave the data path env vars unset so generate_rant.py
        # reads the defaults (data/*.json the fetchers just wrote).
        label = args.label or f"live_{date.today().isoformat()}"
        base_env = os.environ.copy()
        print(f"eval_models: LIVE today's-data run label={label} n={args.n} models={models}")
        # Fold in the Gemini output that actually shipped today as a reference.
        if SHIPPED_OUTPUT.exists():
            order.append(SHIPPED_LABEL)
            refs.add(SHIPPED_LABEL)
            results[SHIPPED_LABEL] = [{"run": 0, "path": str(SHIPPED_OUTPUT),
                                       **summarize(SHIPPED_OUTPUT)}]
            print(f"  reference: {SHIPPED_LABEL} ← {SHIPPED_OUTPUT}")
    else:
        fixture = Path(args.fixture).resolve()
        if not fixture.exists():
            sys.exit(f"error: fixture not found: {fixture}")
        label = args.label or fixture.stem
        try:
            fixture_data = json.loads(fixture.read_text())
        except json.JSONDecodeError as e:
            sys.exit(f"error: fixture not valid JSON: {e}")
        base_env = write_fixture_sections(label, split_fixture(fixture_data))
        print(f"eval_models: fixture={fixture.name} label={label} n={args.n} models={models}")

    for model in models:
        order.append(model)
        results[model] = run_model(model, base_env, label, args.n)

    print_comparison(order, results, refs)
    print_previews(order, results)
    print(f"\nFull outputs in evals/runs/<model>/{label}_*.json — open them to judge voice.")


if __name__ == "__main__":
    main()
