# 종목 후보 선정 (M1 4단계).
#
# 설명을 docstring 이 아니라 주석에 두는 이유: 이 파일은 as_of 가드 사정권이다.
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상 표 이름을
# 찾으면 CI 가 실패한다.
#
# ── 하는 일 ──────────────────────────────────────────────────────────
# 요청 조건을 받아 "담을 수 있는 종목" 목록을 만든다. 시스템 상한(성향, 하드캡)이 없으므로
# 종목마다 비중 범위는 0 부터 1 까지다(주제 변경 결정 3). 사용자가 말한 비중은 LLM 이
# 이 범위 안에서 정한다.
#
# ── 거절을 목록으로 남긴다 ───────────────────────────────────────────
# 담을 수 없는 종목을 조용히 빼면 "그런 종목이 없다" 와 "거래할 수 없다" 를 구분할 수
# 없다. 사용자가 직접 지목한 종목이 걸리면 사유와 함께 rejected 에 남긴다.
# 지목하지 않은 종목은 남기지 않는다.
#
# ── 판정 순서가 곧 설명 순서다 ───────────────────────────────────────
#   1) 표에 있는가         없으면 NOT_FOUND
#   2) 지금 거래되는가     아니면 DELISTED
# 앞에서 걸리면 뒤는 보지 않는다. 사유가 하나여야 사용자에게 설명이 된다.
#
# ── 순서는 항상 같다 ─────────────────────────────────────────────────
# 지목한 종목이 먼저, 그다음 보충분이 종목코드 순으로 붙는다. 같은 요청에 다른
# 후보가 나오면 같은 입력에 다른 전략서가 나온다는 뜻이다.
from __future__ import annotations

from dataclasses import dataclass

from app.repositories import etf_master

# 보충까지 해서 이만큼을 목표로 채운다. 너무 많으면 프롬프트가 길어지고 LLM 이
# 고르기 어려워진다. 너무 적으면 분산이 안 된다.
DEFAULT_TARGET = 10

# 시스템 상한이 없으므로 모든 후보의 비중 범위가 같다.
WEIGHT_MIN = 0.0
WEIGHT_MAX = 1.0

REASON_NOT_FOUND = "종목을 찾을 수 없다"
REASON_DELISTED = "상장폐지된 종목이다"

# 작업 결과(excluded)에 싣는 사유 코드. 화면이 분기할 때 쓴다(m1/excluded.py).
CODE_NOT_FOUND = "NOT_FOUND"
CODE_DELISTED = "DELISTED"

REASON_CODES = {
    REASON_NOT_FOUND: CODE_NOT_FOUND,
    REASON_DELISTED: CODE_DELISTED,
}


@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str
    asset_group_id: str | None
    sector_group_id: str | None
    country_group_id: str | None
    weight_min: float
    weight_max: float
    requested: bool  # 사용자가 직접 지목했는가
    trait: str | None = None  # 요청한 ETF 특성(배당 등)에 맞아 앞에 둔 종목이면 그 특성 이름


@dataclass(frozen=True)
class Rejection:
    ticker: str
    name: str | None
    reason: str

    @property
    def code(self) -> str:
        return REASON_CODES.get(self.reason, self.reason)


@dataclass(frozen=True)
class CandidateSet:
    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(candidate.ticker for candidate in self.candidates)


def select(
    *,
    requested_tickers: list[str] | None = None,
    group_ids: list[str] | None = None,
    sectors: list[str] | None = None,
    name_pattern: str | None = None,
    target: int = DEFAULT_TARGET,
    traits: list[tuple[str, str]] | None = None,
) -> CandidateSet:
    # 지목한 종목을 먼저 판정하고, 모자라면 같은 조건으로 보충한다.
    candidates: list[Candidate] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()

    for ticker, record in _requested_records(requested_tickers or []).items():
        seen.add(ticker)
        if record is None:
            rejected.append(Rejection(ticker=ticker, name=None, reason=REASON_NOT_FOUND))
            continue
        if not record.active:
            rejected.append(Rejection(ticker=ticker, name=record.name, reason=REASON_DELISTED))
            continue
        candidates.append(_as_candidate(record, requested=True))

    # 보충 — 지목한 종목이 있으면 그 업종을 이어서 채운다(사용자 의도에 가깝다).
    fill_sectors = sectors or _sectors_of(candidates) or None

    # 요청한 특성(배당 등)에 맞는 종목을 먼저 둔다. 걸러 내지 않고 앞에 둘 뿐이다 — 특성과
    # 업종이 겹치지 않는 요청에서 후보가 비지 않게. 특성마다 따로 찾는 건 정규식이 Postgres
    # 문법이라 여기서 다시 판정하지 않으려는 것이다.
    for title, pattern in traits or []:
        for record in etf_master.search(group_ids=group_ids, sectors=fill_sectors, name_pattern=pattern):
            if len(candidates) >= target:
                break
            if record.ticker in seen or not record.active:
                continue
            seen.add(record.ticker)
            candidates.append(_as_candidate(record, requested=False, trait=title))

    if len(candidates) < target:
        for record in etf_master.search(group_ids=group_ids, sectors=fill_sectors, name_pattern=name_pattern):
            if len(candidates) >= target:
                break
            if record.ticker in seen or not record.active:
                continue  # 지목하지 않은 종목의 탈락 사유는 남기지 않는다
            seen.add(record.ticker)
            candidates.append(_as_candidate(record, requested=False))

    return CandidateSet(candidates=tuple(candidates), rejected=tuple(rejected))


def _requested_records(tickers: list[str]) -> dict[str, etf_master.EtfRecord | None]:
    # 요청한 순서를 지킨다. 없는 종목도 자리를 남겨야 사유를 붙일 수 있다.
    found = etf_master.get_by_tickers(tickers)
    ordered: dict[str, etf_master.EtfRecord | None] = {}
    for ticker in tickers:
        if ticker not in ordered:
            ordered[ticker] = found.get(ticker)
    return ordered


def _as_candidate(record, *, requested: bool, trait: str | None = None) -> Candidate:
    return Candidate(
        ticker=record.ticker,
        name=record.name,
        asset_group_id=record.asset_group_id,
        sector_group_id=record.sector_group_id,
        country_group_id=record.country_group_id,
        weight_min=WEIGHT_MIN,
        weight_max=WEIGHT_MAX,
        requested=requested,
        trait=trait,
    )


def _sectors_of(candidates: list[Candidate]) -> list[str]:
    # 미분류 버킷으로는 보충하지 않는다. 대부분의 종목이 거기 있어서 사실상 "아무거나" 가 된다.
    return sorted(
        {
            c.sector_group_id
            for c in candidates
            if c.sector_group_id and c.sector_group_id != "SECTOR_OTHER"
        }
    )
