"""시장분석 LightGBM 학습·추론(app/scoring/market_model.py). DB 없이 합성 표본으로 돈다."""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

pytest.importorskip("lightgbm")

from app.scoring.market_model import (  # noqa: E402
    SHAP_TOP_K,
    MarketModel,
    model_version,
    split,
    train,
)
from app.scoring.schedule import EMBARGO  # noqa: E402
from app.views.market.dataset import Sample  # noqa: E402
from app.views.market.features import feature_names  # noqa: E402

pytestmark = pytest.mark.requires_ml

START = date(2020, 1, 1)
N_DAYS = 400
TICKERS = [f"T{i:02d}" for i in range(10)]
CUTOFF = START + timedelta(days=N_DAYS + 30)


def _samples(seed: int = 3) -> list[tuple[str, Sample]]:
    # ret_20 이 클수록 사건 확률이 높고, 일부 피처는 결측(None)이다.
    rng = random.Random(seed)
    out = []
    for day in range(N_DAYS):
        t = START + timedelta(days=day)
        for ticker in TICKERS:
            features = {name: rng.gauss(0.0, 1.0) for name in feature_names()}
            if rng.random() < 0.2:
                features["atr_14_pct"] = None
            p = 0.5 + 0.35 * max(-1.0, min(1.0, features["ret_20"]))
            label = int(rng.random() < p)
            out.append((ticker, Sample(t, features, label, t + timedelta(days=28))))
    return out


@pytest.fixture(scope="module")
def model() -> MarketModel:
    trained = train(_samples(), cutoff=CUTOFF)
    assert trained is not None
    return trained


def test_same_input_same_model_and_predictions(model):
    again = train(_samples(), cutoff=CUTOFF)
    assert again.booster.model_to_string() == model.booster.model_to_string()
    rows = [s.features for _, s in _samples(seed=9)[:500]]
    assert model.predict(rows) == again.predict(rows)


def test_version_and_window(model):
    assert model.version == model_version(CUTOFF) == f"market-lgbm-v0.1-ta9-{CUTOFF.isoformat()}"
    assert model.train_end == CUTOFF
    assert 0.0 <= model.metrics["brier_score"] <= 0.25
    assert model.metrics["accuracy"] > 0.6


def test_label_after_cutoff_is_not_used():
    samples = _samples()
    early = samples[0][1].label_date
    # 모든 표본의 라벨이 cutoff 뒤라면 학습할 것이 없다.
    assert train(samples, cutoff=early - timedelta(days=1)) is None


def test_split_has_embargo_gap():
    ordered = sorted(_samples(), key=lambda pair: (pair[1].trade_date, pair[0]))
    fit, calibration = split(ordered)
    days = sorted({s.trade_date for _, s in ordered})
    last_fit = days.index(max(s.trade_date for _, s in fit))
    first_cal = days.index(min(s.trade_date for _, s in calibration))
    assert first_cal - last_fit - 1 == EMBARGO
    assert fit and calibration


def test_calibrated_prob_is_monotone_in_raw(model):
    rows = [s.features for _, s in _samples(seed=11)[:2000]]
    pairs = sorted((p.prob_raw, p.prob_calibrated) for p in model.predict(rows))
    calibrated = [c for _, c in pairs]
    assert all(a <= b for a, b in zip(calibrated, calibrated[1:]))
    assert all(0.0 <= c <= 1.0 for c in calibrated)


def test_shap_top3_sorted_by_abs(model):
    rows = [s.features for _, s in _samples(seed=5)[:50]]
    names = set(feature_names())
    for prediction in model.predict(rows):
        assert len(prediction.shap) == SHAP_TOP_K
        values = [abs(v) for _, v in prediction.shap]
        assert values == sorted(values, reverse=True)
        assert {name for name, _ in prediction.shap} <= names


def test_missing_features_predict():
    names = feature_names()
    trained = train(_samples(), cutoff=CUTOFF)
    [prediction] = trained.predict([{name: None for name in names}])
    assert 0.0 <= prediction.prob_calibrated <= 1.0


def test_serialization_roundtrip(model):
    loaded = MarketModel.loads(model.dumps())
    rows = [s.features for _, s in _samples(seed=13)[:300]]
    assert loaded.predict(rows) == model.predict(rows)
    assert loaded.version == model.version and loaded.calibrator == model.calibrator
