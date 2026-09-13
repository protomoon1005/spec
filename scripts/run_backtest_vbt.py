"""파이썬(vectorbt) 백테스트를 돌리고 TS 러너 결과와 대조한다.

실행
  python scripts/run_backtest_vbt.py              # 돌리고 TS 와 비교
  python scripts/run_backtest_vbt.py --write      # data/backtest-result-py.json 도 저장
  RISK_LEVEL=3 python scripts/run_backtest_vbt.py # 성향 변경

두 구현이 일치하는지 보는 것이 이 스크립트의 요점이다. 서로 다른 언어와
엔진으로 짠 러너가 같은 입력에 같은 주문을 내면, "같은 입력에 같은 출력"이
말이 아니라 검증이 된다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest_py.policy import HARDCAP, caps_for, profile_for  # noqa: E402
from backtest_py.runner import monthly_first, run, run_buy_and_hold, weekly_dates  # noqa: E402

DATA = ROOT / "data"
MARKET_TICKER = "069500"
INITIAL = 10_000_000

# 데모 Spec 의 유니버스. scripts/run-backtest.ts 의 SPEC_UNIVERSE 와 같아야 한다.
SPEC_UNIVERSE = [
    ("069500", 0.05, 0.35),
    ("091160", 0.00, 0.25),
    ("360750", 0.05, 0.30),
    ("133690", 0.00, 0.25),
    ("273130", 0.10, 0.40),
    ("459580", 0.00, 0.30),
    ("132030", 0.00, 0.15),
]


def main() -> int:
    risk_level = int(os.environ.get("RISK_LEVEL", 4))
    profile = profile_for(risk_level)

    uni = pd.read_csv(DATA / "universe.csv", dtype={"ticker": str}).set_index("ticker")
    prices = pd.read_csv(DATA / "prices.csv", dtype={"ticker": str}, parse_dates=["date"])
    wide = prices.pivot(index="date", columns="ticker", values="close").ffill()
    wide.index = wide.index.strftime("%Y-%m-%d")

    holdings = []
    for ticker, lo, hi in SPEC_UNIVERSE:
        if ticker not in uni.index:
            print(f"  ! {ticker} 가 universe.csv 에 없다 — 건너뜀")
            continue
        row = uni.loc[ticker]
        holdings.append(
            {
                "ticker": ticker,
                "name": row["name"],
                "grade": row["risk_tag"],
                "asset_group": row["asset_group"],
                "sector_group": row["sector_group"],
                "country_group": row["country_group"],
                "min_raw": lo,
                "max_raw": hi,
            }
        )

    all_dates = wide[MARKET_TICKER].dropna().index.tolist()
    weekly = weekly_dates(all_dates)
    rebalance = monthly_first(weekly)

    print(f"성향        {profile['label']} (risk_level {risk_level})")
    print(f"유니버스    {len(holdings)}종목")
    print(f"평가 시점   {len(weekly)}개 ({weekly[0]} ~ {weekly[-1]})")
    print(f"리밸런싱    {len(rebalance)}회")
    print("vectorbt 실행중... (numba JIT 컴파일로 첫 실행이 느리다)", flush=True)

    strategy = run(wide, holdings, risk_level, weekly, rebalance, INITIAL, use_signals=True)
    control = run(wide, holdings, risk_level, weekly, rebalance, INITIAL, use_signals=False)
    market = run_buy_and_hold(wide[MARKET_TICKER], weekly, INITIAL)

    def summary(series: list[dict]) -> dict:
        s = pd.Series([p["equity"] for p in series], index=[p["date"] for p in series])
        years = (pd.Timestamp(series[-1]["date"]) - pd.Timestamp(series[0]["date"])).days / 365.25
        peak = s.cummax()
        return {
            "total": s.iloc[-1] / s.iloc[0] - 1,
            "cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1,
            "mdd": float((s / peak - 1).min()),
        }

    pc = lambda x: f"{x * 100:.1f}%"  # noqa: E731
    print()
    for label, series in [("전략", strategy["series"]), ("대조군", control["series"]), ("시장", market)]:
        m = summary(series)
        print(f"{label:5} 누적 {pc(m['total']):>8}  연환산 {pc(m['cagr']):>7}  MDD {pc(m['mdd']):>7}")
    caps_hit = sum(len(d["capApplications"]) for d in strategy["decisions"])
    print(f"그룹캡 적용 {caps_hit}회 / 리밸런싱 {len(strategy['decisions'])}회")

    # --- TS 결과와 대조 ---
    ts_path = DATA / "backtest-result.json"
    if ts_path.exists():
        ts = json.loads(ts_path.read_text(encoding="utf-8"))
        if ts["profile"]["risk_level"] != risk_level:
            print(f"\n(TS 결과는 risk_level {ts['profile']['risk_level']} 이라 대조 생략)")
        else:
            cmp = pd.DataFrame(
                {
                    "python": [p["equity"] for p in strategy["series"]],
                    "typescript": [p["strategy"] for p in ts["series"]],
                },
                index=[p["date"] for p in strategy["series"]],
            )
            cmp["차이%"] = (cmp["python"] - cmp["typescript"]) / cmp["typescript"] * 100
            print("\n=== TS 러너와 대조 ===")
            py_final = int(cmp["python"].iloc[-1])
            ts_final = int(cmp["typescript"].iloc[-1])
            print(f"최종 평가액   python {py_final:,}   typescript {ts_final:,}")
            print(f"최대 괴리 {cmp['차이%'].abs().max():.4f}%   평균 {cmp['차이%'].abs().mean():.4f}%")

            # 목표 비중도 대조한다. 평가액보다 이쪽이 더 엄격한 검증이다.
            diffs = []
            for pd_, td in zip(strategy["decisions"], ts["decisions"]):
                assert pd_["date"] == td["date"], f"리밸런싱 일정 불일치: {pd_['date']} vs {td['date']}"
                diffs.append(max(abs(pd_["target"][t] - td["target"][t]) for t in pd_["target"]))
            print(f"목표 비중 최대 괴리 {max(diffs) * 100:.6f}%p   (리밸런싱 {len(diffs)}회 전부 비교)")
            cmp.to_csv(DATA / "py-vs-ts.csv", encoding="utf-8")
            print("저장: data/py-vs-ts.csv")

    if "--write" in sys.argv:
        out = {
            "as_of": weekly[-1],
            "period_start": weekly[0],
            "initial": INITIAL,
            "profile": {"label": profile["label"], "risk_level": risk_level, "cash_min": profile["cash_min"]},
            "engine": "vectorbt",
            "hardcap_max_per_asset": HARDCAP["max_weight_per_asset"],
            "universe": [
                {
                    "ticker": h["ticker"], "name": h["name"], "grade": h["grade"],
                    "asset_group": h["asset_group"], "sector_group": h["sector_group"],
                    "country_group": h["country_group"],
                    "weight_min_raw": h["min_raw"], "weight_max_raw": h["max_raw"],
                }
                for h in holdings
            ],
            "caps": caps_for(risk_level),
            "series": [
                {"date": d, "strategy": s["equity"], "control": c["equity"], "market": m["equity"]}
                for d, s, c, m in zip(weekly, strategy["series"], control["series"], market)
            ],
            "decisions": strategy["decisions"],
            "control_decisions": control["decisions"],
        }
        (DATA / "backtest-result-py.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("저장: data/backtest-result-py.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
