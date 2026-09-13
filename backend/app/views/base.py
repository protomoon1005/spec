# 세 관점 스코어러가 공유하는 계약. T3(시장온도)·T4(시장분석)·T7~T8(감성)이
# 전부 여기에 기댄다.
#
# 설명을 docstring이 아니라 주석에 두는 이유: 이 파일은 as_of 가드 사정권이다.
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상
# 테이블명을 찾으면 CI가 실패한다. 주석은 AST 에 안 잡힌다.
#
# ── 반환 형식 ────────────────────────────────────────────────────────
# 반드시 app.contracts.view_score.ViewScore 를 쓴다. 계약 ② 이므로 새 모델을
# 만들지 않는다. 부가정보는 전부 evidence dict 로 가고, 그 키 목록은
# app/views/evidence.py 가 단일 출처다.
#
# ── rules 타입 ───────────────────────────────────────────────────────
# rules 는 Spec 의 signal_rules 해당 블록이라 관점마다 타입이 다르다
# (MarketAnalysisRule / SentimentRule / MarketTemperatureRule). Protocol 은
# BaseModel | None 으로 느슨하게 두고, 각 구현체가 첫 줄에서 자기 타입인지
# 확인해 아니면 TypeError 를 낸다. Generic Protocol 로 조이는 것은 얻는 것에
# 비해 복잡해서 하지 않는다.
#
# ── 중립 반환 ────────────────────────────────────────────────────────
# 중립을 내는 경우는 둘이다.
#   1) rules 가 None      — Spec 이 그 관점을 쓰지 않는다  -> reason="rules_absent"
#   2) 데이터가 없다      — 기사 0건·지표 결측 등          -> reason="no_data"
# 어느 쪽이든 예외로 파이프라인을 끊지 않는다. 데이터가 없다는 것은 판단 계층의
# 정상 상태이지 오류가 아니다.
#
# 중립을 만드는 자리는 neutral_score 하나뿐이어야 한다. T5 의 integrate() 가
# "중립 관점"(가중치를 소비해 가중합을 희석한다)과 "누락된 관점"(스코어러 버그라
# ValueError)을 구분하는데, 중립의 정의가 세 관점에 흩어지면 그 구분이 조용히
# 무너진다.
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.contracts.view_score import ViewScore, ViewType
from app.views.evidence import skipped_evidence

# 중립의 정의. 확률 0.5 는 "모른다"이지 "하락"이 아니다 — Hedge 가 Brier score 로
# 채점하므로 중립 관점은 손실 0.25 를 일정하게 받는다.
NEUTRAL_RAW_SCORE = 0.0
NEUTRAL_CALIBRATED_PROB = 0.5


@runtime_checkable
class ViewScorer(Protocol):
    """세 관점이 공유하는 스코어러 계약."""

    view_type: ViewType

    def score(
        self, tickers: list[str], *, as_of: date, rules: BaseModel | None
    ) -> list[ViewScore]:
        ...


def neutral_score(view_type: ViewType, ticker: str, *, reason: str) -> ViewScore:
    """판단하지 않았음을 뜻하는 중립 점수 한 건."""
    return ViewScore(
        view_type=view_type,
        ticker=ticker,
        raw_score=NEUTRAL_RAW_SCORE,
        calibrated_prob=NEUTRAL_CALIBRATED_PROB,
        evidence=dict(skipped_evidence(reason)),
    )


def neutral_scores(view_type: ViewType, tickers: list[str], *, reason: str) -> list[ViewScore]:
    """유니버스 전체에 대한 중립 점수.

    통합기는 가중치가 있는 관점이 종목 하나라도 빠뜨리면 예외를 낸다. 그래서
    중립도 유니버스 전체를 채워서 돌려줘야 한다 — 빈 목록을 반환하면 안 된다.
    """
    return [neutral_score(view_type, ticker, reason=reason) for ticker in tickers]
