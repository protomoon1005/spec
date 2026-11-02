"""결정론적 백테스트 엔진 v0.2 (docs/strategy-spec-v0.2.md). LLM 미사용, random 미사용.

판단과 체결
- t일 종가까지의 데이터로 판단 → t+1일 시가에 체결. 마지막 날 판단은 체결할 날이 없어 unfilled_signals 에만 남긴다.
- 체결가: 매수 = 시가 × (1 + 체결 오차), 매도 = 시가 × (1 - 체결 오차). 수수료는 매수·매도 금액에 각각.
- 1주 단위로만 산다. 남는 돈은 현금(이자 없음).
- 같은 날 체결은 팔기 먼저, 그다음 사기(종목 순서대로).

비중 (1/N 자리)
- 자리 크기 = 종목 비중(사용자 비중, 없으면 1/N)을 종목당 최대 비중(max_weight)으로 자른 값.
- 사는 날: 그날 시가 기준 총자산 × 자리 크기만큼, 남은 현금 안에서만 산다. 새 종목을 살 때 기존 종목은 팔지 않는다.
- 조건이 맞지 않은 종목의 자리는 현금으로 남는다.

사고파는 조건이 있는 전략 (rules)
- 매일 판단한다. 갖고 있으면 팔 조건만 실행하고, 없으면 살 조건만 실행한다.
- 같은 날 살 조건과 팔 조건이 둘 다 참이면 팔기 우선: 갖고 있으면 팔고, 없으면 사지 않는다 (conflicts 에 기록).
- 산 뒤 전량 매도만 한다. 리밸런싱 주기가 있으면 그날 갖고 있는 종목을 자기 자리 비중으로 다시 맞춘다.
- entry_return = (종가 - 평균 매입 체결가) / 평균 매입 체결가 × 100. 체결 오차는 포함, 수수료는 제외.
- holding_days = 산 날(첫 매수 체결일)부터 지난 판단 달력 거래일 수. 산 날 종가 = 0.

조건이 없는 전략 (배분만)
- 기간 첫 거래일 시가에 비중대로 산다(아무 데이터도 보지 않으므로 미래 참조가 아니다).
- 리밸런싱 날 종가로 판단해 다음 거래일 시가에 비중을 다시 맞춘다.
- weekly(그 주 마지막 거래일)는 다음 거래일 날짜를 본다 — 거래소 휴장일은 미리 공표되는 달력이라 가격 정보는 쓰지 않는다.

세금 (config/tax_rates.yaml, 매도 체결일의 세율)
- 주식: 매도금액 × (거래세 + 농특세). ETF: 거래세 없음.
- 그 밖의 ETF: (매도 체결가 - 평균 매입 체결가) × 수량 × 15.4%, 이익일 때만. 다른 거래와 상계하지 않는다.
- 배당은 반영하지 않는다 (docs/demo-scope.md).

비교 기준
- kospi: 코스피 지수(KS11)를 첫날 시가에 사서 들고 있었을 때. 비용 없음 (지수는 사고팔 수 없음).
- buy_hold: 같은 종목을 자리 비중대로 첫날 시가에 사서 끝까지 들고 있기. 매수 비용 포함, 매도하지 않으므로 매도 비용·세금 없음.

기간 끝에 남은 보유분은 강제로 팔지 않고 마지막 종가로 평가만 한다 (매도 비용·세금 미반영).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core.data import frame_hash, load_ohlcv
from core.dictionary import BENCHMARK_SYMBOL, INDEX_SYMBOLS, INDICATORS, US_SYMBOLS, tax_category
from core.indicators import align_to_calendar, align_us_to_kr, indicator_series
from core.schema import AllOf, AnyOf, Compare, Cond, Operand, Strategy, Value, iter_compares
from core.tax import gain_tax_rate, sell_tax_rate

_EPS = 1e-9


# ---------------------------------------------------------------- 데이터

class _Frames:
    """심볼 → OHLCV. data 를 주면 그것만 쓴다(테스트). 없으면 data/cache (없으면 FDR 로 한 번 받음)."""

    def __init__(self, data: Optional[dict[str, pd.DataFrame]]):
        self.data = data
        self.snapshot: dict[str, dict] = {}
        self._cache: dict[str, Optional[pd.DataFrame]] = {}

    def get(self, symbol: str, required: bool = True) -> Optional[pd.DataFrame]:
        if symbol in self._cache:
            return self._cache[symbol]
        df: Optional[pd.DataFrame] = None
        if self.data is not None:
            if symbol in self.data:
                df = self.data[symbol]
                self.snapshot[symbol] = {"source": "in-memory", "sha256": frame_hash(df)}
        else:
            try:
                df, meta = load_ohlcv(symbol)
                self.snapshot[symbol] = {"source": meta["source"], "fetched_at": meta["fetched_at"],
                                         "sha256": meta["sha256"]}
            except Exception:
                if required:
                    raise
        if df is None and required:
            raise ValueError(f"{symbol}: 가격 데이터가 없습니다.")
        self._cache[symbol] = df
        return df


def _symbol(of: str, ticker: str) -> str:
    if of == "self":
        return ticker
    return INDEX_SYMBOLS.get(of, of)


# ---------------------------------------------------------------- 조건 계산

class _Market:
    """(지표, 기간, 심볼) → 판단 달력에 맞춘 (오늘 값, 전날 값) 배열. 한 번만 계산한다."""

    def __init__(self, frames: _Frames, calendar: pd.DatetimeIndex):
        self.frames = frames
        self.calendar = calendar
        self._memo: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def series(self, op: Operand, ticker: str) -> tuple[np.ndarray, np.ndarray]:
        sym = _symbol(op.of, ticker)
        key = (op.indicator, op.window, sym)
        if key not in self._memo:
            df = self.frames.get(sym)
            if op.indicator == "vix" or sym in US_SYMBOLS:
                s = align_us_to_kr(df["Close"].astype(float), self.calendar)
            else:
                s = align_to_calendar(indicator_series(df, op.indicator, op.window), self.calendar)
            cur = s.to_numpy(dtype=float)
            prev = np.concatenate([[np.nan], cur[:-1]])
            self._memo[key] = (cur, prev)
        return self._memo[key]


@dataclass
class _Pos:
    shares: int = 0
    avg_px: float = 0.0          # 평균 매입 체결가 (체결 오차 포함, 수수료 제외)
    entry_i: Optional[int] = None


def _operand_values(op, ticker, i, market: _Market, closes: np.ndarray, pos: Optional[_Pos]) -> tuple[float, float]:
    """(오늘 값, 전날 값). 계산할 수 없으면 NaN."""
    if isinstance(op, Value):
        return op.value, op.value
    meta = INDICATORS[op.indicator]
    if meta.get("position"):
        if pos is None:
            raise ValueError(f"{op.indicator} 는 보유 상태로 계산하는 지표라 여기서 쓸 수 없습니다.")
        if pos.shares <= 0 or pos.entry_i is None:
            return math.nan, math.nan
        held_prev = pos.entry_i <= i - 1
        if op.indicator == "holding_days":
            return float(i - pos.entry_i), (float(i - 1 - pos.entry_i) if held_prev else math.nan)
        cur = (closes[i] - pos.avg_px) * 100.0 / pos.avg_px
        prev = (closes[i - 1] - pos.avg_px) * 100.0 / pos.avg_px if held_prev and i > 0 else math.nan
        return cur, prev
    cur, prev = market.series(op, ticker)
    return cur[i], prev[i]


def _compare(c: Compare, ticker, i, market, closes, pos) -> bool:
    l, pl = _operand_values(c.left, ticker, i, market, closes, pos)
    r, pr = _operand_values(c.right, ticker, i, market, closes, pos)
    if math.isnan(l) or math.isnan(r):
        return False
    if c.op == "<=":
        return l <= r
    if c.op == "<":
        return l < r
    if c.op == ">=":
        return l >= r
    if c.op == ">":
        return l > r
    if math.isnan(pl) or math.isnan(pr):
        return False
    if c.op == "cross_above":
        return pl <= pr and l > r
    return pl >= pr and l < r          # cross_below


def _eval(cond: Cond, ticker, i, market, closes, pos) -> bool:
    if isinstance(cond, Compare):
        return _compare(cond, ticker, i, market, closes, pos)
    if isinstance(cond, AllOf):
        return all(_eval(c, ticker, i, market, closes, pos) for c in cond.all)
    return any(_eval(c, ticker, i, market, closes, pos) for c in cond.any)


def condition_series(cond: Cond, ticker: str, data: dict[str, pd.DataFrame],
                     calendar: pd.DatetimeIndex) -> pd.Series:
    """시장 데이터만 쓰는 조건을 달력 전체에 대해 계산 (테스트·5단계 시험 실행용).
    entry_return·holding_days 처럼 보유 상태가 필요한 조건은 ValueError."""
    market = _Market(_Frames(data), calendar)
    closes = np.full(len(calendar), np.nan)
    return pd.Series([_eval(cond, ticker, i, market, closes, None) for i in range(len(calendar))],
                     index=calendar, dtype=bool)


# ---------------------------------------------------------------- 리밸런싱 날

def _rebalance_days(period: Optional[str], full: pd.DatetimeIndex) -> np.ndarray:
    """full(판단 달력 전체) 각 날짜가 리밸런싱 판단일인지. 전날·다음 날 날짜만 본다."""
    n = len(full)
    out = np.zeros(n, dtype=bool)
    if period is None or period == "drift_5pp":
        return out
    for k in range(1, n):
        d, p = full[k], full[k - 1]
        new_month = (d.year, d.month) != (p.year, p.month)
        if period == "monthly_first":
            out[k] = new_month
        elif period == "quarterly":
            out[k] = new_month and d.month in (1, 4, 7, 10)
        elif period == "monthly_mid":
            out[k] = d.day >= 15 and (new_month or p.day < 15)
        elif period == "weekly":
            out[k] = k + 1 < n and full[k + 1].isocalendar()[:2] != d.isocalendar()[:2]
    return out


# ---------------------------------------------------------------- 결과

@dataclass
class BacktestResult:
    equity: pd.Series                       # 일별 총자산 (현금 + 보유 종가 평가)
    cash: pd.Series
    positions: pd.DataFrame                 # 일별 보유 수량 (장 마감 기준)
    benchmarks: dict                        # {"kospi": Series | None, "buy_hold": Series}
    fills: list[dict]                       # 체결 하나하나
    trades: list[dict]                      # 보유 0 → 보유 → 0 한 번 (미청산 포함, exit_date=None)
    conflicts: list[dict]                   # 같은 날 살 조건·팔 조건이 둘 다 참이었던 날
    metrics: dict
    unfilled_signals: list[dict]
    spec_hash: str
    data_snapshot: dict
    result_hash: str
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 화면용: 종목별 {"close": 종가 Series, "indicators": [{"indicator", "window", "of", "values": Series}]}.
    # 조건 계산에 쓴 값(_Market 메모)을 그대로 내보낸다. 실행·해시에는 쓰지 않는다.
    series: dict = field(default_factory=dict)


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def spec_hash(spec: Strategy) -> str:
    """실행에 영향을 주는 칸만 해시 (defaults_applied 제외)."""
    return _sha(_canon(spec.model_dump(mode="json", exclude={"defaults_applied"})))


def _r(x, nd: int = 6):
    return None if x is None else round(float(x), nd)


def _mdd(eq: Optional[pd.Series]) -> Optional[float]:
    if eq is None or not len(eq):
        return None
    return float((eq / eq.cummax() - 1).min())


def slot_weights(spec: Strategy) -> list[float]:
    """종목별 자리 크기. 사용자 비중이 있으면 그대로, 없으면 1/N. 종목당 최대 비중으로 자른다."""
    n = len(spec.assets)
    ws = [a.weight if a.weight is not None else 1.0 / n for a in spec.assets]
    if spec.max_weight is not None:
        ws = [min(w, spec.max_weight) for w in ws]
    return ws


# ---------------------------------------------------------------- 엔진

def run_backtest(spec: Strategy, data: Optional[dict[str, pd.DataFrame]] = None,
                 instruments: Optional[dict[str, str]] = None) -> BacktestResult:
    """전략서 실행. data 를 주면 그 OHLCV 만 쓴다(키: 종목코드, KS200·KQ11·VIX·KS11).
    instruments: 종목코드 → 세율 분류(KOSPI_STOCK 등). 없으면 data/tickers.json 에서 찾고, 없으면 ValueError.
    검사(core.validate)를 통과한 전략서를 넣는 것이 전제다."""
    if spec.rules is None and spec.rebalance is None:
        raise ValueError("조건이 없는 전략은 리밸런싱 주기가 있어야 합니다.")
    rules = spec.rules
    bt = spec.backtest
    fee, slip = bt.commission_rate, bt.slippage_rate
    tickers = [a.ticker for a in spec.assets]
    if len(set(tickers)) != len(tickers):
        raise ValueError("같은 종목이 두 번 들어 있습니다.")
    weights = slot_weights(spec)
    notes: list[str] = []
    warnings: list[str] = []

    categories: dict[str, str] = {}
    for t in tickers:
        cat = (instruments or {}).get(t) or tax_category(t)
        if cat is None:
            raise ValueError(f"{t}: 세율 분류를 알 수 없습니다 (data/tickers.json 에 없음). 짐작해서 세율을 쓰지 않습니다.")
        categories[t] = cat

    frames = _Frames(data)
    start, end = pd.Timestamp(bt.start), pd.Timestamp(bt.end)
    dfs = {t: frames.get(t) for t in tickers}
    for t, df in dfs.items():
        if not ((df.index >= start) & (df.index <= end)).any():
            raise ValueError(f"{t}: {bt.start}~{bt.end} 구간 가격이 없습니다 (NO_DATA).")
    # 판단 달력: 종목들의 거래일 합집합. 지표·전날 값은 기간 이전 이력까지 포함해 계산한다.
    full = pd.DatetimeIndex(sorted(set().union(*[set(df.index[df.index <= end]) for df in dfs.values()])))
    first = int(full.searchsorted(start, side="left"))
    market = _Market(frames, full)
    for t in tickers:   # 필요한 데이터를 미리 읽어 누락을 먼저 드러낸다
        if rules is not None:
            for _, c in [*iter_compares(rules.entry, "entry"), *iter_compares(rules.exit, "exit")]:
                for op in (c.left, c.right):
                    if isinstance(op, Operand) and not INDICATORS[op.indicator].get("position"):
                        market.series(op, t)

    n_all = len(full)
    opens = {t: dfs[t]["Open"].reindex(full).to_numpy(dtype=float) for t in tickers}      # 거래 없는 날 NaN
    closes = {t: align_to_calendar(dfs[t]["Close"].astype(float), full).to_numpy(dtype=float) for t in tickers}
    rebal = _rebalance_days(spec.rebalance, full)
    ds = [d.strftime("%Y-%m-%d") for d in full]

    cash = float(bt.initial_cash)
    pos = {t: _Pos() for t in tickers}
    open_trade: dict[str, dict] = {}
    fills: list[dict] = []
    trades: list[dict] = []
    conflicts: list[dict] = []
    pending: list[dict] = []
    eq_rows, cash_rows, pos_rows = [], [], []

    def price_now(t: str, i: int) -> float:
        o = opens[t][i]
        return o if not math.isnan(o) else closes[t][i - 1] if i > 0 else closes[t][i]

    def buy(t: str, i: int, budget: float, reason: str, signal_date):
        nonlocal cash
        px = opens[t][i] * (1 + slip)
        budget = min(budget, cash)
        n = int(math.floor(budget / (px * (1 + fee)) + _EPS))
        if n <= 0:
            return
        gross = n * px
        comm = gross * fee
        cash -= gross + comm
        p = pos[t]
        if p.shares == 0:
            p.entry_i, p.avg_px = i, 0.0
            open_trade[t] = {"ticker": t, "entry_signal_date": signal_date, "entry_date": ds[i],
                             "entry_price": px, "shares": n, "_cost": 0.0, "_proceeds": 0.0}
        p.avg_px = (p.avg_px * p.shares + px * n) / (p.shares + n)
        p.shares += n
        open_trade[t]["_cost"] += gross + comm
        fills.append({"date": ds[i], "signal_date": signal_date, "ticker": t, "side": "buy", "reason": reason,
                      "shares": n, "price": _r(px, 6), "commission": _r(comm), "tax": 0.0})

    def sell(t: str, i: int, n: int, reason: str, signal_date):
        nonlocal cash
        p = pos[t]
        n = min(n, p.shares)
        if n <= 0:
            return
        px = opens[t][i] * (1 - slip)
        gross = n * px
        comm = gross * fee
        day = full[i]
        tax = gross * sell_tax_rate(categories[t], day)
        g_rate = gain_tax_rate(categories[t], day)
        if g_rate:
            tax += max((px - p.avg_px) * n, 0.0) * g_rate
        cash += gross - comm - tax
        p.shares -= n
        tr = open_trade[t]
        tr["_proceeds"] += gross - comm - tax
        fills.append({"date": ds[i], "signal_date": signal_date, "ticker": t, "side": "sell", "reason": reason,
                      "shares": n, "price": _r(px, 6), "commission": _r(comm), "tax": _r(tax)})
        if p.shares == 0:
            tr.update({"exit_signal_date": signal_date, "exit_date": ds[i], "exit_price": px,
                       "exit_reason": reason, "holding_days": i - p.entry_i})
            _close_trade(tr, 0.0)
            trades.append(tr)
            del open_trade[t]
            p.entry_i, p.avg_px = None, 0.0

    def rebalance_to(targets: dict[str, float], i: int, reason: str, signal_date):
        eq_open = cash + sum(pos[t].shares * price_now(t, i) for t in tickers)
        tradable = {t: not math.isnan(opens[t][i]) for t in targets}
        for t, w in targets.items():          # 넘치는 쪽 먼저 판다
            if not tradable[t]:
                continue
            excess = pos[t].shares * opens[t][i] - eq_open * w
            if excess > 0:
                sell(t, i, int(math.floor(excess / opens[t][i] + _EPS)), reason, signal_date)
        for t, w in targets.items():
            if not tradable[t]:
                continue
            deficit = eq_open * w - pos[t].shares * opens[t][i]
            if deficit > 0:
                buy(t, i, deficit, reason, signal_date)

    if rules is None:   # 배분만: 첫날 시가에 비중대로
        pending.append({"kind": "rebalance", "reason": "initial", "signal_date": None, "assets": list(tickers)})

    for i in range(first, n_all):
        # ---- 1) 장 시작: 전날 판단을 오늘 시가로 체결 (팔기 먼저)
        carry: list[dict] = []
        if pending:
            for o in [o for o in pending if o["kind"] == "sell"]:
                if math.isnan(opens[o["ticker"]][i]):
                    carry.append(o)
                else:
                    sell(o["ticker"], i, pos[o["ticker"]].shares, "exit", o["signal_date"])
            for o in [o for o in pending if o["kind"] == "rebalance"]:
                rebalance_to({t: weights[tickers.index(t)] for t in o["assets"]}, i, o["reason"], o["signal_date"])
            buys = [o for o in pending if o["kind"] == "buy"]
            if buys:
                eq_open = cash + sum(pos[t].shares * price_now(t, i) for t in tickers)
                for o in buys:
                    t = o["ticker"]
                    if math.isnan(opens[t][i]):
                        carry.append(o)
                    elif pos[t].shares == 0:
                        buy(t, i, eq_open * weights[tickers.index(t)], "entry", o["signal_date"])
        pending = carry

        # ---- 2) 장 마감: 오늘 종가까지로 판단 → 내일 시가 주문
        pending_tickers = {o.get("ticker") for o in pending}
        if rules is not None:
            for k, t in enumerate(tickers):
                if t in pending_tickers:
                    continue
                p = pos[t]
                ent = rules.entry is not None and _eval(rules.entry, t, i, market, closes[t], p)
                ext = rules.exit is not None and _eval(rules.exit, t, i, market, closes[t], p)
                if ent and ext:
                    conflicts.append({"date": ds[i], "ticker": t, "held": p.shares > 0})
                if p.shares > 0 and ext:
                    pending.append({"kind": "sell", "ticker": t, "signal_date": ds[i]})
                elif p.shares == 0 and ent and not ext:
                    pending.append({"kind": "buy", "ticker": t, "signal_date": ds[i]})
            held = [t for t in tickers if pos[t].shares > 0
                    and not any(o.get("ticker") == t and o["kind"] == "sell" for o in pending)]
            if held and _is_rebalance_day(spec, rebal, i, first, tickers, weights, pos, closes, cash, held):
                pending.append({"kind": "rebalance", "reason": "rebalance", "signal_date": ds[i], "assets": held})
        elif _is_rebalance_day(spec, rebal, i, first, tickers, weights, pos, closes, cash, tickers):
            pending.append({"kind": "rebalance", "reason": "rebalance", "signal_date": ds[i], "assets": list(tickers)})

        value = sum(pos[t].shares * closes[t][i] for t in tickers if pos[t].shares)
        eq_rows.append(cash + value)
        cash_rows.append(cash)
        pos_rows.append([pos[t].shares for t in tickers])

    cal = full[first:]
    equity = pd.Series(eq_rows, index=cal, name="equity")
    cash_s = pd.Series(cash_rows, index=cal, name="cash")
    positions = pd.DataFrame(pos_rows, index=cal, columns=tickers)

    unfilled = []
    for o in pending:
        if o["kind"] == "rebalance":
            unfilled += [{"ticker": t, "side": "rebalance", "signal_date": o["signal_date"]} for t in o["assets"]]
        else:
            unfilled.append({"ticker": o["ticker"], "side": o["kind"], "signal_date": o["signal_date"]})

    last = n_all - 1
    for t, tr in open_trade.items():
        tr.update({"exit_signal_date": None, "exit_date": None, "exit_price": None, "exit_reason": "open",
                   "holding_days": last - pos[t].entry_i})
        _close_trade(tr, pos[t].shares * closes[t][last])
        trades.append(tr)
    trades.sort(key=lambda x: (x["entry_date"], x["ticker"]))
    for tr in trades:
        for k in ("entry_price", "exit_price", "pnl", "return_pct"):
            tr[k] = _r(tr[k])

    benchmarks = {"kospi": _kospi_benchmark(frames, cal, bt.initial_cash, notes),
                  "buy_hold": _buy_hold(tickers, weights, opens, closes, first, n_all, cal, bt)}
    metrics = _metrics(equity, benchmarks, trades, fills, bt.initial_cash)

    if conflicts:
        warnings.append(f"같은 날 살 조건과 팔 조건이 함께 참인 날이 {len(conflicts)}번 있었습니다. 그날은 팔기를 우선했습니다.")
    if rules is not None and not any(f["side"] == "buy" for f in fills):
        warnings.append("이 기간에 한 번도 사지 않습니다.")
    notes.append("배당 수익은 반영하지 않았습니다 (데모).")
    notes.append("상장폐지 종목을 넣지 않아(생존 편향) 성과가 실제보다 좋게 나올 수 있습니다.")
    if any(c == "ETF_OTHER" for c in categories.values()):
        notes.append("그 밖의 ETF 차익세는 과표기준가 대신 실제 차익 × 15.4% 로 계산했습니다 (실제보다 같거나 조금 많음).")

    series = {t: {"close": pd.Series(closes[t][first:], index=cal, name=t),
                  "indicators": _used_indicators(rules, t, market, first, cal)} for t in tickers}

    s_hash = spec_hash(spec)
    payload = {"spec_hash": s_hash, "fills": fills, "trades": trades, "unfilled": unfilled,
               "equity": [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in equity.items()]}
    return BacktestResult(
        equity=equity, cash=cash_s, positions=positions, benchmarks=benchmarks, fills=fills, trades=trades,
        conflicts=conflicts, metrics=metrics, unfilled_signals=unfilled, spec_hash=s_hash,
        data_snapshot=dict(sorted(frames.snapshot.items())), result_hash=_sha(_canon(payload)),
        notes=notes, warnings=warnings, series=series,
    )


def _used_indicators(rules, ticker: str, market: _Market, first: int, cal: pd.DatetimeIndex) -> list[dict]:
    """조건에 쓴 시장 지표의 (오늘) 값. 이미 계산해 둔 메모를 꺼낼 뿐 다시 계산하지 않는다."""
    if rules is None:
        return []
    out, seen = [], set()
    for _, c in [*iter_compares(rules.entry, "entry"), *iter_compares(rules.exit, "exit")]:
        for op in (c.left, c.right):
            if not isinstance(op, Operand) or INDICATORS[op.indicator].get("position"):
                continue
            key = (op.indicator, op.window, op.of)
            if key in seen:
                continue
            seen.add(key)
            cur, _ = market.series(op, ticker)
            out.append({"indicator": op.indicator, "window": op.window, "of": op.of,
                        "values": pd.Series(cur[first:], index=cal)})
    return out


def _close_trade(tr: dict, mark_value: float) -> None:
    cost, proceeds = tr.pop("_cost"), tr.pop("_proceeds")
    tr["pnl"] = proceeds + mark_value - cost
    tr["return_pct"] = tr["pnl"] / cost if cost else None


def _is_rebalance_day(spec, rebal, i, first, tickers, weights, pos, closes, cash, assets) -> bool:
    if i == first:            # 첫날은 기간 시작 매수가 대신한다
        return False
    if spec.rebalance == "drift_5pp":
        eq = cash + sum(pos[t].shares * closes[t][i] for t in tickers)
        return eq > 0 and any(abs(pos[t].shares * closes[t][i] / eq - weights[tickers.index(t)]) >= 0.05 - _EPS
                              for t in assets)
    return bool(rebal[i])


def _kospi_benchmark(frames: _Frames, cal: pd.DatetimeIndex, cash0: float, notes: list[str]) -> Optional[pd.Series]:
    ks = frames.get(BENCHMARK_SYMBOL, required=False)
    if ks is None:
        notes.append("코스피 지수(KS11) 데이터가 없어 코스피 비교를 하지 않았습니다.")
        return None
    after = ks.index[ks.index >= cal[0]]
    if not len(after):
        notes.append("코스피 지수(KS11) 데이터가 기간을 덮지 않아 코스피 비교를 하지 않았습니다.")
        return None
    o0 = float(ks["Open"].loc[after[0]])
    s = align_to_calendar(ks["Close"].astype(float), cal) * (cash0 / o0)
    return s.rename("kospi")


def _buy_hold(tickers, weights, opens, closes, first, n_all, cal, bt) -> pd.Series:
    cash = float(bt.initial_cash)
    shares = {}
    for t, w in zip(tickers, weights):
        j = next((k for k in range(first, n_all) if not math.isnan(opens[t][k])), None)
        if j is None:
            continue
        px = opens[t][j] * (1 + bt.slippage_rate)
        n = int(math.floor(bt.initial_cash * w / (px * (1 + bt.commission_rate)) + _EPS))
        cash -= n * px * (1 + bt.commission_rate)
        shares[t] = (j, n)
    vals = np.full(n_all - first, cash)
    for t, (j, n) in shares.items():
        part = np.array([n * closes[t][k] if k >= j else n * opens[t][j] * (1 + bt.slippage_rate) * (1 + bt.commission_rate)
                         for k in range(first, n_all)])
        vals = vals + part
    return pd.Series(vals, index=cal, name="buy_hold")


def _metrics(equity, benchmarks, trades, fills, cash0) -> dict:
    closed = [t for t in trades if t["exit_date"] is not None]
    days = (equity.index[-1] - equity.index[0]).days
    total = equity.iloc[-1] / cash0 - 1

    def tot(s):
        return None if s is None else float(s.iloc[-1] / cash0 - 1)

    k, bh = benchmarks["kospi"], benchmarks["buy_hold"]
    return {
        "total_return": float(total),
        "cagr": float((1 + total) ** (365.25 / days) - 1) if days > 0 else None,
        "mdd": _mdd(equity),
        "n_trades": len(closed),
        "n_open_positions": len(trades) - len(closed),
        "n_fills": len(fills),
        "win_rate": (sum(t["pnl"] > 0 for t in closed) / len(closed)) if closed else None,
        "total_commission": float(sum(f["commission"] for f in fills)),
        "total_tax": float(sum(f["tax"] for f in fills)),
        "kospi_total_return": tot(k),
        "kospi_mdd": _mdd(k),
        "excess_vs_kospi": None if k is None else float(total) - tot(k),
        "buy_hold_total_return": tot(bh),
        "buy_hold_mdd": _mdd(bh),
        "start": equity.index[0].strftime("%Y-%m-%d"),
        "end": equity.index[-1].strftime("%Y-%m-%d"),
    }
