# fresh — video-game weekly pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data pipeline (fetch → score → build) and its test suite for `fresh`, a weekly video-game news digest, forked from the `tentac`/`ai-weekly` pipeline architecture and retargeted to a games source list and a 7-section taxonomy.

**Architecture:** Three Python stages under `pipeline/`, each a standalone CLI script sharing a pydantic schema (`pipeline/models.py`) and ISO-week helpers (`pipeline/dates.py`): `fetch.py` reads `config/sources.yaml` and writes `data/raw/<week>.jsonl`; `score.py` dedupes/prefilters that (pure code) into `data/prefiltered/<week>.jsonl`, and — once classified — ranks `data/scored/<week>.jsonl` in place; `build.py` assembles the final `data/<week>.json` plus `data/index.json` and `data/seen.json`. No web site, no CI workflows, no local Claude-Code skill in this plan — those are separate plans layered on top of this one once the pipeline is proven with tests.

**Tech Stack:** Python 3.12, pydantic v2, httpx + requests + feedparser (RSS), selectolax (HTML scraping), rapidfuzz (near-dupe titles), PyYAML, the `anthropic` SDK (only for the optional fully-automated classify/headline path — unused by the default no-API-key workflow), pytest + respx + freezegun for tests.

**Spec:** `docs/superpowers/specs/2026-09-01-video-game-weekly-design.md`

## Global Constraints

- Python 3.12, matching `.python-version`.
- Dependency floors: `httpx[http2]>=0.27`, `requests>=2.32`, `feedparser>=6.0`, `pydantic>=2.7`, `PyYAML>=6.0`, `selectolax>=0.3`, `rapidfuzz>=3.9`, `anthropic>=0.40`. (No `tenacity` — nothing in this codebase actually imports it; don't add an unused dependency.)
- ISO weeks throughout, UTC, half-open interval `[monday 00:00, next monday 00:00)`.
- No `ANTHROPIC_API_KEY` required anywhere in this plan's default path — `classify_and_score`/`write_headline`/`write_title` exist and are unit-tested (mocked `Anthropic` client) but nothing in this plan's tasks calls them against a real API.
- Every multi-file write in `build.py` is atomic: write to a temp path, then `os.replace`.
- 7 sections only, no `security` section: `releases`, `indie`, `industry`, `esports`, `hardware`, `community-modding`, `dev-tech`.
- A section with 0 items is omitted from the built issue, never padded.

---

### Task 1: Project scaffolding + `models.py`

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `pipeline/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `SectionId` (`Literal["releases","indie","industry","esports","hardware","community-modding","dev-tech"]`), `ItemKind` (`Literal["article"]`), `RawItem` (fields: `source_id: str`, `kind: ItemKind`, `title: str`, `url: HttpUrl`, `published_at: datetime`, `summary: str = ""`, `authors: list[str] = []`, `meta: dict = {}`; property `dedupe_key -> str`), `ScoredItem(RawItem)` (+ `section: SectionId`, `score: float`, `why: str`, `mirrors: list[HttpUrl] = []`), `Section` (`id: SectionId`, `label: str`, `blurb: str`, `items: list[ScoredItem]`, `summary: str = ""`), `Issue` (`week: str`, `starts_on: datetime`, `ends_on: datetime`, `generated_at: datetime`, `title: str = ""`, `headline: str`, `sections: list[Section]`, `stats: dict = {}`) — all in `pipeline/models.py`.

- [ ] **Step 1: Create the directory layout and dependency/config files**

```
D:\Projets\fresh\
├── pipeline\
├── config\
├── data\
└── tests\
```

`requirements.txt`:
```
httpx[http2]>=0.27
requests>=2.32
feedparser>=6.0
pydantic>=2.7
PyYAML>=6.0
selectolax>=0.3
rapidfuzz>=3.9
anthropic>=0.40
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.24
respx>=0.21
freezegun>=1.5
```

`pytest.ini`:
```ini
[pytest]
pythonpath = pipeline
testpaths = tests
asyncio_mode = auto
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.cache/
__pycache__/
.env
.venv/
data/raw/*.tmp
```

- [ ] **Step 2: Create the venv and install dev dependencies**

Run (from `D:\Projets\fresh`):
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
```
Expected: installs cleanly, no errors.

- [ ] **Step 3: Write the failing tests for `RawItem.dedupe_key`**

`tests/test_models.py`:
```python
from models import RawItem


def _item(**overrides) -> RawItem:
    base = dict(
        source_id="ign",
        kind="article",
        title="A Story",
        url="https://example.com/a",
        published_at="2026-08-18T00:00:00Z",
        meta={},
    )
    base.update(overrides)
    return RawItem(**base)


def test_dedupe_key_normalizes_url_strips_utm_www_trailing_slash():
    item = _item(url="https://www.example.com/blog/post/?utm_source=x&utm_medium=y")
    assert item.dedupe_key == "url:https://example.com/blog/post"


def test_dedupe_key_uses_normalized_url():
    item = _item(url="https://example.com/a")
    assert item.dedupe_key == "url:https://example.com/a"


def test_dedupe_key_strips_trailing_slash():
    item = _item(url="https://example.com/a/")
    assert item.dedupe_key == "url:https://example.com/a"
```

- [ ] **Step 4: Run the tests to verify they fail (module doesn't exist yet)**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'models'`

- [ ] **Step 5: Write `pipeline/models.py`**

```python
"""Shared schema. Every stage reads and writes these types."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl

SectionId = Literal[
    "releases",
    "indie",
    "industry",
    "esports",
    "hardware",
    "community-modding",
    "dev-tech",
]

ItemKind = Literal["article"]


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    )
    path = parts.path.rstrip("/") or ""
    return urlunsplit((parts.scheme, netloc, path, query, ""))


class RawItem(BaseModel):
    """What fetch.py emits. One per URL, before dedupe or scoring."""

    source_id: str
    kind: ItemKind
    title: str
    url: HttpUrl
    published_at: datetime
    summary: str = ""
    authors: list[str] = Field(default_factory=list)
    # Currently populated only by score.py's dedupe() (mirror_urls). Reserved
    # for a future social signal (score.py's rank() reads meta["social_signal"]
    # if present) — no fetcher sets it today.
    meta: dict = Field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        """Normalized identity. This domain has no DOI/arXiv/CVE-style stable
        ID the way the AI-digest sibling project does, so identity is always
        the normalized URL."""
        return f"url:{_normalize_url(str(self.url))}"


class ScoredItem(RawItem):
    """What score.py emits."""

    section: SectionId
    score: float  # 0..1
    why: str  # one sentence, shown in the UI as the editorial line
    mirrors: list[HttpUrl] = Field(default_factory=list)  # other URLs for same item


class Section(BaseModel):
    id: SectionId
    label: str
    blurb: str
    items: list[ScoredItem]
    summary: str = ""  # one AI-written sentence on what happened in this section this week


class Issue(BaseModel):
    """The weekly artifact. Serialized to data/YYYY-Www.json."""

    week: str  # ISO week, e.g. "2026-W34"
    starts_on: datetime
    ends_on: datetime
    generated_at: datetime
    title: str = ""  # short punchy title (a few words), distinct from headline
    headline: str  # single-sentence take on the week
    sections: list[Section]
    stats: dict = Field(default_factory=dict)  # items_seen, items_kept, per-source counts
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini .python-version .gitignore pipeline/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
Add project scaffolding and pipeline/models.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `dates.py`

**Files:**
- Create: `pipeline/dates.py`
- Test: `tests/test_dates.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `week_bounds(week: str) -> tuple[datetime, datetime]`, `current_week() -> str`, `week_from_date(date_str: str) -> str`, all in `pipeline/dates.py`. `fetch.py`, `score.py`, and `build.py` (Tasks 4, 5/6, 7) all import these three names.

- [ ] **Step 1: Write the failing tests**

`tests/test_dates.py`:
```python
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from dates import current_week, week_bounds, week_from_date


def test_week_bounds_mid_year():
    start, end = week_bounds("2026-W34")
    assert start == datetime(2026, 8, 17, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 24, tzinfo=timezone.utc)


def test_week_bounds_year_boundary():
    start, end = week_bounds("2026-W01")
    assert start == datetime(2025, 12, 29, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_week_bounds_rejects_bad_format():
    with pytest.raises(ValueError):
        week_bounds("2026-34")


@freeze_time("2026-08-24 10:00:00")  # a Monday
def test_current_week_on_monday_returns_prior_week():
    assert current_week() == "2026-W34"


@freeze_time("2026-08-20 10:00:00")  # a Thursday, mid-week
def test_current_week_midweek_returns_prior_complete_week():
    assert current_week() == "2026-W33"


def test_week_from_date_mid_week():
    assert week_from_date("2026-08-24") == "2026-W35"


def test_week_from_date_monday_itself():
    assert week_from_date("2026-08-17") == "2026-W34"


def test_week_from_date_year_boundary():
    assert week_from_date("2025-12-29") == "2026-W01"


def test_week_from_date_rejects_bad_format():
    with pytest.raises(ValueError):
        week_from_date("24-08-2026")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dates'`

- [ ] **Step 3: Write `pipeline/dates.py`**

```python
"""ISO-week arithmetic, UTC throughout. Every pipeline stage uses this."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def week_bounds(week: str) -> tuple[datetime, datetime]:
    """Half-open UTC interval [monday 00:00, next monday 00:00) for an ISO week."""
    match = _WEEK_RE.match(week)
    if not match:
        raise ValueError(f"expected ISO week like '2026-W34', got {week!r}")
    year, week_num = int(match.group(1)), int(match.group(2))
    monday = datetime.fromisocalendar(year, week_num, 1).replace(tzinfo=timezone.utc)
    return monday, monday + timedelta(days=7)


def current_week() -> str:
    """ISO week string of the last complete week as of now (UTC)."""
    now = datetime.now(timezone.utc)
    last_complete_monday = now - timedelta(days=now.isoweekday())
    iso = last_complete_monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_from_date(date_str: str) -> str:
    """ISO week string (e.g. '2026-W34') containing the given 'YYYY-MM-DD' date."""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"expected a date like '2026-08-24', got {date_str!r}") from exc
    iso = date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_dates.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/dates.py tests/test_dates.py
git commit -m "$(cat <<'EOF'
Add pipeline/dates.py ISO-week helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `config/sources.yaml`

**Files:**
- Create: `config/sources.yaml`

**Interfaces:**
- Consumes: `SectionId` values from Task 1 (the `sections:` block's `id`s must be exactly the 7 `SectionId` literals).
- Produces: the on-disk source registry that `fetch.py` (Task 4), `score.py` (Tasks 5–6), and `build.py` (Task 7) all read at runtime. Top-level source groups: `outlets`, `industry`, `esports`, `community` — `fetch.py._load_sources` and `score.py._load_source_tiers`/`_load_source_families` iterate exactly this 4-tuple, so Task 4/5 code must use this same group list.

No tests in this task — it's data, exercised by later tasks' tests via `monkeypatch`/`tmp_path`-scoped fixture YAML, and by Task 8's manual verification against the real file.

- [ ] **Step 1: Write `config/sources.yaml`**

```yaml
# =============================================================================
# sources.yaml — source registry for the fresh weekly video-game feed
#
# kind: rss | scrape
# tier: 1 = always surface if relevant, 2 = normal, 3 = only if strong signal
# sections: candidate sections. The classifier picks the final one, but a
#           source's declared sections act as a prior / restrict the choices.
#
# relevance_keywords is intentionally empty: every source below is a
# dedicated games outlet, so (unlike the AI-digest sibling project's arXiv
# firehose, which is only ~10% AI-relevant) there is no off-topic-noise
# problem to filter on title keywords. See pipeline/score.py's prefilter().
# =============================================================================

relevance_keywords: []

sections:
  - id: releases
    label: "Releases & updates"
    blurb: "New launches, major patches, DLC"
  - id: indie
    label: "Indie spotlight"
    blurb: "Indie & showcase highlights"
  - id: industry
    label: "Industry"
    blurb: "Funding, acquisitions, layoffs, publisher news"
  - id: esports
    label: "Esports"
    blurb: "Competitive scene, tournaments"
  - id: hardware
    label: "Hardware"
    blurb: "Consoles, GPUs, peripherals, storefront tech"
  - id: community-modding
    label: "Community & modding"
    blurb: "Mods, UGC, speedrunning"
  - id: dev-tech
    label: "Dev & tech"
    blurb: "Engines, tools, postmortems, technical breakdowns"

# -----------------------------------------------------------------------------
# OUTLETS — general games press
# -----------------------------------------------------------------------------
outlets:
  - id: ign
    kind: rss
    url: https://feeds.ign.com/ign/games-all
    tier: 1
    sections: [releases, industry, hardware]
  - id: kotaku
    kind: rss
    url: https://kotaku.com/rss
    tier: 1
    sections: [releases, industry, community-modding]
  - id: polygon
    kind: rss
    url: https://www.polygon.com/rss/index.xml
    tier: 1
    sections: [releases, indie, industry]
  - id: rockpapershotgun
    kind: rss
    url: https://www.rockpapershotgun.com/feed
    tier: 1
    sections: [indie, community-modding, releases]
  - id: pcgamer
    kind: rss
    url: https://www.pcgamer.com/rss/
    tier: 2
    sections: [hardware, releases, community-modding]
  - id: eurogamer
    kind: rss
    url: https://www.eurogamer.net/feed
    tier: 2
    sections: [releases, industry]
  - id: gamespot
    kind: rss
    url: https://www.gamespot.com/feeds/mashup/
    tier: 2
    sections: [releases, industry, hardware]
  - id: vg247
    kind: rss
    url: https://www.vg247.com/feed
    tier: 2
    sections: [industry, releases]
  - id: arstechnica-gaming
    kind: rss
    url: https://feeds.arstechnica.com/arstechnica/gaming
    tier: 3
    sections: [hardware, industry]

# -----------------------------------------------------------------------------
# INDUSTRY — trade press
# -----------------------------------------------------------------------------
industry:
  - id: gamedeveloper
    kind: rss
    url: https://www.gamedeveloper.com/rss.xml
    tier: 2
    sections: [dev-tech, industry]

# -----------------------------------------------------------------------------
# ESPORTS
# -----------------------------------------------------------------------------
esports:
  - id: dotesports
    kind: rss
    url: https://dotesports.com/feed
    tier: 2
    sections: [esports]
  - id: dexerto
    kind: rss
    url: https://www.dexerto.com/feed/
    tier: 3
    sections: [esports]

# -----------------------------------------------------------------------------
# COMMUNITY
# -----------------------------------------------------------------------------
community:
  - id: reddit-games
    kind: rss
    url: https://www.reddit.com/r/Games/.rss
    tier: 3
    sections: [community-modding, indie]
  - id: reddit-gamedev
    kind: rss
    url: https://www.reddit.com/r/gamedev/.rss
    tier: 3
    sections: [dev-tech, community-modding]

# -----------------------------------------------------------------------------
# NOT YET INCLUDED
#
# Steam's own news feed and itch.io's new-and-popular listing would be
# strong platform-native sources (a "platforms" group), but this plan does
# not include them: no page on either site was confirmed by hand to expose
# a per-item RSS/JSON feed with a real published_at (itch.io's catalog pages
# in particular look like they lack one). Add a "platforms" group here once
# a specific URL has been verified against a real fetch run — see Task 8 and
# pipeline/fetch.py's fetch_scrape as the fallback if no feed exists.
# -----------------------------------------------------------------------------
```

- [ ] **Step 2: Commit**

```bash
git add config/sources.yaml
git commit -m "$(cat <<'EOF'
Add config/sources.yaml source registry for video-game sections

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `fetch.py`

**Files:**
- Create: `pipeline/fetch.py`
- Test: `tests/test_fetch_rss.py`
- Test: `tests/test_fetch_scrape.py`
- Test: `tests/test_fetch_main.py`

**Interfaces:**
- Consumes: `RawItem` (Task 1), `week_bounds`/`current_week`/`week_from_date` (Task 2, used only inside `main()`), the `outlets`/`industry`/`esports`/`community` group names (Task 3).
- Produces: `fetch_rss(source: dict, since: datetime, until: datetime) -> list[RawItem]`, `fetch_scrape(source: dict, since: datetime, until: datetime) -> list[RawItem]`, `SELECTORS: dict[str, dict[str, str]]`, `FETCHERS: dict[str, callable]` (keys `"rss"`, `"scrape"`), `main() -> None` — all in `pipeline/fetch.py`. Later tasks don't import from `fetch.py`, but Task 8's manual verification runs it as a script.

- [ ] **Step 1: Write the failing RSS/scrape fetcher tests**

`tests/test_fetch_rss.py`:
```python
from datetime import datetime, timezone

import httpx
import respx

from fetch import fetch_rss

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<item>
  <title>In window</title>
  <link>https://example.com/in-window</link>
  <pubDate>Tue, 18 Aug 2026 12:00:00 GMT</pubDate>
  <description>Summary A</description>
</item>
<item>
  <title>Out of window</title>
  <link>https://example.com/out-of-window</link>
  <pubDate>Tue, 11 Aug 2026 12:00:00 GMT</pubDate>
  <description>Summary B</description>
</item>
</channel></rss>
"""


@respx.mock
def test_fetch_rss_filters_to_window_and_maps_fields():
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, text=FEED_XML)
    )
    source = {"id": "example-outlet", "url": "https://example.com/feed.xml"}
    since = datetime(2026, 8, 17, tzinfo=timezone.utc)
    until = datetime(2026, 8, 24, tzinfo=timezone.utc)

    items = fetch_rss(source, since, until)

    assert len(items) == 1
    item = items[0]
    assert item.source_id == "example-outlet"
    assert item.kind == "article"
    assert item.title == "In window"
    assert str(item.url) == "https://example.com/in-window"
    assert item.summary == "Summary A"
    assert item.published_at == datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
```

`tests/test_fetch_scrape.py`:
```python
from datetime import datetime, timezone

import httpx
import respx

from fetch import SELECTORS, fetch_scrape

SAMPLE_HTML = """
<html><body>
<article class="post-card">
  <h2 class="post-title"><a href="/news/one">In window post</a></h2>
  <time datetime="2026-08-18T00:00:00Z"></time>
</article>
<article class="post-card">
  <h2 class="post-title"><a href="/news/two">Out of window post</a></h2>
  <time datetime="2026-08-01T00:00:00Z"></time>
</article>
</body></html>
"""


@respx.mock
def test_fetch_scrape_uses_registered_selector():
    SELECTORS["example-store"] = {
        "item": "article.post-card",
        "title": "h2.post-title a",
        "link": "h2.post-title a",
        "date": "time",
    }
    respx.get("https://example.com/blog/").mock(
        return_value=httpx.Response(200, text=SAMPLE_HTML)
    )
    source = {"id": "example-store", "url": "https://example.com/blog/"}
    since = datetime(2026, 8, 17, tzinfo=timezone.utc)
    until = datetime(2026, 8, 24, tzinfo=timezone.utc)

    items = fetch_scrape(source, since, until)

    assert len(items) == 1
    assert items[0].title == "In window post"
    assert str(items[0].url) == "https://example.com/news/one"


SAMPLE_HTML_SELF_LINK = """
<html><body>
<a href="/news/one" class="listItem">
  <time>Aug 18, 2026</time>
  <span class="title">In window post, self-linked card</span>
</a>
<a href="/news/two" class="listItem">
  <time>Aug 1, 2026</time>
  <span class="title">Out of window post, self-linked card</span>
</a>
</body></html>
"""


@respx.mock
def test_fetch_scrape_uses_item_as_link_when_no_link_selector_configured():
    SELECTORS["example-lister"] = {
        "item": "a.listItem",
        "title": "span.title",
        "link": "",
        "date": "time",
    }
    respx.get("https://example.com/blog/").mock(
        return_value=httpx.Response(200, text=SAMPLE_HTML_SELF_LINK)
    )
    source = {"id": "example-lister", "url": "https://example.com/blog/"}
    since = datetime(2026, 8, 17, tzinfo=timezone.utc)
    until = datetime(2026, 8, 24, tzinfo=timezone.utc)

    items = fetch_scrape(source, since, until)

    assert len(items) == 1
    assert items[0].title == "In window post, self-linked card"
    assert str(items[0].url) == "https://example.com/news/one"
    assert items[0].published_at == datetime(2026, 8, 18, tzinfo=timezone.utc)
```

`tests/test_fetch_main.py`:
```python
import json

import pytest

import fetch
from models import RawItem


def _item(source_id: str, url: str) -> RawItem:
    return RawItem(
        source_id=source_id,
        kind="article",
        title=f"Item from {source_id}",
        url=url,
        published_at="2026-08-18T00:00:00Z",
    )


def _sources_yaml(*ids_and_tiers: tuple[str, int]) -> str:
    """Build a minimal single-group sources.yaml, one rss source per (id, tier) pair."""
    if not ids_and_tiers:
        return "outlets: []\n"
    lines = ["outlets:"]
    for source_id, tier in ids_and_tiers:
        lines += [
            f"  - id: {source_id}",
            "    kind: rss",
            f"    url: https://example.com/{source_id}.xml",
            f"    tier: {tier}",
            "    sections: [releases]",
        ]
    return "\n".join(lines) + "\n"


def _write_sources_yaml(tmp_path, *ids_and_tiers: tuple[str, int]) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(_sources_yaml(*ids_and_tiers))


def test_main_writes_jsonl_and_survives_one_dead_source(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1), ("dead-source", 2))

    def fake_fetch_rss(source, since, until):
        if source["id"] == "dead-source":
            raise ConnectionError("boom")
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    out_path = tmp_path / "data" / "raw" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["source_id"] == "good-source"

    captured = capsys.readouterr()
    assert "dead-source" in captured.out
    assert "boom" in captured.out
    assert "1 source(s) failed this run" in captured.out
    assert "- dead-source: boom" in captured.out


def test_main_prints_all_succeeded_when_no_failures(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    captured = capsys.readouterr()
    assert "all sources fetched successfully" in captured.out


def test_main_accepts_date_instead_of_week(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--date", "2026-08-24"])

    fetch.main()

    out_path = tmp_path / "data" / "raw" / "2026-W35.jsonl"
    assert out_path.exists()


def test_main_rejects_week_and_date_together(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)

    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--date", "2026-08-24"]
    )

    with pytest.raises(SystemExit):
        fetch.main()


def test_main_with_only_preserves_other_sources_existing_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("source-b", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    existing = [
        _item("source-a", "https://example.com/old-a").model_dump_json(),
        _item("source-b", "https://example.com/old-b").model_dump_json(),
    ]
    (raw_dir / "2026-W34.jsonl").write_text("\n".join(existing) + "\n")

    def fake_fetch_rss(source, since, until):
        assert source["id"] == "source-b", "only source-b should be re-fetched"
        return [_item("source-b", "https://example.com/new-b")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "source-b"]
    )

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    by_source = {item["source_id"]: item["url"] for item in lines}
    assert len(lines) == 2, "source-a's old line must survive, source-b's must be replaced"
    assert by_source["source-a"] == "https://example.com/old-a", "untouched source must be preserved verbatim"
    assert by_source["source-b"] == "https://example.com/new-b", "re-fetched source must use fresh data"


def test_main_with_only_preserves_prior_data_on_repeat_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("flaky-source", 2))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    existing = [
        _item("source-a", "https://example.com/old-a").model_dump_json(),
        _item("flaky-source", "https://example.com/old-flaky").model_dump_json(),
    ]
    (raw_dir / "2026-W34.jsonl").write_text("\n".join(existing) + "\n")

    def fake_fetch_rss(source, since, until):
        if source["id"] == "flaky-source":
            raise ConnectionError("still down")
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "flaky-source"]
    )

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    by_source = {item["source_id"]: item["url"] for item in lines}
    assert len(lines) == 2, "a failed retry must not drop the source's prior data"
    assert by_source["flaky-source"] == "https://example.com/old-flaky", "prior data survives a repeat failure"
    assert by_source["source-a"] == "https://example.com/old-a", "sources outside --only are always untouched"


def test_main_without_only_still_overwrites_fully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "2026-W34.jsonl").write_text(
        _item("stale-source-no-longer-configured", "https://example.com/stale").model_dump_json() + "\n"
    )

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    assert len(lines) == 1, "a full run (no --only) must still fully overwrite, not merge"
    assert lines[0]["source_id"] == "good-source"


def test_main_warns_on_unmatched_only_source_id_but_still_fetches_real_ones(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("real-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys,
        "argv",
        ["fetch.py", "--week", "2026-W34", "--only", "real-source,nonexistent-source"],
    )

    fetch.main()

    captured = capsys.readouterr()
    assert "nonexistent-source" in captured.out
    assert "WARNING" in captured.out

    out_path = tmp_path / "data" / "raw" / "2026-W34.jsonl"
    lines = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["source_id"] == "real-source"


def test_main_preserves_malformed_line_in_existing_raw_file_during_merge(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("source-b", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    valid_line = _item("source-a", "https://example.com/old-a").model_dump_json()
    malformed_line = "not valid json"
    (raw_dir / "2026-W34.jsonl").write_text(valid_line + "\n" + malformed_line + "\n")

    def fake_fetch_rss(source, since, until):
        assert source["id"] == "source-b", "only source-b should be re-fetched"
        return [_item("source-b", "https://example.com/new-b")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "source-b"]
    )

    fetch.main()

    captured = capsys.readouterr()
    assert "WARNING" in captured.out

    lines = (raw_dir / "2026-W34.jsonl").read_text().splitlines()
    assert malformed_line in lines, "malformed line must be preserved verbatim, not dropped"
    assert valid_line in lines, "valid pre-existing line must still be preserved"
    parsed = [json.loads(line) for line in lines if line != malformed_line]
    by_source = {item["source_id"]: item["url"] for item in parsed}
    assert by_source["source-b"] == "https://example.com/new-b"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_fetch_rss.py tests/test_fetch_scrape.py tests/test_fetch_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fetch'`

- [ ] **Step 3: Write `pipeline/fetch.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_fetch_rss.py tests/test_fetch_scrape.py tests/test_fetch_main.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/fetch.py tests/test_fetch_rss.py tests/test_fetch_scrape.py tests/test_fetch_main.py
git commit -m "$(cat <<'EOF'
Add pipeline/fetch.py (rss + scrape fetchers, CLI)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `score.py` — dedupe + prefilter

**Files:**
- Create: `pipeline/score.py` (this task writes `dedupe`, `_is_relevant`, `prefilter`, and the module-level config globals; Task 6 appends `classify_and_score`, `rank`, and `main` to the same file)
- Test: `tests/test_score_dedupe.py`
- Test: `tests/test_score_prefilter.py`

**Interfaces:**
- Consumes: `RawItem` (Task 1).
- Produces: `SOURCE_TIERS: dict[str, int]`, `SOURCE_FAMILIES: dict[str, str]`, `RELEVANCE_KEYWORDS: list[str]` (module globals, monkeypatched directly by tests), `_tier(source_id: str) -> int`, `_family(source_id: str) -> str`, `_load_seen() -> set[str]`, `dedupe(items: list[RawItem]) -> list[RawItem]`, `_is_relevant(item: RawItem) -> bool`, `prefilter(items: list[RawItem]) -> list[RawItem]` — all in `pipeline/score.py`. Task 6 reads `SOURCE_TIERS`/`SOURCE_FAMILIES`/`_tier`/`_family` and defines `rank()` in the same module.

- [ ] **Step 1: Write the failing dedupe tests**

`tests/test_score_dedupe.py`:
```python
from models import RawItem
from score import dedupe


def _item(source_id, url, title="Same Story", **meta):
    return RawItem(
        source_id=source_id,
        kind="article",
        title=title,
        url=url,
        published_at="2026-08-18T00:00:00Z",
        meta=meta,
    )


def test_dedupe_collapses_identical_url_keeps_highest_tier_source(monkeypatch):
    tiers = {"ign": 1, "vg247": 2}
    monkeypatch.setattr("score.SOURCE_TIERS", tiers)

    same_url = "https://syndicated.example.com/story"
    items = [
        _item("vg247", same_url),
        _item("ign", same_url),
    ]
    result = dedupe(items)

    assert len(result) == 1
    assert result[0].source_id == "ign"


def test_dedupe_collapses_near_dupe_titles(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"ign": 1, "small-blog": 3})
    items = [
        _item("ign", "https://ign.example.com/a", title="New Patch Fixes Server Crashes"),
        _item("small-blog", "https://small-blog.example.com/a", title="new patch fixes server crashes!"),
    ]
    result = dedupe(items)
    assert len(result) == 1
    assert result[0].source_id == "ign"


def test_dedupe_keeps_distinct_items():
    items = [_item("s1", "https://a.com/1", title="A"), _item("s2", "https://a.com/2", title="B")]
    result = dedupe(items)
    assert len(result) == 2


def test_dedupe_does_not_merge_title_subset_of_a_different_story():
    """rapidfuzz.token_set_ratio scores a strict token-subset as 100, which
    would otherwise wrongly merge two distinct stories whenever one title's
    words are a subset of the other's (e.g. a story and its follow-up).
    """
    items = [
        _item("s1", "https://a.com/1", title="Studio Announces New Game"),
        _item(
            "s2",
            "https://a.com/2",
            title="Studio Announces New Game Delayed To Next Year",
        ),
    ]
    result = dedupe(items)
    assert len(result) == 2


def test_dedupe_transitive_chain_collapses_via_equivalence_closure():
    """A founds a group by dedupe_key. B has a *different* key but a
    near-dupe title, so it joins A's group via the title path. C shares
    B's exact dedupe_key (identical URL) but has a title that does NOT
    near-dupe-match A's (the group founder). All three must still collapse
    into one group, because B's key must have been registered when it
    joined — C is identity-equal to B by the exact-key rule even though
    C's title alone would never match A's.
    """
    item_a = _item(
        "ign",
        "https://ign.example.com/story",
        title="Big Studio Reveals New Sequel",
    )
    item_b = _item(
        "aggregator",
        "https://aggregator.example.com/repost",
        title="Big Studio Reveals New Sequel",
    )
    item_c = _item(
        "aggregator",
        "https://aggregator.example.com/repost",
        title="Completely Unrelated Headline About Something Else",
    )

    result = dedupe([item_a, item_b, item_c])

    assert len(result) == 1
```

- [ ] **Step 2: Write the failing prefilter tests**

`tests/test_score_prefilter.py`:
```python
from models import RawItem
from score import prefilter


def _item(source_id, title, summary="A short summary.", **meta):
    return RawItem(
        source_id=source_id,
        kind="article",
        title=title,
        url=f"https://example.com/{title.replace(' ', '-')}",
        published_at="2026-08-18T00:00:00Z",
        summary=summary,
        meta=meta,
    )


def test_prefilter_drops_empty_summary(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    items = [_item("s", "No Summary", summary="")]
    assert prefilter(items) == []


def test_prefilter_auto_keeps_tier1_even_without_summary(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"tier1-src": 1})
    monkeypatch.setattr("score._load_seen", lambda: set())
    items = [_item("tier1-src", "Tier 1 Item", summary="")]
    assert len(prefilter(items)) == 1


def test_prefilter_drops_already_seen_item(monkeypatch):
    from models import RawItem as _RawItem

    already_seen = _item("s", "Old Story")
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: {already_seen.dedupe_key})
    assert prefilter([already_seen]) == []


def test_prefilter_drops_tier2_item_with_no_relevance_keyword(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["speedrun"])
    items = [_item("s", "A Recipe Blog Post About Soup")]
    assert prefilter(items) == []


def test_prefilter_keeps_tier2_item_matching_relevance_keyword(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["speedrun"])
    items = [_item("s", "New World Record Speedrun Set Overnight")]
    assert len(prefilter(items)) == 1


def test_prefilter_relevance_keyword_matches_title_not_summary(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["speedrun"])
    items = [
        _item(
            "s",
            "A Recipe Blog Post About Soup",
            summary="Unrelated aside: a speedrun record was also set this week.",
        )
    ]
    assert prefilter(items) == []


def test_prefilter_relevance_keyword_matches_despite_trailing_punctuation(monkeypatch):
    """Regression: a naive space-padded substring check misses "DLC:" because
    a colon, not a space, follows the keyword. The word-boundary regex must
    tolerate any non-alphanumeric neighbor, not just whitespace."""
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["dlc"])
    items = [_item("s", "New DLC: Everything Announced Today")]
    assert len(prefilter(items)) == 1


def test_prefilter_relevance_keyword_does_not_match_inside_larger_word(monkeypatch):
    """A short keyword like "mod" must not match inside an unrelated word
    that happens to contain those letters (e.g. "modern")."""
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["mod"])
    items = [_item("s", "A Modern History Retrospective")]
    assert prefilter(items) == []


def test_prefilter_relevance_keyword_matches_plural_form(monkeypatch):
    """Regression: "mod" as a keyword must also match the far more common
    plural "mods" in a title."""
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", ["mod"])
    items = [_item("s", "Best Mods Of The Year So Far")]
    assert len(prefilter(items)) == 1


def test_prefilter_empty_relevance_keywords_keeps_everything(monkeypatch):
    """config/sources.yaml intentionally ships relevance_keywords: [] for
    this domain — every source is already a dedicated games outlet, so an
    empty list must mean "keep everything", not "drop everything"."""
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    monkeypatch.setattr("score._load_seen", lambda: set())
    monkeypatch.setattr("score.RELEVANCE_KEYWORDS", [])
    items = [_item("s", "Anything At All")]
    assert len(prefilter(items)) == 1
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_score_dedupe.py tests/test_score_prefilter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'score'`

- [ ] **Step 4: Write `pipeline/score.py` (dedupe + prefilter section)**

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_score_dedupe.py tests/test_score_prefilter.py -v`
Expected: 15 passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_score_dedupe.py tests/test_score_prefilter.py
git commit -m "$(cat <<'EOF'
Add score.py dedupe + prefilter

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `score.py` — classify_and_score + rank + main

**Files:**
- Modify: `pipeline/score.py` (append to the file created in Task 5)
- Test: `tests/test_score_classify.py`
- Test: `tests/test_score_rank.py`
- Test: `tests/test_score_main.py`

**Interfaces:**
- Consumes: `dedupe`, `prefilter`, `SOURCE_TIERS`, `SOURCE_FAMILIES`, `RELEVANCE_KEYWORDS`, `_tier`, `_family`, `RawItem`, `ScoredItem`, `SectionId` (Task 1 + Task 5), `week_from_date` (Task 2, used only inside `main()`).
- Produces: `classify_and_score(items: list[RawItem]) -> list[ScoredItem]`, `rank(items: list[ScoredItem]) -> list[ScoredItem]`, `_load_source_tiers() -> dict[str, int]`, `_load_source_families() -> dict[str, str]`, `_load_relevance_keywords() -> list[str]`, `main() -> None` — appended to `pipeline/score.py`. Task 7 (`build.py`) does not import from `score.py`, but consumes its output files (`data/scored/<week>.jsonl`).

- [ ] **Step 1: Write the failing classify tests**

`tests/test_score_classify.py`:
```python
import json
from unittest.mock import MagicMock, patch

from models import RawItem
from score import classify_and_score


def _item(url, title="A Story", **meta):
    return RawItem(
        source_id="s",
        kind="article",
        title=title,
        url=url,
        published_at="2026-08-18T00:00:00Z",
        summary="Summary",
        meta=meta,
    )


def _fake_response(payload: list[dict]):
    response = MagicMock()
    response.content = [MagicMock(type="text", text=json.dumps(payload))]
    return response


@patch("score.Anthropic")
def test_classify_and_score_maps_valid_rows(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://example.com/a",
                "section": "releases",
                "score": 0.8,
                "why": "A major sequel just got a release date.",
            }
        ]
    )

    items = [_item("https://example.com/a", mirror_urls=["https://mirror.com/a"])]
    result = classify_and_score(items)

    assert len(result) == 1
    scored = result[0]
    assert scored.section == "releases"
    assert scored.score == 0.8
    assert scored.why == "A major sequel just got a release date."
    assert str(scored.mirrors[0]) == "https://mirror.com/a"


@patch("score.Anthropic")
def test_classify_and_score_drops_invalid_section(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://example.com/a",
                "section": "not-a-real-section",
                "score": 0.5,
                "why": "x",
            }
        ]
    )

    items = [_item("https://example.com/a")]
    result = classify_and_score(items)

    assert result == []


@patch("score.Anthropic")
def test_classify_and_score_batches_by_twenty(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response([])

    items = [_item(f"https://example.com/{i}") for i in range(45)]
    classify_and_score(items)

    assert mock_client.messages.create.call_count == 3


@patch("score.Anthropic")
def test_classify_and_score_handles_non_list_json_response(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock(type="text", text=json.dumps({"error": "rate limited"}))]
    mock_client.messages.create.return_value = response

    items = [_item("https://example.com/a")]
    result = classify_and_score(items)

    assert result == []
```

- [ ] **Step 2: Write the failing rank tests**

`tests/test_score_rank.py`:
```python
from models import ScoredItem
from score import rank


def _scored(section, score, source_id="s", **meta):
    return ScoredItem(
        source_id=source_id,
        kind="article",
        title=f"Item {score}",
        url=f"https://example.com/{section}-{score}-{source_id}",
        published_at="2026-08-18T00:00:00Z",
        section=section,
        score=score,
        why="why",
        meta=meta,
    )


def test_rank_caps_six_per_section(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s0": 2, "s1": 2, "s2": 2, "s3": 2})
    items = [_scored("releases", 0.9 - i * 0.01, source_id=f"s{i % 4}") for i in range(10)]
    result = rank(items)
    assert len(result) == 6


def test_rank_orders_by_blended_score_descending(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"tier1": 1, "tier3": 3})
    low_score_tier1 = _scored("releases", 0.5, source_id="tier1")
    high_score_tier3 = _scored("releases", 0.9, source_id="tier3")
    result = rank([high_score_tier3, low_score_tier1])
    assert result[0].score == 0.9


def test_rank_empty_section_returns_empty_list(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"s": 2})
    assert rank([]) == []


def test_rank_asserts_exact_blended_formula_weights(monkeypatch):
    """Assert rank() uses exact formula: 0.55*model + 0.25*social + 0.20*source_tier.

    Constructs a case where blended formula reverses raw-score ordering:
    lower raw score + high social + tier-1 beats higher raw score + no social + tier-4.
    Raw-score-only sort would fail this test.
    """
    import math

    monkeypatch.setattr("score.SOURCE_TIERS", {"tier1": 1, "tier4": 4})

    item_a = _scored("releases", 0.6, source_id="tier1", social_signal=10000)
    item_b = _scored("releases", 0.9, source_id="tier4", social_signal=0)

    social_a = min(math.log1p(10000) / math.log1p(1000), 1.0)
    tier_a = (4 - 1) / 3
    final_a = 0.55 * 0.6 + 0.25 * social_a + 0.20 * tier_a  # ~0.78

    social_b = min(math.log1p(0) / math.log1p(1000), 1.0)
    tier_b = (4 - 4) / 3
    final_b = 0.55 * 0.9 + 0.25 * social_b + 0.20 * tier_b  # 0.495

    result = rank([item_b, item_a])
    assert result[0].score == 0.6, f"Item A should rank first, got {result[0].score}"
    assert result[1].score == 0.9, f"Item B should rank second, got {result[1].score}"


def test_rank_caps_three_per_source_within_a_section(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"dominant": 2, "other": 2, "filler": 2})
    dominant_items = [
        _scored("releases", 0.9 - i * 0.01, source_id="dominant") for i in range(10)
    ]
    other_items = [_scored("releases", 0.5, source_id="other") for _ in range(2)]
    filler_items = [_scored("releases", 0.4, source_id="filler") for _ in range(1)]
    result = rank(dominant_items + other_items + filler_items)

    assert len(result) == 6, "section cap of 6 should still apply"
    by_source = [item.source_id for item in result]
    assert by_source.count("dominant") == 3, "no source should exceed 3 items in a section"
    assert by_source.count("other") == 2, "lower-scoring source should fill the freed-up slots"
    assert by_source.count("filler") == 1, "third source should fill the last freed-up slot"


def test_rank_caps_three_per_family_across_different_source_ids(monkeypatch):
    monkeypatch.setattr("score.SOURCE_TIERS", {"net-a": 2, "net-b": 2, "other": 2, "filler": 2})
    monkeypatch.setattr("score.SOURCE_FAMILIES", {"net-a": "network-group", "net-b": "network-group"})
    net_a_items = [_scored("releases", 0.9 - i * 0.01, source_id="net-a") for i in range(4)]
    net_b_items = [_scored("releases", 0.8 - i * 0.01, source_id="net-b") for i in range(4)]
    other_items = [_scored("releases", 0.5, source_id="other") for _ in range(2)]
    filler_items = [_scored("releases", 0.4, source_id="filler") for _ in range(1)]
    result = rank(net_a_items + net_b_items + other_items + filler_items)

    assert len(result) == 6, "section cap of 6 should still apply"
    by_source = [item.source_id for item in result]
    network_count = sum(1 for s in by_source if s in ("net-a", "net-b"))
    assert network_count == 3, "combined family (across both source_ids) must not exceed 3"
    assert by_source.count("other") == 2, "other family fills freed-up slots"
    assert by_source.count("filler") == 1, "filler family fills the last freed-up slot"
```

- [ ] **Step 3: Write the failing `main()` tests**

`tests/test_score_main.py`:
```python
import json

import pytest

import score
from models import ScoredItem


def _write_raw_item(tmp_path, week):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(
        "outlets:\n  - id: s\n    kind: rss\n    tier: 1\n    sections: [releases]\n"
    )
    raw_item = {
        "source_id": "s",
        "kind": "article",
        "title": "A Story",
        "url": "https://example.com/a",
        "published_at": "2026-08-18T00:00:00Z",
        "summary": "Summary",
        "authors": [],
        "meta": {},
    }
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / f"{week}.jsonl").write_text(json.dumps(raw_item) + "\n")


def test_main_prefilter_stage_writes_prefiltered_raw_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_raw_item(tmp_path, "2026-W34")

    monkeypatch.setattr("sys.argv", ["score.py", "--week", "2026-W34", "--stage", "prefilter"])
    score.main()

    out_path = tmp_path / "data" / "prefiltered" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["title"] == "A Story"
    assert "section" not in row  # RawItem, not yet a ScoredItem


def test_main_rank_stage_reads_and_overwrites_scored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    scored_item = ScoredItem(
        source_id="s",
        kind="article",
        title="A Story",
        url="https://example.com/a",
        published_at="2026-08-18T00:00:00Z",
        section="releases",
        score=0.9,
        why="why",
    )
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text(
        scored_item.model_dump_json() + "\n"
    )

    monkeypatch.setattr("sys.argv", ["score.py", "--week", "2026-W34", "--stage", "rank"])
    score.main()

    out_path = tmp_path / "data" / "scored" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["section"] == "releases"


def test_main_rejects_unknown_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["score.py", "--week", "2026-W34", "--stage", "bogus"]
    )
    with pytest.raises(SystemExit):
        score.main()


def test_main_accepts_date_instead_of_week(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_raw_item(tmp_path, "2026-W35")

    monkeypatch.setattr(
        "sys.argv", ["score.py", "--date", "2026-08-24", "--stage", "prefilter"]
    )
    score.main()

    assert (tmp_path / "data" / "prefiltered" / "2026-W35.jsonl").exists()


def test_main_rejects_week_and_date_together(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["score.py", "--week", "2026-W34", "--date", "2026-08-24", "--stage", "prefilter"],
    )
    with pytest.raises(SystemExit):
        score.main()
```

- [ ] **Step 4: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_score_classify.py tests/test_score_rank.py tests/test_score_main.py -v`
Expected: FAIL — `AttributeError`/`ImportError` (`classify_and_score`, `rank`, `main` don't exist yet)

- [ ] **Step 5: Append `classify_and_score`, `rank`, `_load_*`, and `main` to `pipeline/score.py`**

Append this to the end of the file written in Task 5:

```python

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
            for line in f:
                raw_items.append(RawItem.model_validate_json(line))

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
            for line in f:
                scored_items.append(ScoredItem.model_validate_json(line))

        ranked = rank(scored_items)

        with open(f"data/scored/{args.week}.jsonl", "w", encoding="utf-8") as out:
            for item in ranked:
                out.write(item.model_dump_json() + "\n")

        print(f"week {args.week}: {len(scored_items)} classified -> {len(ranked)} ranked")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_score_classify.py tests/test_score_rank.py tests/test_score_main.py -v`
Expected: 15 passed

- [ ] **Step 7: Run the full test suite so far**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all tests across Tasks 1–6 pass (48 tests)

- [ ] **Step 8: Commit**

```bash
git add pipeline/score.py tests/test_score_classify.py tests/test_score_rank.py tests/test_score_main.py
git commit -m "$(cat <<'EOF'
Add score.py classify_and_score + rank + CLI

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `build.py`

**Files:**
- Create: `pipeline/build.py`
- Test: `tests/test_build.py`
- Test: `tests/test_build_main.py`

**Interfaces:**
- Consumes: `Issue`, `ScoredItem`, `Section` (Task 1), `week_bounds` (Task 2, used inside `build_issue`).
- Produces: `write_headline(items: list[ScoredItem]) -> str`, `write_title(items: list[ScoredItem]) -> str`, `build_issue(week, items, headline=None, title=None, analyzed_by=None, section_summaries=None) -> Issue`, `main() -> None` — all in `pipeline/build.py`. Nothing later in this plan consumes these (the site/automation plans that follow read the JSON files this stage writes, not the Python functions).

- [ ] **Step 1: Write the failing tests**

`tests/test_build.py`:
```python
from unittest.mock import MagicMock, patch

from build import build_issue, write_headline, write_title
from models import ScoredItem


def _scored(section, score, title="Item"):
    return ScoredItem(
        source_id="s",
        kind="article",
        title=title,
        url=f"https://example.com/{section}-{title}".replace(" ", "-"),
        published_at="2026-08-18T00:00:00Z",
        section=section,
        score=score,
        why="why line",
    )


@patch("build.Anthropic")
def test_write_headline_returns_model_text(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock(type="text", text="A quiet week for new releases.")]
    mock_client.messages.create.return_value = response

    items = [_scored("releases", 0.9)]
    assert write_headline(items) == "A quiet week for new releases."


@patch("build.Anthropic")
def test_write_headline_handles_no_items(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock(type="text", text="It was a quiet week.")]
    mock_client.messages.create.return_value = response

    assert write_headline([]) == "It was a quiet week."
    mock_client.messages.create.assert_called_once()


@patch("build.Anthropic")
def test_write_title_returns_model_text(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock(type="text", text="A Sequel Nobody Saw Coming")]
    mock_client.messages.create.return_value = response

    items = [_scored("releases", 0.9)]
    assert write_title(items) == "A Sequel Nobody Saw Coming"


@patch("build.Anthropic")
def test_write_title_handles_no_items(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock(type="text", text="A Quiet Week")]
    mock_client.messages.create.return_value = response

    assert write_title([]) == "A Quiet Week"
    mock_client.messages.create.assert_called_once()


def test_build_issue_groups_by_section_and_sets_bounds(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {
            "releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"},
            "esports": {"label": "Esports", "blurb": "Competitive scene, tournaments"},
        },
    )
    items = [_scored("releases", 0.9, "Alpha"), _scored("releases", 0.7, "Beta")]
    with patch("build.write_headline", return_value="Steady week for releases."), patch(
        "build.write_title", return_value="Steady Progress"
    ):
        issue = build_issue("2026-W34", items)

    assert issue.week == "2026-W34"
    assert issue.headline == "Steady week for releases."
    assert issue.title == "Steady Progress"
    section_ids = [s.id for s in issue.sections]
    assert "releases" in section_ids
    assert "esports" not in section_ids  # no items -> quiet week -> omitted, not padded
    releases_section = next(s for s in issue.sections if s.id == "releases")
    assert len(releases_section.items) == 2
    assert issue.stats["items_kept"] == 2


def test_build_issue_uses_given_headline_without_calling_write_headline(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    with patch("build.write_headline") as mock_write_headline:
        issue = build_issue(
            "2026-W34", items, headline="Supplied by Claude Code locally.", title="A title"
        )

    assert issue.headline == "Supplied by Claude Code locally."
    mock_write_headline.assert_not_called()


def test_build_issue_records_analyzed_by_in_stats(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    issue = build_issue("2026-W34", items, headline="h", title="t", analyzed_by="claude-sonnet-5")

    assert issue.stats["analyzed_by"] == "claude-sonnet-5"


def test_build_issue_applies_section_summaries(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {
            "releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"},
            "esports": {"label": "Esports", "blurb": "Competitive scene, tournaments"},
        },
    )
    items = [_scored("releases", 0.9, "Alpha")]
    issue = build_issue(
        "2026-W34",
        items,
        headline="h",
        title="t",
        section_summaries={"releases": "A quiet week for new releases."},
    )

    releases_section = next(s for s in issue.sections if s.id == "releases")
    assert releases_section.summary == "A quiet week for new releases."


def test_build_issue_defaults_summary_to_empty_string(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    issue = build_issue("2026-W34", items, headline="h", title="t")

    assert issue.sections[0].summary == ""


def test_build_issue_uses_given_title_without_calling_write_title(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    with patch("build.write_title") as mock_write_title:
        issue = build_issue(
            "2026-W34", items, headline="h", title="A Sequel Nobody Saw Coming"
        )

    assert issue.title == "A Sequel Nobody Saw Coming"
    mock_write_title.assert_not_called()


def test_build_issue_generates_title_by_default(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    with patch("build.write_title", return_value="Generated Title") as mock_write_title:
        issue = build_issue("2026-W34", items, headline="h")

    assert issue.title == "Generated Title"
    mock_write_title.assert_called_once_with(items)
```

`tests/test_build_main.py`:
```python
import json
from unittest.mock import patch

import build

_SOURCES_YAML = (
    "sections:\n"
    "  - id: releases\n"
    '    label: "Releases & updates"\n'
    '    blurb: "New launches, major patches, DLC"\n'
)


def _write_sources_yaml(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(_SOURCES_YAML)


def test_main_writes_issue_index_and_seen(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    scored_item = {
        "source_id": "s",
        "kind": "article",
        "title": "A Story",
        "url": "https://example.com/a",
        "published_at": "2026-08-18T00:00:00Z",
        "summary": "",
        "authors": [],
        "meta": {},
        "section": "releases",
        "score": 0.9,
        "why": "why line",
        "mirrors": [],
    }
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text(json.dumps(scored_item) + "\n")

    with patch("build.write_headline", return_value="Notable week."), patch(
        "build.write_title", return_value="Notable Week"
    ):
        monkeypatch.setattr("sys.argv", ["build.py", "--week", "2026-W34"])
        build.main()

    issue = json.loads((tmp_path / "data" / "2026-W34.json").read_text())
    assert issue["week"] == "2026-W34"
    assert issue["headline"] == "Notable week."
    assert issue["title"] == "Notable Week"

    index = json.loads((tmp_path / "data" / "index.json").read_text())
    assert index[0]["week"] == "2026-W34"

    seen = json.loads((tmp_path / "data" / "seen.json").read_text())
    assert "url:https://example.com/a" in seen


def test_main_is_atomic_no_tmp_file_left_behind(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    (tmp_path / "data" / "scored" / "2026-W35.jsonl").write_text("")

    with patch("build.write_headline", return_value="Quiet week."), patch(
        "build.write_title", return_value="Quiet Week"
    ):
        monkeypatch.setattr("sys.argv", ["build.py", "--week", "2026-W35"])
        build.main()

    assert not (tmp_path / "data" / ".2026-W35.json.tmp").exists()
    assert (tmp_path / "data" / "2026-W35.json").exists()


def test_main_headline_flag_skips_write_headline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text("")

    with patch("build.write_headline") as mock_write_headline, patch(
        "build.write_title", return_value="Title"
    ):
        monkeypatch.setattr(
            "sys.argv", ["build.py", "--week", "2026-W34", "--headline", "Written by Claude Code."]
        )
        build.main()

    mock_write_headline.assert_not_called()
    issue = json.loads((tmp_path / "data" / "2026-W34.json").read_text())
    assert issue["headline"] == "Written by Claude Code."


def test_main_title_flag_sets_issue_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text("")

    with patch("build.write_headline", return_value="Quiet week."), patch(
        "build.write_title"
    ) as mock_write_title:
        monkeypatch.setattr(
            "sys.argv",
            ["build.py", "--week", "2026-W34", "--title", "A Sequel Nobody Saw Coming"],
        )
        build.main()

    mock_write_title.assert_not_called()
    issue = json.loads((tmp_path / "data" / "2026-W34.json").read_text())
    assert issue["title"] == "A Sequel Nobody Saw Coming"


def test_main_without_title_flag_generates_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text("")

    with patch("build.write_headline", return_value="Quiet week."), patch(
        "build.write_title", return_value="Generated Title"
    ):
        monkeypatch.setattr("sys.argv", ["build.py", "--week", "2026-W34"])
        build.main()

    issue = json.loads((tmp_path / "data" / "2026-W34.json").read_text())
    assert issue["title"] == "Generated Title"


def test_main_accepts_date_instead_of_week(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    (tmp_path / "data" / "scored" / "2026-W35.jsonl").write_text("")

    with patch("build.write_headline", return_value="Quiet week."), patch(
        "build.write_title", return_value="Quiet Week"
    ):
        monkeypatch.setattr("sys.argv", ["build.py", "--date", "2026-08-24"])
        build.main()

    assert (tmp_path / "data" / "2026-W35.json").exists()


def test_main_section_summaries_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    scored_item = {
        "source_id": "s",
        "kind": "article",
        "title": "A Story",
        "url": "https://example.com/a",
        "published_at": "2026-08-18T00:00:00Z",
        "summary": "",
        "authors": [],
        "meta": {},
        "section": "releases",
        "score": 0.9,
        "why": "why line",
        "mirrors": [],
    }
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text(json.dumps(scored_item) + "\n")

    monkeypatch.setattr(
        "sys.argv",
        [
            "build.py",
            "--week",
            "2026-W34",
            "--headline",
            "h",
            "--title",
            "t",
            "--section-summaries",
            '{"releases": "A quiet week for new releases."}',
        ],
    )
    build.main()

    issue = json.loads((tmp_path / "data" / "2026-W34.json").read_text())
    releases_section = next(s for s in issue["sections"] if s["id"] == "releases")
    assert releases_section["summary"] == "A quiet week for new releases."
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_build.py tests/test_build_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build'`

- [ ] **Step 3: Write `pipeline/build.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_build.py tests/test_build_main.py -v`
Expected: 16 passed

- [ ] **Step 5: Run the entire suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all tests pass (64 tests)

- [ ] **Step 6: Commit**

```bash
git add pipeline/build.py tests/test_build.py tests/test_build_main.py
git commit -m "$(cat <<'EOF'
Add build.py issue assembly + CLI

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Verify real source URLs resolve

**Files:** none (verification only, may touch `config/sources.yaml` if a URL needs correcting)

**Interfaces:**
- Consumes: `fetch.py`'s `main()` and `--only` flag (Task 4), `config/sources.yaml` (Task 3).
- Produces: nothing new — this task's only output is confidence that Task 3's source URLs are real, or corrected URLs committed to `config/sources.yaml`.

This mirrors tentac's own PLAN.md guidance ("run against a real week, look at the output by hand before writing another fetcher") — several URLs in `config/sources.yaml` were written from general knowledge of each outlet's feed conventions, not confirmed live, and must be checked before the Monday cron (a later plan) is trusted.

- [ ] **Step 1: Run fetch against one source at a time**

From `D:\Projets\fresh`, with network access:
```bash
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only ign
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only kotaku
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only polygon
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only rockpapershotgun
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only pcgamer
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only eurogamer
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only gamespot
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only vg247
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only arstechnica-gaming
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only gamedeveloper
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only dotesports
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only dexerto
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only reddit-games
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24 --only reddit-gamedev
```
Expected per command: `[source-id] N items` with `N` a plausible weekly count, not `FAILED`.

- [ ] **Step 2: Fix any source that failed**

For each `FAILED` line, look up that outlet's actual feed URL by hand (view-source the site's `<link rel="alternate" type="application/rss+xml">` tag, or check the outlet's own "RSS"/"feeds" page) and update its `url:` in `config/sources.yaml`. Re-run that one source's `--only` command to confirm the fix.

- [ ] **Step 3: Inspect one real week's raw output by hand**

```bash
.venv/Scripts/python pipeline/fetch.py --date 2026-08-24
```
Open `data/raw/2026-W35.jsonl` and skim titles/URLs/timestamps for a few sources — confirm dates fall in-window and titles look like real games coverage, not feed boilerplate.

- [ ] **Step 4: Commit any URL fixes**

```bash
git add config/sources.yaml
git commit -m "$(cat <<'EOF'
Fix source feed URLs found broken during manual verification

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

(Skip the commit if nothing needed fixing.)

---

## What's next (not in this plan)

- **Site plan:** a minimal-newsletter Astro site reading `data/*.json`, per the spec's visual-design section — a separate plan, since it's an independent subsystem (frontend, not Python).
- **Automation + repo plan:** `.github/workflows/weekly.yml` and `deploy.yml`, the local `.claude/skills/weekly-analysis` classification skill (adapted from tentac's, retargeted to the 7 game sections), README, and pushing this repo to `github.com/stiflex` (created empty by the user first) — another separate plan.
