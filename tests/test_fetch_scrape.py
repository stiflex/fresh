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
