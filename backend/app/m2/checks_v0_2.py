# 전략서 v0.2 검사 1~3단계(설계 문서 "전략서 검사"). 규칙만 쓰고 AI 는 쓰지 않는다.
# 4단계(근거 검사)는 AI 짝짓기 직후에 돈다(app/m1/pairing.py).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 app/m1 과 같다(as_of 가드 사정권).
#
# 결과는 항목(Finding)마다 종류를 붙인다(validation_logs.violations, db/migrate/001)
#   block  막힘. 거절한다
#   ask    되묻기. 사용자 문장 자체가 모순이라 사용자가 고쳐야 한다
#   warn   경고. 진행하되 기록하고 알린다
#
# 1단계 모양: 계약(StrategyV0_2)을 통과하는가. 전략서는 규칙이 조립하므로 걸리면 조립 코드의 빈틈이다
# 2단계 존재: 종목이 원장에 있고 기간 시작 전에 상장했는가, 지표 준비용 과거 이력이 있는가,
#            지표와 기간이 허용 목록에 있는가. 지수 · VIX 를 보는 조건은 데이터가 없어 경고(데모 범위 밖)
# 3단계 말이 되는지: 단위, 숫자 범위, 자기 자신 비교, 손절 · 보유일을 살 때 씀, 지수에 거래량 비율을 씀,
#            사자마자 팔게 되는 모순(되묻기), 지정 비중이 종목당 상한보다 큼(되묻기)
# 검사는 되물을 거리가 남은 부분 전략서(spec_dict)에도 돌린다. 있는 부분만 본다.
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date

from pydantic import ValidationError

from app.contracts.strategy_v0_2 import StrategyV0_2

ALLOWED_WINDOWS = {
    "ma": (5, 10, 20, 60, 120),
    "rsi": (14,),
    "ret": (1, 5, 20, 60),
    "vol": (20,),
    "volume_ratio": (20,),
}
UNITS = {
    "close": "price",
    "ma": "price",
    "rsi": "score",
    "ret": "return",
    "entry_return": "return",
    "vol": "volatility",
    "volume_ratio": "times",
    "vix": "index",
    "holding_days": "days",
}
# 숫자로 비교할 때 받을 수 있는 범위(닫힌 구간). 수익률은 -100% 아래로 갈 수 없다
VALUE_RANGES = {
    "rsi": (0.0, 100.0),
    "ret": (-1.0, 100.0),
    "entry_return": (-1.0, 100.0),
    "vol": (0.0, 1.0),
    "volume_ratio": (0.0, 1000.0),
    "vix": (0.0, 200.0),
    "holding_days": (0.0, 5000.0),
    "close": (0.0, 1e12),
    "ma": (0.0, 1e12),
}
INDEX_SUBJECTS = ("KOSPI200", "KOSDAQ")
INDEX_INDICATORS = ("close", "ma", "rsi", "ret", "vol")
POSITION_INDICATORS = ("entry_return", "holding_days")
STATE_OPS = ("<", "<=", ">", ">=")
STAGE_NAMES = {1: "모양", 2: "존재", 3: "말이 되는지", 4: "근거"}


@dataclass(frozen=True)
class Finding:
    stage: int
    kind: str  # block · ask · warn
    code: str  # block: NO_DATA · VALIDATION, ask: 되묻기 이름, warn: 경고 이름
    message: str
    path: str = ""
    context: Mapping = field(default_factory=dict)

    def public(self) -> dict:
        return {"kind": self.kind, "code": self.code, "message": self.message, "path": self.path}


# ── 1단계 모양 ───────────────────────────────────────────────────────


def check_shape(spec_dict: dict) -> list[Finding]:
    data = {k: v for k, v in spec_dict.items() if not k.startswith("_")}
    try:
        StrategyV0_2.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return [
            Finding(1, "block", "VALIDATION", f"전략서 모양이 맞지 않아요({where}: {first['msg']})", where)
        ]
    return []


# ── 2단계 존재 ───────────────────────────────────────────────────────


def check_existence(
    spec_dict: dict,
    *,
    known_tickers: Callable[[list[str]], set[str]],
    coverage: Callable[[list[str], date, date], dict[str, dict]],
    first_trade_day: Callable[[date, date], date | None],
    latest_trade_day: Callable[[], date | None] = lambda: None,
) -> list[Finding]:
    out: list[Finding] = []
    tickers = list((spec_dict.get("universe") or {}).get("tickers") or [])
    for _, cond in _conditions(spec_dict):
        for operand in _operands(cond):
            if (
                operand.get("of") not in (None, "self", *INDEX_SUBJECTS, "VIX")
                and operand["of"] not in tickers
            ):
                tickers.append(operand["of"])
    if not tickers:
        return out
    backtest = spec_dict.get("backtest") or {}
    start = date.fromisoformat(str(backtest.get("start", "2023-01-01")))
    end = date.fromisoformat(str(backtest.get("end", "2025-12-31")))

    unknown = sorted(set(tickers) - known_tickers(tickers))
    for ticker in unknown:
        out.append(
            Finding(
                2,
                "block",
                "NO_DATA",
                f"종목 원장에 없는 종목이에요({ticker})",
                "universe",
                {"ticker": ticker},
            )
        )
    found = coverage([t for t in tickers if t not in unknown], start, end)
    opening = first_trade_day(start, end)
    need = _lookback_needed(spec_dict)
    for ticker in tickers:
        if ticker in unknown:
            continue
        cov = found.get(ticker)
        if cov is None or cov["in_period"] == 0:
            out.append(
                Finding(
                    2,
                    "block",
                    "NO_DATA",
                    f"백테스트 기간에 가격이 없어요({ticker})",
                    "universe",
                    {"ticker": ticker},
                )
            )
        elif opening is not None and cov["first"] > opening:
            out.append(
                Finding(
                    2,
                    "block",
                    "NO_DATA",
                    f"백테스트를 시작하는 {start.isoformat()} 에는 아직 상장 전이에요"
                    f"({ticker}, 첫 거래일 {cov['first'].isoformat()})",
                    "universe",
                    {"ticker": ticker, "first": cov["first"].isoformat()},
                )
            )
        elif cov["before_start"] < need:
            out.append(
                Finding(
                    2,
                    "block",
                    "NO_DATA",
                    f"지표 계산에 시작일 앞 {need}거래일이 필요한데 {cov['before_start']}거래일뿐이에요"
                    f"({ticker})",
                    "backtest.start",
                    {"ticker": ticker, "need": need},
                )
            )
    # 기간 끝이 가격 스냅샷의 마지막 거래일보다 뒤일 때만 알린다. 12월 31일 같은 휴장일 때문에
    # 종목의 마지막 거래일이 기간 끝보다 앞서는 것은 정상이다
    last = latest_trade_day()
    if last is not None and last < end:
        out.append(
            Finding(
                2,
                "warn",
                "PERIOD_AFTER_DATA",
                f"가격 데이터가 {last.isoformat()} 까지라 그 뒤는 백테스트하지 않아요",
                "backtest.end",
            )
        )

    for path, cond in _conditions(spec_dict):
        for operand in _operands(cond):
            name, window = operand.get("indicator"), operand.get("window")
            if name in ALLOWED_WINDOWS and window not in ALLOWED_WINDOWS[name]:
                out.append(
                    Finding(2, "block", "VALIDATION", f"{name} 의 기간 {window} 은 허용 목록에 없어요", path)
                )
            if operand.get("of") in (*INDEX_SUBJECTS, "VIX"):
                out.append(
                    Finding(
                        2,
                        "warn",
                        "SUBJECT_DATA_NOT_IN_DEMO",
                        f"{operand['of']} 데이터가 아직 없어 데모에서는 백테스트할 수 없어요",
                        path,
                    )
                )
    return _unique(out)


def _lookback_needed(spec_dict: dict) -> int:
    # 그날 값이 나오려면 그날 앞에 있어야 하는 거래일 수. "~하는 순간" 은 전날 값도 필요해 하루 더
    need = 0
    for _, cond in _conditions(spec_dict):
        for compare in _compares(cond):
            extra = 1 if compare["op"] in ("cross_below", "cross_above") else 0
            for operand in (compare["left"], compare["right"]):
                name, window = operand.get("indicator"), operand.get("window") or 0
                base = (
                    window - 1
                    if name == "ma"
                    else window
                    if name in ("rsi", "ret", "vol", "volume_ratio")
                    else 0
                )
                need = max(need, base + extra)
    return need


# ── 3단계 말이 되는지 ────────────────────────────────────────────────


def check_logic(spec_dict: dict) -> list[Finding]:
    out: list[Finding] = []
    for path, cond in _conditions(spec_dict):
        side = path.rsplit(".", 1)[-1]
        for compare in _compares(cond):
            out.extend(_compare_findings(path, side, compare))
    for i, rule in enumerate(spec_dict.get("rules") or []):
        out.extend(_always_both_true(f"rules[{i}]", rule))
    out.extend(_weight_vs_cap(spec_dict))
    return _unique(out)


def _compare_findings(path: str, side: str, compare: dict) -> list[Finding]:
    left, right, op = compare["left"], compare["right"], compare["op"]
    name = left.get("indicator")
    out = []
    if "indicator" in right:
        if UNITS.get(name) != UNITS.get(right["indicator"]):
            out.append(
                Finding(
                    3,
                    "block",
                    "VALIDATION",
                    f"단위가 다른 것끼리 비교했어요({_label(left)}와 {_label(right)})",
                    path,
                )
            )
        if _same_operand(left, right):
            out.append(
                Finding(
                    3,
                    "block",
                    "VALIDATION",
                    f"같은 값끼리 비교해서 조건이 성립하지 않아요({_label(left)})",
                    path,
                )
            )
    else:
        low, high = VALUE_RANGES.get(name, (float("-inf"), float("inf")))
        if not low <= right["value"] <= high:
            out.append(
                Finding(
                    3,
                    "block",
                    "VALIDATION",
                    f"{_label(left)} 는 {low:g} ~ {high:g} 사이 값이에요(받은 값 {right['value']:g})",
                    path,
                )
            )
    for operand in (left, right):
        n, of = operand.get("indicator"), operand.get("of", "self")
        if n in POSITION_INDICATORS and side == "entry":
            out.append(
                Finding(
                    3,
                    "block",
                    "VALIDATION",
                    f"{_label(operand)} 는 사기 전에는 없어서 살 때 조건에 쓸 수 없어요",
                    path,
                )
            )
        if n in POSITION_INDICATORS and of != "self":
            out.append(Finding(3, "block", "VALIDATION", f"{_label(operand)} 는 그 종목 자신에만 써요", path))
        if of in INDEX_SUBJECTS and n not in INDEX_INDICATORS:
            out.append(
                Finding(3, "block", "VALIDATION", f"지수({of})에는 {_label(operand)} 를 쓸 수 없어요", path)
            )
        if (n == "vix") != (of == "VIX") and n is not None:
            out.append(Finding(3, "block", "VALIDATION", "VIX 는 VIX 지표로만 비교해요", path))
    if op in ("cross_below", "cross_above") and name == "holding_days":
        out.append(Finding(3, "block", "VALIDATION", "보유 일수는 '~이상' 처럼 비교해요", path))
    return out


def _always_both_true(path: str, rule: dict) -> list[Finding]:
    # 같은 지표를 숫자 하나와 "~인 동안" 으로 비교한 경우만 본다(설계 문서 2026-11-02).
    # 살 범위가 팔 범위 안에 다 들어가면, 팔기 우선 규칙 때문에 한 번도 살 수 없다
    entry, exit_ = rule.get("entry"), rule.get("exit")
    if not entry or not exit_ or "left" not in entry:
        return []
    candidates = [exit_] if "left" in exit_ else (exit_.get("any") or [])
    for sell in candidates:
        if (
            not _simple_state(entry)
            or not _simple_state(sell)
            or not _same_operand(entry["left"], sell["left"])
        ):
            continue
        if _interval(entry) and _inside(_interval(entry), _interval(sell)):
            flipped = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}[sell["op"]]
            return [
                Finding(
                    3,
                    "ask",
                    "always_both_true",
                    "사는 조건이 맞는 날은 파는 조건도 늘 맞아요",
                    path,
                    {
                        "entry": _say(entry),
                        "exit": _say(sell),
                        "flip_label": f"{_say({**sell, 'op': flipped}, ending=True)} 팔기",
                    },
                )
            ]
    return []


def _weight_vs_cap(spec_dict: dict) -> list[Finding]:
    cap = (spec_dict.get("constraint") or {}).get("max_weight_per_asset")
    weights = spec_dict.get("weights")
    if cap is None or not isinstance(weights, dict):
        return []
    over = {t: w for t, w in weights.items() if w > cap + 1e-12}
    if not over:
        return []
    return [
        Finding(
            3,
            "ask",
            "weight_vs_cap",
            "지정한 비중이 종목당 최대 비중보다 커요",
            "weights",
            {"cap": cap, "over": over},
        )
    ]


# ── 도우미 ───────────────────────────────────────────────────────────


def _conditions(spec_dict: dict):
    for i, rule in enumerate(spec_dict.get("rules") or []):
        for side in ("entry", "exit"):
            if rule.get(side):
                yield f"rules[{i}].{side}", rule[side]


def _compares(cond: dict) -> list[dict]:
    return cond.get("all") or cond.get("any") or [cond]


def _operands(cond: dict) -> list[dict]:
    out = []
    for compare in _compares(cond):
        out.append(compare["left"])
        if "indicator" in compare["right"]:
            out.append(compare["right"])
    return out


def _same_operand(a: dict, b: dict) -> bool:
    key = ("indicator", "window", "of")
    return all(
        (a.get(k) or ("self" if k == "of" else None)) == (b.get(k) or ("self" if k == "of" else None))
        for k in key
    )


def _simple_state(compare: dict) -> bool:
    return "left" in compare and compare["op"] in STATE_OPS and "value" in compare["right"]


def _interval(compare: dict) -> tuple[float, float, bool, bool] | None:
    # (아래 끝, 위 끝, 아래 끝 포함, 위 끝 포함)
    v, op = compare["right"]["value"], compare["op"]
    inf = float("inf")
    return {
        "<": (-inf, v, False, False),
        "<=": (-inf, v, False, True),
        ">": (v, inf, False, False),
        ">=": (v, inf, True, False),
    }[op]


def _inside(a, b) -> bool:
    (alo, ahi, alo_in, ahi_in), (blo, bhi, blo_in, bhi_in) = a, b
    low_ok = alo > blo or (alo == blo and (blo_in or not alo_in))
    high_ok = ahi < bhi or (ahi == bhi and (bhi_in or not ahi_in))
    return low_ok and high_ok


def _label(operand: dict) -> str:
    name = operand.get("indicator")
    window = operand.get("window")
    names = {
        "close": "종가",
        "ma": "이동평균",
        "rsi": "RSI",
        "ret": "수익률",
        "vol": "변동성",
        "volume_ratio": "거래량 비율",
        "vix": "VIX",
        "entry_return": "산 가격 대비 수익률",
        "holding_days": "보유 일수",
    }
    return f"{window}일 {names.get(name, name)}" if window and name not in ("rsi",) else names.get(name, name)


def _say(compare: dict, *, ending: bool = False) -> str:
    # ending 이면 "~이면" 꼴로 끝낸다("RSI 40 이상이면", "20일 수익률 0% 위면")
    words = {"<": "밑", "<=": "이하", ">": "위", ">=": "이상"}
    if ending:
        words = {"<": "밑이면", "<=": "이하이면", ">": "위면", ">=": "이상이면"}
    value = compare["right"]["value"]
    shown = (
        f"{value * 100:g}%" if compare["left"].get("indicator") in ("ret", "entry_return") else f"{value:g}"
    )
    return f"{_label(compare['left'])} {shown} {words[compare['op']]}"


def _unique(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.stage, f.kind, f.code, f.message, f.path)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def stage_results(findings: list[Finding], stages=(1, 2, 3)) -> list[dict]:
    # 응답의 checks 와 validation_logs 행. 앞 단계에서 막히면 뒤 단계는 돌리지 않은 것으로 적는다
    out, blocked = [], False
    for stage in stages:
        items = [f for f in findings if f.stage == stage]
        if blocked:
            out.append({"stage": stage, "name": STAGE_NAMES[stage], "result": "not_run", "items": []})
            continue
        result = (
            "block"
            if any(f.kind == "block" for f in items)
            else ("ask" if any(f.kind == "ask" for f in items) else ("warn" if items else "pass"))
        )
        blocked = result == "block"
        out.append(
            {
                "stage": stage,
                "name": STAGE_NAMES[stage],
                "result": result,
                "items": [f.public() for f in items],
            }
        )
    return out
