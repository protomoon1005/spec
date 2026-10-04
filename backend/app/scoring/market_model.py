"""시장분석 관점(T3) LightGBM 학습·추론·직렬화. DB 를 모른다 — 표본은 호출자가 준다.

재현성(plans/M3_3관점판단.md T3 확정): seed 고정 + deterministic + num_threads=1 +
force_row_wise. 표본 순서도 (t, 종목) 으로 고정한다 — 순서가 흔들리면 같은 파라미터로도
다른 트리가 나온다.

학습 창 분할: 라벨 확정일 <= cutoff 인 표본만 받는다. 그 안을 시간순으로
[fit] — EMBARGO 거래일 공백 — [보정] 으로 나눈다. 라벨이 20거래일 앞을 보므로 fit 끝
표본의 라벨 구간이 보정 구간과 겹치면 보정이 fit 이 이미 본 수익률로 채점된다.

결측 피처(None)는 NaN 으로 넘겨 LightGBM 의 결측 분기에 맡긴다. 0 으로 채우지 않는다
(features.py 결측 방침).
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import lightgbm as lgb
import numpy as np

from app.scoring.schedule import EMBARGO
from app.views.calibration import CALIBRATION_METHOD, IsotonicCalibrator, fit_isotonic
from app.views.market.dataset import Sample
from app.views.market.features import CURRENT_FEATURE_SET_VERSION, feature_names

PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "seed": 42,
    "deterministic": True,
    "num_threads": 1,
    "force_row_wise": True,
    "verbose": -1,
}
NUM_BOOST_ROUND = 200
# 학습 창 끝쪽 이 비율(거래일 기준)을 isotonic 보정에 쓴다.
CALIBRATION_FRACTION = 0.2
# 이보다 적으면 학습하지 않는다(스코어러가 중립을 낸다).
MIN_FIT_SAMPLES = 1000
MIN_CALIBRATION_SAMPLES = 200
SHAP_TOP_K = 3


def model_version(cutoff: date) -> str:
    return f"market-lgbm-{CURRENT_FEATURE_SET_VERSION}-{cutoff.isoformat()}"


@dataclass(frozen=True)
class Prediction:
    prob_raw: float
    prob_calibrated: float
    shap: list[tuple[str, float]]  # 절댓값 상위 SHAP_TOP_K 개, 로그오즈 단위


@dataclass
class MarketModel:
    version: str
    booster: lgb.Booster
    calibrator: IsotonicCalibrator
    train_start: date
    train_end: date
    metrics: dict  # accuracy · brier_score (보정 구간, 보정 후 확률)

    def predict(self, rows: Sequence[dict[str, float | None]]) -> list[Prediction]:
        names = feature_names()
        matrix = _matrix(rows, names)
        raw = self.booster.predict(matrix)
        # LightGBM 내장 TreeSHAP. shap 자체 C++ TreeSHAP 과 최대 4.4e-16 차(2,680행, 결측 포함)이고
        # shap.TreeExplainer 도 LightGBM 에는 이 함수에 위임한다(2026-10-04 실측) — shap 불필요.
        contrib = self.booster.predict(matrix, pred_contrib=True)
        out = []
        for prob, row in zip(raw, contrib, strict=True):
            pairs = sorted(zip(names, map(float, row[:-1])), key=lambda pair: (-abs(pair[1]), pair[0]))
            out.append(
                Prediction(
                    prob_raw=float(prob),
                    prob_calibrated=_clip01(self.calibrator.predict(float(prob))),
                    shap=pairs[:SHAP_TOP_K],
                )
            )
        return out

    def dumps(self) -> bytes:
        return json.dumps(
            {
                "version": self.version,
                "feature_set": CURRENT_FEATURE_SET_VERSION,
                "booster": self.booster.model_to_string(),
                "calibrator": self.calibrator.to_dict(),
                "calibration_method": CALIBRATION_METHOD,
                "train_start": self.train_start.isoformat(),
                "train_end": self.train_end.isoformat(),
                "metrics": self.metrics,
            },
            ensure_ascii=False,
        ).encode()

    @classmethod
    def loads(cls, blob: bytes) -> MarketModel:
        data = json.loads(blob)
        if data["feature_set"] != CURRENT_FEATURE_SET_VERSION:
            raise ValueError(f"피처셋이 다르다: {data['feature_set']} != {CURRENT_FEATURE_SET_VERSION}")
        return cls(
            version=data["version"],
            booster=lgb.Booster(model_str=data["booster"]),
            calibrator=IsotonicCalibrator.from_dict(data["calibrator"]),
            train_start=date.fromisoformat(data["train_start"]),
            train_end=date.fromisoformat(data["train_end"]),
            metrics=data["metrics"],
        )


def split(samples: Sequence[tuple[str, Sample]]) -> tuple[list, list]:
    """(종목, 표본) 목록을 시간순 (fit, 보정) 으로 나눈다. 둘 사이에 EMBARGO 거래일.

    거래일 축은 표본에 등장한 t 의 합집합이다 — DB 달력을 보지 않아도 된다.
    """
    days = sorted({sample.trade_date for _, sample in samples})
    calibration_start = len(days) - max(1, int(len(days) * CALIBRATION_FRACTION))
    fit_end = calibration_start - EMBARGO  # 이 인덱스 미만만 fit
    if fit_end <= 0:
        return [], []
    first_calibration_day = days[calibration_start]
    last_fit_day = days[fit_end - 1]
    fit = [pair for pair in samples if pair[1].trade_date <= last_fit_day]
    calibration = [pair for pair in samples if pair[1].trade_date >= first_calibration_day]
    return fit, calibration


def train(samples: Sequence[tuple[str, Sample]], *, cutoff: date) -> MarketModel | None:
    """표본으로 cutoff 모델을 학습한다. 표본이 모자라거나 한쪽 라벨뿐이면 None."""
    ordered = sorted(
        (pair for pair in samples if pair[1].label_date <= cutoff),
        key=lambda pair: (pair[1].trade_date, pair[0]),
    )
    fit, calibration = split(ordered)
    if len(fit) < MIN_FIT_SAMPLES or len(calibration) < MIN_CALIBRATION_SAMPLES:
        return None
    fit_labels = [sample.label for _, sample in fit]
    if len(set(fit_labels)) < 2:
        return None

    names = feature_names()
    dataset = lgb.Dataset(
        _matrix([sample.features for _, sample in fit], names),
        label=np.asarray(fit_labels, dtype=float),
        feature_name=list(names),
        params={"verbose": -1},
    )
    booster = lgb.train(PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)

    calibration_labels = [sample.label for _, sample in calibration]
    raw = [float(p) for p in booster.predict(_matrix([s.features for _, s in calibration], names))]
    calibrator = fit_isotonic(raw, calibration_labels)
    calibrated = [_clip01(calibrator.predict(p)) for p in raw]
    # 보정 구간 지표. 보정기가 이 구간으로 맞춰졌으므로 표본 내 값이다 — 낙관적이다.
    metrics = {
        "accuracy": sum((p >= 0.5) == bool(y) for p, y in zip(calibrated, calibration_labels))
        / len(calibrated),
        "brier_score": math.fsum((p - y) ** 2 for p, y in zip(calibrated, calibration_labels))
        / len(calibrated),
        "n_fit": len(fit),
        "n_calibration": len(calibration),
    }
    return MarketModel(
        version=model_version(cutoff),
        booster=booster,
        calibrator=calibrator,
        train_start=ordered[0][1].trade_date,
        train_end=cutoff,
        metrics=metrics,
    )


def _matrix(rows: Sequence[dict[str, float | None]], names: Sequence[str]) -> np.ndarray:
    return np.asarray(
        [[math.nan if row.get(name) is None else row[name] for name in names] for row in rows],
        dtype=float,
    ).reshape(len(rows), len(names))


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))
