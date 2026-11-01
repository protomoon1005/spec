# 요청 문장에서 손실 제약을 확정적으로 읽는다 (M1, 2026-10-27 사용자 결정).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 숫자로 말한 손실 한도는 LLM 에게 맡기지 않는다 ───────────────────
# "최대 5%만 잃고" 를 LLM 이 constraint.max_drawdown 에 옮기지 않고 성향 기본값(0.20)을
# 그대로 냈다(2026-10-27 데모 케이스 10). 숫자는 문장에 그대로 있으니 정규식으로 읽어
# 생성 결과를 덮어쓴다. LLM 이 무엇을 내든 같은 문장이면 같은 값이다.
#
#   - 한 절(쉼표·마침표·"~는데/~지만/~고" 로 나눈 조각) 안에 손실 낱말(잃·손실·손해·낙폭·
#     빠지·떨어·하락·MDD …)과 "N%" (퍼센트·프로) 가 함께 있으면 그 N 을 한도로 본다
#   - 한 절에 숫자가 여럿이면 손실 낱말에 가장 가까운 것
#   - 숫자가 없는 말("크게 잃지 않게")은 한도를 만들지 않는다 — 지금처럼 LLM 과 성향 기본값
#
# ── 성향 한도보다 느슨하면 성향 한도로 자른다 ────────────────────────
# 말 한마디로 자기 한도를 올릴 수 없다(intent.py 의 "성향은 여기서 안 정한다" 와 같은 뜻).
# 성향 한도는 risk_profile_defaults.max_drawdown_default 다. 수치는 시드 그대로 쓴다.
# 시스템 상한(하드캡 0.25)은 여기서 보지 않는다 — Validator 4단 몫이다.
#
# ── 손실을 꺼리는가 (채권 보충 판정) ─────────────────────────────────
# 섹터를 지목하고 손실을 꺼리면 후보에 채권을 보충한다(candidates.guard_bonds). 꺼린다는 판정은
#   1) 숫자로 말한 손실 한도가 있다
#   2) BBL 의 저위험 필터 블록(FL_LOW_RISK_ONLY) 의 kw: 낱말(안전하게 · 보수적)이 의도에 있다
#   3) "잃지 않게 · 안 잃게 · 손실은 줄이고 · 원금은 지키고" 류의 말이 문장에 있다
# 3) 은 BBL 에 손실 회피 낱말이 없어서 둔다. "크게 잃지는 않게" 를 의도 추출이 kw:비중유지 로만
# 옮겼다(2026-10-27, qwen3:8b, temperature 0) — LLM 에 기대면 데모 케이스 2 가 흔들린다.
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.repositories import bbl

LOW_RISK_FILTER_BLOCK = "FL_LOW_RISK_ONLY"
MAX_DRAWDOWN_FIELD = "constraint.max_drawdown"

_LOSS_WORD = re.compile(r"잃|손실|손해|낙폭|MDD|mdd|빠지|빠져|빠질|떨어지|떨어져|떨어질|하락|깨지|까먹")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|％|퍼센트|프로)")
# 절을 나누는 자리. 문장부호(소수점은 빼고)와 "~고 · ~는데 · ~지만 · ~면서 · ~되" 뒤 띄어쓰기에서 끊는다.
# "~되" 를 빼면 "반도체 10% 싣되 크게 잃지 않게" 의 비중 10% 가 손실 한도로 읽힌다.
_CLAUSE_BREAK = re.compile(r"[,!?;\n，。]|\.(?!\d)|(?<=는데)\s|(?<=지만)\s|(?<=면서)\s|(?<=고)\s|(?<=되)\s")
_LOSS_AVERSE_PHRASE = re.compile(
    r"잃지\s*(?:는|도|만|를)?\s*않"
    r"|안\s*잃"
    r"|손실\s*(?:은|을|이|는)?\s*(?:최소|줄|적|없|피|막)"
    r"|손해\s*(?:는|를|가)?\s*(?:안|덜|없|피)"
    r"|원금\s*(?:은|을|만|이)?\s*(?:지키|보전|보장|잃지)"
)


@dataclass(frozen=True)
class LossLimit:
    # 추출 근거. 작업 결과(completed 이벤트의 loss_limit)에 그대로 싣는다.
    field: str
    matched: str  # 문장에서 읽은 절
    requested: float  # 사용자가 말한 값(비율)
    applied: float  # 성향 한도로 자른 뒤 전략서에 넣은 값
    profile_limit: float | None  # 성향 한도(max_drawdown_default)
    clamped_to_profile: bool

    def as_dict(self) -> dict:
        return asdict(self)


def find_loss_limit(text: str) -> tuple[float, str] | None:
    # (비율, 근거 절). 손실 한도로 읽히는 숫자가 없으면 None. 첫 번째로 걸린 절을 쓴다.
    for clause in _CLAUSE_BREAK.split(text or ""):
        clause = clause.strip()
        losses = list(_LOSS_WORD.finditer(clause))
        numbers = list(_PERCENT.finditer(clause))
        if not losses or not numbers:
            continue
        best = min(numbers, key=lambda n: min(_gap(n.span(), w.span()) for w in losses))
        value = float(best.group(1)) / 100
        if 0 < value < 1:
            return value, clause
    return None


def loss_limit_for(text: str, *, profile_limit: float | None) -> LossLimit | None:
    found = find_loss_limit(text)
    if found is None:
        return None
    requested, clause = found
    clamped = profile_limit is not None and requested > profile_limit
    return LossLimit(
        field=MAX_DRAWDOWN_FIELD,
        matched=clause,
        requested=requested,
        applied=profile_limit if clamped else requested,
        profile_limit=profile_limit,
        clamped_to_profile=clamped,
    )


def apply_loss_limit(raw: dict, limit: LossLimit | None) -> dict:
    # LLM 출력의 max_drawdown 을 덮어쓴다. 원래 dict 는 건드리지 않는다.
    if limit is None or not isinstance(raw, dict):
        return raw
    out = dict(raw)
    constraint = dict(out.get("constraint") or {})
    constraint["max_drawdown"] = limit.applied
    out["constraint"] = constraint
    return out


def loss_averse_keywords() -> frozenset[str]:
    # BBL 저위험 필터 블록의 kw: 값. 시드가 바뀌면 따라간다.
    block = bbl.get_block(LOW_RISK_FILTER_BLOCK)
    if block is None:
        return frozenset()
    return frozenset(tag.removeprefix("kw:") for tag in block.tags if tag.startswith("kw:"))


def is_loss_averse(text: str, keywords: tuple[str, ...] | list[str], *, has_limit: bool) -> bool:
    if has_limit:
        return True
    if loss_averse_keywords() & set(keywords):
        return True
    return _LOSS_AVERSE_PHRASE.search(text or "") is not None


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))
