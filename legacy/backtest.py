"""결정론적 백테스트 엔진 (docs/demo-spec.md §B7). LLM 미사용.

체결 규칙
- T일 장 마감 후 T일 종가까지의 지표로 신호 판정 → T+1일 시가에 체결.
- 마지막 날 신호는 체결할 다음 날이 없으므로 unfilled_signals 에 기록만 한다.
- 종목별 슬리브: weight × 초기자금을 배정하고, 그 슬리브 현금 안에서만 정수 주식 매매.
  매도 대금은 같은 종목 슬리브로 돌아간다(종목 간 자금 이동 없음). 남은 비중은 현금.
- 보유 중이면 매도 조건만, 미보유면 매수 조건만 본다. 피라미딩·부분매도 없음(전액 매수 / 전량 매도).
- 손절·익절: T일 종가 기준 (종가/매수가 - 1) 로 판정 → T+1 시가 체결. 장중 터치는 보지 않는다.
- 보유기간: 매수 체결일을 1일째로 센 거래일 수가 max_hold_days 에 도달한 날 종가에 판정 → 다음 날 시가 매도.
- 기간 종료 시 미청산 포지션은 종가로 평가만 하고 강제 청산하지 않는다.
- 비용: 수수료 매수·매도 각 commission_bps, 세금은 매도 시 매도금액 × 세율(config/costs.yaml).
  원 단위 절사 없이 실수로 계산한다. 슬리피지 미반영.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from legacy.data import frame_hash, load_ohlcv
from legacy.dictionary import asset_types as dictionary_asset_types
from legacy.indicators import compute_indicators
from legacy.schema import Condition, DemoSpec, EntryRule, ExitRule, RefMul

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
COSTS_PATH = LEGACY / "config" / "costs.yaml"


# ---------------------------------------------------------------- 신호

def _operand(ind: pd.DataFrame, x) -> pd.Series:
    if isinstance(x, RefMul):
        return ind[x.ref] * x.mul
    if isinstance(x, str):
        return ind[x]
    return pd.Series(float(x), index=ind.index)


def _condition(ind: pd.DataFrame, c: Condition) -> pd.Series:
    """조건 하나 → bool Series. 비교 대상에 NaN 이 있으면 False."""
    left, right = ind[c.left], _operand(ind, c.right)
    valid = left.notna() & right.notna()
    if c.op == "lt":
        res = left < right
    elif c.op == "gt":
        res = left > right
    else:
        # cross: 전일·당일 값만 사용 (shift(+1) = 과거 방향)
        pl, pr = left.shift(1), right.shift(1)
        valid = valid & pl.notna() & pr.notna()
        if c.op == "cross_above":
            res = (pl <= pr) & (left > right)
        else:  # cross_below
            res = (pl >= pr) & (left < right)
    return (res & valid).astype(bool)


def compute_signals(ind: pd.DataFrame, rule: EntryRule | ExitRule) -> pd.Series:
    """규칙의 지표 조건 결합. 조건이 없으면 전부 False (손절·보유기간은 엔진에서 처리)."""
    if not rule.conditions:
        return pd.Series(False, index=ind.index)
    parts = [_condition(ind, c) for c in rule.conditions]
    out = parts[0]
    for p in parts[1:]:
        out = (out & p) if rule.logic == "all" else (out | p)
    return out.astype(bool)


# ---------------------------------------------------------------- 비용

def buy_fill(cash: float, price: float, commission_bps: float) -> tuple[int, float, float]:
    """cash 안에서 수수료 포함 살 수 있는 최대 정수 주식. (수량, 매수금액, 수수료)."""
    rate = commission_bps / 10_000
    shares = int(math.floor(cash / (price * (1 + rate)) + 1e-9))
    gross = shares * price
    return shares, gross, gross * rate


def sell_fill(shares: int, price: float, commission_bps: float, tax_rate: float) -> tuple[float, float, float]:
    """(매도금액, 수수료, 세금)."""
    gross = shares * price
    return gross, gross * commission_bps / 10_000, gross * tax_rate


def load_costs(path: Path | str = COSTS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- 결과

@dataclass
class BacktestResult:
    equity: pd.Series                 # 일별 총자산 (현금 + 보유 종가 평가)
    benchmark: pd.Series              # 같은 비중 단순 보유 (첫날 시가 매수, 동일 비용)
    trades: list[dict]                # 미청산 포지션 포함 (exit_date=None)
    metrics: dict
    unfilled_signals: list[dict]
    spec_hash: str
    data_snapshot: dict
    result_hash: str
    notes: list[str] = field(default_factory=list)


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def spec_hash(spec: DemoSpec) -> str:
    """실행에 영향을 주는 필드만 해시 (verdict·provenance 등 설명 필드 제외)."""
    d = spec.model_dump(mode="json", include={"schema_version", "universe", "entry", "exit", "backtest", "costs"})
    return _sha(_canon(d))


def _r(x: Optional[float], nd: int = 6):
    return None if x is None else round(float(x), nd)


def _mdd(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0


# ---------------------------------------------------------------- 엔진

def _run_sleeve(ticker: str, df: pd.DataFrame, ind: pd.DataFrame, spec: DemoSpec, cash0: float,
                tax_rate: float, mask: pd.Series):
    entry_sig = compute_signals(ind, spec.entry)
    exit_sig = compute_signals(ind, spec.exit)
    ex, bps = spec.exit, spec.costs.commission_bps
    dates = df.index[mask.to_numpy()]
    opens = df["Open"].loc[dates].to_numpy()
    closes = df["Close"].loc[dates].to_numpy()
    e_sig = entry_sig.loc[dates].to_numpy()
    x_sig = exit_sig.loc[dates].to_numpy()

    cash, shares = cash0, 0
    pending: Optional[dict] = None
    pos: Optional[dict] = None
    trades, unfilled, equity = [], [], np.empty(len(dates))
    ds = [d.strftime("%Y-%m-%d") for d in dates]

    for i in range(len(dates)):
        # 1) 장 시작: 전일 신호를 오늘 시가로 체결
        if pending is not None:
            if pending["side"] == "buy":
                n, gross, comm = buy_fill(cash, opens[i], bps)
                if n > 0:
                    cash -= gross + comm
                    shares = n
                    pos = {"ticker": ticker, "entry_signal_date": pending["date"], "entry_date": ds[i],
                           "entry_price": float(opens[i]), "shares": n, "entry_commission": comm,
                           "_cost_basis": gross + comm, "_i": i}
            else:
                gross, comm, tax = sell_fill(shares, opens[i], bps, tax_rate)
                proceeds = gross - comm - tax
                cash += proceeds
                pos.update({"exit_signal_date": pending["date"], "exit_date": ds[i],
                            "exit_price": float(opens[i]), "exit_commission": comm, "exit_tax": tax,
                            "exit_reason": pending["reason"], "pnl": proceeds - pos["_cost_basis"],
                            "return_pct": proceeds / pos["_cost_basis"] - 1, "hold_days": i - pos["_i"]})
                trades.append(pos)
                pos, shares = None, 0
            pending = None

        # 2) 장 마감: 오늘 종가까지로 신호 판정 → 내일 시가 주문
        if pos is not None:
            reasons = []
            ret = closes[i] / pos["entry_price"] - 1
            if ex.stop_loss is not None and ret <= -ex.stop_loss:
                reasons.append("stop_loss")
            if ex.take_profit is not None and ret >= ex.take_profit:
                reasons.append("take_profit")
            if ex.max_hold_days is not None and (i - pos["_i"] + 1) >= ex.max_hold_days:
                reasons.append("max_hold_days")
            if x_sig[i]:
                reasons.append("condition")
            if reasons:
                pending = {"side": "sell", "date": ds[i], "reason": "+".join(reasons)}
        elif e_sig[i]:
            pending = {"side": "buy", "date": ds[i]}

        equity[i] = cash + shares * closes[i]

    if pending is not None:
        unfilled.append({"ticker": ticker, "side": pending["side"], "signal_date": pending["date"]})
    if pos is not None:
        pos.update({"exit_signal_date": None, "exit_date": None, "exit_price": None,
                    "exit_commission": None, "exit_tax": None, "exit_reason": "open",
                    "pnl": shares * closes[-1] - pos["_cost_basis"],
                    "return_pct": shares * closes[-1] / pos["_cost_basis"] - 1,
                    "hold_days": len(dates) - pos["_i"]})
        trades.append(pos)

    for t in trades:
        t.pop("_cost_basis"); t.pop("_i")
        for k in ("entry_commission", "exit_commission", "exit_tax", "pnl", "return_pct"):
            t[k] = _r(t[k])
    return pd.Series(equity, index=dates), trades, unfilled


def _benchmark(df: pd.DataFrame, mask: pd.Series, cash0: float, bps: float) -> pd.Series:
    d = df.loc[mask.to_numpy()]
    n, gross, comm = buy_fill(cash0, d["Open"].iloc[0], bps)
    return (cash0 - gross - comm) + n * d["Close"]


def _refuse_escape_values(spec: DemoSpec) -> None:
    """탈출구 값(UNKNOWN 종목·UNSUPPORTED 지표·unmapped_phrases)은 검증기가 막지만, 엔진에서도 한 번 더 거부한다."""
    from legacy.schema import UNKNOWN_TICKER, UNSUPPORTED_INDICATOR, RefMul
    bad = [u.ticker for u in spec.universe if u.ticker == UNKNOWN_TICKER]
    for rule in (spec.entry, spec.exit):
        for c in rule.conditions:
            r = c.right.ref if isinstance(c.right, RefMul) else c.right
            bad += [v for v in (c.left, r) if v == UNSUPPORTED_INDICATOR]
    if bad or spec.unmapped_phrases:
        raise ValueError(f"사전에 없는 값이 남아 있어 실행할 수 없습니다: {bad or spec.unmapped_phrases}")


def run_backtest(spec: DemoSpec, data: Optional[dict[str, pd.DataFrame]] = None,
                 asset_types: Optional[dict[str, str]] = None,
                 costs_config: Optional[dict] = None) -> BacktestResult:
    """명세 실행. data 를 주면 그 OHLCV 를 쓰고(테스트용), 없으면 data/cache 를 쓴다.

    asset_types 를 주지 않으면 data/tickers.json 의 asset_type(stock/etf)으로 세율을 고른다.
    사전에 없는 종목은 costs.yaml default_asset_type 을 쓰고 notes 에 남긴다.
    검증기(legacy.validate)를 통과한 명세만 넣는 것이 전제다.
    """
    if spec.entry is None or spec.exit is None or not spec.universe:
        raise ValueError("실행할 수 없는 명세입니다 (universe/entry/exit 없음). 검증기를 먼저 통과시키세요.")
    _refuse_escape_values(spec)
    costs_cfg = costs_config or load_costs()
    profile = costs_cfg["tax_profiles"][spec.costs.tax_profile]
    default_type = costs_cfg.get("default_asset_type", "stock")
    asset_types = dictionary_asset_types() if asset_types is None else asset_types
    notes: list[str] = []
    if costs_cfg.get("slippage_bps", 0):
        raise NotImplementedError("슬리피지는 데모에서 미지원 (costs.yaml slippage_bps=0 이어야 함)")

    start, end = pd.Timestamp(spec.backtest.start), pd.Timestamp(spec.backtest.end)
    cash_total = float(spec.backtest.initial_cash)
    snapshot: dict = {}
    sleeves_eq, bench_eq, trades, unfilled = [], [], [], []

    for u in spec.universe:
        if data is not None:
            df = data[u.ticker]
            snapshot[u.ticker] = {"source": "in-memory", "sha256": frame_hash(df)}
        else:
            df, meta = load_ohlcv(u.ticker)
            snapshot[u.ticker] = {"source": meta["source"], "fetched_at": meta["fetched_at"],
                                  "sha256": meta["sha256"]}
        ind = compute_indicators(df)          # 전체 이력으로 계산 (기간 이전 = 워밍업, 과거만 사용)
        mask = (df.index >= start) & (df.index <= end)
        mask = pd.Series(mask, index=df.index)
        if not mask.any():
            raise ValueError(f"{u.ticker}: {spec.backtest.start}~{spec.backtest.end} 구간 데이터가 없습니다.")
        atype = asset_types.get(u.ticker)
        if atype is None:
            atype = default_type
            notes.append(f"{u.ticker}: 종목 유형 미지정 → '{default_type}' 세율 적용")
        alloc = cash_total * u.weight
        eq, tr, uf = _run_sleeve(u.ticker, df, ind, spec, alloc, float(profile[atype]), mask)
        sleeves_eq.append(eq); trades += tr; unfilled += uf
        bench_eq.append(_benchmark(df, mask, alloc, spec.costs.commission_bps))

    idle_cash = cash_total * (1 - sum(u.weight for u in spec.universe))
    # 종목별 거래일이 다르면 직전 값으로 채움 (과거 방향 ffill 만)
    equity = pd.concat(sleeves_eq, axis=1).sort_index().ffill().sum(axis=1) + idle_cash
    benchmark = pd.concat(bench_eq, axis=1).sort_index().ffill().sum(axis=1) + idle_cash
    equity.name, benchmark.name = "equity", "benchmark"
    trades.sort(key=lambda t: (t["entry_date"], t["ticker"]))

    closed = [t for t in trades if t["exit_date"] is not None]
    days = (equity.index[-1] - equity.index[0]).days
    total = equity.iloc[-1] / cash_total - 1
    metrics = {
        "total_return": float(total),
        "cagr": float((1 + total) ** (365.25 / days) - 1) if days > 0 else None,
        "mdd": _mdd(equity),
        "n_trades": len(closed),
        "n_open_positions": len(trades) - len(closed),
        "win_rate": (sum(t["pnl"] > 0 for t in closed) / len(closed)) if closed else None,
        "benchmark_total_return": float(benchmark.iloc[-1] / cash_total - 1),
        "benchmark_mdd": _mdd(benchmark),
        "start": equity.index[0].strftime("%Y-%m-%d"),
        "end": equity.index[-1].strftime("%Y-%m-%d"),
    }
    s_hash = spec_hash(spec)
    payload = {
        "spec_hash": s_hash,
        "trades": trades,
        "equity": [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in equity.items()],
        "unfilled": unfilled,
    }
    return BacktestResult(
        equity=equity, benchmark=benchmark, trades=trades, metrics=metrics,
        unfilled_signals=unfilled, spec_hash=s_hash, data_snapshot=snapshot,
        result_hash=_sha(_canon(payload)), notes=notes,
    )
