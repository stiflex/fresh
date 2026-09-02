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


def test_build_issue_applies_section_subtitles(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    issue = build_issue(
        "2026-W34",
        items,
        headline="h",
        title="t",
        section_subtitles={"releases": "A quiet week"},
    )

    assert issue.sections[0].subtitle == "A quiet week"


def test_build_issue_defaults_subtitle_to_empty_string(monkeypatch):
    monkeypatch.setattr(
        "build._section_meta",
        lambda: {"releases": {"label": "Releases & updates", "blurb": "New launches, major patches, DLC"}},
    )
    items = [_scored("releases", 0.9, "Alpha")]
    issue = build_issue("2026-W34", items, headline="h", title="t")

    assert issue.sections[0].subtitle == ""


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
