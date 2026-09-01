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
