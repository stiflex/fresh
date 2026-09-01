"""Stage 2 — dedupe, classify, rank. The stage that decides whether this is
a useful product or a firehose. Spend your effort here.

Input:  data/raw/<week>.jsonl
Output: data/scored/<week>.jsonl
"""

from __future__ import annotations

import argparse
from typing import get_args

from anthropic import Anthropic
from pydantic import ValidationError

from models import RawItem, ScoredItem, SectionId

SOURCE_TIERS: dict[str, int] = {}
SOURCE_FAMILIES: dict[str, str] = {}
RELEVANCE_KEYWORDS: list[str] = []


def _tier(source_id: str) -> int:
    return SOURCE_TIERS.get(source_id, 3)


def _family(source_id: str) -> str:
    return SOURCE_FAMILIES.get(source_id, source_id)


def _load_seen() -> set[str]:
    import json
    import os

    if not os.path.exists("data/seen.json"):
        return set()
    with open("data/seen.json", encoding="utf-8") as f:
        return set(json.load(f))


def dedupe(items: list[RawItem]) -> list[RawItem]:
    """Collapse on dedupe_key, then near-dupe titles.

    Title matching: lowercase, strip punctuation, compare with rapidfuzz
    token_set_ratio >= 92. Keep the item from the highest-tier source and
    push the rest into mirrors.
    """
    import re

    from rapidfuzz import fuzz

    groups: list[list[RawItem]] = []
    key_to_group: dict[str, int] = {}

    for item in items:
        key = item.dedupe_key
        if key in key_to_group:
            groups[key_to_group[key]].append(item)
            continue

        normalized_title = re.sub(r"[^\w\s]", "", item.title.lower())
        normalized_word_count = len(normalized_title.split())
        matched_group = None
        for idx, group in enumerate(groups):
            for member in group:
                member_title = re.sub(r"[^\w\s]", "", member.title.lower())
                member_word_count = len(member_title.split())
                if abs(normalized_word_count - member_word_count) > 2:
                    continue
                if fuzz.token_set_ratio(normalized_title, member_title) >= 92:
                    matched_group = idx
                    break
            if matched_group is not None:
                break

        if matched_group is not None:
            groups[matched_group].append(item)
        else:
            matched_group = len(groups)
            groups.append([item])

        key_to_group[key] = matched_group

    survivors: list[RawItem] = []
    for group in groups:
        group.sort(key=lambda i: _tier(i.source_id))
        winner = group[0]
        winner_url = str(winner.url)
        mirrors = [
            url
            for url in dict.fromkeys(str(i.url) for i in group[1:])
            if url != winner_url
        ]
        if mirrors:
            winner = winner.model_copy(
                update={"meta": {**winner.meta, "mirror_urls": mirrors}}
            )
        survivors.append(winner)
    return survivors


def _is_relevant(item: RawItem) -> bool:
    """Title-only match against RELEVANCE_KEYWORDS (config/sources.yaml).

    Matches on a word boundary (not embedded in a larger alphanumeric
    token) but tolerates surrounding punctuation and a trailing plural "s"
    — a naive space-padded substring check misses "DLC:" (colon immediately
    follows) and "mods" (plural "s" immediately follows), even though both
    should count as a match.
    """
    import re

    if not RELEVANCE_KEYWORDS:
        return True  # empty list = every configured source is already on-topic
    title = item.title.lower()
    for keyword in RELEVANCE_KEYWORDS:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"s?" + r"(?![a-z0-9])"
        if re.search(pattern, title):
            return True
    return False


def prefilter(items: list[RawItem]) -> list[RawItem]:
    """Cheap cuts before spending any tokens. Drop: already published in a
    prior issue (data/seen.json), and — for anything that isn't a tier-1
    source — no summary, or a title that doesn't match a relevance keyword.
    Auto-keep: tier 1 sources.
    """
    seen = _load_seen()
    kept: list[RawItem] = []
    for item in items:
        if item.dedupe_key in seen:
            continue
        if _tier(item.source_id) == 1:
            kept.append(item)
            continue
        if not item.summary.strip():
            continue
        if not _is_relevant(item):
            continue
        kept.append(item)
    return kept


_SCORE_PROMPT = """You are scoring items for a weekly video-game digest read \
by players and industry watchers. For each item below, decide:

- section: exactly one of {sections}
- score: 0.0-1.0, "would a reader regret missing this" — not "is this well \
written"
- why: <=20 words, the editorial line the reader sees. State what changed, \
no hedging, no "in this article" framing.

Return a JSON array only, no prose, one object per item:
[{{"url": "...", "section": "...", "score": 0.0, "why": "..."}}, ...]

Items:
{items}
"""


def classify_and_score(items: list[RawItem]) -> list[ScoredItem]:
    """One batched LLM call per ~20 items. Ask for strict JSON:

        {"url": ..., "section": <SectionId>, "score": 0..1, "why": "<=20 words"}

    Validate every returned section against SectionId and drop malformed
    rows rather than trusting the model's output shape.
    """
    import json

    valid_sections = set(get_args(SectionId))
    by_url = {str(item.url): item for item in items}
    client = Anthropic()
    results: list[ScoredItem] = []

    batch_size = 20
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        if not batch:
            continue
        items_text = "\n".join(
            f"- url: {item.url}\n  title: {item.title}\n  summary: {item.summary[:500]}"
            for item in batch
        )
        prompt = _SCORE_PROMPT.format(sections=sorted(valid_sections), items=items_text)
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            rows = json.loads(text)
        except json.JSONDecodeError:
            continue

        if not isinstance(rows, list):
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            source_item = by_url.get(url)
            if source_item is None:
                continue
            if row.get("section") not in valid_sections:
                continue
            score = row.get("score")
            if not isinstance(score, (int, float)) or not (0 <= score <= 1):
                continue
            why = row.get("why")
            if not why:
                continue

            mirror_urls = source_item.meta.get("mirror_urls", [])
            results.append(
                ScoredItem(
                    **source_item.model_dump(exclude={"meta"}),
                    meta=source_item.meta,
                    section=row["section"],
                    score=float(score),
                    why=why,
                    mirrors=mirror_urls,
                )
            )
    return results


def rank(items: list[ScoredItem]) -> list[ScoredItem]:
    """Blend the model score with hard signals, then cap per section.

        final = 0.55 * model_score
              + 0.25 * social      (log-normalized meta["social_signal"];
                                     no fetcher populates this today, so it
                                     is presently always 0 — a reserved
                                     extension point, e.g. a future Reddit-
                                     upvote or Steam-review-count signal)
              + 0.20 * source_tier

    Cap at 6 items per section. Within a section, no single source family
    may contribute more than 3 items to a section — a structural diversity
    floor so one high-volume source can't fill an entire section on volume
    alone. A source with no declared family (config/sources.yaml) is its
    own family, keyed on its source_id. A section with 0 items renders as
    "quiet week" — do not pad it.
    """
    import math
    from collections import defaultdict

    def social_component(item: ScoredItem) -> float:
        raw = item.meta.get("social_signal") or 0
        return min(math.log1p(raw) / math.log1p(1000), 1.0)

    def source_tier_component(item: ScoredItem) -> float:
        return (4 - _tier(item.source_id)) / 3

    def final_score(item: ScoredItem) -> float:
        return (
            0.55 * item.score
            + 0.25 * social_component(item)
            + 0.20 * source_tier_component(item)
        )

    SECTION_CAP = 6
    MAX_PER_SOURCE = 3

    by_section: dict[str, list[ScoredItem]] = defaultdict(list)
    for item in items:
        by_section[item.section].append(item)

    ranked: list[ScoredItem] = []
    for section, section_items in by_section.items():
        section_items.sort(key=final_score, reverse=True)

        survivors: list[ScoredItem] = []
        per_source_count: dict[str, int] = defaultdict(int)
        for item in section_items:
            if len(survivors) >= SECTION_CAP:
                break
            if per_source_count[_family(item.source_id)] >= MAX_PER_SOURCE:
                continue
            survivors.append(item)
            per_source_count[_family(item.source_id)] += 1

        ranked.extend(survivors)
    return ranked


def _load_source_tiers() -> dict[str, int]:
    import os

    import yaml

    if not os.path.exists("config/sources.yaml"):
        return {}
    with open("config/sources.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    tiers = {}
    for group in ("outlets", "industry", "esports", "community"):
        for source in config.get(group, []):
            tiers[source["id"]] = source["tier"]
    return tiers


def _load_source_families() -> dict[str, str]:
    import os

    import yaml

    if not os.path.exists("config/sources.yaml"):
        return {}
    with open("config/sources.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    families = {}
    for group in ("outlets", "industry", "esports", "community"):
        for source in config.get(group, []):
            if "family" in source:
                families[source["id"]] = source["family"]
    return families


def _load_relevance_keywords() -> list[str]:
    import os

    import yaml

    if not os.path.exists("config/sources.yaml"):
        return []
    with open("config/sources.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return [str(keyword).lower() for keyword in config.get("relevance_keywords", [])]


def main() -> None:
    global SOURCE_TIERS, SOURCE_FAMILIES, RELEVANCE_KEYWORDS
    import os

    from dates import week_from_date

    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="ISO week, e.g. 2026-W34.")
    parser.add_argument(
        "--date", help="Any date within the target week, e.g. 2026-08-24 — an alternative to --week."
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["prefilter", "rank"],
        help=(
            "prefilter: dedupe+prefilter data/raw -> data/prefiltered (no LLM, runs in CI). "
            "rank: apply the rank formula to an already-classified data/scored/<week>.jsonl "
            "in place (no LLM, runs locally after manual/Claude-Code classification)."
        ),
    )
    args = parser.parse_args()

    if args.week and args.date:
        parser.error("--week and --date are mutually exclusive")
    if not args.week and not args.date:
        parser.error("one of --week or --date is required")
    args.week = week_from_date(args.date) if args.date else args.week

    SOURCE_TIERS = _load_source_tiers()
    SOURCE_FAMILIES = _load_source_families()
    RELEVANCE_KEYWORDS = _load_relevance_keywords()

    if args.stage == "prefilter":
        raw_items: list[RawItem] = []
        with open(f"data/raw/{args.week}.jsonl", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    raw_items.append(RawItem.model_validate_json(line))
                except ValidationError as exc:
                    print(f"WARNING: data/raw/{args.week}.jsonl:{line_no} unparseable, skipping: {exc}")

        deduped = dedupe(raw_items)
        filtered = prefilter(deduped)

        os.makedirs("data/prefiltered", exist_ok=True)
        with open(f"data/prefiltered/{args.week}.jsonl", "w", encoding="utf-8") as out:
            for item in filtered:
                out.write(item.model_dump_json() + "\n")

        print(f"week {args.week}: {len(raw_items)} raw -> {len(filtered)} prefiltered")

    elif args.stage == "rank":
        scored_items: list[ScoredItem] = []
        with open(f"data/scored/{args.week}.jsonl", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    scored_items.append(ScoredItem.model_validate_json(line))
                except ValidationError as exc:
                    print(f"WARNING: data/scored/{args.week}.jsonl:{line_no} unparseable, skipping: {exc}")

        ranked = rank(scored_items)

        with open(f"data/scored/{args.week}.jsonl", "w", encoding="utf-8") as out:
            for item in ranked:
                out.write(item.model_dump_json() + "\n")

        print(f"week {args.week}: {len(scored_items)} classified -> {len(ranked)} ranked")


if __name__ == "__main__":
    main()
