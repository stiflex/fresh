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
