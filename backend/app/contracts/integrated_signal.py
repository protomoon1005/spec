# (모듈 설명 일부를 docstring이 아니라 이 주석에 둔다 — scripts/check_asof_guard.py 가
#  문자열 리터럴에서 가드 대상 테이블명을 찾으면 CI를 실패시키는데, docstring도
#  ast.Constant 라 잡힌다. 주석은 AST에 안 잡힌다. 현서가 contracts/view_weights.py
#  에서 쓴 수법 그대로다.)
#
# 이 계약이 앉는 자리: M3 판단 계층이 종목별 신호 하나를 만들고 나면, 그 결과가
# M2의 RiskSizer로 넘어가 리스크 한도 -> 선형매핑 -> 그룹캡 -> 주문으로 이어진다.
# 그 인계 지점의 형식이 이 파일이다. 통합에 쓰인 관점 가중치(view_weights_used)도
# 함께 실어 보내는 이유는, 그 값이 "왜 이 신호가 나왔는가"의 절반이라서다 —
# 판단 시점의 가중치를 결정기록에 같이 남기지 않으면 나중에 재현할 수 없다.
"""계약 ⑤ 통합 신호 출력 형식 (2026-09-12 팀 합의로 계약 승격).

M3(판단 계층)가 세 관점의 점수를 시점별 가중치로 묶어 만든 종목별 신호다.
계약 4종(docs/infra-spec.md 6단계 표)에는 없던 형식이라 M3 내부 모델로 시작했지만,
M2(RiskSizer)와 M4(백테스트 러너)가 읽고 DECISION_RECORDS의 integrated_signal
JSONB 컬럼에 스냅샷으로 그대로 남는다는 점에서 전원 합의가 필요한 계약이라고
판단해 2026-09-12에 계약 ⑤로 승격했다.

**형식만 여기 있다. 통합 알고리즘(FN-407)의 구현은 M3 소유이고
`app/views/integrator.py`의 `integrate()`에 있다.** 계약은 형식이고 구현은
담당 라인의 것이라는 경계를 지키기 위해서다 — 다른 네 계약이 목업만 두고
실구현을 담당 라인에 맡긴 것과 같은 이유다.

신호 s의 값 규약(상세설계서 1.5.4 확정본, 수치 변경 금지):
가중합 m을 `tanh(m / 0.5)`로 스케일한 뒤 `|s| < 0.10`이면 0으로 누른다.
따라서 `signals`의 값은 [-1, 1] 구간이고, 데드존에 눌린 종목은
`deadzone_applied`에 남아 "신호가 없다"와 "판단하지 않았다"가 구분된다.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class IntegratedSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    signals: dict[str, float] = Field(description="ticker -> s, [-1, 1]")
    view_weights_used: dict[str, float] = Field(description="view_type -> w, Sigma w = 1")
    per_view_scores: dict[str, dict[str, float]] = Field(
        description="view_type -> {ticker: s_k}, 통합 전 관점별 원점수"
    )
    deadzone_applied: list[str] = Field(
        default_factory=list, description="|s| < 0.10 이라 0으로 눌린 ticker"
    )
