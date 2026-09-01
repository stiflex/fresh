"""Stage 1 — collect. Reads config/sources.yaml, writes data/raw/<week>.jsonl.

Rules:
  - Never let one dead feed kill the run. Catch per-source, log, continue.
  - Window is [monday 00:00 UTC, next monday 00:00 UTC).
  - This stage does NOT filter on quality. It only filters on the date window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from models import RawItem

# Some sites 403 the default python-httpx / python-requests user-agent
# string outright, independent of IP reputation. A realistic browser UA
# costs nothing and recovers those; it does NOT get past sites doing real
# bot-challenge/fingerprint checks (Cloudflare-style), which need a
# different approach entirely.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}


def fetch_rss(source: dict, since: datetime, until: datetime) -> list[RawItem]:
    """feedparser. Fall back to <updated> when <published> is missing.

    Retry once with requests on a 403 from httpx — a TLS-handshake
    fingerprint quirk seen on some sites, not necessarily an active bot
    challenge.
    """
    import feedparser
    import httpx

    response = httpx.get(source["url"], follow_redirects=True, timeout=30, headers=_HEADERS)
    if response.status_code == 403:
        import requests

        response = requests.get(source["url"], timeout=30, headers=_HEADERS, allow_redirects=True)
    response.raise_for_status()
    parsed = feedparser.parse(response.text)
    items: list[RawItem] = []
    for entry in parsed.entries:
        time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if not time_struct:
            continue
        published_at = datetime(*time_struct[:6], tzinfo=timezone.utc)
        if not (since <= published_at < until):
            continue
        items.append(
            RawItem(
                source_id=source["id"],
                kind="article",
                title=entry.get("title", ""),
                url=entry.get("link"),
                published_at=published_at,
                summary=entry.get("summary", ""),
            )
        )
    return items


# One selector set per scraped source. Keep every source's selectors here so
# breakage from a site redesign is visible and repairable in one place. Empty
# today — no configured source uses kind: scrape yet (see config/sources.yaml's
# "NOT YET INCLUDED" note) — but fetch_scrape stays in place as the documented
# fallback for a source with no working feed.
SELECTORS: dict[str, dict[str, str]] = {}


def _parse_scrape_date(date_str: str) -> datetime | None:
    """ISO first (most sites); fall back to "Mon D, YYYY". Date-only formats
    resolve to midnight UTC — fine for week-window filtering.
    """
    date_str = date_str.strip()
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(date_str, "%b %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_scrape(source: dict, since: datetime, until: datetime) -> list[RawItem]:
    """Last resort for a source with no working feed. httpx + selectolax.

    An empty/missing "link" selector means the item node itself is the link
    (its own href is used) — some sites wrap the whole card in a single <a>,
    with no separate inner link to select.
    """
    from urllib.parse import urljoin

    import httpx
    from selectolax.parser import HTMLParser

    selectors = SELECTORS[source["id"]]
    response = httpx.get(source["url"], timeout=30, follow_redirects=True, headers=_HEADERS)
    response.raise_for_status()
    tree = HTMLParser(response.text)

    items: list[RawItem] = []
    for node in tree.css(selectors["item"]):
        title_node = node.css_first(selectors["title"])
        link_node = node if not selectors.get("link") else node.css_first(selectors["link"])
        date_node = node.css_first(selectors["date"])
        if not (title_node and link_node and date_node):
            continue
        date_str = date_node.attributes.get("datetime") or date_node.text()
        published_at = _parse_scrape_date(date_str)
        if published_at is None:
            continue
        if not (since <= published_at < until):
            continue
        href = link_node.attributes.get("href", "")
        items.append(
            RawItem(
                source_id=source["id"],
                kind="article",
                title=title_node.text(strip=True),
                url=urljoin(source["url"], href),
                published_at=published_at,
            )
        )
    return items


FETCHERS = {
    "rss": fetch_rss,
    "scrape": fetch_scrape,
}


def _load_sources(only: set[str] | None) -> list[dict]:
    import yaml

    with open("config/sources.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    sources = []
    for group in ("outlets", "industry", "esports", "community"):
        for source in config.get(group, []):
            if only is None or source["id"] in only:
                sources.append(source)
    return sources


def main() -> None:
    from dates import current_week, week_bounds, week_from_date

    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="ISO week, e.g. 2026-W34. Defaults to last complete week.")
    parser.add_argument(
        "--date",
        help="Any date within the target week, e.g. 2026-08-24 — an alternative to --week.",
    )
    parser.add_argument(
        "--only",
        help=(
            "Comma-separated source ids. Only these are (re-)fetched. Existing "
            "data/raw/<week>.jsonl lines for every other source are preserved "
            "untouched, so this is safe to use to refresh or backfill one "
            "temporarily-broken source without re-fetching everything."
        ),
    )
    args = parser.parse_args()

    if args.week and args.date:
        parser.error("--week and --date are mutually exclusive")

    week = week_from_date(args.date) if args.date else (args.week or current_week())
    since, until = week_bounds(week)
    only = set(args.only.split(",")) if args.only else None

    sources = _load_sources(only)
    if only is not None:
        unmatched = only - {s["id"] for s in sources}
        for source_id in sorted(unmatched):
            print(f"[{source_id}] WARNING: not found in config/sources.yaml, nothing to fetch")

    stats = {"sources_ok": 0, "sources_failed": 0, "items": 0}
    failures: list[tuple[str, str]] = []
    out_dir = "data/raw"
    os.makedirs(out_dir, exist_ok=True)
    out_path = f"{out_dir}/{week}.jsonl"

    refetched_lines: dict[str, list[str]] = {}
    for source in sources:
        fetcher = FETCHERS.get(source["kind"])
        if fetcher is None:
            reason = f"no fetcher for kind={source['kind']!r}"
            print(f"[{source['id']}] SKIPPED: {reason}")
            stats["sources_failed"] += 1
            failures.append((source["id"], reason))
            continue
        try:
            items = fetcher(source, since, until)
        except Exception as exc:  # noqa: BLE001 - one dead feed must never kill the run
            print(f"[{source['id']}] FAILED: {exc}")
            stats["sources_failed"] += 1
            failures.append((source["id"], str(exc)))
            continue
        refetched_lines[source["id"]] = [item.model_dump_json() for item in items]
        stats["items"] += len(items)
        stats["sources_ok"] += 1
        print(f"[{source['id']}] {len(items)} items")

    if only is not None:
        preserved_lines: list[str] = []
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as existing_file:
                for line in existing_file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        source_id = json.loads(line)["source_id"]
                    except (json.JSONDecodeError, KeyError) as exc:
                        print(f"WARNING: could not parse existing line, preserving as-is: {exc}")
                        preserved_lines.append(line)
                        continue
                    if source_id not in refetched_lines:
                        preserved_lines.append(line)
        all_lines = preserved_lines + [
            line for lines in refetched_lines.values() for line in lines
        ]
    else:
        all_lines = [line for lines in refetched_lines.values() for line in lines]

    tmp_path = f"{out_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as out:
        for line in all_lines:
            out.write(line + "\n")
    os.replace(tmp_path, out_path)

    print(f"done: {stats}")
    if failures:
        print(f"\n=== {len(failures)} source(s) failed this run ===")
        for source_id, reason in failures:
            print(f"  - {source_id}: {reason}")
    else:
        print("\nall sources fetched successfully")


if __name__ == "__main__":
    main()
