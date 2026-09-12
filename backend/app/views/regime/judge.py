# FN-406 시장온도 — 국면 판정. 순수 함수다 (DB 를 모른다).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/views 모듈과 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴에서 가드 대상 테이블명을 찾는다.
#
# 입력은 이미 조회·가공된 지표 값 dict 다. 조회는 호출자가 저장소 계층의
# get_macro 로 하고, 이동평균 이격도 같은 파생값도 호출자가 만들어 넣는다.
# 그래야 이 함수가 postgres 없이 테스트되고 T4b(종목별 펼치기)도 같은 값을 쓴다.
#
# 1차는 임계값 규칙만이다. hmmlearn 은 데모 계획상 "시간 여유 시 자유 항목"이라
# 손대지 않는다.
#
# ── 임계값 수치의 출처 ───────────────────────────────────────────────
# 설계 문서에 임계값이 없다. 그래서 **실측 분포의 분위수**로 정했다 —
# 2023-01-03 ~ 2025-12-31 구간(백테스트 창과 같다)의 실제 값으로 잰 것이다.
# 이건 프리셋·하드캡 같은 팀 확정 수치가 **아니다.** 10월 백테스트로 조정할
# 대상이라고 계획서에 적어 뒀다.
#
#   VIX_CLOSE              25% 14.19 · 50% 16.37 · 75% 18.92 · 90% 21.68  -> 임계 20.0
#   CREDIT_SPREAD_BAA10Y   25%  1.56 · 50%  1.72 · 75%  1.84 · 90%  2.00  -> 임계 2.00
#   USDKRW_GAP60           25% -0.011 · 50% +0.009 · 75% +0.021 · 90% +0.031 -> 임계 +0.03
#   TREND_GAP              측정 불가(추세 지수 미확보)                     -> 임계 0.0
#
# VIX 와 스프레드는 75~90분위 사이의 둥근 수를 택했다. 75분위는 너무 자주 걸리고
# (4분기에 한 번꼴로 위험회피가 된다) 90분위는 너무 늦다. 환율은 수준이 추세를
# 타서 이격도로 본다 — 원화가 60일 평균 대비 3% 이상 약세면 위험회피 신호로 읽는다.
# 추세 이격도만 분위수가 아니라 0이다. 이격도는 0을 중심으로 하고 "이동평균 위냐
# 아래냐"가 추세의 정의 그 자체라, 다른 근거가 필요 없다.
#
# 임계값 묶음에는 버전을 붙인다(feature_set_version 과 같은 이유 — 값이 바뀌면
# 과거 판정을 재현할 수 없다). regime_snapshots 에 버전 컬럼이 없어서
# threshold_state JSONB 안에 넣는다.
from __future__ import annotations

import math
from dataclasses import dataclass

THRESHOLD_SET_VERSION = "regime-v0.1-2026-09"

# 판정에 필요한 최소 지표 수. 이보다 적으면 "판정했다"고 말하지 않는다.
MIN_AVAILABLE_INDICATORS = 2

LABEL_RISK_ON = "risk_on"
LABEL_RISK_OFF = "risk_off"
LABEL_UNKNOWN = "unknown"

ABOVE_IS_RISK_OFF = "above_is_risk_off"
BELOW_IS_RISK_OFF = "below_is_risk_off"


@dataclass(frozen=True)
class ThresholdRule:
    key: str  # 입력 dict 의 키
    label: str
    threshold: float
    direction: str
    scale: float  # intensity 정규화 폭. 임계에서 이만큼 벗어나면 |기여도| 1 이 된다.
    basis: str


THRESHOLDS: tuple[ThresholdRule, ...] = (
    ThresholdRule(
        key="VIX_CLOSE",
        label="변동성지수",
        threshold=20.0,
        direction=ABOVE_IS_RISK_OFF,
        scale=5.0,
        basis="2023-2025 분포 75%=18.92 / 90%=21.68 사이의 둥근 수",
    ),
    ThresholdRule(
        key="CREDIT_SPREAD_BAA10Y",
        label="신용스프레드",
        threshold=2.00,
        direction=ABOVE_IS_RISK_OFF,
        scale=0.30,
        basis="2023-2025 분포 90%=2.00",
    ),
    ThresholdRule(
        key="USDKRW_GAP60",
        label="환율 60일 이격도",
        threshold=0.03,
        direction=ABOVE_IS_RISK_OFF,
        scale=0.025,
        basis="2023-2025 분포 75%=+0.021 / 90%=+0.031, 원화 약세가 위험회피 신호",
    ),
    ThresholdRule(
        key="TREND_GAP",
        label="추세 지수 이동평균 이격도",
        threshold=0.0,
        direction=BELOW_IS_RISK_OFF,
        scale=0.05,
        basis="이격도 0(이동평균)이 추세의 정의 그 자체라 분위수가 필요 없다",
    ),
)


@dataclass(frozen=True)
class RegimeJudgement:
    label: str
    intensity: float  # -1(위험회피) ~ +1(위험선호)
    threshold_state: dict


def judge(indicators: dict[str, float | None]) -> RegimeJudgement:
    """지표 값 dict -> (국면 라벨, 강도, 임계 상태).

    결측 지표는 **남은 지표로 판정한다.** 예외를 내지 않는다 — 지표 하나가 비는
    것은 판단 계층의 정상 상태이고, 여기서 끊으면 파이프라인 전체가 멈춘다.
    다만 쓸 수 있는 지표가 MIN_AVAILABLE_INDICATORS 미만이면 라벨을 unknown 으로
    두고 강도를 0 으로 한다. 근거가 하나뿐인데 "위험회피 국면"이라고 말하면
    그건 판정이 아니라 추측이다. T4b 의 스코어러는 unknown 을 중립으로 읽는다.
    """
    state: dict = {}
    scores: list[float] = []

    for rule in THRESHOLDS:
        value = indicators.get(rule.key)
        if value is None or not math.isfinite(value):
            state[rule.key] = {"label": rule.label, "value": None, "missing": True}
            continue

        distance = (value - rule.threshold) / rule.scale
        # + 가 위험선호다. above_is_risk_off 면 부호를 뒤집는다.
        score = -distance if rule.direction == ABOVE_IS_RISK_OFF else distance
        score = max(-1.0, min(1.0, score))
        breached = (
            value > rule.threshold
            if rule.direction == ABOVE_IS_RISK_OFF
            else value < rule.threshold
        )

        scores.append(score)
        state[rule.key] = {
            "label": rule.label,
            "value": round(float(value), 6),
            "threshold": rule.threshold,
            "direction": rule.direction,
            "breached": breached,
            "score": round(score, 6),
            "missing": False,
        }

    if len(scores) < MIN_AVAILABLE_INDICATORS:
        return RegimeJudgement(
            label=LABEL_UNKNOWN,
            intensity=0.0,
            threshold_state=_envelope(state, len(scores), reason="지표가 모자라 판정하지 않았다"),
        )

    intensity = max(-1.0, min(1.0, math.fsum(scores) / len(scores)))
    label = LABEL_RISK_ON if intensity >= 0 else LABEL_RISK_OFF
    return RegimeJudgement(
        label=label,
        intensity=round(intensity, 6),
        threshold_state=_envelope(state, len(scores)),
    )


def _envelope(state: dict, available: int, *, reason: str | None = None) -> dict:
    envelope = {
        "threshold_set_version": THRESHOLD_SET_VERSION,
        "available_count": available,
        "min_required": MIN_AVAILABLE_INDICATORS,
        "indicators": state,
    }
    if reason is not None:
        envelope["reason"] = reason
    return envelope
