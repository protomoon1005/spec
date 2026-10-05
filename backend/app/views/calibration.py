# isotonic 보정 — 단조 증가 계단함수를 PAV(Pool Adjacent Violators)로 맞춘다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 hedge.py 와 같다 — 이 파일은
# as_of 가드 사정권이다.
#
# ── 왜 scikit-learn 이 아니라 직접 쓰는가 ────────────────────────────
# app/views/ 는 stdlib 만 쓴다(CLAUDE.md). 시장분석(T3)·시장온도(T4b) 두 관점이
# 같은 보정을 쓰는데, 시장온도는 ML 의존성 없이 돌아야 한다. PAV 는 30줄이고
# sklearn.isotonic.IsotonicRegression(out_of_bounds="clip", increasing=True) 과
# 같은 값을 낸다 — tests/test_calibration.py 가 requires_ml 로 둘을 대조한다.
#
# ── 동점 x 처리 ──────────────────────────────────────────────────────
# 같은 x 는 먼저 평균 한 점으로 묶고(가중치 = 개수) PAV 를 돌린다. sklearn 과
# 같은 규칙이다. 묶지 않으면 입력 순서에 따라 결과가 달라진다.
#
# ── 예측: 구간 사이는 선형 보간, 바깥은 끝값 ──────────────────────────
from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

CALIBRATION_METHOD = "isotonic"


@dataclass(frozen=True)
class IsotonicCalibrator:
    xs: tuple[float, ...]
    ys: tuple[float, ...]

    def predict(self, x: float) -> float:
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        right = bisect.bisect_right(xs, x)
        x0, x1, y0, y1 = xs[right - 1], xs[right], ys[right - 1], ys[right]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def to_dict(self) -> dict:
        return {"xs": list(self.xs), "ys": list(self.ys)}

    @classmethod
    def from_dict(cls, data: dict) -> IsotonicCalibrator:
        return cls(tuple(map(float, data["xs"])), tuple(map(float, data["ys"])))


def fit_isotonic(xs: Sequence[float], ys: Sequence[float]) -> IsotonicCalibrator:
    if len(xs) != len(ys):
        raise ValueError(f"길이가 다르다: x {len(xs)} · y {len(ys)}")
    if not xs:
        raise ValueError("보정 표본이 비었다")

    # 1) 같은 x 를 평균 한 점으로 묶는다. 블록 = [x, y 평균, 가중치]
    blocks: list[list[float]] = []
    for x, y in sorted(zip(map(float, xs), map(float, ys))):
        if blocks and blocks[-1][0] == x:
            last = blocks[-1]
            last[1] = (last[1] * last[2] + y) / (last[2] + 1)
            last[2] += 1
        else:
            blocks.append([x, y, 1.0])

    # 2) PAV — 단조가 깨지면 인접 블록을 가중평균으로 합친다. 합친 블록은 x 구간을
    #    가지므로 (첫 x, 끝 x, y, w) 로 들고 다닌다.
    pooled: list[list[float]] = []
    for x, y, w in blocks:
        pooled.append([x, x, y, w])
        while len(pooled) > 1 and pooled[-2][2] > pooled[-1][2]:
            right = pooled.pop()
            left = pooled[-1]
            total = left[3] + right[3]
            left[2] = (left[2] * left[3] + right[2] * right[3]) / total
            left[1] = right[1]
            left[3] = total

    # 3) 블록마다 양 끝 x 에 같은 y 를 찍는다(한 점 블록은 한 번만).
    out_x: list[float] = []
    out_y: list[float] = []
    for first, last, y, _ in pooled:
        out_x.append(first)
        out_y.append(y)
        if last != first:
            out_x.append(last)
            out_y.append(y)
    return IsotonicCalibrator(tuple(out_x), tuple(out_y))
