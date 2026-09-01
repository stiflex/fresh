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
