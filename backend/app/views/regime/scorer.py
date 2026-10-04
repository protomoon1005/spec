# FN-407 시장온도 — 국면(시장 전체 상태)을 종목별 점수로 "펴는" 규칙. 순수 함수다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/views 모듈과 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴에서 가드 대상 테이블명을 찾는다.
#
# ── 규칙 ─────────────────────────────────────────────────────────────
# raw_score = 2·P(사건 | 종목 묶음, 국면 라벨) − 1
#   사건    = 20거래일 선행 수익률 > 0 (app/views/market/labels.py 정의 그대로)
#   묶음    = 자산군(EQUITY/BOND/COMMODITY), 인버스면 따로(EQUITY_INVERSE)
#   P       = 학습 cutoff 까지 실현된 표본의 조건부 빈도, 라플라스 평활 (a+1)/(n+2)
# 표가 없는 칸은 0.5(모른다) → raw 0. 2p−1 이라 [-1,1] 은 저절로 지켜지지만
# 부동소수 안전을 위해 한 번 더 자른다. unknown 국면·자산군 없는 종목은 None —
# 호출자가 neutral_score(REASON_NO_DATA) 로 바꾼다.
#
# ── 왜 고정 부호(주식 +intensity, 채권 −0.5·intensity)가 아닌가 ────────
# 2019~2025 실데이터 워크포워드(20거래일 격자 재학습, 실현일 <= cutoff 표본만,
# 활성 73종목, 보정 후 Brier, 평가 t >= 2020-01) 비교 결과:
#   상수(기준선, 기저율)          Brier 0.23984  적중 0.6095
#   전부 +intensity               Brier 0.24040  적중 0.6028
#   고정 부호 E+1/B−.5/C−.5        Brier 0.24205  적중 0.6024
#   고정 부호 + 인버스 반전         Brier 0.24195  적중 0.6018
#   부호만 데이터로(자산군별 on/off 빈도차) Brier 0.24837 적중 0.5949
#   조건부 빈도(채택)               Brier 0.23837  적중 0.6022
#   조건부 빈도 + intensity 3구간   Brier 0.24680  적중 0.5764
# (평가 t >= 2023-01: 상수 0.22971/0.6608, 고정 0.23106/0.6608, 채택 0.22468/0.6705)
# 고정 부호 규칙은 두 구간 모두 **기저율보다 못했다** — 이 기간 채권 ETF 는 국면과
# 무관하게 대체로 올라서 "risk_on 이면 채권 하락" 이 틀린 쪽에 걸렸다. 기저율을
# 넘은 것은 조건부 빈도뿐이라 이것을 택했다. intensity 를 구간으로 더 쪼개면 칸당
# 표본이 줄어 오히려 나빠졌다. 그래서 intensity 는 라벨(부호)로만 들어가고 크기는
# 근거(evidence)로만 남는다.
#
# 레버리지는 묶음을 나누지 않는다 — 배율은 방향(사건 확률)을 바꾸지 않는다.
# 인버스는 방향이 뒤집히므로 나눈다. 원장에 인버스 칸이 없어 종목명으로 가린다.
from __future__ import annotations

from collections.abc import Iterable

from app.views.regime.judge import LABEL_UNKNOWN

ASSET_GROUPS = ("EQUITY", "BOND", "COMMODITY")
INVERSE_NAME_MARK = "인버스"
INVERSE_SUFFIX = "_INVERSE"


def group_key(asset_group: str | None, *, name: str) -> str | None:
    # 종목 묶음 키. 자산군이 없거나 모르는 값이면 None(판단 불가).
    if asset_group not in ASSET_GROUPS:
        return None
    return asset_group + INVERSE_SUFFIX if INVERSE_NAME_MARK in name else asset_group


def event_rates(pairs: Iterable[tuple[str, str, int]]) -> dict[tuple[str, str], float]:
    # (묶음 키, 국면 라벨, 사건 0/1) 표본 -> {(키, 라벨): 평활 빈도}.
    counts: dict[tuple[str, str], list[int]] = {}
    for key, label, outcome in pairs:
        cell = counts.setdefault((key, label), [0, 0])
        cell[0] += outcome
        cell[1] += 1
    return {cell: (hits + 1) / (n + 2) for cell, (hits, n) in counts.items()}


def raw_score(
    *, regime_label: str, key: str | None, rates: dict[tuple[str, str], float]
) -> float | None:
    if regime_label == LABEL_UNKNOWN or key is None:
        return None
    probability = rates.get((key, regime_label), 0.5)
    return max(-1.0, min(1.0, 2.0 * probability - 1.0))
