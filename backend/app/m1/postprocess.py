# 전략서 후처리 — 파싱·검사·저장 (M1 5단계).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 스키마가 막지 못하는 것만 여기서 본다 ────────────────────────────
# 종목 목록과 비중 범위는 스키마가 이미 강제했다. JSON Schema 로 표현할 수 없는 것은
# **칸끼리의 관계**뿐이라 그것만 확인한다.
#   - weight_min 이 weight_max 보다 크지 않은가
#   - 비중 하한의 합과 최소 현금이 1 을 넘지 않는가 (그러면 실행이 불가능하다)
#   - 같은 종목이 두 번 들어오지 않았는가
#
# 후보 밖 종목과 범위 이탈도 다시 본다. **Ollama 는 형식 강제가 약해 스키마를 어긴
# 출력이 실제로 나온다.** 로컬 개발이 전부 Ollama 이므로 여기가 유일한 방어선이다.
#
# ── 하드캡은 여기서 접지 않는다 ──────────────────────────────────────
# 2026-09-20 팀 결정: 프리셋은 M1 이 스키마로 "애초에 못 어기게" 하고, 하드캡은
# Validator 가 "접는다". 그래서 저장할 때 원출력과 확정값이 같고 was_adjusted 는
# false 다. **Validator 본체가 아직 없어 하드캡이 아무 데서도 안 걸린다** —
# 마일스톤 리스크에 적어 뒀다.
from __future__ import annotations

from dataclasses import dataclass

from app.contracts.spec import SpecV0_1
from app.m1.candidates import CandidateSet
from app.repositories import presets, specs

WEIGHT_TOLERANCE = 1e-9


class CompileError(ValueError):
    """LLM 출력이 쓸 수 없는 상태다. 되묻거나 다시 생성해야 한다."""


@dataclass(frozen=True)
class CompiledSpec:
    spec: SpecV0_1
    candidate_set: CandidateSet


def parse(raw: dict, candidate_set: CandidateSet, *, spec_id: str, user_id: int) -> CompiledSpec:
    # 식별 정보는 LLM 이 정하게 두지 않는다. 사용자 번호를 지어내면 남의 전략서가 되고,
    # 전략서 번호가 겹치면 저장이 깨진다.
    payload = dict(raw)
    payload["spec_id"] = spec_id
    payload["user_id"] = user_id
    payload.setdefault("spec_version", SPEC_VERSION)

    # 종목명은 모델이 쓴 것을 믿지 않고 우리가 아는 값으로 덮어쓴다. 스키마에서
    # 이름을 고정하지 않은 이유이기도 하다 — 문법을 키우지 않으려고 뺐다.
    known = {c.ticker: c.name for c in candidate_set.candidates}
    for item in payload.get("universe") or []:
        if isinstance(item, dict) and item.get("ticker") in known:
            item["name"] = known[item["ticker"]]

    try:
        spec = SpecV0_1(**payload)
    except Exception as exc:  # noqa: BLE001 — 형식 위반은 원인을 그대로 올린다
        raise CompileError(f"전략서 형식을 벗어났다: {exc}") from exc

    _check_universe(spec, candidate_set)
    _check_feasible(spec)
    return CompiledSpec(spec=spec, candidate_set=candidate_set)


SPEC_VERSION = "0.1"


def _check_universe(spec: SpecV0_1, candidate_set: CandidateSet) -> None:
    allowed = {c.ticker: c for c in candidate_set.candidates}

    seen: set[str] = set()
    for item in spec.universe:
        candidate = allowed.get(item.ticker)
        if candidate is None:
            raise CompileError(f"후보에 없는 종목이다: {item.ticker}")
        if item.ticker in seen:
            raise CompileError(f"같은 종목이 두 번 들어왔다: {item.ticker}")
        seen.add(item.ticker)

        if item.weight_min > item.weight_max + WEIGHT_TOLERANCE:
            raise CompileError(
                f"{item.ticker}: 하한이 상한보다 크다 ({item.weight_min} > {item.weight_max})"
            )
        if item.weight_max > candidate.weight_max + WEIGHT_TOLERANCE:
            raise CompileError(
                f"{item.ticker}: 이 성향의 상한을 넘었다 "
                f"({item.weight_max} > {candidate.weight_max})"
            )
        if item.weight_min < candidate.weight_min - WEIGHT_TOLERANCE:
            raise CompileError(
                f"{item.ticker}: 이 성향의 하한보다 낮다 "
                f"({item.weight_min} < {candidate.weight_min})"
            )


def _check_feasible(spec: SpecV0_1) -> None:
    # 하한의 합과 최소 현금이 1 을 넘으면 어떤 비중을 골라도 만족시킬 수 없다.
    floor = sum(item.weight_min for item in spec.universe) + spec.constraint.cash_min
    if floor > 1 + WEIGHT_TOLERANCE:
        raise CompileError(
            f"비중 하한의 합({floor - spec.constraint.cash_min:.2f})과 "
            f"최소 현금({spec.constraint.cash_min:.2f})을 더하면 1 을 넘는다"
        )


def save(
    compiled: CompiledSpec,
    *,
    user_id: int,
    profile_id: int,
    input_prompt: str | None = None,
) -> specs.SavedSpec:
    hardcap = presets.get_active_hardcap()
    if hardcap is None:
        raise CompileError("활성 하드캡이 없다 — db/seeds/01_hardcap_v0_1.sql 적재 여부를 확인할 것")

    spec = compiled.spec
    by_ticker = {c.ticker: c for c in compiled.candidate_set.candidates}
    universe = [
        {
            "ticker": item.ticker,
            "preset_id": by_ticker[item.ticker].preset_id,
            # 스키마가 이미 허용 범위를 강제했으므로 원출력이 곧 확정값이다.
            # 하드캡으로 접는 일은 Validator 몫이고, 그때 이 둘이 갈라진다.
            "weight_min": item.weight_min,
            "weight_max": item.weight_max,
            "weight_min_raw": item.weight_min,
            "weight_max_raw": item.weight_max,
            "was_adjusted": False,
        }
        for item in spec.universe
    ]

    return specs.insert_spec(
        spec_id=spec.spec_id,
        user_id=user_id,
        profile_id=profile_id,
        hardcap_version=hardcap["hardcap_version"],
        spec_version=spec.spec_version,
        name=spec.name,
        input_prompt=input_prompt,
        rebalance=spec.rebalance.model_dump(mode="json"),
        signal_rules=spec.signal_rules.model_dump(mode="json"),
        constraint=spec.constraint.model_dump(mode="json"),
        universe=universe,
    )
