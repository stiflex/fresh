import json

import pytest

import score
from models import ScoredItem


def _write_raw_item(tmp_path, week):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(
        "outlets:\n  - id: s\n    kind: rss\n    tier: 1\n    sections: [releases]\n"
    )
    raw_item = {
        "source_id": "s",
        "kind": "article",
        "title": "A Story",
        "url": "https://example.com/a",
        "published_at": "2026-08-18T00:00:00Z",
        "summary": "Summary",
        "authors": [],
        "meta": {},
    }
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / f"{week}.jsonl").write_text(json.dumps(raw_item) + "\n")


def test_main_prefilter_stage_writes_prefiltered_raw_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_raw_item(tmp_path, "2026-W34")

    monkeypatch.setattr("sys.argv", ["score.py", "--week", "2026-W34", "--stage", "prefilter"])
    score.main()

    out_path = tmp_path / "data" / "prefiltered" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["title"] == "A Story"
    assert "section" not in row  # RawItem, not yet a ScoredItem


def test_main_rank_stage_reads_and_overwrites_scored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "scored").mkdir(parents=True)
    scored_item = ScoredItem(
        source_id="s",
        kind="article",
        title="A Story",
        url="https://example.com/a",
        published_at="2026-08-18T00:00:00Z",
        section="releases",
        score=0.9,
        why="why",
    )
    (tmp_path / "data" / "scored" / "2026-W34.jsonl").write_text(
        scored_item.model_dump_json() + "\n"
    )

    monkeypatch.setattr("sys.argv", ["score.py", "--week", "2026-W34", "--stage", "rank"])
    score.main()

    out_path = tmp_path / "data" / "scored" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["section"] == "releases"


def test_main_rejects_unknown_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["score.py", "--week", "2026-W34", "--stage", "bogus"]
    )
    with pytest.raises(SystemExit):
        score.main()


def test_main_accepts_date_instead_of_week(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_raw_item(tmp_path, "2026-W35")

    monkeypatch.setattr(
        "sys.argv", ["score.py", "--date", "2026-08-24", "--stage", "prefilter"]
    )
    score.main()

    assert (tmp_path / "data" / "prefiltered" / "2026-W35.jsonl").exists()


def test_main_rejects_week_and_date_together(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["score.py", "--week", "2026-W34", "--date", "2026-08-24", "--stage", "prefilter"],
    )
    with pytest.raises(SystemExit):
        score.main()
