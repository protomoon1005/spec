"""TS 러너와 파이썬 러너가 같은 주문을 내는지 검증한다.

이 프로젝트가 주장하는 "같은 입력에 같은 출력"은 말로 하면 증명이 아니다.
서로 다른 언어·다른 엔진으로 짠 두 구현이 같은 목표 비중을 내놓아야 한다.

입력(시세)은 저장소 루트 data/ 에서 읽는다 — 정본이다. TS 러너도 같은 곳을
읽으므로 두 러너가 같은 입력을 본다. frontend/data/backtest-result.json 은
그 TS 러너가 낸 결과이고, 여기서는 파이썬 러너를 돌려 목표 비중을 대조한다.

CI 는 .[dev] 만 설치하므로 requires_backtest 로 막아 둔다. 로컬에서는
  pip install -e "backend[backtest]"
  pytest -m requires_backtest
로 돌린다.

주의: 신호 경로가 M3 bridge 로 바뀐 뒤로 TS 러너(자체 지표)와 파이썬
러너(M3 판단)는 **다른 신호**를 쓴다. 그래서 신호가 개입하지 않는
대조군(use_signals=False) 으로 대조한다 — 비중 매핑·정규화·클램프·그룹캡
파이프라인이 두 언어에서 같은지를 보는 것이 목적이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data"                      # 입력 — 정본 시세·유니버스
OUT = ROOT / "frontend" / "data"         # TS 러너 산출물

pytestmark = pytest.mark.requires_backtest


@pytest.fixture(scope="module")
def ts_result() -> dict:
    path = OUT / "backtest-result.json"
    if not path.exists():
        pytest.skip(f"{path} 가 없다 — frontend 쪽 러너를 먼저 돌려야 한다")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def py_control(ts_result: dict):
    """파이썬 러너의 대조군(신호 미사용) 결정 기록."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    pytest.importorskip("vectorbt")

    from app.backtest.runner import run

    prices = pd.read_csv(SRC / "prices.csv", dtype={"ticker": str}, parse_dates=["date"])
    wide = prices.pivot(index="date", columns="ticker", values="close").ffill()
    wide.index = wide.index.strftime("%Y-%m-%d")

    holdings = [
        {
            "ticker": u["ticker"],
            "grade": u["grade"],
            "asset_group": u["asset_group"],
            "sector_group": u["sector_group"],
            "country_group": u["country_group"],
            "min_raw": u["weight_min_raw"],
            "max_raw": u["weight_max_raw"],
        }
        for u in ts_result["universe"]
    ]
    dates = [p["date"] for p in ts_result["series"]]
    rebal = [d["date"] for d in ts_result["control_decisions"]]

    return run(
        wide,
        holdings,
        ts_result["profile"]["risk_level"],
        dates,
        rebal,
        ts_result["initial"],
        use_signals=False,
    )


def test_리밸런싱_일정이_같다(py_control, ts_result):
    py_dates = [d["date"] for d in py_control["decisions"]]
    ts_dates = [d["date"] for d in ts_result["control_decisions"]]
    assert py_dates == ts_dates


def test_목표_비중이_같다(py_control, ts_result):
    """비중 파이프라인이 두 언어에서 같은 값을 내는지.

    평가액보다 이쪽이 엄격하다 — 평가액은 반올림 누적 때문에 미세하게 갈리지만
    목표 비중은 순수 계산이라 완전히 같아야 한다.
    """
    worst = 0.0
    for py_d, ts_d in zip(py_control["decisions"], ts_result["control_decisions"], strict=True):
        for ticker, py_w in py_d["target"].items():
            worst = max(worst, abs(py_w - ts_d["target"][ticker]))
    assert worst < 1e-9, f"목표 비중이 어긋난다 — 최대 {worst:.3e}"


def test_그룹캡_적용이_같다(py_control, ts_result):
    for py_d, ts_d in zip(py_control["decisions"], ts_result["control_decisions"], strict=True):
        py_caps = sorted((a["stage"], a["groupId"]) for a in py_d["capApplications"])
        ts_caps = sorted((a["stage"], a["groupId"]) for a in ts_d["capApplications"])
        assert py_caps == ts_caps, f"{py_d['date']} 에서 캡 적용이 다르다"


def test_비중과_현금의_합이_1이다(py_control):
    for d in py_control["decisions"]:
        total = sum(d["target"].values()) + d["cash"]
        assert abs(total - 1.0) < 1e-9, f"{d['date']}: 합이 {total}"


def test_현금이_하한_이상이다(py_control, ts_result):
    cash_min = ts_result["profile"]["cash_min"]
    for d in py_control["decisions"]:
        assert d["cash"] >= cash_min - 1e-9, f"{d['date']}: 현금 {d['cash']} < 하한 {cash_min}"
