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
