import json

import pytest

import fetch
from models import RawItem


def _item(source_id: str, url: str) -> RawItem:
    return RawItem(
        source_id=source_id,
        kind="article",
        title=f"Item from {source_id}",
        url=url,
        published_at="2026-08-18T00:00:00Z",
    )


def _sources_yaml(*ids_and_tiers: tuple[str, int]) -> str:
    """Build a minimal single-group sources.yaml, one rss source per (id, tier) pair."""
    if not ids_and_tiers:
        return "outlets: []\n"
    lines = ["outlets:"]
    for source_id, tier in ids_and_tiers:
        lines += [
            f"  - id: {source_id}",
            "    kind: rss",
            f"    url: https://example.com/{source_id}.xml",
            f"    tier: {tier}",
            "    sections: [releases]",
        ]
    return "\n".join(lines) + "\n"


def _write_sources_yaml(tmp_path, *ids_and_tiers: tuple[str, int]) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(_sources_yaml(*ids_and_tiers))


def test_main_writes_jsonl_and_survives_one_dead_source(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1), ("dead-source", 2))

    def fake_fetch_rss(source, since, until):
        if source["id"] == "dead-source":
            raise ConnectionError("boom")
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    out_path = tmp_path / "data" / "raw" / "2026-W34.jsonl"
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["source_id"] == "good-source"

    captured = capsys.readouterr()
    assert "dead-source" in captured.out
    assert "boom" in captured.out
    assert "1 source(s) failed this run" in captured.out
    assert "- dead-source: boom" in captured.out


def test_main_prints_all_succeeded_when_no_failures(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    captured = capsys.readouterr()
    assert "all sources fetched successfully" in captured.out


def test_main_accepts_date_instead_of_week(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--date", "2026-08-24"])

    fetch.main()

    out_path = tmp_path / "data" / "raw" / "2026-W35.jsonl"
    assert out_path.exists()


def test_main_rejects_week_and_date_together(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path)

    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--date", "2026-08-24"]
    )

    with pytest.raises(SystemExit):
        fetch.main()


def test_main_with_only_preserves_other_sources_existing_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("source-b", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    existing = [
        _item("source-a", "https://example.com/old-a").model_dump_json(),
        _item("source-b", "https://example.com/old-b").model_dump_json(),
    ]
    (raw_dir / "2026-W34.jsonl").write_text("\n".join(existing) + "\n")

    def fake_fetch_rss(source, since, until):
        assert source["id"] == "source-b", "only source-b should be re-fetched"
        return [_item("source-b", "https://example.com/new-b")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "source-b"]
    )

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    by_source = {item["source_id"]: item["url"] for item in lines}
    assert len(lines) == 2, "source-a's old line must survive, source-b's must be replaced"
    assert by_source["source-a"] == "https://example.com/old-a", "untouched source must be preserved verbatim"
    assert by_source["source-b"] == "https://example.com/new-b", "re-fetched source must use fresh data"


def test_main_with_only_preserves_prior_data_on_repeat_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("flaky-source", 2))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    existing = [
        _item("source-a", "https://example.com/old-a").model_dump_json(),
        _item("flaky-source", "https://example.com/old-flaky").model_dump_json(),
    ]
    (raw_dir / "2026-W34.jsonl").write_text("\n".join(existing) + "\n")

    def fake_fetch_rss(source, since, until):
        if source["id"] == "flaky-source":
            raise ConnectionError("still down")
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "flaky-source"]
    )

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    by_source = {item["source_id"]: item["url"] for item in lines}
    assert len(lines) == 2, "a failed retry must not drop the source's prior data"
    assert by_source["flaky-source"] == "https://example.com/old-flaky", "prior data survives a repeat failure"
    assert by_source["source-a"] == "https://example.com/old-a", "sources outside --only are always untouched"


def test_main_without_only_still_overwrites_fully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("good-source", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "2026-W34.jsonl").write_text(
        _item("stale-source-no-longer-configured", "https://example.com/stale").model_dump_json() + "\n"
    )

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(fetch.sys, "argv", ["fetch.py", "--week", "2026-W34"])

    fetch.main()

    lines = [json.loads(line) for line in (raw_dir / "2026-W34.jsonl").read_text().splitlines()]
    assert len(lines) == 1, "a full run (no --only) must still fully overwrite, not merge"
    assert lines[0]["source_id"] == "good-source"


def test_main_warns_on_unmatched_only_source_id_but_still_fetches_real_ones(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("real-source", 1))

    def fake_fetch_rss(source, since, until):
        return [_item(source["id"], "https://example.com/a")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys,
        "argv",
        ["fetch.py", "--week", "2026-W34", "--only", "real-source,nonexistent-source"],
    )

    fetch.main()

    captured = capsys.readouterr()
    assert "nonexistent-source" in captured.out
    assert "WARNING" in captured.out

    out_path = tmp_path / "data" / "raw" / "2026-W34.jsonl"
    lines = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["source_id"] == "real-source"


def test_main_preserves_malformed_line_in_existing_raw_file_during_merge(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_sources_yaml(tmp_path, ("source-a", 1), ("source-b", 1))
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    valid_line = _item("source-a", "https://example.com/old-a").model_dump_json()
    malformed_line = "not valid json"
    (raw_dir / "2026-W34.jsonl").write_text(valid_line + "\n" + malformed_line + "\n")

    def fake_fetch_rss(source, since, until):
        assert source["id"] == "source-b", "only source-b should be re-fetched"
        return [_item("source-b", "https://example.com/new-b")]

    monkeypatch.setattr(fetch, "FETCHERS", {**fetch.FETCHERS, "rss": fake_fetch_rss})
    monkeypatch.setattr(
        fetch.sys, "argv", ["fetch.py", "--week", "2026-W34", "--only", "source-b"]
    )

    fetch.main()

    captured = capsys.readouterr()
    assert "WARNING" in captured.out

    lines = (raw_dir / "2026-W34.jsonl").read_text().splitlines()
    assert malformed_line in lines, "malformed line must be preserved verbatim, not dropped"
    assert valid_line in lines, "valid pre-existing line must still be preserved"
    parsed = [json.loads(line) for line in lines if line != malformed_line]
    by_source = {item["source_id"]: item["url"] for item in parsed}
    assert by_source["source-b"] == "https://example.com/new-b"
