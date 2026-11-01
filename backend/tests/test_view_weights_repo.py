"""insert_view_weights 왕복·충돌 테스트 (FN-408 쓰기 경로).

postgres가 필요하다 — conftest.py의 portfolio_id 픽스처(FK 체인을 최소로 만들고
teardown에서 역순으로 지우는 패턴)를 그대로 쓴다. 순수 계산 쪽 회귀는
test_hedge.py 가 DB 없이 따로 지킨다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.contracts.view_weights import WEIGHT_FLOOR
from app.repositories.view_weights import get_view_weights, insert_view_weights
from app.views.hedge import UPDATE_RULE_VERSION, rolling_weights

AS_OF = date(2026, 9, 10)
NEXT_DAY = AS_OF + timedelta(days=1)
EQUAL_WEIGHTS = {"market": 1 / 3, "sentiment": 1 / 3, "regime": 1 / 3}


def _row_count(engine, portfolio_id: int, as_of: date) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM view_weights "
                "WHERE portfolio_id = :pid AND as_of = :as_of"
            ),
            {"pid": portfolio_id, "as_of": as_of},
        ).scalar_one()


def test_roundtrip_sum_is_one(engine, portfolio_id) -> None:
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        weights=EQUAL_WEIGHTS,
        update_rule_version=UPDATE_RULE_VERSION,
    )

    # get_view_weights 는 strict < 라 당일이 아니라 다음날로 읽어야 보인다.
    read_back = get_view_weights(portfolio_id, as_of=NEXT_DAY)

    assert sorted(read_back) == ["market", "regime", "sentiment"]
    assert sum(read_back.values()) == pytest.approx(1.0, abs=1e-9)
    for view_type, weight in EQUAL_WEIGHTS.items():
        assert read_back[view_type] == pytest.approx(weight, abs=1e-9)


def test_hedge_output_roundtrips(engine, portfolio_id) -> None:
    # 순수 함수가 낸 값을 그대로 넣고 다시 읽어도 합이 1에서 벗어나지 않는다.
    weights = rolling_weights(
        {"market": [0.04] * 60, "sentiment": [0.64] * 60, "regime": [0.25] * 60}
    )
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        weights=weights,
        update_rule_version=UPDATE_RULE_VERSION,
    )

    read_back = get_view_weights(portfolio_id, as_of=NEXT_DAY)
    assert sum(read_back.values()) == pytest.approx(1.0, abs=1e-9)
    assert min(read_back.values()) >= WEIGHT_FLOOR - 1e-9
    assert read_back["market"] == pytest.approx(0.80, abs=1e-9)


def test_floor_and_rule_version_are_recorded(engine, portfolio_id) -> None:
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        weights=EQUAL_WEIGHTS,
        update_rule_version=UPDATE_RULE_VERSION,
    )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT weight_floor, update_rule_version FROM view_weights "
                "WHERE portfolio_id = :pid AND as_of = :as_of"
            ),
            {"pid": portfolio_id, "as_of": AS_OF},
        ).all()

    assert len(rows) == 3
    for row in rows:
        assert float(row.weight_floor) == pytest.approx(WEIGHT_FLOOR)
        assert row.update_rule_version == UPDATE_RULE_VERSION


def test_same_key_twice_raises(engine, portfolio_id) -> None:
    # uq_view_weight (portfolio_id, as_of, view_type) 충돌은 잡지 않고 올린다.
    # append-only 테이블에서 조용한 덮어쓰기는 이력을 거짓말하게 만든다.
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        weights=EQUAL_WEIGHTS,
        update_rule_version=UPDATE_RULE_VERSION,
    )
    with pytest.raises(IntegrityError):
        insert_view_weights(
            portfolio_id=portfolio_id,
            as_of=AS_OF,
            weights=EQUAL_WEIGHTS,
            update_rule_version=UPDATE_RULE_VERSION,
        )
    assert _row_count(engine, portfolio_id, AS_OF) == 3


def test_conflicting_batch_is_all_or_nothing(engine, portfolio_id) -> None:
    # 한 관점만 먼저 들어가 있는 상태에서 두 관점을 넣으면, 충돌하지 않는
    # 나머지 하나도 들어가면 안 된다 — 합이 1이 아닌 시점이 남으면 읽는 쪽이
    # 예외를 맞는다.
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=AS_OF,
        weights={"market": 1.0},
        update_rule_version=UPDATE_RULE_VERSION,
    )
    with pytest.raises(IntegrityError):
        insert_view_weights(
            portfolio_id=portfolio_id,
            as_of=AS_OF,
            weights={"market": 0.5, "sentiment": 0.5},
            update_rule_version=UPDATE_RULE_VERSION,
        )
    assert _row_count(engine, portfolio_id, AS_OF) == 1


def test_sum_not_one_is_rejected_before_writing(engine, portfolio_id) -> None:
    with pytest.raises(ValueError, match="가중치 합이 1이 아니다"):
        insert_view_weights(
            portfolio_id=portfolio_id,
            as_of=AS_OF,
            weights={"market": 0.5, "sentiment": 0.3, "regime": 0.1},
            update_rule_version=UPDATE_RULE_VERSION,
        )
    assert _row_count(engine, portfolio_id, AS_OF) == 0


def test_negative_weight_is_rejected(engine, portfolio_id) -> None:
    with pytest.raises(ValueError, match="음수 가중치"):
        insert_view_weights(
            portfolio_id=portfolio_id,
            as_of=AS_OF,
            weights={"market": 1.2, "sentiment": -0.2},
            update_rule_version=UPDATE_RULE_VERSION,
        )
    assert _row_count(engine, portfolio_id, AS_OF) == 0


def test_run_id_axis_is_not_implemented_yet() -> None:
    # Q3 마이그레이션(run_id 컬럼 + portfolio_id NULLABLE + 부분 UNIQUE 2개)이
    # 들어오면 SQL만 채운다. 시그니처는 지금부터 최종형이다.
    with pytest.raises(NotImplementedError, match="run_id"):
        insert_view_weights(
            run_id=1,
            as_of=AS_OF,
            weights=EQUAL_WEIGHTS,
            update_rule_version=UPDATE_RULE_VERSION,
        )


@pytest.mark.parametrize("kwargs", [{}, {"portfolio_id": 1, "run_id": 1}])
def test_exactly_one_key_axis_is_required(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="정확히 하나만"):
        insert_view_weights(
            as_of=AS_OF,
            weights=EQUAL_WEIGHTS,
            update_rule_version=UPDATE_RULE_VERSION,
            **kwargs,
        )
