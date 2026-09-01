"""Stage 3 — assemble the issue and write the artifact the site reads.

Output:
  data/<week>.json        the issue
  data/index.json         list of all weeks, newest first
  data/seen.json          dedupe_keys already published, so items don't repeat
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from anthropic import Anthropic

from models import Issue, ScoredItem, Section

_HEADLINE_PROMPT = """Write one sentence summarizing the most notable \
video-game news this week for a reader digest. No hype, no "X: Y" \
colon-subtitle construction. If nothing stands out, say plainly that the \
week was quiet.

Top items this week, highest ranked first:
{items}
"""

_TITLE_PROMPT = """Write a short punchy title (a few words, headline-style, \
no ending punctuation) capturing the single most notable video-game story \
this week for a reader digest. No hype, no "X: Y" colon-subtitle \
construction. If nothing stands out, write a plain title saying the week \
was quiet.

Top items this week, highest ranked first:
{items}
"""


def _top_items_text(items: list[ScoredItem]) -> str:
    top = sorted(items, key=lambda i: i.score, reverse=True)[:10]
    return "\n".join(f"- [{i.section}] {i.title}: {i.why}" for i in top) or "(no items this week)"


def write_headline(items: list[ScoredItem]) -> str:
    """One LLM call over the top ~10 items. One sentence, no hype, no colon-
    then-subtitle construction. If nothing stands out, say the week was quiet.
    """
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        messages=[{"role": "user", "content": _HEADLINE_PROMPT.format(items=_top_items_text(items))}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def write_title(items: list[ScoredItem]) -> str:
    """One LLM call over the top ~10 items. A short punchy title (a few
    words), distinct from write_headline's longer sentence.
    """
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=64,
        messages=[{"role": "user", "content": _TITLE_PROMPT.format(items=_top_items_text(items))}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _section_meta() -> dict[str, dict[str, str]]:
    import yaml

    with open("config/sources.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return {s["id"]: {"label": s["label"], "blurb": s["blurb"]} for s in config["sections"]}


def build_issue(
    week: str,
    items: list[ScoredItem],
    headline: str | None = None,
    title: str | None = None,
    analyzed_by: str | None = None,
    section_summaries: dict[str, str] | None = None,
) -> Issue:
    """Assemble the issue. `headline`, if given, is used as-is (no LLM call) —
    this is how the no-API-key workflow supplies a headline written locally
    by Claude Code. Omit it to fall back to `write_headline` (an LLM call).
    `title` works the same way. `analyzed_by`, if given, is recorded in
    `stats["analyzed_by"]`. `section_summaries`, if given, maps a
    `SectionId` to a one-sentence recap shown under that section's heading.
    """
    from collections import defaultdict

    from dates import week_bounds

    starts_on, ends_on = week_bounds(week)
    meta = _section_meta()
    section_summaries = section_summaries or {}

    by_section: dict[str, list[ScoredItem]] = defaultdict(list)
    for item in items:
        by_section[item.section].append(item)

    sections = [
        Section(
            id=section_id,
            label=meta[section_id]["label"],
            blurb=meta[section_id]["blurb"],
            items=sorted(section_items, key=lambda i: i.score, reverse=True),
            summary=section_summaries.get(section_id, ""),
        )
        for section_id, section_items in by_section.items()
        if section_items  # a section with 0 items renders as a quiet week, never padded
    ]
    order = list(meta.keys())
    sections.sort(key=lambda s: order.index(s.id))

    stats = {"items_kept": len(items)}
    if analyzed_by:
        stats["analyzed_by"] = analyzed_by

    return Issue(
        week=week,
        starts_on=starts_on,
        ends_on=ends_on,
        generated_at=datetime.now(timezone.utc),
        title=title if title is not None else write_title(items),
        headline=headline if headline is not None else write_headline(items),
        sections=sections,
        stats=stats,
    )


def main() -> None:
    """Write atomically (tmp file + rename) so a crash never leaves the site
    reading a half-written issue.
    """
    from dates import week_from_date
    from models import ScoredItem

    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="ISO week, e.g. 2026-W34.")
    parser.add_argument(
        "--date", help="Any date within the target week, e.g. 2026-08-24 — an alternative to --week."
    )
    parser.add_argument(
        "--headline",
        help=(
            "Use this headline text as-is instead of calling write_headline (an LLM call). "
            "This is how the no-API-key workflow supplies a headline written locally by Claude Code."
        ),
    )
    parser.add_argument(
        "--title",
        help=(
            "Use this title text as-is instead of calling write_title (an LLM call). "
            "A short punchy title (a few words), distinct from --headline's longer "
            "sentence. This is how the no-API-key workflow supplies a title written "
            "locally by Claude Code."
        ),
    )
    parser.add_argument(
        "--analyzed-by",
        help="Recorded in stats.analyzed_by — e.g. 'claude-sonnet-5'. Shown as a credit line on the site.",
    )
    parser.add_argument(
        "--section-summaries",
        help=(
            'JSON object mapping SectionId -> one-sentence recap, e.g. '
            '\'{"releases": "A quiet week for new releases."}\'. '
            "Shown under each section's heading on the site. Sections with no "
            "entry get an empty summary — never an error."
        ),
    )
    args = parser.parse_args()

    if args.week and args.date:
        parser.error("--week and --date are mutually exclusive")
    if not args.week and not args.date:
        parser.error("one of --week or --date is required")
    args.week = week_from_date(args.date) if args.date else args.week

    section_summaries = json.loads(args.section_summaries) if args.section_summaries else None

    items: list[ScoredItem] = []
    with open(f"data/scored/{args.week}.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(ScoredItem.model_validate_json(line))

    issue = build_issue(
        args.week,
        items,
        headline=args.headline,
        title=args.title,
        analyzed_by=args.analyzed_by,
        section_summaries=section_summaries,
    )

    tmp_path = f"data/.{args.week}.json.tmp"
    final_path = f"data/{args.week}.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(issue.model_dump_json(indent=2))
    os.replace(tmp_path, final_path)

    index_path = "data/index.json"
    index = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    index = [entry for entry in index if entry["week"] != args.week]
    index.insert(0, {"week": issue.week, "headline": issue.headline, "generated_at": issue.generated_at.isoformat()})
    index.sort(key=lambda e: e["week"], reverse=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    seen_path = "data/seen.json"
    seen = set()
    if os.path.exists(seen_path):
        with open(seen_path, encoding="utf-8") as f:
            seen = set(json.load(f))
    seen.update(item.dedupe_key for item in items)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)

    print(f"built {final_path}: {len(items)} items across {len(issue.sections)} sections")


if __name__ == "__main__":
    main()
