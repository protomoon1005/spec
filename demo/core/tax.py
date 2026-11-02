"""세율 표 조회 (config/tax_rates.yaml). 세율을 코드에 박지 않고 데이터로 둔다.

- sell_tax_rate: 매도금액에 매기는 세율 = 거래세(TRANSACTION) + 농특세(RURAL). 거래한 날짜 기준.
- gain_tax_rate: 차익에 매기는 세율(GAIN). 그 밖의 ETF 만 15.4%, 나머지는 0.
- 표에 없는 분류·날짜는 ValueError. 짐작해서 다른 해 세율을 쓰지 않는다.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from core.dictionary import TAX_CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
TAX_RATES_PATH = ROOT / "config" / "tax_rates.yaml"


@lru_cache(maxsize=None)
def _load(path: str) -> tuple[dict, ...]:
    with open(path, encoding="utf-8") as f:
        rows = yaml.safe_load(f)["rates"]
    for r in rows:
        if r["applies_to"] not in TAX_CATEGORIES:
            raise ValueError(f"세율 표: 모르는 분류 {r['applies_to']}")
        if not isinstance(r["valid_from"], date) or not (r["valid_to"] is None or isinstance(r["valid_to"], date)):
            raise ValueError(f"세율 표: 날짜 형식 오류 {r}")
    return tuple(rows)


def load_tax_rates(path: Path | str = TAX_RATES_PATH) -> list[dict]:
    return [dict(r) for r in _load(str(path))]


def _covering(category: str, kind: str, day: date) -> list[dict]:
    if category not in TAX_CATEGORIES:
        raise ValueError(f"세율 분류가 아닙니다: {category}")
    return [r for r in _load(str(TAX_RATES_PATH))
            if r["applies_to"] == category and r["tax_kind"] == kind
            and r["valid_from"] <= day and (r["valid_to"] is None or day <= r["valid_to"])]


def _as_date(day) -> date:
    return day.date() if hasattr(day, "date") and callable(day.date) else day


def sell_tax_rate(category: str, day) -> float:
    day = _as_date(day)
    tx = _covering(category, "TRANSACTION", day)
    if len(tx) != 1:
        raise ValueError(f"세율 표에 {category} {day} 의 거래세가 {'없' if not tx else '여러 개 있'}습니다.")
    return float(tx[0]["rate"]) + sum(float(r["rate"]) for r in _covering(category, "RURAL", day))


def gain_tax_rate(category: str, day) -> float:
    day = _as_date(day)
    rows = _covering(category, "GAIN", day)
    if not rows:
        has_any = any(r["applies_to"] == category and r["tax_kind"] == "GAIN" for r in _load(str(TAX_RATES_PATH)))
        if has_any:
            raise ValueError(f"세율 표에 {category} {day} 의 차익 세율이 없습니다.")
        return 0.0
    return float(rows[0]["rate"])


def rate_history(category: str) -> list[tuple[date, date | None, float]]:
    """재서술용: (시작, 끝, 매도 세율 합계) 목록."""
    rows = load_tax_rates()
    starts = sorted({r["valid_from"] for r in rows if r["applies_to"] == category
                     and r["tax_kind"] in ("TRANSACTION", "RURAL")})
    return [(s, _covering(category, "TRANSACTION", s)[0]["valid_to"], sell_tax_rate(category, s)) for s in starts]
