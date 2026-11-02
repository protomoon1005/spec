# 값 바꾸기와 전략서 조립 (전략서 v0.2 명세화 3번째 일). 규칙만 쓰고 AI 는 쓰지 않는다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 설계 문서 "문장을 빈칸으로 나누는 방법", "사용자가 말하지 않은 빈칸 채우기", "되묻기 질문"(2026-11-02).
#   - 입력: 조각 찾기 결과(SpanResult)와 정리된 AI 짝짓기(Pairing), 되묻기에 받은 답(overrides)
#   - 조각의 meaning 에서 값을 만든다. AI 가 쓴 값은 없다
#   - 비어 있거나 허용 밖인 값은 되물을 거리(Ask)로, "못 다룸" 은 거절 거리(Reject)로 모은다
#   - 말하지 않은 칸은 기본값으로 채우고 meta.defaults_used 에 적는다
#   - 되물을 거리가 남아도 채울 수 있는 데까지 조립한다(spec_dict).
#     다 채워지면 계약(StrategyV0_2)을 통과시킨다
# 되묻기에 답하면 그 답을 overrides 에 넣고 이 함수를 다시 부른다. AI 짝짓기는 다시 하지 않는다.
# overrides 의 키는 Ask.key 다. 같은 짝짓기에서는 같은 키가 나온다.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.contracts.strategy_v0_2 import StrategyV0_2
from app.m1.pairing import Condition, Item, Pairing, RulePairing
from app.m1.spans import Span, SpanResult, negated_span_ids

ALLOWED_WINDOWS = {"ma": (5, 10, 20, 60, 120), "rsi": (14,), "ret": (1, 5, 20, 60), "vol": (20,),
                   "volume_ratio": (20,)}
# 말하지 않으면 채우는 기간. 허용 기간이 하나뿐인 지표는 그 값이다
DEFAULT_WINDOWS = {"rsi": 14, "vol": 20, "volume_ratio": 20}
RET_WORD_WINDOWS = {"하루": 1, "일주일": 5, "한달": 20, "석달": 60}
MONEY_SCALE = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, None: 1}
DEFAULT_START, DEFAULT_END, DEFAULT_CAPITAL = date(2023, 1, 1), date(2025, 12, 31), 10_000_000
FLIP = {
    "cross_below": "cross_above", "cross_above": "cross_below", "<": ">=", "<=": ">", ">": "<=", ">=": "<",
}
DOWN_OPS = ("cross_below", "<", "<=")

# 되묻는 순서(설계 문서 "되묻는 순서"). 숫자가 작을수록 먼저
TIER = {
    "universe": 1, "sector_not_in_list": 1, "selection_needed": 1,
    "entry": 2, "exit": 2,
    "ma_window": 3, "cross_windows": 3, "window_not_allowed": 3, "threshold": 3, "direction": 3,
    "indicator_missing": 3,
    "weights_over_100": 4, "weight_vs_cap": 4, "rebalance_not_allowed": 4,
    "always_both_true": 5,
}
EVIDENCE_TIER = {"instrument": 1, "subject": 1, "sector": 1, "selection": 1, "indicator": 3, "number": 3,
                 "verb": 3}


@dataclass(frozen=True)
class Ask:
    # eval/GUIDE.md 의 되묻기 이름. 더한 것: direction, indicator_missing, evidence_unused, ambiguous_quote
    kind: str
    path: str  # 어느 칸인지. 예: rules[0].exit, rules[0].entry.items[1]
    position: int  # 문장 안 위치. 같은 순위에서는 앞에 나온 것부터 묻는다
    context: Mapping = field(default_factory=dict)  # 질문 문구와 선택지에 쓰는 값

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.path}"

    @property
    def tier(self) -> int:
        if self.kind == "evidence_unused":
            return EVIDENCE_TIER.get(self.context.get("span_kind"), 4)
        if self.kind == "ambiguous_quote":
            return 1 if self.context.get("slot") == "universe" else 3
        return TIER.get(self.kind, 4)


@dataclass(frozen=True)
class Reject:
    code: str
    evidence: str
    position: int
    stage: int | None = None


@dataclass
class Assembly:
    spec_dict: dict
    spec: StrategyV0_2 | None  # 되물을 거리 · 거절 거리가 없고 계약을 통과하면 채워진다
    asks: list[Ask]
    rejects: list[Reject]
    defaults_used: list[str]
    evidence: dict[str, list[str]]
    # "rules[0]" 같은 칸의 문장 위치. 검사 결과를 붙인다
    positions: dict[str, int] = field(default_factory=dict)


def assemble(
    result: SpanResult,
    pairing: Pairing,
    *,
    unresolved_issues: tuple = (),
    overrides: Mapping[str, Any] | None = None,
) -> Assembly:
    return _Builder(result, pairing, dict(overrides or {})).build(unresolved_issues)


class _Builder:
    def __init__(self, result, pairing, overrides):
        self.result = result
        self.pairing = pairing
        self.overrides = overrides
        self.asks: list[Ask] = []
        self.rejects: list[Reject] = []
        self.defaults: list[str] = []
        self.evidence: dict[str, list[str]] = {}
        self.negated = negated_span_ids(result)
        self.positions: dict[str, int] = {}

    # ── 전체 ──────────────────────────────────────────────────────────

    def build(self, unresolved_issues) -> Assembly:
        self._quotes()
        self._unresolved(unresolved_issues)
        tickers = self._universe()
        rules = self._rules(tickers)
        spec_dict: dict = {"universe": {"tickers": tickers} if tickers else {}}
        spec_dict["weights"] = self._weights(tickers)
        spec_dict["rebalance"] = self._rebalance(bool(rules))
        spec_dict["rules"] = rules
        spec_dict["constraint"] = self._constraint()
        spec_dict["backtest"] = self._backtest()
        if rules and not any(s.kind == "subject" for s in self._used_spans()):
            self._default("subject")
        spec_dict["meta"] = {
            "defaults_used": _ordered(self.defaults),
            "evidence": self.evidence,
            "dictionary_version": self.result.dictionary_version,
        }
        spec = None
        if not self.asks and not self.rejects:
            try:
                spec = StrategyV0_2.model_validate(spec_dict)
            except ValidationError as exc:  # 조립 규칙의 빈틈이다. 검사 1단계가 막고 기록한다
                spec_dict["_shape_error"] = str(exc)
        return Assembly(spec_dict, spec, self.asks, self.rejects, _ordered(self.defaults), self.evidence,
                        self.positions)

    # ── 기록 도우미 ───────────────────────────────────────────────────

    def _ask(self, kind: str, path: str, spans: list[Span] | tuple = (), **context) -> None:
        position = min((s.start for s in spans if s is not None), default=len(self.result.text))
        ask = Ask(kind, path, position, context)
        if all(a.key != ask.key for a in self.asks):
            self.asks.append(ask)

    def _answer(self, kind: str, path: str):
        return self.overrides.get(f"{kind}:{path}")

    def _default(self, name: str) -> None:
        if name not in self.defaults:
            self.defaults.append(name)

    def _cite(self, slot: str, spans) -> None:
        texts = self.evidence.setdefault(slot, [])
        for s in spans:
            if s is not None and s.text not in texts:
                texts.append(s.text)

    def _used_spans(self) -> list[Span]:
        ids = self.pairing.used_span_ids()
        return [s for s in self.result.spans if s.id in ids]

    # ── 인용과 근거 검사에 남은 것 ────────────────────────────────────

    def _quotes(self) -> None:
        for q in self.pairing.quotes:
            pos = self.result.text.find(q.text)
            if pos < 0:
                continue  # 원문에 없는 인용은 근거 검사가 이미 걸렀다
            if q.state == "unsupported":
                code = "UNSUPPORTED_ASSET" if q.slot == "universe" else "UNKNOWN_INDICATOR"
                self.rejects.append(Reject(code, q.text, pos))
            elif self._answer("ambiguous_quote", q.text) != "ignore":
                self.asks.append(Ask("ambiguous_quote", q.text, pos, {"text": q.text, "slot": q.slot}))

    def _unresolved(self, issues) -> None:
        by_id = {s.id: s for s in self.result.spans}
        for issue in issues:
            if issue.code != "unused_span" or issue.span_id not in by_id:
                continue
            span = by_id[issue.span_id]
            if self._answer("evidence_unused", f"span{span.id}") != "ignore":
                self._ask("evidence_unused", f"span{span.id}", [span], text=span.text, span_kind=span.kind)

    # ── 종목 ──────────────────────────────────────────────────────────

    def _universe(self) -> list[str]:
        answered = self._answer("universe", "universe")
        spans = list(self.pairing.universe)
        spans += [a for a, _ in self.pairing.weights if a is not None]
        spans += [r.target for r in self.pairing.rules if r.target is not None]
        tickers: list[str] = []
        for s in spans:
            ticker = s.meaning.get("ticker")
            if ticker and ticker not in tickers:
                tickers.append(ticker)
        for ticker in answered or ():
            if ticker not in tickers:
                tickers.append(ticker)
        self._cite("universe", spans)
        if tickers:
            return tickers
        if any(s.kind in ("sector", "selection") for s in self.result.spans):
            self._ask("universe", "universe", reason="selector_not_in_demo")
        else:
            self._ask("universe", "universe", reason="none", has_rules=bool(self.pairing.rules))
        return []

    # ── 사고파는 조건 ─────────────────────────────────────────────────

    def _rules(self, tickers: list[str]) -> list[dict]:
        out = []
        for i, rule in enumerate(self.pairing.rules):
            built = self._rule(i, rule, tickers)
            if built is not None:
                out.append(built)
        return out

    def _rule(self, i: int, rule: RulePairing, tickers: list[str]) -> dict | None:
        target = "each" if rule.target is None else rule.target.meaning.get("ticker", "each")
        self_ticker = target if target != "each" else (tickers[0] if len(tickers) == 1 else None)
        entry = self._condition(f"rules[{i}].entry", rule.entry, self_ticker)
        exit_ = self._condition(f"rules[{i}].exit", rule.exit, self_ticker)
        rule_spans = [s for c in (rule.entry, rule.exit) if c is not None for s in c.spans()]
        self.positions[f"rules[{i}]"] = min((s.start for s in rule_spans), default=len(self.result.text))

        # 반대쪽 조건을 말했으면, 그쪽을 아직 다 조립하지 못했어도(기준 숫자를 되묻는 중이어도) 묻는다
        if rule.entry is None and rule.exit is not None:
            answer = self._answer("entry", f"rules[{i}].entry")
            if answer == "reverse" and exit_ is not None:
                entry = _reverse(exit_)
            elif answer != "reverse":
                self._ask("entry", f"rules[{i}].entry", rule_spans)
        if rule.exit is None and rule.entry is not None:
            answer = self._answer("exit", f"rules[{i}].exit")
            if answer == "reverse" and entry is not None:
                exit_ = _reverse(entry)
            elif answer == "reverse":
                pass
            elif answer == "take_profit_10":
                exit_ = {
                    "left": {"indicator": "entry_return", "of": "self"}, "op": ">=", "right": {"value": 0.1},
                }
            elif answer != "hold":
                self._ask("exit", f"rules[{i}].exit", rule_spans)
        if entry is None and exit_ is None:
            return None
        # 검사 3단계의 되묻기 답: 사자마자 팔게 되는 모순이면 팔 조건 방향을 뒤집는다
        if exit_ is not None and self._answer("always_both_true", f"rules[{i}]") == "flip_exit":
            exit_ = _reverse(exit_)
        return {"target": target, "entry": entry, "exit": exit_}

    def _condition(self, path: str, cond: Condition | None, self_ticker: str | None) -> dict | None:
        if cond is None:
            return None
        side = "entry" if path.endswith("entry") else "exit"
        self._cite(side, cond.spans())
        compares = []
        for j, item in enumerate(cond.items):
            built = self._compare(f"{path}.items[{j}]", item, cond, self_ticker)
            if built is not None:
                compares.append(built)
        if not compares:
            return None
        if len(compares) == 1:
            return compares[0]
        return {"all" if cond.join == "all" else "any": compares}

    def _compare(self, path: str, item: Item, cond: Condition, self_ticker: str | None) -> dict | None:
        of = self._subject(item.subject, self_ticker)
        exit_kinds = [a.meaning.get("exit_kind") for a in cond.actions if a.meaning.get("exit_kind")]
        lm = dict(item.left.meaning) if item.left else {}
        spans = item.spans()

        # 골든크로스 · 데드크로스: 기간 두 개를 되묻는다
        if lm.get("cross"):
            windows = self._answer("cross_windows", path)
            if not windows:
                self._ask("cross_windows", path, spans, cross=lm["cross"])
                return None
            short, long_ = windows
            op = "cross_above" if lm["cross"] == "golden" else "cross_below"
            return {"left": {"indicator": "ma", "window": short, "of": of}, "op": op,
                    "right": {"indicator": "ma", "window": long_, "of": of}}

        # 왼쪽 지표
        if item.left is None and exit_kinds:
            lm = {"indicator": "entry_return"}
        if item.left is None and not lm:
            if item.right is not None and item.right.kind == "number":
                answered = self._answer("indicator_missing", path)
                if not answered:
                    self._ask("indicator_missing", path, spans)
                    return None
                lm = dict(answered)
            else:
                lm = {"indicator": "close"}
        left = self._indicator(path + ".left", lm, of, spans, item.left)
        if left is None:
            return None
        name = left["indicator"]
        if name in ("entry_return", "holding_days", "vix"):
            left["of"] = "VIX" if name == "vix" else "self"

        # 오른쪽
        right = self._right(path, item, lm, name, of, spans)
        if right is None:
            return None

        # 비교 방향
        op = self._op(path, item, lm, name, right, exit_kinds, spans)
        if op is None:
            return None
        if name == "entry_return" and "value" in right:
            right = {"value": _signed_return(right["value"], item.right, op)}
            op = "<=" if right["value"] < 0 else ">="
        return {"left": left, "op": op, "right": right}

    def _subject(self, span: Span | None, self_ticker: str | None) -> str:
        if span is None:
            return "self"
        if span.kind == "subject":
            return span.meaning.get("of", "self")
        ticker = span.meaning.get("ticker")
        return "self" if ticker is None or ticker == self_ticker else ticker

    def _indicator(
        self, path: str, meaning: Mapping, of: str, spans, span: Span | None = None
    ) -> dict | None:
        # 기간을 되물을 때 키는 조각 단위다. "이평선 깨지면 팔고 다시 올라오면 사" 의 이평선은 한 번만 묻는다
        if span is not None:
            path = f"span{span.id}"
        name = meaning.get("indicator")
        if name is None:
            self._ask("indicator_missing", path, spans)
            return None
        out = {"indicator": name, "of": of}
        window = meaning.get("window")
        if name == "ret" and window is None and meaning.get("period_word"):
            window = RET_WORD_WINDOWS.get(str(meaning["period_word"]).replace(" ", ""))
        if name in ALLOWED_WINDOWS:
            if window is None:
                if name == "ma":
                    answered = self._answer("ma_window", path)
                    if answered is None:
                        self._ask("ma_window", path, spans)
                        return None
                    window = answered
                elif name in DEFAULT_WINDOWS:
                    window = DEFAULT_WINDOWS[name]
                    if name == "rsi":
                        self._default("rsi_window")
                else:
                    self._ask("indicator_missing", path, spans, indicator=name)
                    return None
            if window not in ALLOWED_WINDOWS[name]:
                answered = self._answer("window_not_allowed", path)
                if answered is None:
                    self._ask("window_not_allowed", path, spans, indicator=name, window=window,
                              allowed=list(ALLOWED_WINDOWS[name]))
                    return None
                window = answered
            out["window"] = int(window)
        return out

    def _right(self, path, item: Item, lm: Mapping, name: str, of: str, spans) -> dict | None:
        span = item.right
        if span is not None and span.kind == "indicator":
            return self._indicator(path + ".right", span.meaning, of, spans, span)
        if span is not None and span.kind == "number":
            value = number_value(span, name)
            if value is not None:
                return {"value": value}
        if isinstance(lm.get("value"), (int, float)):
            if lm.get("value") in (30, 70) and name == "rsi" and lm.get("op"):
                self._default("rsi_level")
            return {"value": float(lm["value"])}
        answered = self._answer("threshold", path)
        if answered is not None:
            return {"value": float(answered)}
        self._ask("threshold", path, spans, indicator=name, direction=_direction(item))
        return None

    def _op(self, path, item: Item, lm: Mapping, name: str, right: dict, exit_kinds, spans) -> str | None:
        op = item.op.meaning.get("op") if item.op is not None else None
        if name in ("entry_return",):
            # 손절 · 익절은 상태 비교다(eval/GUIDE.md). 방향은 값의 부호로 정한다
            if op is None and exit_kinds:
                op = ">=" if exit_kinds[0] == "take_profit" else "<="
            return {"cross_below": "<=", "<": "<=", "cross_above": ">=", ">": ">="}.get(op, op)
        if name == "holding_days":
            return ">="
        if op is None and lm.get("op"):
            op = lm["op"]
        if op is None:
            answered = self._answer("direction", path)
            if answered is None:
                self._ask("direction", path, spans)
                return None
            op = answered
        return op

    # ── 비중 · 리밸런싱 · 제한 · 백테스트 설정 ───────────────────────────

    def _weights(self, tickers: list[str]):
        pairs = self.pairing.weights
        self._cite("weights", [s for pair in pairs for s in pair])
        weights: dict[str, float] = {}
        mode = None
        unassigned = [t for t in tickers]
        for asset, value in pairs:
            if value is None:
                continue
            m = value.meaning
            if value.kind == "weight":
                if m.get("weights") == "half" and len(tickers) == 2:
                    weights = {t: 0.5 for t in tickers}
                elif m.get("weights") in ("equal", "half"):
                    mode = "equal"
                continue
            if m.get("unit") == "ratio" and len(tickers) == 2:
                total = m["left"] + m["right"]
                weights = {tickers[0]: m["left"] / total, tickers[1]: m["right"] / total}
                continue
            if m.get("unit") == "percent":
                if asset is not None:
                    ticker = asset.meaning.get("ticker")
                else:
                    ticker = unassigned[0] if unassigned else None
                if ticker:
                    weights[ticker] = m["value"] / 100
                    if ticker in unassigned:
                        unassigned.remove(ticker)
        if not weights:
            if mode is None:
                self._default("weights")
            return "equal"
        cap = self._answer("weight_vs_cap", "weights")
        if cap == "cap":
            limit = self._constraint().get("max_weight_per_asset")
            if limit is not None:
                weights = {t: min(w, limit) for t, w in weights.items()}
        total = sum(weights.values())
        if total > 1 + 1e-9:
            if self._answer("weights_over_100", "weights") == "scale":
                return {t: w / total for t, w in weights.items()}
            self._ask("weights_over_100", "weights", [s for p in pairs for s in p if s is not None],
                      total=round(total * 100, 6))
        return weights

    def _rebalance(self, has_rules: bool):
        spans = self.pairing.rebalance
        self._cite("rebalance", spans)
        for s in spans:
            m = s.meaning
            chosen = m.get("rebalance")
            if isinstance(chosen, str) and chosen.startswith("RB_"):
                return chosen
            if m.get("rebalance") == "drift" and m.get("value") == 5:
                return "RB_THRESHOLD_5"
            if m.get("not_allowed") or m.get("rebalance") == "drift":
                answered = self._answer("rebalance_not_allowed", "rebalance")
                if answered:
                    return answered
                self._ask("rebalance_not_allowed", "rebalance", [s],
                          said=m.get("not_allowed") or f"drift_{m.get('value')}")
                return None
        if has_rules:
            return None
        self._default("rebalance")
        return "RB_MONTHLY_FIRST"

    def _constraint(self) -> dict:
        span = self.pairing.max_weight
        if span is None:
            return {}
        self._cite("constraint", [span])
        m = span.meaning
        value = m.get("max_weight", m.get("value") if m.get("unit") == "percent" else None)
        return {"max_weight_per_asset": value / 100} if isinstance(value, (int, float)) else {}

    def _backtest(self) -> dict:
        start, end, capital = DEFAULT_START, DEFAULT_END, DEFAULT_CAPITAL
        period = self.pairing.period
        self._cite("period", period)
        dates = [s.meaning["date"] for s in period if s.meaning.get("date")]
        if any(s.meaning.get("year") for s in period):
            year = int(next(s.meaning["year"] for s in period if s.meaning.get("year")))
            start, end = date(year, 1, 1), date(year, 12, 31)
        elif dates:
            start = date.fromisoformat(dates[0])
            if len(dates) > 1:
                end = date.fromisoformat(dates[1])
        elif any(s.meaning.get("last_years") for s in period):
            years = int(next(s.meaning["last_years"] for s in period if s.meaning.get("last_years")))
            start = date(DEFAULT_END.year - years + 1, 1, 1)
        else:
            self._default("period")
        money = [s for s in self.pairing.capital if s.meaning.get("unit") == "won"]
        self._cite("capital", self.pairing.capital)
        if money:
            m = money[0].meaning
            capital = float(m["value"]) * MONEY_SCALE.get(m.get("scale"), 1)
        else:
            self._default("capital")
        self._default("costs")
        said_dividend = [s for s in self.result.spans if s.kind == "dividend"]
        include = any(s.id not in self.negated for s in said_dividend)
        if not said_dividend:
            self._default("dividends")
        self._cite("dividends", said_dividend)
        return {"start": start.isoformat(), "end": end.isoformat(), "capital": capital,
                "include_dividends": include}


# ── 값 도우미 ────────────────────────────────────────────────────────


def number_value(span: Span, indicator: str) -> float | None:
    m = span.meaning
    unit = m.get("unit")
    value = m.get("value")
    if not isinstance(value, (int, float)):
        return None
    if unit in ("percent", "none") and indicator in ("ret", "entry_return"):
        # 수익률은 소수로 적는다(0.10 = 10%). "10 오르면" 처럼 % 를 빼고 말해도 % 로 본다
        return value / 100
    if unit == "percent" and indicator == "vol":
        return value / 100  # 변동성도 소수다(signals.py)
    if unit in ("percent", "none", "times"):
        return float(value)
    return None


def _signed_return(value: float, span: Span | None, op: str) -> float:
    # 부호를 직접 말했으면("-8%") 그대로, 아니면 비교 방향(빠지면 · 손절은 아래)으로 정한다
    if span is not None and span.text.lstrip()[:1] in "+-":
        return value
    return -abs(value) if op in DOWN_OPS else abs(value)


def _direction(item: Item) -> str | None:
    if item.op is None:
        return None
    op = item.op.meaning.get("op")
    return "down" if op in DOWN_OPS else "up" if op else None


def _reverse(cond: dict) -> dict:
    # 조건을 반대로. 내려가는 순간과 올라가는 순간을 서로 바꾸고, "~보다 낮은 동안" 은 "~이상인 동안" 으로.
    # 묶음은 그리고와 또는을 서로 바꾼다
    if "all" in cond:
        return {"any": [_reverse(c) for c in cond["all"]]}
    if "any" in cond:
        return {"all": [_reverse(c) for c in cond["any"]]}
    return {**cond, "op": FLIP[cond["op"]]}


def _ordered(names: list[str]) -> list[str]:
    order = ["weights", "rebalance", "period", "capital", "costs", "dividends", "rsi_window", "rsi_level",
             "subject"]
    return [n for n in order if n in names]
