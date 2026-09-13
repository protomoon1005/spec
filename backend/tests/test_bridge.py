# app/views/bridge.py 회귀. 픽스처 없이, pandas 없이 돈다.
#
# pandas 를 쓰지 않는 것이 이 파일의 전제다. bridge 는 러너(app/backtest/)가
# 부르는 모듈이지만 러너를 모르고, 러너의 의존성(numpy·pandas·vectorbt)은
# backend/pyproject.toml 에 선언조차 되어 있지 않다. 여기서 pandas 를 끌어들이면
# 이 테스트가 CI 에서 통째로 죽는다 — 그래서 가격 입력은 전부 순수 dict 다.
from __future__ import annotations

import ast
import math
import pathlib
from datetime import date, timedelta

import pytest

from app.contracts.view_score import ViewScore
from app.contracts.view_weights import VIEW_TYPES, WEIGHT_FLOOR
from app.views.bridge import (
    CLOSE_ONLY_UNAVAILABLE,
    MOCK_SOURCE,
    SCORERS,
    BacktestJudge,
)
from app.views.market.features import FEATURE_WARMUP_ROWS

START = date(2024, 1, 1)
TICKERS = ["069500", "133690"]


def _closes(count: int, *, seed: float, step: float) -> list[float]:
    # 결정론적 톱니 시계열. 랜덤을 쓰지 않는다.
    out = []
    value = seed
    for index in range(count):
        value += step * (1 if index % 3 else -1.5)
        out.append(round(value, 4))
    return out


def _prices(series: dict[str, list[float]]) -> dict[str, list[tuple[date, float]]]:
    return {
        ticker: [(START + timedelta(days=index), close) for index, close in enumerate(closes)]
        for ticker, closes in series.items()
    }


def _as_of(closes_count: int) -> date:
    return START + timedelta(days=closes_count - 1)


def _long_prices(count: int = 400) -> tuple[dict, date]:
    series = {
        TICKERS[0]: _closes(count, seed=100.0, step=0.7),
        TICKERS[1]: _closes(count, seed=250.0, step=1.3),
    }
    return _prices(series), _as_of(count)


def _feature_scorer(view_type: str, *, sign: float = 1.0):
    # 목업과 달리 피처를 실제로 보는 스코어러. 워밍업 절단이 정말 먹는지
    # 확인하려면 결과가 피처에 의존해야 한다.
    # sign 은 관점마다 다른 판단을 내게 하는 손잡이다 — 세 관점이 같은 확률을
    # 내면 Brier 손실도 같아져서 가중치가 영원히 1/3 에 머문다.
    def scorer(tickers, *, as_of, features):
        out = []
        for ticker in tickers:
            rsi = features[ticker]["rsi_14"]
            raw = max(-1.0, min(1.0, sign * (rsi - 50.0) / 50.0))
            out.append(
                ViewScore(
                    view_type=view_type,
                    ticker=ticker,
                    raw_score=round(raw, 6),
                    calibrated_prob=round((raw + 1) / 2, 6),
                    evidence={"source": "test-feature"},
                )
            )
        return out

    return scorer


# 관점마다 다르게 판단하게 만든다: market 은 추세추종, sentiment 는 그 반대,
# regime 은 늘 "모른다"(0.5). 손실이 갈려야 Hedge 가 움직인다.
_SIGNS = {"market": 1.0, "sentiment": -1.0, "regime": 0.0}


def _feature_scorers() -> dict:
    return {view_type: _feature_scorer(view_type, sign=_SIGNS[view_type]) for view_type in VIEW_TYPES}


# ── 단방향 의존 ──────────────────────────────────────────────────────


def test_bridge_imports_neither_pandas_nor_the_runner():
    # 러너가 bridge 를 알고, bridge 는 러너를 모른다. 이게 깨지면 이 테스트가
    # CI 에서 못 도는 상태로 조용히 바뀐다.
    source = pathlib.Path(__file__).resolve().parents[1] / "app" / "views" / "bridge.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = [name for name in imported if name.split(".")[0] in {"pandas", "numpy"}]
    assert forbidden == [], f"bridge 가 금지된 의존성을 import 한다: {forbidden}"
    assert [name for name in imported if name.startswith("app.backtest")] == []


# ── 재현성 ──────────────────────────────────────────────────────────


def test_same_input_twice_gives_the_same_signal():
    prices, as_of = _long_prices()

    first = BacktestJudge(TICKERS).judge(prices, as_of=as_of)
    second = BacktestJudge(TICKERS).judge(prices, as_of=as_of)

    assert first == second


def test_a_whole_run_replays_identically():
    # 같은 순서로 판단·정산을 반복하면 손실 이력까지 같은 값으로 재현된다.
    prices, _ = _long_prices()
    days = [_as_of(360), _as_of(375), _as_of(390), _as_of(400)]

    def replay() -> tuple[list, dict]:
        judge = BacktestJudge(TICKERS, scorers=_feature_scorers())
        signals = []
        for day in days:
            judge.record_outcome(prices, as_of=day)
            signals.append(judge.judge(prices, as_of=day))
        return signals, judge.loss_history()

    assert replay() == replay()


# ── 워밍업 120 ──────────────────────────────────────────────────────


def test_history_shorter_than_warmup_is_neutral_in_every_view():
    count = FEATURE_WARMUP_ROWS - 20
    prices = _prices({ticker: _closes(count, seed=100.0, step=0.5) for ticker in TICKERS})

    judge = BacktestJudge(TICKERS)
    signal = judge.judge(prices, as_of=_as_of(count))

    assert len(judge.last_scores) == len(VIEW_TYPES) * len(TICKERS)
    for score in judge.last_scores:
        assert score.evidence["skipped"] is True
        assert score.evidence["reason"] == "no_data"
        assert score.raw_score == 0.0
    # 중립만 모이면 가중합이 0 이라 데드존에 전부 걸린다.
    assert signal.signals == {ticker: 0.0 for ticker in TICKERS}
    assert sorted(signal.deadzone_applied) == sorted(TICKERS)


def test_only_the_last_warmup_rows_reach_the_scorers():
    count = 400
    cut = count - FEATURE_WARMUP_ROWS  # 이 앞쪽은 결과에 영향을 주면 안 된다
    assert cut > 0

    base = {
        TICKERS[0]: _closes(count, seed=100.0, step=0.7),
        TICKERS[1]: _closes(count, seed=250.0, step=1.3),
    }
    tampered = {ticker: list(closes) for ticker, closes in base.items()}
    for ticker in TICKERS:
        for index in range(cut):
            tampered[ticker][index] = round(tampered[ticker][index] * 3.0 + 17.0, 4)

    as_of = _as_of(count)
    original, altered = _prices(base), _prices(tampered)

    # 러너는 전체 이력을 넘긴다. 자르는 책임은 bridge 에 있다.
    assert BacktestJudge(TICKERS).features(original, as_of=as_of) == BacktestJudge(TICKERS).features(
        altered, as_of=as_of
    )

    scorers = _feature_scorers()
    assert BacktestJudge(TICKERS, scorers=scorers).judge(original, as_of=as_of) == BacktestJudge(
        TICKERS, scorers=scorers
    ).judge(altered, as_of=as_of)


def test_rows_after_as_of_are_cut_before_the_warmup_slice():
    count = 400
    prices, _ = _long_prices(count)
    early = _as_of(300)

    trimmed = {
        ticker: [(day, close) for day, close in rows if day <= early] for ticker, rows in prices.items()
    }

    assert BacktestJudge(TICKERS).features(prices, as_of=early) == BacktestJudge(TICKERS).features(
        trimmed, as_of=early
    )


def test_close_only_input_leaves_high_low_volume_features_missing():
    # 종가만으로는 ATR 도 거래량비율도 정의되지 않는다. H=L=C 로 채워 만든
    # 그럴듯한 숫자를 내보내지 않는다는 것을 고정한다.
    prices, as_of = _long_prices()
    values = BacktestJudge(TICKERS).features(prices, as_of=as_of)[TICKERS[0]]

    for name in CLOSE_ONLY_UNAVAILABLE:
        assert values[name] is None, f"{name} 이 종가만으로 채워졌다"
    assert values["rsi_14"] is not None
    assert values["ma_gap_20"] is not None


# ── Hedge 가중치 ────────────────────────────────────────────────────


def test_empty_loss_history_is_exactly_uniform():
    judge = BacktestJudge(TICKERS)
    weights = judge.current_weights()

    assert sorted(weights) == sorted(VIEW_TYPES)
    for value in weights.values():
        assert value == pytest.approx(1 / len(VIEW_TYPES))
    assert math.fsum(weights.values()) == pytest.approx(1.0)


def test_recorded_outcomes_move_the_weights_but_never_below_the_floor():
    prices, _ = _long_prices()
    judge = BacktestJudge(TICKERS, scorers=_feature_scorers())

    for offset in range(200, 400, 10):
        day = _as_of(offset)
        judge.record_outcome(prices, as_of=day)
        judge.judge(prices, as_of=day)

    history = judge.loss_history()
    assert {len(values) for values in history.values()} == {len(range(200, 400, 10)) - 1}

    weights = judge.current_weights()
    assert math.fsum(weights.values()) == pytest.approx(1.0)
    assert min(weights.values()) >= WEIGHT_FLOOR - 1e-9
    assert any(abs(value - 1 / len(VIEW_TYPES)) > 1e-6 for value in weights.values())


def test_record_outcome_before_any_judgement_is_a_no_op():
    prices, as_of = _long_prices()
    judge = BacktestJudge(TICKERS)

    judge.record_outcome(prices, as_of=as_of)

    assert judge.loss_history() == {view_type: [] for view_type in VIEW_TYPES}


def test_the_same_as_of_is_not_graded_twice():
    prices, _ = _long_prices()
    judge = BacktestJudge(TICKERS, scorers=_feature_scorers())
    day = _as_of(300)

    judge.judge(prices, as_of=day)
    judge.record_outcome(prices, as_of=day)  # 아직 실현되지 않았다

    assert judge.loss_history() == {view_type: [] for view_type in VIEW_TYPES}


# ── 스코어러 교체 지점 ───────────────────────────────────────────────


def test_scorers_default_to_the_contract_mock():
    prices, as_of = _long_prices()
    judge = BacktestJudge(TICKERS)

    assert judge.scorer_sources() == {view_type: MOCK_SOURCE for view_type in VIEW_TYPES}
    assert judge.last_scores == []  # judge 전이다

    judge.judge(prices, as_of=as_of)

    # 목업을 쓰고 있다는 사실이 evidence 에 드러나야 한다 — 리포트를 보는
    # 사람이 이 숫자를 실력으로 읽으면 안 된다.
    assert {score.evidence["source"] for score in judge.last_scores} == {MOCK_SOURCE}


def test_swapping_one_registry_entry_changes_only_that_view():
    prices, as_of = _long_prices()
    swapped = dict(SCORERS)
    swapped["regime"] = _feature_scorer("regime")

    before = BacktestJudge(TICKERS).judge(prices, as_of=as_of)
    after = BacktestJudge(TICKERS, scorers=swapped).judge(prices, as_of=as_of)

    assert before.per_view_scores["market"] == after.per_view_scores["market"]
    assert before.per_view_scores["sentiment"] == after.per_view_scores["sentiment"]
    assert before.per_view_scores["regime"] != after.per_view_scores["regime"]
    assert BacktestJudge(TICKERS, scorers=swapped).scorer_sources()["regime"] != MOCK_SOURCE


def test_a_missing_view_scorer_is_rejected():
    partial = {view_type: SCORERS[view_type] for view_type in ("market", "regime")}

    with pytest.raises(ValueError, match="스코어러가 없는 관점"):
        BacktestJudge(TICKERS, scorers=partial)


def test_an_empty_universe_is_rejected():
    with pytest.raises(ValueError, match="종목이 하나도 없다"):
        BacktestJudge([])
