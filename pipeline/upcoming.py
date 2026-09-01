"""Cross-week upcoming-release calendar. Tracks each game once, keyed by a
normalized name, until its stated release date passes.

Input:  data/upcoming.json (existing calendar, if any) + this week's
        data/scored/<week>.jsonl
Output: data/upcoming.json (updated calendar), atomic write

MUST run after classification but BEFORE `score.py --stage rank`. rank
overwrites data/scored/<week>.jsonl in place, keeping only each section's
top ~6 items — a game whose article didn't make that cut still belongs in
the calendar, so this stage needs the full classified set, not the
trimmed one. Weekly sequence:
  score.py --stage prefilter -> [classify] -> upcoming.py -> score.py --stage rank -> build.py
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from models import ScoredItem

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")
_YEAR_RE = re.compile(r"^(\d{4})$")


def parse_release_hint(text: str) -> date | None:
    """Best-effort parse of a free-text release_date hint. Returns the LAST
    day the hint could plausibly still mean (a bare year "2027" means
    2027-12-31, "Jan 2027" means 2027-01-31) — this is used only to decide
    when an entry has definitely passed, so it errs toward keeping an entry
    rather than dropping it early. Returns None for anything unrecognized;
    an unparseable hint is never auto-expired.
    """
    text = text.strip()

    match = _ISO_RE.match(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    match = _MONTH_YEAR_RE.match(text)
    if match:
        month = _MONTHS.get(match.group(1).lower()[:3])
        year = int(match.group(2))
        if month is None:
            return None
        next_month_first = date(year + (month == 12), (month % 12) + 1, 1)
        return next_month_first - timedelta(days=1)

    match = _YEAR_RE.match(text)
    if match:
        return date(int(match.group(1)), 12, 31)

    return None


def _normalize_name(name: str) -> str:
    return re.sub(r"[^\w]", "", name.lower())


def merge_upcoming(
    existing: list[dict], items: list[ScoredItem], week: str, today: date
) -> list[dict]:
    """Add any newly-seen upcoming game not already tracked (matched by
    normalized game_name), and drop any tracked entry whose parsed
    release_date has passed as of `today`. Only items with both
    `game_name` and `release_date` set are candidates: an item about a
    specific game with no stated date isn't calendar-worthy, and an item
    with no single game_name (general industry news) never is. A game
    already tracked is never overwritten by a later mention — first
    source wins.
    """
    tracked = {_normalize_name(e["game_name"]): dict(e) for e in existing}

    for item in items:
        if not item.game_name or not item.release_date:
            continue
        key = _normalize_name(item.game_name)
        if key in tracked:
            continue
        tracked[key] = {
            "game_name": item.game_name,
            "release_date": item.release_date,
            "platforms": item.platforms,
            "url": str(item.url),
            "first_seen_week": week,
        }

    survivors = [
        entry
        for entry in tracked.values()
        if not ((parsed := parse_release_hint(entry["release_date"])) and parsed < today)
    ]
    survivors.sort(key=lambda e: e["game_name"].lower())
    return survivors


def main() -> None:
    import argparse
    import json
    import os
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser()
    parser.add_argument("--week", required=True, help="ISO week, e.g. 2026-W34.")
    args = parser.parse_args()

    items: list[ScoredItem] = []
    with open(f"data/scored/{args.week}.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(ScoredItem.model_validate_json(line))

    existing: list[dict] = []
    if os.path.exists("data/upcoming.json"):
        with open("data/upcoming.json", encoding="utf-8") as f:
            existing = json.load(f)

    today = datetime.now(timezone.utc).date()
    updated = merge_upcoming(existing, items, args.week, today)

    tmp_path = "data/.upcoming.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
    os.replace(tmp_path, "data/upcoming.json")

    existing_keys = {_normalize_name(e["game_name"]) for e in existing}
    added = sum(1 for e in updated if _normalize_name(e["game_name"]) not in existing_keys)
    dropped = len(existing) - (len(updated) - added)
    print(f"data/upcoming.json: {len(updated)} tracked ({added} added, {dropped} expired)")


if __name__ == "__main__":
    main()
