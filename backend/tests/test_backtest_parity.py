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


# ── BBL 리밸런싱 블록별 대조 ─────────────────────────────────────────
# frontend/scripts/run-backtest.ts 가 블록마다 대조군을 돌려 backtest-schedules.json 에 남긴다.
# 파이썬은 같은 규칙을 inputs.plan_schedule 로 일정으로 바꾸고 runner.run 으로 돌려
# (1) 일정 (2) 실제 리밸런싱일(threshold 는 러너가 정한다) (3) 목표 비중 (4) 그룹캡을 대조한다.


@pytest.fixture(scope="module")
def ts_schedules() -> dict:
    path = OUT / "backtest-schedules.json"
    if not path.exists():
        pytest.skip(f"{path} 가 없다 — frontend 쪽 러너를 먼저 돌려야 한다")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def py_schedule_runs(ts_schedules: dict) -> dict:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("vectorbt")

    from app.backtest.inputs import plan_schedule
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
        for u in ts_schedules["universe"]
    ]
    weekly = ts_schedules["valuation_dates"]

    out = {}
    for entry in ts_schedules["schedules"]:
        if entry["block_id"] == "RB_SIGNAL":
            # TS 에는 판단이 없다 — 파이썬이 (목업 스코어러로) 정한 날짜를 지금 다시 정해 대조군을 돌린다
            strategy = _vbt_script().signal_rebalance(wide, holdings, ts_schedules["risk_level"], weekly)
            result = run(
                wide, holdings, ts_schedules["risk_level"], weekly, strategy["rebalance_dates"],
                ts_schedules["initial"], use_signals=False,
            )
            out["RB_SIGNAL"] = (strategy["rebalance_dates"], strategy["skipped_rebalance_dates"], result)
            continue
        plan = plan_schedule(entry["rule"], weekly)
        result = run(
            wide, holdings, ts_schedules["risk_level"], weekly, plan.dates,
            ts_schedules["initial"], use_signals=False, drift=plan.drift,
        )
        out[entry["block_id"]] = (plan.dates, plan.skipped, result)
    return out


def _vbt_script():
    import importlib.util

    path = ROOT / "scripts" / "run_backtest_vbt.py"
    spec = importlib.util.spec_from_file_location("run_backtest_vbt", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPECTED_BLOCKS = {
    "RB_MONTHLY_FIRST", "RB_MONTHLY_MID", "RB_QUARTERLY", "RB_WEEKLY", "RB_THRESHOLD_5", "RB_SIGNAL",
}


def test_블록_대조_대상이_빠지지_않았다(ts_schedules):
    assert {e["block_id"] for e in ts_schedules["schedules"]} == EXPECTED_BLOCKS


@pytest.mark.parametrize("block_id", sorted(EXPECTED_BLOCKS))
def test_블록별_일정과_목표_비중이_같다(block_id, ts_schedules, py_schedule_runs):
    ts = next(e for e in ts_schedules["schedules"] if e["block_id"] == block_id)
    candidates, skipped, py = py_schedule_runs[block_id]

    assert candidates == ts["candidates"], "일정(후보일)이 다르다"
    assert py["rebalance_dates"] == ts["rebalance_dates"], "실제 리밸런싱일이 다르다"
    if block_id == "RB_SIGNAL":
        # 신호형은 파이썬이 정한 날짜(= 대조군 날짜)를 TS 가 그대로 받는다. 지금 다시 정해도 같아야 한다
        assert skipped == ts["skipped_rebalance_dates"]
    else:
        assert skipped + py["skipped_rebalance_dates"] == ts["skipped_rebalance_dates"]
    assert len(ts["rebalance_dates"]) >= 2, "리밸런싱이 한 번뿐이면 대조할 것이 없다"

    worst = 0.0
    for py_d, ts_d in zip(py["decisions"], ts["control_decisions"], strict=True):
        assert py_d["date"] == ts_d["date"]
        for ticker, py_w in py_d["target"].items():
            worst = max(worst, abs(py_w - ts_d["target"][ticker]))
        py_caps = sorted((a["stage"], a["groupId"]) for a in py_d["capApplications"])
        ts_caps = sorted((a["stage"], a["groupId"]) for a in ts_d["capApplications"])
        assert py_caps == ts_caps, f"{py_d['date']} 에서 캡 적용이 다르다"
    assert worst < 1e-9, f"목표 비중이 어긋난다 — 최대 {worst:.3e}"


def test_블록별_최종_평가액이_가깝다(ts_schedules, py_schedule_runs):
    # 체결 엔진이 달라(vectorbt · 자체 체결) 평가액은 반올림만큼 갈린다. 목표 비중이 같으면 0.1% 안이다
    for entry in ts_schedules["schedules"]:
        py_final = py_schedule_runs[entry["block_id"]][2]["series"][-1]["equity"]
        assert abs(py_final / entry["final_equity"] - 1) < 1e-3, entry["block_id"]


def test_신호형_일정은_같은_입력에_같다(ts_schedules):
    # 재현성: 같은 입력으로 두 번 정한 신호형 리밸런싱 날짜 · 신호가 같다
    pd = pytest.importorskip("pandas")
    prices = pd.read_csv(SRC / "prices.csv", dtype={"ticker": str}, parse_dates=["date"])
    wide = prices.pivot(index="date", columns="ticker", values="close").ffill()
    wide.index = wide.index.strftime("%Y-%m-%d")
    holdings = [
        {"ticker": u["ticker"], "grade": u["grade"], "asset_group": u["asset_group"],
         "sector_group": u["sector_group"], "country_group": u["country_group"],
         "min_raw": u["weight_min_raw"], "max_raw": u["weight_max_raw"]}
        for u in ts_schedules["universe"]
    ]
    script = _vbt_script()
    weekly = ts_schedules["valuation_dates"]
    first = script.signal_rebalance(wide, holdings, ts_schedules["risk_level"], weekly)
    second = script.signal_rebalance(wide, holdings, ts_schedules["risk_level"], weekly)

    assert first["rebalance_dates"] == second["rebalance_dates"]
    assert [d["signals"] for d in first["decisions"]] == [d["signals"] for d in second["decisions"]]
    assert first["series"] == second["series"]
