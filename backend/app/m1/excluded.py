# 사용자가 원했지만 못 담은 것 — 작업 결과의 excluded (M1, 2026-10-27 사용자 결정).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 모양은 고정이다 ──────────────────────────────────────────────────
#   {"ticker": str | None, "name": str, "reason_code": str, "reason": str}
# 화면이 이 모양으로 그린다. 스키마(표)에는 남기지 않고 compile_spec 작업 결과에만 싣는다.
#
# 사용자가 지목한 종목이 후보 판정에서 걸린 것만 남긴다(candidates.rejected 그대로).
# 사유 코드: NOT_FOUND · DELISTED
from __future__ import annotations

from app.m1.candidates import CandidateSet


def build(candidate_set: CandidateSet) -> list[dict]:
    return [
        {
            "ticker": r.ticker,
            "name": r.name or r.ticker,
            "reason_code": r.code,
            "reason": r.reason,
        }
        for r in candidate_set.rejected
    ]
