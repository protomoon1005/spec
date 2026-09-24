# 종목 후보 선정 (M1 4단계).
#
# 설명을 docstring 이 아니라 주석에 두는 이유: 이 파일은 as_of 가드 사정권이다.
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상 표 이름을
# 찾으면 CI 가 실패한다. app/views/* 가 같은 이유로 주석을 쓴다.
#
# ── 하는 일 ──────────────────────────────────────────────────────────
# 성향과 요청 조건을 받아 "담을 수 있는 종목" 목록을 만든다. 종목마다 그 성향에서
# 허용되는 비중 범위가 함께 붙는다. 그 범위가 5단계에서 스키마에 박혀,
# LLM 이 애초에 어길 수 없게 된다.
#
# ── 거절을 목록으로 남긴다 ───────────────────────────────────────────
# 담을 수 없는 종목을 조용히 빼면 "그런 종목이 없다" 와 "규칙이 막았다" 를 구분할 수
# 없다. 사용자가 **직접 지목한** 종목이 걸리면 사유와 함께 rejected 에 남긴다.
# 지목하지 않은 종목은 남기지 않는다 — 173종목의 탈락 사유를 다 적을 이유가 없다.
#
# ── 판정 순서가 곧 설명 순서다 ───────────────────────────────────────
#   1) 표에 있는가                없으면 NOT_FOUND
#   2) 지금 거래되는가            아니면 DELISTED
#   3) 레버리지인가               하드캡이 금지하면 LEVERAGE_FORBIDDEN
#   4) 그 성향이 담을 수 있는가   상한이 0 이면 PRESET_ZERO
# 앞에서 걸리면 뒤는 보지 않는다. 사유가 하나여야 사용자에게 설명이 된다.
#
# ── 순서는 항상 같다 ─────────────────────────────────────────────────
# 지목한 종목이 먼저, 그다음 보충분이 종목코드 순으로 붙는다. 같은 요청에 다른
# 후보가 나오면 같은 입력에 다른 전략서가 나온다는 뜻이다.
from __future__ import annotations

from dataclasses import dataclass

from app.repositories import etf_master, presets

# 보충까지 해서 이만큼을 목표로 채운다. 너무 많으면 프롬프트가 길어지고 LLM 이
# 고르기 어려워진다. 너무 적으면 분산이 안 된다.
DEFAULT_TARGET = 10

REASON_NOT_FOUND = "종목을 찾을 수 없다"
REASON_DELISTED = "상장폐지된 종목이다"
REASON_LEVERAGE_FORBIDDEN = "레버리지·인버스는 시스템이 금지한다"
REASON_PRESET_ZERO = "이 성향은 담을 수 없는 위험등급이다"


@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str
    risk_tag: str
    asset_group_id: str | None
    sector_group_id: str | None
    country_group_id: str | None
    weight_min: float
    weight_max: float
    preset_id: int
    requested: bool  # 사용자가 직접 지목했는가


@dataclass(frozen=True)
class Rejection:
    ticker: str
    name: str | None
    reason: str


@dataclass(frozen=True)
class CandidateSet:
    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejection, ...]
    risk_level: int
    preset_version: str

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(candidate.ticker for candidate in self.candidates)


def select(
    *,
    risk_level: int,
    requested_tickers: list[str] | None = None,
    group_ids: list[str] | None = None,
    sectors: list[str] | None = None,
    name_pattern: str | None = None,
    target: int = DEFAULT_TARGET,
    preset_version: str | None = None,
) -> CandidateSet:
    # 지목한 종목을 먼저 판정하고, 모자라면 같은 조건으로 보충한다.
    version = preset_version or presets.active_preset_version()
    if version is None:
        raise LookupError("활성 기준표가 없다 — db/seeds/02_preset_v0_1.sql 적재 여부를 확인할 것")

    bounds = presets.get_asset_bounds(risk_level, preset_version=version)
    leverage_allowed = bool(presets.get_active_hardcap()["leverage_allowed"])

    candidates: list[Candidate] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()

    for ticker, record in _requested_records(requested_tickers or []).items():
        seen.add(ticker)
        if record is None:
            rejected.append(Rejection(ticker=ticker, name=None, reason=REASON_NOT_FOUND))
            continue
        reason = _blocked_reason(record, bounds, leverage_allowed=leverage_allowed)
        if reason is not None:
            rejected.append(Rejection(ticker=ticker, name=record.name, reason=reason))
            continue
        candidates.append(_as_candidate(record, bounds, requested=True))

    # 보충 — 지목한 종목이 있으면 그 업종을 이어서 채운다(사용자 의도에 가깝다).
    fill_sectors = sectors or _sectors_of(candidates) or None
    if len(candidates) < target:
        for record in etf_master.search(
            group_ids=group_ids,
            sectors=fill_sectors,
            name_pattern=name_pattern,
            include_leveraged=False,
        ):
            if len(candidates) >= target:
                break
            if record.ticker in seen:
                continue
            if _blocked_reason(record, bounds, leverage_allowed=leverage_allowed) is not None:
                continue  # 지목하지 않은 종목의 탈락 사유는 남기지 않는다
            seen.add(record.ticker)
            candidates.append(_as_candidate(record, bounds, requested=False))

    return CandidateSet(
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        risk_level=risk_level,
        preset_version=version,
    )


def _requested_records(tickers: list[str]) -> dict[str, etf_master.EtfRecord | None]:
    # 요청한 순서를 지킨다. 없는 종목도 자리를 남겨야 사유를 붙일 수 있다.
    found = etf_master.get_by_tickers(tickers)
    ordered: dict[str, etf_master.EtfRecord | None] = {}
    for ticker in tickers:
        if ticker not in ordered:
            ordered[ticker] = found.get(ticker)
    return ordered


def _blocked_reason(record, bounds, *, leverage_allowed: bool) -> str | None:
    if not record.active:
        return REASON_DELISTED
    if record.is_leveraged and not leverage_allowed:
        return REASON_LEVERAGE_FORBIDDEN
    bound = bounds.get(record.risk_tag) if record.risk_tag else None
    if bound is None or bound.allowed_max <= 0:
        return REASON_PRESET_ZERO
    return None


def _as_candidate(record, bounds, *, requested: bool) -> Candidate:
    bound = bounds[record.risk_tag]
    return Candidate(
        ticker=record.ticker,
        name=record.name,
        risk_tag=record.risk_tag,
        asset_group_id=record.asset_group_id,
        sector_group_id=record.sector_group_id,
        country_group_id=record.country_group_id,
        weight_min=bound.allowed_min,
        weight_max=bound.allowed_max,
        preset_id=bound.preset_id,
        requested=requested,
    )


def _sectors_of(candidates: list[Candidate]) -> list[str]:
    # 미분류 버킷으로는 보충하지 않는다. 173종목 중 138개가 거기 있어서
    # 사실상 "아무거나" 가 된다.
    return sorted(
        {
            c.sector_group_id
            for c in candidates
            if c.sector_group_id and c.sector_group_id != "SECTOR_OTHER"
        }
    )
