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
#   - 숫자가 없는 말("크게 잃지 않게")은 한도를 만들지 않는다
#
# 사용자가 말한 값을 그대로 쓴다. 성향 한도나 시스템 상한으로 자르지 않는다(주제 변경 결정 3).
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

MAX_DRAWDOWN_FIELD = "constraint.max_drawdown"

_LOSS_WORD = re.compile(r"잃|손실|손해|낙폭|MDD|mdd|빠지|빠져|빠질|떨어지|떨어져|떨어질|하락|깨지|까먹")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|％|퍼센트|프로)")
# 절을 나누는 자리. 문장부호(소수점은 빼고)와 "~고 · ~는데 · ~지만 · ~면서 · ~되" 뒤 띄어쓰기에서 끊는다.
# "~되" 를 빼면 "반도체 10% 싣되 크게 잃지 않게" 의 비중 10% 가 손실 한도로 읽힌다.
_CLAUSE_BREAK = re.compile(r"[,!?;\n，。]|\.(?!\d)|(?<=는데)\s|(?<=지만)\s|(?<=면서)\s|(?<=고)\s|(?<=되)\s")
@dataclass(frozen=True)
class LossLimit:
    # 추출 근거. 작업 결과(completed 이벤트의 loss_limit)에 그대로 싣는다.
    field: str
    matched: str  # 문장에서 읽은 절
    requested: float  # 사용자가 말한 값(비율)
    applied: float  # 전략서에 넣은 값. 사용자가 말한 값 그대로다

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


def loss_limit_for(text: str) -> LossLimit | None:
    found = find_loss_limit(text)
    if found is None:
        return None
    requested, clause = found
    return LossLimit(field=MAX_DRAWDOWN_FIELD, matched=clause, requested=requested, applied=requested)


def apply_loss_limit(raw: dict, limit: LossLimit | None) -> dict:
    # LLM 출력의 max_drawdown 을 덮어쓴다. 원래 dict 는 건드리지 않는다.
    if limit is None or not isinstance(raw, dict):
        return raw
    out = dict(raw)
    constraint = dict(out.get("constraint") or {})
    constraint["max_drawdown"] = limit.applied
    out["constraint"] = constraint
    return out


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))
