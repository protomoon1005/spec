# ViewScore.evidence 의 관점별 키 스키마 — 2026-09-12 하늘(M4)과 합의된 정본.
#
# 설명을 docstring이 아니라 주석에 두는 이유는 integrator.py·hedge.py 와 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상
# 테이블명을 찾으면 CI가 실패한다. 주석은 AST에 안 잡힌다.
#
# ViewScore 는 extra="forbid" 라 필드를 새로 붙일 수 없다. 그래서 SHAP 기여도,
# 근거 기사 ID, 임계값 상태, 모델 버전 같은 부가정보가 전부 evidence dict 로
# 들어간다. 그런데 evidence 는 타입이 dict 일 뿐이라 구조가 강제되지 않는다 —
# 합의한 키 목록이 계획서에만 있으면 시간이 지나며 조용히 어긋난다.
# M4가 P06 판단근거 상세 화면에서 이 키들을 읽으므로(시장탭은 SHAP와 보정
# 전후 확률, 감성탭은 기사 목록, 온도탭은 임계값 대비 위치), 합의된 목록을
# 코드 한 곳에 묶어 두는 것이 이 모듈의 목적이다.
#
# TypedDict 는 런타임 강제를 하지 않는다(빌더를 거치지 않고 dict 를 직접 만들면
# 막을 수 없다). 대신 빌더 함수를 거치면 키 오타가 잡히고, 무엇보다 "무엇이
# 합의된 키인가"의 단일 출처가 생긴다. 스코어러는 반드시 빌더를 거쳐라.
from __future__ import annotations

import math
from typing import TypedDict

# 중립 반환 시 쓰는 사유 코드. 두 경우를 구분해야 나중에 "그 관점이 꺼져 있었다"와
# "데이터가 없었다"를 판단근거 화면에서 다르게 설명할 수 있다.
REASON_RULES_ABSENT = "rules_absent"  # Spec의 signal_rules 해당 블록이 None
REASON_NO_DATA = "no_data"  # 기사 0건·지표 결측 등


class ShapContribution(TypedDict):
    feature: str
    value: float


class MarketEvidence(TypedDict):
    shap: list[ShapContribution]
    prob_raw: float
    prob_calibrated: float
    model_version: str


class SentimentEvidence(TypedDict):
    article_ids: list[int]
    lens_id: str
    sector: str
    n_articles: int
    model_version: str


class RegimeEvidence(TypedDict):
    trend_index: str
    regime_label: str
    intensity: float
    threshold_state: dict


class SkippedEvidence(TypedDict):
    skipped: bool
    reason: str


def market_evidence(
    *,
    shap: list[tuple[str, float]],
    prob_raw: float,
    prob_calibrated: float,
    model_version: str,
) -> MarketEvidence:
    # shap 은 (피처명, 기여도) 목록이다. 상위 3개만 담는 게 확정 사양이지만
    # 개수 제한은 호출자가 정한다 — 여기서 자르면 "왜 3개인가"가 두 군데에 적힌다.
    _require_probability("prob_raw", prob_raw)
    _require_probability("prob_calibrated", prob_calibrated)
    if not model_version:
        raise ValueError("model_version 이 비었다")
    return MarketEvidence(
        shap=[ShapContribution(feature=name, value=float(value)) for name, value in shap],
        prob_raw=float(prob_raw),
        prob_calibrated=float(prob_calibrated),
        model_version=model_version,
    )


def sentiment_evidence(
    *,
    article_ids: list[int],
    lens_id: str,
    sector: str,
    model_version: str,
    n_articles: int | None = None,
) -> SentimentEvidence:
    # n_articles 는 기본적으로 article_ids 길이다. 중복제거 후 집계에 쓴 기사 수와
    # 근거로 보여줄 기사 목록이 달라질 수 있어(목록만 상위 N개로 자르는 경우)
    # 따로 받을 수 있게 뒀다.
    if not lens_id:
        raise ValueError("lens_id 가 비었다")
    if not model_version:
        raise ValueError("model_version 이 비었다")
    count = len(article_ids) if n_articles is None else n_articles
    if count < 0:
        raise ValueError(f"n_articles 가 음수다: {count}")
    return SentimentEvidence(
        article_ids=[int(article_id) for article_id in article_ids],
        lens_id=lens_id,
        sector=sector,
        n_articles=count,
        model_version=model_version,
    )


def regime_evidence(
    *,
    trend_index: str,
    regime_label: str,
    intensity: float,
    threshold_state: dict,
) -> RegimeEvidence:
    if not trend_index:
        raise ValueError("trend_index 가 비었다")
    if not math.isfinite(intensity):
        raise ValueError(f"intensity 가 유한하지 않다: {intensity!r}")
    return RegimeEvidence(
        trend_index=trend_index,
        regime_label=regime_label,
        intensity=float(intensity),
        threshold_state=dict(threshold_state),
    )


def skipped_evidence(reason: str) -> SkippedEvidence:
    # 중립 반환의 evidence. app/views/base.py 의 neutral_score 만 이걸 쓴다 —
    # 중립의 정의가 흩어지지 않게 하려는 것이다.
    if not reason:
        raise ValueError("reason 이 비었다")
    return SkippedEvidence(skipped=True, reason=reason)


def _require_probability(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} 은 [0,1] 이어야 한다: {value!r}")
