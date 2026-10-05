from datetime import date, timedelta

import pytest

from app.scoring import schedule

DAYS = [date(2020, 1, 1) + timedelta(days=i) for i in range(100)]


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(
        schedule.price_daily,
        "get_trade_dates",
        lambda *, start, as_of: [d for d in DAYS if start <= d <= as_of],
    )


def test_cutoff_sits_on_a_fixed_grid_every_retrain_days():
    assert schedule.cutoff_for(DAYS[0]) == DAYS[0]
    assert schedule.cutoff_for(DAYS[19]) == DAYS[0]
    assert schedule.cutoff_for(DAYS[20]) == DAYS[20]
    assert schedule.cutoff_for(DAYS[59]) == DAYS[40]


def test_cutoff_is_none_before_the_calendar_starts():
    assert schedule.cutoff_for(date(2019, 12, 31)) is None
