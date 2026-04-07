#!/usr/bin/env python3
"""
fetch_nfl.py — Fetches recent New England Patriots news from the ESPN API.

Endpoint:
    https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=ne

Outputs:
    data/patriots_news.json  — Top 3 headlines with descriptions + dates

NOTE — OFFSEASON MODE ONLY:
    This script fetches news headlines only. During the NFL regular season
    (September–January), this should be replaced or supplemented with a full
    box score fetcher using the ESPN scoreboard endpoint:
        https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD
    TODO: Add full game boxscore fetcher (score, passing/rushing/receiving leaders,
    scoring summary) when the NFL regular season starts in September.
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"

NEWS_PATH = DATA_DIR / "patriots_news.json"

ESPN_NEWS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=ne"
)

TOP_N = 3   # Number of headlines to capture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """
    Fetch a URL and return the parsed JSON body.

    Raises:
        RuntimeError: On HTTP error, network failure, timeout, or bad JSON.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "patriots-fanbot/1.0 "
                "(+https://github.com/goodvibes413/boston-dans-hub)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


def parse_pub_date(raw: str) -> str:
    """
    Normalise an ESPN publication date string to ISO 8601 UTC.

    ESPN returns dates as "2026-04-06T18:32:00Z". If parsing fails, the
    raw string is returned as-is so downstream code always has a value.
    """
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

def fetch_news() -> None:
    """
    Fetch the top Patriots news headlines from ESPN and write to
    data/patriots_news.json.

    Each article entry contains:
        headline    — Article headline / title
        description — Short summary / subheadline (may be empty)
        published   — Publication datetime (ISO 8601 UTC)
        url         — Link to full article on ESPN
    """
    now_utc = datetime.now(timezone.utc)

    try:
        print(f"  Fetching Patriots news from ESPN...")
        data     = fetch_json(ESPN_NEWS_URL)
        articles = data.get("articles", [])

        if not articles:
            print("  No articles returned from ESPN.")
            result = {
                "generated_at": now_utc.isoformat(),
                "mode":         "offseason",
                "headlines":    [],
            }
            NEWS_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved (empty) to {NEWS_PATH}")
            return

        headlines = []
        for article in articles[:TOP_N]:
            # Prefer the web-facing URL; fall back to the API link
            links     = article.get("links", {})
            web_href  = links.get("web", {}).get("href", "")
            api_href  = links.get("api", {}).get("news", {}).get("href", "")
            url       = web_href or api_href

            headlines.append({
                "headline":    article.get("headline", "").strip(),
                "description": article.get("description", "").strip(),
                "published":   parse_pub_date(article.get("published", "")),
                "url":         url,
            })

        print(f"  Captured {len(headlines)} headline(s).")
        for i, h in enumerate(headlines, 1):
            print(f"  {i}. {h['headline'][:72]}")

        result = {
            "generated_at": now_utc.isoformat(),
            "mode":         "offseason",
            "headlines":    headlines,
        }
        NEWS_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved to {NEWS_PATH}")

    except Exception as e:
        print(f"[ERROR] fetch_news failed: {e}")
        error_result = {
            "generated_at": now_utc.isoformat(),
            "error":        str(e),
            "headlines":    [],
        }
        try:
            NEWS_PATH.write_text(json.dumps(error_result, indent=2))
        except Exception:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the Patriots news fetcher."""
    print("=" * 52)
    print("  Boston Dan's Hub — NFL Patriots News Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    print("\n[1/1] Fetching latest Patriots headlines")
    fetch_news()

    print("\nDone. File written:")
    print(f"  {NEWS_PATH}")


if __name__ == "__main__":
    main()
