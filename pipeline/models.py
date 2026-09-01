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
