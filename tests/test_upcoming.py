from datetime import date

from models import ScoredItem
from upcoming import merge_upcoming, parse_release_hint


def _item(game_name, release_date, platforms=None, url="https://example.com/a", score=0.5):
    return ScoredItem(
        source_id="s",
        kind="article",
        title=f"{game_name} news",
        url=url,
        published_at="2026-08-18T00:00:00Z",
        section="new-releases",
        score=score,
        why="why",
        game_name=game_name,
        release_date=release_date,
        platforms=platforms or [],
    )


def test_parse_release_hint_iso_date():
    assert parse_release_hint("2026-09-15") == date(2026, 9, 15)


def test_parse_release_hint_bare_year_means_end_of_year():
    assert parse_release_hint("2027") == date(2027, 12, 31)


def test_parse_release_hint_month_year_means_end_of_month():
    assert parse_release_hint("Jan 2027") == date(2027, 1, 31)


def test_parse_release_hint_rejects_unrecognized_text():
    assert parse_release_hint("sometime next year") is None


def test_parse_release_hint_rejects_invalid_iso_date():
    assert parse_release_hint("2026-13-40") is None


def test_merge_upcoming_adds_new_game():
    items = [_item("Witcher 4", "2028")]
    result = merge_upcoming([], items, "2026-W35", today=date(2026, 8, 24))
    assert len(result) == 1
    assert result[0]["game_name"] == "Witcher 4"
    assert result[0]["release_date"] == "2028"
    assert result[0]["first_seen_week"] == "2026-W35"
    assert result[0]["url"] == "https://example.com/a"


def test_merge_upcoming_does_not_duplicate_already_tracked_game():
    existing = [{
        "game_name": "Witcher 4", "release_date": "2028", "platforms": [],
        "url": "https://example.com/old", "first_seen_week": "2026-W30",
    }]
    items = [_item("Witcher 4", "2028", url="https://example.com/new")]
    result = merge_upcoming(existing, items, "2026-W35", today=date(2026, 8, 24))
    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/old", "first source wins, not overwritten"
    assert result[0]["first_seen_week"] == "2026-W30"


def test_merge_upcoming_matches_case_and_punctuation_insensitively():
    existing = [{
        "game_name": "Witcher 4", "release_date": "2028", "platforms": [],
        "url": "https://example.com/old", "first_seen_week": "2026-W30",
    }]
    items = [_item("witcher  4!", "2028")]
    result = merge_upcoming(existing, items, "2026-W35", today=date(2026, 8, 24))
    assert len(result) == 1


def test_merge_upcoming_skips_item_without_game_name():
    items = [_item(None, "2028")]
    result = merge_upcoming([], items, "2026-W35", today=date(2026, 8, 24))
    assert result == []


def test_merge_upcoming_skips_item_without_release_date():
    items = [_item("Witcher 4", None)]
    result = merge_upcoming([], items, "2026-W35", today=date(2026, 8, 24))
    assert result == []


def test_merge_upcoming_drops_expired_entry():
    existing = [{
        "game_name": "Old Game", "release_date": "2020", "platforms": [],
        "url": "https://example.com/old", "first_seen_week": "2020-W01",
    }]
    result = merge_upcoming(existing, [], "2026-W35", today=date(2026, 8, 24))
    assert result == []


def test_merge_upcoming_keeps_entry_with_unparseable_date():
    existing = [{
        "game_name": "Mystery Game", "release_date": "TBA", "platforms": [],
        "url": "https://example.com/old", "first_seen_week": "2020-W01",
    }]
    result = merge_upcoming(existing, [], "2026-W35", today=date(2026, 8, 24))
    assert len(result) == 1


def test_merge_upcoming_keeps_platforms_and_sorts_by_name():
    items = [_item("Zelda Sequel", "2027", platforms=["Switch 2"]), _item("Avowed 2", "2027")]
    result = merge_upcoming([], items, "2026-W35", today=date(2026, 8, 24))
    assert [e["game_name"] for e in result] == ["Avowed 2", "Zelda Sequel"]
    assert result[1]["platforms"] == ["Switch 2"]
