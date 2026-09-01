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
