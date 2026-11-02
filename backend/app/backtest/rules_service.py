# 조건형 전략서(v0.2) 백테스트를 DB 의 가격 · 종목 원장으로 돌린다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
# 가격은 저장소 함수로만 읽고 as_of 는 기간 끝(backtest.end)이다. 그 뒤의 값은 들어오지 않는다.
# 지표 준비용 과거 이력(최대 121거래일)은 기간 시작 전의 행을 그대로 쓴다. 스냅샷이 2021-11 부터다.
#
# 비교 기준(설계 문서 규칙 7)
#   - 같은 종목을 시작일에 자리 비율만큼 사서 끝까지 들고 있기
#   - KODEX 200(069500) 사서 들고 있기. 코스피 지수가 DB 에 없어 대신한다. 화면에 그렇게 밝힌다
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from app.backtest import rules_engine, taxes
from app.contracts.strategy_v0_2 import StrategyV0_2
from app.repositories import etf_master, price_daily

MARKET_PROXY = "069500"
MARKET_PROXY_NOTE = "코스피 지수 대신 KODEX 200(069500)을 사서 들고 있었을 때와 비교했습니다"
NOTES = (
    "판단은 그날 종가까지의 데이터로 하고, 체결은 다음 거래일 시가에 합니다",
    "기간 끝까지 들고 있는 종목은 팔지 않고 마지막 종가로 평가만 했습니다. 그래서 그 종목의 세금은 없습니다",
    "배당은 반영하지 않았습니다",
    "지금 상장된 종목만 있어서(상장폐지 종목 제외) 성과가 실제보다 좋게 나올 수 있습니다",
)


@dataclass(frozen=True)
class RulesBacktest:
    metrics: dict
    equity: list[tuple[date, float]]
    fills: list[dict]
    trades: list[dict]
    unfilled: list[dict]
    same_day_conflicts: list[dict]
    holdings_at_end: dict[str, int]
    benchmarks: dict[str, dict]
    names: dict[str, str]
    notes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def run_rules_backtest(spec: StrategyV0_2) -> RulesBacktest:
    tickers = spec.universe.held_tickers()
    if tickers is None:
        raise rules_engine.UnsupportedStrategy("종류로 고른 종목(selector)을 아직 풀지 않았다")
    subjects = _other_subjects(spec)
    records = etf_master.get_by_tickers(sorted(set(tickers) | subjects | {MARKET_PROXY}))
    unknown = sorted(set(tickers) - set(records))
    if unknown:
        raise ValueError(f"종목 원장에 없는 종목: {unknown}")

    end = spec.backtest.end
    bars = price_daily.get_bar_history(sorted(set(tickers) | subjects | {MARKET_PROXY}), as_of=end)
    calendar = price_daily.get_trade_dates(start=spec.backtest.start, as_of=end)
    categories = {
        t: taxes.tax_category(
            taxes.InstrumentTaxInfo(
                kind=records[t].kind,
                asset_group_id=records[t].asset_group_id,
                country_group_id=records[t].country_group_id,
                is_leveraged=records[t].is_leveraged,
            )
        )
        for t in tickers
    }

    result = rules_engine.run_rules(spec, bars, calendar, categories)
    fractions = rules_engine.slot_fractions(spec, sorted(tickers))
    capital = spec.backtest.capital
    hold_same = rules_engine.run_buy_and_hold(fractions, bars, calendar, capital)
    hold_market = rules_engine.run_buy_and_hold({MARKET_PROXY: 1.0}, bars, calendar, capital)

    metrics = rules_engine.summarize(result.equity)
    metrics.update({
        "fills": len(result.fills),
        "round_trips": len(result.trades),
        "win_rate": (
            sum(1 for t in result.trades if t["pnl"] > 0) / len(result.trades) if result.trades else None
        ),
    })
    notes = (*NOTES, MARKET_PROXY_NOTE)
    if not result.fills:
        notes = ("이 기간에 조건이 한 번도 맞지 않아 거래가 없었습니다",) + notes
    return RulesBacktest(
        metrics=metrics,
        equity=result.equity,
        fills=result.fills,
        trades=result.trades,
        unfilled=result.unfilled,
        same_day_conflicts=result.same_day_conflicts,
        holdings_at_end=result.holdings_at_end,
        benchmarks={
            "hold_same": {"equity": hold_same, "metrics": rules_engine.summarize(hold_same)},
            "hold_market_proxy": {"equity": hold_market, "metrics": rules_engine.summarize(hold_market)},
        },
        names={t: r.name for t, r in records.items()},
        notes=notes,
    )


def _other_subjects(spec: StrategyV0_2) -> set[str]:
    # 다른 종목을 보는 조건(of = 종목코드)의 종목. 그 종목의 가격도 읽어야 한다
    out: set[str] = set()
    for rule in spec.rules:
        for cond in (rule.entry, rule.exit):
            for operand in rules_engine.operands(cond):
                if operand.of not in ("self", *rules_engine.UNSUPPORTED_SUBJECTS):
                    out.add(operand.of)
    return out



def to_run_record(result: RulesBacktest) -> tuple[dict, dict]:
    # backtest_metrics 칸(cagr · mdd · win_rate · benchmark_cagr)과 window_results(JSONB)에 넣을 모양.
    # sharpe · sortino 는 v0.2 에서 내지 않는다(비워 둔다)
    market = result.benchmarks["hold_market_proxy"]["metrics"]
    metrics = {"cagr": result.metrics.get("cagr"), "mdd": result.metrics.get("mdd"),
               "win_rate": result.metrics.get("win_rate"), "benchmark_cagr": market.get("cagr")}
    by_day: dict = {}
    for key, equity in (("strategy", result.equity),
                        ("hold_same", result.benchmarks["hold_same"]["equity"]),
                        ("market_proxy", result.benchmarks["hold_market_proxy"]["equity"])):
        for day, value in equity:
            by_day.setdefault(day.isoformat(), {"date": day.isoformat()})[key] = round(value, 2)
    window = {
        "engine": "rules_v0_2",
        "summary": {"strategy": result.metrics, "hold_same": result.benchmarks["hold_same"]["metrics"],
                    "market_proxy": market},
        "series": list(by_day.values()),
        "fills": result.fills,
        "trades": result.trades,
        "unfilled": result.unfilled,
        "same_day_conflicts": result.same_day_conflicts,
        "holdings_at_end": result.holdings_at_end,
        "names": result.names,
        "notes": list(result.notes),
    }
    return metrics, window
