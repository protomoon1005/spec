# 사용자가 원했지만 못 담은 것 — 작업 결과의 excluded (M1, 2026-10-27 사용자 결정).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 모양은 고정이다 ──────────────────────────────────────────────────
#   {"ticker": str | None, "name": str, "reason_code": str, "reason": str}
# 화면이 이 모양으로 그린다. 스키마(표)에는 남기지 않고 compile_spec 작업 결과에만 싣는다.
#
# ── 두 종류 ──────────────────────────────────────────────────────────
#   종목 단위   사용자가 지목한 종목이 후보 판정에서 걸렸다(candidates.rejected 그대로).
#               사유 코드: NOT_FOUND · DELISTED · LEVERAGE_FORBIDDEN · PRESET_ZERO
#   묶음 단위   사용자가 가리킨 자산군·업종·위험등급 묶음이 이 성향에서 **통째로** 못 담긴다.
#               ticker 는 None, name 은 묶음 이름. 사유 코드 GROUP_PRESET_ZERO.
#               예: 안정투자형이 "수익률 높게" → kw:수익 이 BBL 의 FL_HIGH_RISK_ONLY(G1·G2) 에
#               걸리는데 성향 1 은 G1·G2 상한이 0 이다.
#
# 가리켰다는 판정은 의도(intent) 에서만 한다 — asset_groups · sectors, 그리고 사용자 말(kw:)에
# 걸린 BBL 필터 블록 중 자산군(group_id) 이나 위험등급(risk_tag) 으로 거르는 것. 묶음 안에
# 담을 수 있는 종목이 하나라도 있으면 남기지 않는다("일부만 막혔다" 는 제외가 아니다).
# 레버리지·상장폐지 종목만 있는 묶음도 남기지 않는다 — 그건 성향 때문이 아니다.
from __future__ import annotations

from app.m1.candidates import CODE_PRESET_ZERO, CandidateSet
from app.m1.intent import ResolvedIntent
from app.repositories import bbl, etf_master, presets

CODE_GROUP_PRESET_ZERO = "GROUP_PRESET_ZERO"
OTHER_SECTOR = "SECTOR_OTHER"

RISK_LEVEL_LABELS = {1: "안정투자형", 2: "안정추구형", 3: "위험중립형", 4: "성장투자형", 5: "공격투자형"}


def build(candidate_set: CandidateSet, resolved: ResolvedIntent | None = None) -> list[dict]:
    level = candidate_set.risk_level
    out = [_ticker_entry(r, level) for r in candidate_set.rejected]
    if resolved is not None:
        out += _group_entries(candidate_set, resolved)
    return out


def _level_name(level: int) -> str:
    return f"성향 {level}({RISK_LEVEL_LABELS.get(level, level)})"


def _ticker_entry(rejection, level: int) -> dict:
    reason = rejection.reason
    if rejection.code == CODE_PRESET_ZERO:
        grade = f" {rejection.risk_tag}" if rejection.risk_tag else ""
        reason = f"{_level_name(level)} 의{grade} 허용 상한이 0 이다"
    return {
        "ticker": rejection.ticker,
        "name": rejection.name or rejection.ticker,
        "reason_code": rejection.code,
        "reason": reason,
    }


def _group_entries(candidate_set: CandidateSet, resolved: ResolvedIntent) -> list[dict]:
    level = candidate_set.risk_level
    bounds = presets.get_asset_bounds(level, preset_version=candidate_set.preset_version)
    asset_labels = presets.list_group_labels(1)
    sector_labels = presets.list_group_labels(2)

    targets: list[tuple[str, dict]] = []  # (묶음 이름, etf_master.search 인자)
    for group in resolved.intent.asset_groups:
        targets.append((asset_labels.get(group, group), {"group_ids": [group]}))
    for sector in resolved.intent.sectors:
        if sector != OTHER_SECTOR:
            targets.append((f"{sector_labels.get(sector, sector)} 업종", {"sectors": [sector]}))
    targets += _filter_targets(resolved.intent.keywords, asset_labels)

    out: list[dict] = []
    seen: set[str] = set()
    for name, query in targets:
        if name in seen:
            continue
        seen.add(name)
        records = etf_master.search(include_leveraged=False, **query)
        if not records or any(_holdable(r, bounds) for r in records):
            continue
        grades = sorted({r.risk_tag for r in records if r.risk_tag})
        out.append(
            {
                "ticker": None,
                "name": name,
                "reason_code": CODE_GROUP_PRESET_ZERO,
                "reason": f"{_level_name(level)} 의 {'·'.join(grades)} 허용 상한이 0 이다"
                f" — {name} {len(records)}종목을 하나도 담을 수 없다",
            }
        )
    return out


def _filter_targets(keywords: tuple[str, ...], asset_labels: dict[str, str]) -> list[tuple[str, dict]]:
    # 사용자 말(kw:)에 걸린 필터 블록 중 자산군 · 위험등급으로 거르는 것만.
    said = {f"kw:{value}" for value in keywords}
    out: list[tuple[str, dict]] = []
    for block in bbl.list_blocks("filter"):
        params = block.params_schema or {}
        if not (said & set(block.tags)):
            continue
        column, value = params.get("column"), params.get("value")
        values = value if isinstance(value, list) else [value]
        if column == "group_id":
            out.append((asset_labels.get(values[0], values[0]), {"group_ids": values}))
        elif column == "risk_tag":
            title = (block.title or block.block_id).removesuffix("만").strip()
            out.append((f"{title}({'·'.join(values)})", {"risk_tags": values}))
    return out


def _holdable(record, bounds) -> bool:
    bound = bounds.get(record.risk_tag) if record.risk_tag else None
    return bound is not None and bound.allowed_max > 0
