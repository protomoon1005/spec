# 시장분석 관점(T3) 학습 표본. 한 종목의 일봉 이력에서 (t, 피처, 라벨, 라벨 확정일)을
# 만든다. DB 도 ML 도 모르는 순수 함수다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다 — 이 파일은
# as_of 가드 사정권이다.
#
# ── 피처는 bridge 와 같은 규칙으로 만든다 ────────────────────────────
# t 에서 끝나는 직전 FEATURE_WARMUP_ROWS 행 창으로 compute_features 를 부른다.
# 이력이 창보다 짧은 t 는 표본에서 뺀다 — bridge 가 추론 때 그런 종목을 중립으로
# 내므로, 학습에 넣으면 추론에서 볼 일 없는 분포를 배운다.
#
# ── 라벨은 labels.py 정의를 그대로 쓴다 ──────────────────────────────
# make_label 이 사건 정의·THETA 필터·train_end 경계를 다 처리한다. 여기서는 그 결과에
# 라벨 확정일(t 로부터 HORIZON 행 뒤 거래일)만 붙인다.
#
# ── 메모 ─────────────────────────────────────────────────────────────
# t 의 피처는 t 이하 과거 창만 보므로 데이터가 바뀌지 않는 한 값이 고정이다. 그래서
# 호출자가 {t: 피처} dict 를 넘기면 재사용한다(cutoff 마다 재학습할 때 다시 계산하지
# 않으려는 것). 종목별로 따로 넘겨야 한다.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.views.market.features import FEATURE_WARMUP_ROWS, PriceBar, compute_features
from app.views.market.labels import HORIZON, make_label


@dataclass(frozen=True)
class Sample:
    trade_date: date  # 판단 시점 t
    features: dict[str, float | None]
    label: int
    label_date: date  # t 로부터 HORIZON 행 뒤 — 이 날 라벨이 확정된다


def build_samples(
    bars: Sequence[PriceBar],
    *,
    cutoff: date,
    memo: dict[date, dict[str, float | None]] | None = None,
) -> list[Sample]:
    # cutoff 뒤 행은 처음에 잘라낸다. make_label 도 train_end 로 거르지만, 미래 행이
    # 피처 창에 섞일 여지를 입구에서 없앤다.
    ordered = sorted((bar for bar in bars if bar.trade_date <= cutoff), key=lambda bar: bar.trade_date)
    position = {bar.trade_date: index for index, bar in enumerate(ordered)}

    samples: list[Sample] = []
    for trade_date, label in make_label(ordered, train_end=cutoff):
        index = position[trade_date]
        if index + 1 < FEATURE_WARMUP_ROWS:
            continue
        features = memo.get(trade_date) if memo is not None else None
        if features is None:
            window = ordered[index + 1 - FEATURE_WARMUP_ROWS : index + 1]
            features = compute_features(window, as_of=trade_date)
            if memo is not None:
                memo[trade_date] = features
        samples.append(Sample(trade_date, features, label, ordered[index + HORIZON].trade_date))
    return samples
