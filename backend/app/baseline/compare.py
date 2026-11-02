# 화면의 "LLM 코드 생성과 비교"(docs/v0.2-plan.md 11단계). 현서 님 demo 의 baseline_compare 를 옮겼다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
# 저장된 전략서 하나에 대해
#   1 우리 쪽: 전략서를 우리 엔진으로 돌린 체결과 평가액
#   2 비교 대상: 같은 문장을 LLM 이 파이썬 코드로 직접 짜고(codegen), 샌드박스에서 돌려 받은 주문을
#     우리 엔진과 같은 비용 · 세금으로 체결한 결과(orders)
# 를 나란히 돌려준다. 종목은 문장에 나온 종목만 넘긴다(9c 와 같은 A안). 기간은 전략서의 기간이다.
# 평가 스크립트(scripts/evaluate_baseline.py)와 같은 함수를 쓴다.
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from app.backtest import rules_engine, taxes
from app.backtest.rules_service import run_rules_backtest
from app.baseline import codegen, orders, sandbox
from app.contracts.strategy_v0_2 import StrategyV0_2
from app.m1 import compile_v0_2
from app.repositories import etf_master, price_daily

CACHE = Path("/tmp/baseline-cache")
DEFAULT_START, DEFAULT_END = date(2023, 1, 1), date(2025, 12, 31)


def frames(tickers: list[str], as_of: date) -> dict[str, pd.DataFrame]:
    # 비교 대상에 넘길 일봉. 인덱스는 거래일, 열은 Open · High · Low · Close · Volume
    bars = price_daily.get_bar_history(tickers, as_of=as_of)
    out = {}
    for t, rows in bars.items():
        frame = pd.DataFrame(rows)
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("trade_date")))
        frame.columns = [c.capitalize() for c in frame.columns]
        out[t] = frame[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return out


def tax_category(record) -> str:
    return taxes.tax_category(
        taxes.InstrumentTaxInfo(
            kind=record.kind,
            asset_group_id=record.asset_group_id,
            country_group_id=record.country_group_id,
            is_leveraged=record.is_leveraged,
        )
    )


def sentence_tickers(text: str) -> tuple[list[str], dict]:
    # 문장에서 우리 종목 이름 찾기로 찾은 종목(가격이 있는 것만)과 원장 기록
    found = sorted({s.meaning["ticker"] for s in compile_v0_2.find(text).by_kind("instrument")})
    records = etf_master.get_by_tickers(found)
    return [t for t in found if t in records], records


def compare(text: str, spec: StrategyV0_2, client) -> dict:
    start, end, capital = spec.backtest.start, spec.backtest.end, float(spec.backtest.capital)
    ours = run_rules_backtest(spec)
    tickers, records = sentence_tickers(text)
    prices = frames(tickers, max(end, DEFAULT_END))
    tickers = [t for t in tickers if t in prices]
    names = {**ours.names, **{t: records[t].name for t in tickers}}
    listing = [(t, records[t].name, "ETF" if records[t].kind == "etf" else "주식") for t in tickers]

    prompt = codegen.build_prompt(listing, DEFAULT_START.isoformat(), DEFAULT_END.isoformat())
    gen = codegen.generate(text, prompt, client, cache_dir=CACHE)
    out: dict = {
        "outcome": gen.kind,  # code · clarify · reject · error
        "message": gen.message,
        "code": gen.code,
        "raw": None if gen.code else gen.raw,
        "seconds": round(gen.elapsed_s, 1),
        "cached": gen.cached,
        "tickers": tickers,
        "period": [start.isoformat(), end.isoformat()],
        "ours": {"total_return": ours.metrics.get("total_return"), "orders": _rows(ours.fills, names)},
        "base": None,
        "run_error": None,
        "same_orders": None,
        "series": None,
    }
    if gen.kind != "code":
        return out
    run = sandbox.run_code(gen.code, prices, start.isoformat(), end.isoformat(), known=set(tickers))
    if not run.ok:
        out["run_error"] = f"{run.stage}: {run.error}"
        return out
    clean = [{**o, "weight": min(max(float(o["weight"]), 0.0), 1.0)} for o in run.orders]
    categories = {t: tax_category(records[t]) for t in tickers}
    fills = orders.execute_orders(clean, prices, start, end, capital, categories)
    calendar = [d for d, _ in ours.equity]
    base_equity = orders.equity_curve(fills, prices, calendar, capital)
    out["base"] = {
        "total_return": rules_engine.summarize(base_equity).get("total_return") if base_equity else None,
        "orders": _rows(fills, names),
        "ignored": len(run.orders) - len(fills),
    }
    out["same_orders"] = orders.same_fills(ours.fills, fills)
    out["series"] = [
        {"date": d.isoformat(), "ours": round(a, 2), "base": round(b, 2)}
        for (d, a), (_, b) in zip(ours.equity, base_equity, strict=True)
    ]
    return out


def _rows(fills: list[dict], names: dict[str, str]) -> list[dict]:
    return [
        {
            "date": f["date"].isoformat(),
            "ticker": f["ticker"],
            "name": names.get(f["ticker"], f["ticker"]),
            "side": f["side"],
            "weight": round(float(f.get("target_weight", 0.0)), 4),
        }
        for f in fills
    ]
