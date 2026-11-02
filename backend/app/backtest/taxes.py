# 조건형 백테스트(v0.2)의 세금. DB 를 모르는 순수 함수다.
#
# 설계 문서 "세금과 배당", "데모 범위와 조건형 백테스트" 5번(2026-11-02).
# 데모에서는 세율 표(tax_rates)를 만들지 않고 여기 상수로 둔다.
#
# ── 종류 셋 ──────────────────────────────────────────────────────────
#   stock                 주식. 팔 때 판 금액 x 그 해 증권거래세율(농어촌특별세 포함). 차익은 비과세
#   etf_domestic_equity   국내 주식형 ETF. 거래세도 차익세도 없다
#   etf_other             그 밖의 ETF(채권 · 해외 · 원자재 · 레버리지 · 인버스). 차익 x 15.4%, 손해면 0
# 국내 주식형 ETF 판정: ETF 이고, 자산군 EQUITY, 국가 한국, 레버리지 아님.
# 인버스 ETF 는 원장에서 모두 레버리지로 표시돼 있어 여기서 빠진다(2026-11-02 확인).
#
# ── 증권거래세율 ─────────────────────────────────────────────────────
# 증권거래세법 시행령 제5조(설계 문서 "증권거래세"). 2023~2026 은 코스피 · 코스닥이 같아 시장을 보지 않는다.
# 2021 · 2022 는 설계 문서 표 밖이다. 가격 스냅샷이 2021-11 부터라 기간을 앞당기면 쓰일 수 있어,
# 당시 세율 0.23%(코스피 0.08% + 농어촌특별세 0.15%, 코스닥 0.23%)를 넣었다. 표에 없는 해는 오류로 멈춘다.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

STOCK = "stock"
ETF_DOMESTIC_EQUITY = "etf_domestic_equity"
ETF_OTHER = "etf_other"

STOCK_SELL_TAX_RATE = {
    2021: 0.0023,
    2022: 0.0023,
    2023: 0.0020,
    2024: 0.0018,
    2025: 0.0015,
    2026: 0.0020,
}
ETF_GAIN_TAX_RATE = 0.154


@dataclass(frozen=True)
class InstrumentTaxInfo:
    kind: str | None  # stock · etf (instruments.kind)
    asset_group_id: str | None
    country_group_id: str | None
    is_leveraged: bool


def tax_category(info: InstrumentTaxInfo) -> str:
    if info.kind == "stock":
        return STOCK
    if info.kind != "etf":
        raise ValueError(f"상품 종류를 모른다: {info.kind!r}. 종목 원장을 다시 적재해야 한다")
    if (
        info.asset_group_id == "EQUITY"
        and info.country_group_id == "COUNTRY_KR"
        and not info.is_leveraged
    ):
        return ETF_DOMESTIC_EQUITY
    return ETF_OTHER


def sell_tax(category: str, *, trade_date: date, proceeds: float, gain: float) -> float:
    # proceeds = 판 금액(체결가 x 수량), gain = (판 체결가 - 산 체결가) x 수량
    if category == STOCK:
        rate = STOCK_SELL_TAX_RATE.get(trade_date.year)
        if rate is None:
            raise ValueError(f"{trade_date.year}년 증권거래세율이 표에 없다")
        return proceeds * rate
    if category == ETF_DOMESTIC_EQUITY:
        return 0.0
    if category == ETF_OTHER:
        return max(gain, 0.0) * ETF_GAIN_TAX_RATE
    raise ValueError(f"세금 종류를 모른다: {category!r}")
