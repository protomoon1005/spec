"""관점 스코어러 계약(T1) 테스트.

픽스처를 하나도 요청하지 않는다 — postgres 도 ML 의존성도 없이 돈다.
중립의 정의와 evidence 키 목록이 세 관점에 흩어지지 않게 하는 것이 목적이다.
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

from app.contracts.view_score import ViewScore
from app.views.base import (
    NEUTRAL_CALIBRATED_PROB,
    NEUTRAL_RAW_SCORE,
    ViewScorer,
    neutral_score,
    neutral_scores,
)
from app.views.evidence import (
    REASON_NO_DATA,
    REASON_RULES_ABSENT,
    MarketEvidence,
    RegimeEvidence,
    SentimentEvidence,
    SkippedEvidence,
    market_evidence,
    regime_evidence,
    sentiment_evidence,
    skipped_evidence,
)

AS_OF = date(2026, 9, 11)
TICKERS = ["069500", "232080", "133690"]


class _DummyScorer:
    """계약을 만족하는 최소 구현. 실제 스코어러는 T3·T4·T7에서 붙는다."""

    view_type = "market"

    def score(
        self, tickers: list[str], *, as_of: date, rules: BaseModel | None
    ) -> list[ViewScore]:
        if rules is None:
            return neutral_scores(self.view_type, tickers, reason=REASON_RULES_ABSENT)
        return neutral_scores(self.view_type, tickers, reason=REASON_NO_DATA)


class _MissingScoreMethod:
    view_type = "market"


def test_neutral_score_is_exactly_zero_and_half() -> None:
    score = neutral_score("sentiment", "069500", reason=REASON_RULES_ABSENT)

    assert score.raw_score == 0.0
    assert score.calibrated_prob == 0.5
    assert score.raw_score == NEUTRAL_RAW_SCORE
    assert score.calibrated_prob == NEUTRAL_CALIBRATED_PROB
    assert score.evidence == {"skipped": True, "reason": REASON_RULES_ABSENT}


def test_neutral_scores_cover_the_whole_universe() -> None:
    # 통합기는 가중치가 있는 관점이 종목 하나라도 빠뜨리면 예외를 낸다.
    scores = neutral_scores("regime", TICKERS, reason=REASON_NO_DATA)

    assert [s.ticker for s in scores] == TICKERS
    assert all(s.view_type == "regime" for s in scores)
    assert all(s.evidence["skipped"] is True for s in scores)
    assert all(s.evidence["reason"] == REASON_NO_DATA for s in scores)


def test_two_neutral_reasons_are_distinguishable() -> None:
    rules_absent = neutral_score("market", "069500", reason=REASON_RULES_ABSENT)
    no_data = neutral_score("market", "069500", reason=REASON_NO_DATA)

    assert rules_absent.evidence["reason"] != no_data.evidence["reason"]
    # 점수 자체는 같다 — 구분은 evidence 에만 남는다.
    assert rules_absent.raw_score == no_data.raw_score
    assert rules_absent.calibrated_prob == no_data.calibrated_prob


def test_empty_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="reason"):
        neutral_score("market", "069500", reason="")


def test_dummy_scorer_satisfies_the_protocol() -> None:
    assert isinstance(_DummyScorer(), ViewScorer)
    assert not isinstance(_MissingScoreMethod(), ViewScorer)


def test_scorer_returns_neutral_when_rules_are_none() -> None:
    # 수용 기준: signal_rules 의 해당 블록이 None 이면 예외 없이 중립을 낸다.
    scores = _DummyScorer().score(TICKERS, as_of=AS_OF, rules=None)

    assert len(scores) == len(TICKERS)
    assert all(s.calibrated_prob == 0.5 for s in scores)
    assert all(s.evidence["reason"] == REASON_RULES_ABSENT for s in scores)


def test_market_evidence_has_every_agreed_key() -> None:
    built = market_evidence(
        shap=[("rsi_14", 0.31), ("ma_gap_20", -0.12), ("vol_20", 0.08)],
        prob_raw=0.71,
        prob_calibrated=0.66,
        model_version="market-lgbm-v0.1-20231231",
    )

    assert set(built) == set(MarketEvidence.__required_keys__)
    assert built["shap"][0] == {"feature": "rsi_14", "value": 0.31}
    assert built["prob_raw"] == 0.71
    assert built["prob_calibrated"] == 0.66


def test_sentiment_evidence_has_every_agreed_key() -> None:
    built = sentiment_evidence(
        article_ids=[11, 12, 13],
        lens_id="LENS_SENTIMENT_V1",
        sector="SECTOR_SEMICONDUCTOR",
        model_version="kf-deberta-base",
    )

    assert set(built) == set(SentimentEvidence.__required_keys__)
    assert built["n_articles"] == 3  # 기본값은 article_ids 길이


def test_sentiment_evidence_can_report_more_articles_than_it_lists() -> None:
    # 근거로 보여줄 목록만 상위 N개로 자르는 경우를 위해 따로 받을 수 있다.
    built = sentiment_evidence(
        article_ids=[11, 12],
        lens_id="LENS_SENTIMENT_V1",
        sector="SECTOR_FINANCE",
        model_version="kf-deberta-base",
        n_articles=57,
    )
    assert built["n_articles"] == 57
    assert len(built["article_ids"]) == 2


def test_regime_evidence_has_every_agreed_key() -> None:
    built = regime_evidence(
        trend_index="KOSPI200",
        regime_label="risk_off",
        intensity=-0.42,
        threshold_state={"ma_gap_20": "below", "vix": "above"},
    )

    assert set(built) == set(RegimeEvidence.__required_keys__)
    assert built["threshold_state"]["vix"] == "above"


def test_skipped_evidence_has_every_agreed_key() -> None:
    built = skipped_evidence(REASON_NO_DATA)
    assert set(built) == set(SkippedEvidence.__required_keys__)


@pytest.mark.parametrize("bad_prob", [1.5, -0.01])
def test_market_evidence_rejects_bad_probability(bad_prob: float) -> None:
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        market_evidence(
            shap=[],
            prob_raw=bad_prob,
            prob_calibrated=0.5,
            model_version="market-lgbm-v0.1-20231231",
        )


def test_evidence_builders_reject_empty_identifiers() -> None:
    with pytest.raises(ValueError, match="model_version"):
        market_evidence(shap=[], prob_raw=0.5, prob_calibrated=0.5, model_version="")
    with pytest.raises(ValueError, match="lens_id"):
        sentiment_evidence(article_ids=[], lens_id="", sector="X", model_version="m")
    with pytest.raises(ValueError, match="trend_index"):
        regime_evidence(trend_index="", regime_label="risk_on", intensity=0.1, threshold_state={})


def test_regime_evidence_rejects_non_finite_intensity() -> None:
    with pytest.raises(ValueError, match="유한하지"):
        regime_evidence(
            trend_index="KOSPI200",
            regime_label="risk_on",
            intensity=float("nan"),
            threshold_state={},
        )


@pytest.mark.parametrize(
    "builder",
    [
        lambda: market_evidence(
            shap=[("rsi_14", 0.3)],
            prob_raw=0.7,
            prob_calibrated=0.65,
            model_version="market-lgbm-v0.1-20231231",
        ),
        lambda: sentiment_evidence(
            article_ids=[1],
            lens_id="LENS_SENTIMENT_V1",
            sector="SECTOR_FINANCE",
            model_version="kf-deberta-base",
        ),
        lambda: regime_evidence(
            trend_index="KOSPI200",
            regime_label="risk_on",
            intensity=0.4,
            threshold_state={"vix": "below"},
        ),
        lambda: skipped_evidence(REASON_NO_DATA),
    ],
)
def test_builder_output_passes_view_score_validation(builder) -> None:
    # ViewScore 는 extra="forbid" 다. 빌더 결과를 evidence 에 넣는 경로가 실제로
    # 통과하는지 확인한다 — evidence 는 dict 필드라 통과해야 정상이다.
    score = ViewScore(
        view_type="market",
        ticker="069500",
        raw_score=0.3,
        calibrated_prob=0.65,
        evidence=dict(builder()),
    )
    assert score.evidence == dict(builder())
