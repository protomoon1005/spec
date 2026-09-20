# 컴파일 프롬프트 (M1 5단계).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 프롬프트가 할 일은 "고르게 하는 것" 이다 ─────────────────────────
# 무엇을 쓸 수 있는지는 이미 스키마가 막고 있다. 프롬프트는 **후보 중에서 왜 그것을
# 고르는지** 를 판단하게 하는 데 쓴다. 스키마가 이미 강제하는 것을 말로 또 시키면
# 길어지기만 하고 지켜지지도 않는다.
#
# ── 그래도 적어 주는 것 ──────────────────────────────────────────────
# 스키마로 표현할 수 없는 것만 적는다.
#   - 비중 하한의 합이 1 을 넘으면 안 된다 (칸끼리의 관계라 스키마로 못 막는다)
#   - 현금을 남겨야 한다
#   - 종목을 몇 개쯤 담는 게 좋은지
# 이것들은 어차피 후처리가 다시 확인한다. 프롬프트는 첫 시도를 좋게 만들 뿐이다.
from __future__ import annotations

from app.m1.candidates import CandidateSet
from app.repositories.bbl import Block

RISK_LABELS = {
    1: "안정투자형(가장 보수적)",
    2: "안정추구형",
    3: "위험중립형",
    4: "성장투자형",
    5: "공격투자형(가장 공격적)",
}

SYSTEM_PROMPT = (
    "너는 투자 전략서를 만드는 도구다. "
    "주어진 후보 종목 중에서만 고르고, 정해진 비중 범위를 벗어나지 않는다. "
    "설명하지 말고 JSON 만 낸다."
)


def build_compile_prompt(
    *,
    user_text: str,
    candidate_set: CandidateSet,
    blocks: list[Block] | None = None,
    defaults: dict[str, float] | None = None,
) -> str:
    lines = [
        f"사용자 요청: {user_text}",
        "",
        f"투자 성향: {RISK_LABELS.get(candidate_set.risk_level, candidate_set.risk_level)}",
        "",
        "고를 수 있는 종목과 비중 범위:",
    ]
    for candidate in candidate_set.candidates:
        mark = " (사용자가 직접 지목함)" if candidate.requested else ""
        lines.append(
            f"  - {candidate.ticker} {candidate.name} "
            f"[{candidate.weight_min:.2f} ~ {candidate.weight_max:.2f}]{mark}"
        )

    if candidate_set.rejected:
        lines += ["", "사용자가 말했지만 담을 수 없는 종목 (전략서에 넣지 마라):"]
        lines += [
            f"  - {r.ticker} {r.name or ''} — {r.reason}".rstrip()
            for r in candidate_set.rejected
        ]

    if blocks:
        lines += ["", "요청과 맞아 보이는 구성 요소:"]
        lines += [f"  - {b.title}: {b.description}" for b in blocks if b.title]

    if defaults:
        lines += [
            "",
            "이 성향의 기본 제약 (사용자가 달리 말하지 않았으면 그대로 써라):",
            f"  - 최소 현금 비중 {defaults['cash_min_default']:.2f}",
            f"  - 최대 낙폭 {defaults['max_drawdown_default']:.2f}",
            f"  - 1회 최대 손실 {defaults['max_loss_per_trade_default']:.2f}",
        ]

    lines += [
        "",
        "지켜야 할 것:",
        "  - 종목별 weight_min 의 합과 최소 현금 비중을 더해 1 을 넘지 않게 하라",
        "  - weight_min 은 weight_max 보다 클 수 없다",
        "  - 후보가 여럿이면 한 종목에 몰지 말고 나눠 담아라",
        "  - name 은 전략을 한눈에 알아볼 수 있는 짧은 한국어 이름으로 지어라",
    ]
    return "\n".join(lines)
