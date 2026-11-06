import logging

from finance_context.graph.stage import _reset_rss_mark, _rss_mark


def test_rss_mark_is_quiet_without_the_env(caplog, monkeypatch) -> None:
    monkeypatch.delenv("GRAPH_RSS_LOG", raising=False)
    _reset_rss_mark()
    with caplog.at_level(logging.INFO, logger="finance_context.graph.stage"):
        _rss_mark("edges_begin", groups=2)
    assert caplog.records == []


def test_rss_mark_ignores_a_path(caplog, monkeypatch, tmp_path) -> None:
    target = tmp_path / "rss.jsonl"
    monkeypatch.setenv("GRAPH_RSS_LOG", str(target))
    _reset_rss_mark()
    with caplog.at_level(logging.INFO, logger="finance_context.graph.stage"):
        _rss_mark("edges_begin", groups=2)
    assert caplog.records == []
    assert not target.exists()


def test_rss_mark_logs_counts_only(caplog, monkeypatch) -> None:
    monkeypatch.setenv("GRAPH_RSS_LOG", "1")
    _reset_rss_mark()
    with caplog.at_level(logging.INFO, logger="finance_context.graph.stage"):
        _rss_mark("edges_begin", groups=2, rows=10, formula="=SECRET", path="/data/secret")
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.event == "graph_rss"
    assert record.mark == "edges_begin"
    assert record.groups == 2
    assert record.rows == 10
    assert isinstance(record.rss_mb, int)
    assert not hasattr(record, "formula")
    assert not hasattr(record, "path")
    assert "SECRET" not in caplog.text
    assert "/data/secret" not in caplog.text
