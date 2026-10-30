"""빅카인즈 채점 · 적재 스크립트의 순수 부분(torch · DB 없이)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.views.sentiment import lens


def _load(name: str):
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py",
        Path(f"/repo/scripts/{name}.py"),
    ]
    path = next((p for p in candidates if p.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score_news = _load("score_news")
ingest_news = _load("ingest_news")
KST = timezone(timedelta(hours=9))


def test_published_at_is_identifier_time_in_kst():
    expected = datetime(2023, 1, 15, 0, 25, 12, tzinfo=KST)
    assert score_news.published_at("02100601.20230115002512002") == expected


def _row(article_id, title, cats, excluded):
    return {"id": article_id, "title": title, "cat1": cats[0], "cat2": cats[1], "cat3": cats[2],
            "excluded": excluded}


def test_selection_drops_excluded_and_out_of_scope():
    rows = [
        _row("a", "증시", ("경제>증권_증시", "경제>자원", ""), ""),
        _row("b", "증시", ("경제>증권_증시", "", ""), "중복"),
        _row("c", "금리", ("경제>유통", "", ""), ""),
        _row("d", "금통위 동결", ("경제>유통", "", "경제>외환"), " "),
        _row("e", "환율 급등", ("경제>외환", "", ""), ""),
    ]
    picked = [(row["id"], groups) for row, groups in score_news.selected(rows)]
    assert picked == [("a", ("EQUITY", "COMMODITY")), ("d", ("BOND",))]


def _record(article_id: str, url: str, groups: str = "EQUITY", probs=("0.1", "0.2", "0.7")):
    return {"id": article_id, "published_at": "2023-01-15T00:25:12+09:00", "outlet": "한국경제",
            "title": "t", "url": url, "groups": groups, "p_neg": probs[0], "p_neu": probs[1],
            "p_pos": probs[2], "model_version": lens.MODEL_VERSION}


def test_duplicate_urls_keep_only_the_first_identifier():
    rows = ingest_news.article_rows([_record("a", "u1"), _record("b", "u1"), _record("c", "")])
    assert [r["url"] for r in rows] == ["u1", None, None]
    assert rows[0]["published_at"].tzinfo is not None
    assert rows[0]["dedup_hash"] == "a"


def test_sentiment_rows_one_per_group():
    rows = ingest_news.sentiment_rows(_record("a", "u", groups="EQUITY;BOND"), 42)
    assert rows == [(42, "EQUITY", pytest.approx(0.6), 0.7), (42, "BOND", pytest.approx(0.6), 0.7)]
    with pytest.raises(ValueError):
        ingest_news.sentiment_rows(_record("a", "u", groups="SECTOR_X"), 1)
