"""Stage 2 — dedupe, classify, rank. The stage that decides whether this is
a useful product or a firehose. Spend your effort here.

Input:  data/raw/<week>.jsonl
Output: data/scored/<week>.jsonl
"""

from __future__ import annotations

import argparse
from typing import get_args

from anthropic import Anthropic

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
