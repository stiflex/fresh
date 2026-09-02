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


@patch("score.Anthropic")
def test_classify_and_score_passes_through_game_name(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://example.com/a",
                "section": "new-releases",
                "score": 0.8,
                "why": "why",
                "game_name": "Witcher 4",
            }
        ]
    )

    result = classify_and_score([_item("https://example.com/a")])

    assert result[0].game_name == "Witcher 4"


@patch("score.Anthropic")
def test_classify_and_score_defaults_missing_game_name_to_none(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [{"url": "https://example.com/a", "section": "industry", "score": 0.5, "why": "why"}]
    )

    result = classify_and_score([_item("https://example.com/a")])

    assert result[0].game_name is None


@patch("score.Anthropic")
def test_classify_and_score_folds_same_batch_duplicate_into_mirrors(mock_anthropic_cls):
    """Two different outlets covering the same story with unrelated wording
    aren't caught by dedupe()'s title-similarity check — classify_and_score
    is the layer with enough judgment to recognize "same story" and fold
    the duplicate into the canonical item's mirrors instead of keeping it
    as a separate digest entry.
    """
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://outlet-a.com/story",
                "section": "hardware",
                "score": 0.5,
                "why": "Xbox announces a disc-to-digital program.",
            },
            {
                "url": "https://outlet-b.com/story",
                "section": "hardware",
                "score": 0.4,
                "why": "different wording, same news",
                "duplicate_of": "https://outlet-a.com/story",
            },
        ]
    )

    items = [_item("https://outlet-a.com/story"), _item("https://outlet-b.com/story")]
    result = classify_and_score(items)

    assert len(result) == 1
    assert str(result[0].url) == "https://outlet-a.com/story"
    assert [str(m) for m in result[0].mirrors] == ["https://outlet-b.com/story"]


@patch("score.Anthropic")
def test_classify_and_score_ignores_duplicate_of_pointing_outside_batch(mock_anthropic_cls):
    """A duplicate_of value that isn't a URL present in this same batch
    (e.g. the model hallucinated it, or it refers to a different batch)
    must not silently drop the item — treat it as a normal primary item.
    """
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://example.com/a",
                "section": "hardware",
                "score": 0.5,
                "why": "why",
                "duplicate_of": "https://example.com/not-in-this-batch",
            }
        ]
    )

    result = classify_and_score([_item("https://example.com/a")])

    assert len(result) == 1
    assert str(result[0].url) == "https://example.com/a"
    assert result[0].mirrors == []


@patch("score.Anthropic")
def test_classify_and_score_merges_duplicates_mirror_urls_with_dedupe_mirrors(mock_anthropic_cls):
    """A canonical item that already carries mirror_urls from score.py's
    dedupe() (near-identical-title collapsing) keeps those AND gains any
    classify-time duplicate — both signals of "same story" combine.
    """
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _fake_response(
        [
            {
                "url": "https://outlet-a.com/story",
                "section": "hardware",
                "score": 0.5,
                "why": "why",
            },
            {
                "url": "https://outlet-b.com/story",
                "section": "hardware",
                "score": 0.4,
                "why": "why",
                "duplicate_of": "https://outlet-a.com/story",
            },
        ]
    )

    items = [
        _item("https://outlet-a.com/story", mirror_urls=["https://mirror.com/from-dedupe"]),
        _item("https://outlet-b.com/story"),
    ]
    result = classify_and_score(items)

    assert len(result) == 1
    mirrors = {str(m) for m in result[0].mirrors}
    assert mirrors == {"https://mirror.com/from-dedupe", "https://outlet-b.com/story"}
