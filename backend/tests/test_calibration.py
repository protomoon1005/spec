import random

import pytest

from app.views.calibration import IsotonicCalibrator, fit_isotonic


def test_monotone_input_is_kept_as_is():
    cal = fit_isotonic([0.1, 0.2, 0.3], [0.0, 0.5, 1.0])
    assert [cal.predict(x) for x in (0.1, 0.2, 0.3)] == [0.0, 0.5, 1.0]


def test_violators_are_pooled_into_their_weighted_mean():
    cal = fit_isotonic([1, 2, 3, 4], [0, 1, 0, 1])
    assert [cal.predict(x) for x in (1, 2, 3, 4)] == [0.0, 0.5, 0.5, 1.0]


def test_ties_are_averaged_before_pooling_regardless_of_order():
    a = fit_isotonic([1, 1, 2], [1, 0, 1])
    b = fit_isotonic([2, 1, 1], [1, 1, 0])
    assert a == b
    assert a.predict(1) == 0.5


def test_prediction_interpolates_inside_and_clips_outside():
    cal = fit_isotonic([0.0, 1.0], [0.2, 0.8])
    assert cal.predict(0.5) == pytest.approx(0.5)
    assert cal.predict(-3) == 0.2
    assert cal.predict(3) == 0.8


def test_round_trips_through_dict():
    cal = fit_isotonic([0.3, 0.1, 0.2], [1, 0, 0])
    assert IsotonicCalibrator.from_dict(cal.to_dict()) == cal


def test_rejects_empty_or_mismatched_input():
    with pytest.raises(ValueError):
        fit_isotonic([], [])
    with pytest.raises(ValueError):
        fit_isotonic([1, 2], [1])


@pytest.mark.requires_ml
def test_matches_sklearn_isotonic_regression():
    from sklearn.isotonic import IsotonicRegression

    rng = random.Random(7)
    for _ in range(100):
        xs = [round(rng.uniform(-1, 1), rng.choice([1, 2, 6])) for _ in range(rng.randint(1, 200))]
        ys = [float(rng.random() < (x + 1) / 2) for x in xs]
        reference = IsotonicRegression(out_of_bounds="clip").fit(xs, ys)
        ours = fit_isotonic(xs, ys)
        queries = xs + [rng.uniform(-1.2, 1.2) for _ in range(20)]
        for q in queries:
            assert ours.predict(q) == pytest.approx(float(reference.predict([q])[0]), abs=1e-12)
