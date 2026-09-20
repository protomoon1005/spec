# 거시지표 코드 레지스트리 — M3 가 실제로 조회 가능한 코드 집합의 정본.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/views 모듈과 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴에서 가드 대상 테이블명을 찾는다.
#
# ── 왜 이 파일이 필요한가 ────────────────────────────────────────────
# Spec 의 signal_rules.market_temperature 는 trend_index · volatility_index 를
# **코드 문자열**로 가리킨다. 허용값 집합은 BBL 확정 후 M1 이 채우기로 돼 있는데,
# M3 가 조회 가능한 코드를 먼저 주지 않으면 M1 이 없는 코드를 지어내 Spec 에 넣는다.
# 그래서 이 표가 M1 에게 넘기는 목록이다. available=False 인 코드는 아직 데이터를
# 못 받는다는 뜻이고, M1 은 그 코드를 Spec 에 넣으면 안 된다.
#
# ── released_at 추정 규칙 ────────────────────────────────────────────
# macro_indicators 는 as_of(지표가 가리키는 시점)와 released_at(공표 시점)을 따로
# 갖고, 조회가 released_at <= as_of 로도 거른다. 공표 전 값이 과거 판단에 섞이면
# 미래를 미리 보는 것이기 때문이다.
#
# FRED 는 시계열 관측값에 공표일을 함께 주지 않는다(ALFRED 의 vintage 기능을 쓰면
# 받을 수 있지만 별도 질의가 필요하다). 그래서 release_lag_days 로 **추정**한다.
# **시리즈마다 다르다.** 관측 빈도가 일별이어도 갱신 주기는 제각각이라, 전부
# +1일로 뭉뚱그리면 낙관적이고 그 차이만큼 미래를 미리 보게 된다.
# 2026-09-12 실측(오늘 기준 최신 관측일까지의 지연):
#     VIXCLS   최신 2026-09-10 -> 2일 지연
#     BAA10Y   최신 2026-09-10 -> 2일 지연
#     DEXKOUS  최신 2026-09-04 -> **8일 지연** (H.10 은 주 1회 월요일 일괄 갱신)
# 확인이 안 되는 부분은 보수적으로 크게 잡았다. 늦게 잡으면 판단이 보수적으로
# 틀리고, 빠르게 잡으면 미래를 미리 본다 — 후자가 훨씬 나쁘다.
#   월간 지표: lag 를 지표별로 따로 잡는다. 현재 쓰는 월간 지표는 없다.
#
# **released_at 이 추정값이라는 사실을 어딘가 남겨야 한다.** source 컬럼은
# ecos|fred|krx CHECK 가 걸려 있어 여기 못 쓴다. 그래서 이 표의
# released_at_is_estimated 와 README·적재 스크립트 주석에 남긴다. 나중에 정확한
# 공표일을 받게 되면 이 플래그가 켜진 코드를 다시 채우면 된다.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroIndicator:
    code: str  # Spec/BBL 에서 쓰는 코드. 적재 시 indicator_code 컬럼 값이다.
    label: str
    source: str  # 'fred' | 'ecos' | 'krx' — DB CHECK 제약과 같은 집합
    external_id: str  # FRED series id, ECOS 통계표코드 등
    frequency: str  # 'daily' | 'monthly'
    release_lag_days: int
    available: bool  # 지금 실제로 받을 수 있는가
    released_at_is_estimated: bool
    note: str = ""


INDICATORS: tuple[MacroIndicator, ...] = (
    MacroIndicator(
        code="VIX_CLOSE",
        label="CBOE 변동성지수 종가",
        source="fred",
        external_id="VIXCLS",
        frequency="daily",
        release_lag_days=3,
        available=True,
        released_at_is_estimated=True,
        note="실측 지연 2일(2026-09-12). 주말을 넘기는 경우를 덮으려 3일로 잡았다.",
    ),
    MacroIndicator(
        code="CREDIT_SPREAD_BAA10Y",
        label="Baa 회사채 - 10년 국채 스프레드",
        source="fred",
        external_id="BAA10Y",
        frequency="daily",
        release_lag_days=3,
        available=True,
        released_at_is_estimated=True,
        note=(
            "美 신용스프레드다. 국내 신용스프레드(회사채 AA- 3년 - 국고 3년)는 ECOS 인증키가 필요하다. "
            "실측 지연 2일(2026-09-12), 주말을 넘기는 경우를 덮으려 3일."
        ),
    ),
    MacroIndicator(
        code="USDKRW",
        label="원/달러 환율",
        source="fred",
        external_id="DEXKOUS",
        frequency="daily",
        release_lag_days=10,
        available=True,
        released_at_is_estimated=True,
        note=(
            "**H.10 은 주 1회(월요일) 일괄 갱신이라 지연이 크다.** 2026-09-12 실측 8일. "
            "월요일 관측이 다음 월요일에 공표되는 최악의 경우가 7일이고, 공휴일 이동 여유를 "
            "더해 10일로 잡았다. 이 lag 가 부담되면 일별 갱신되는 다른 환율 소스로 "
            "바꿔야 한다 — 다만 시리즈가 바뀌므로 임계값을 다시 재야 한다."
        ),
    ),
    MacroIndicator(
        code="KOSPI200",
        label="코스피200 지수 종가",
        source="krx",
        external_id="KS200",
        frequency="daily",
        release_lag_days=1,
        available=True,
        released_at_is_estimated=True,
        note=(
            "추세 지수. FinanceDataReader 로 받는다. pykrx 는 2026-09-12 실측상 KRX "
            "계정(KRX_ID/KRX_PW)이 있어야 하고 세션 없이는 400 LOGOUT 이 돌아온다. "
            "이건 지수라 ETF 일봉 테이블과 자물쇠를 공유하지 않는다. "
            "**종가만 적재한다** — 이동평균 이격도는 판정 로직이 계산한다."
        ),
    ),
)

BY_CODE: dict[str, MacroIndicator] = {indicator.code: indicator for indicator in INDICATORS}

# Spec 의 signal_rules.market_temperature 가 가리킬 수 있는 값 집합. M1 에게 넘긴다.
TREND_INDEX_CODES: tuple[str, ...] = ("KOSPI200",)
VOLATILITY_INDEX_CODES: tuple[str, ...] = ("VIX_CLOSE",)

# FN-406 이 고정으로 쓰는 입력(Spec 이 고르지 않는다).
CREDIT_SPREAD_CODE = "CREDIT_SPREAD_BAA10Y"
FX_CODE = "USDKRW"


def available_codes() -> tuple[str, ...]:
    return tuple(indicator.code for indicator in INDICATORS if indicator.available)


def estimate_released_at(code: str, as_of_date):
    """공표일 추정. 지표별 lag 를 더한다."""
    from datetime import timedelta

    indicator = BY_CODE.get(code)
    if indicator is None:
        raise ValueError(f"등록되지 않은 지표 코드: {code!r} (아는 것: {sorted(BY_CODE)})")
    return as_of_date + timedelta(days=indicator.release_lag_days)
