"""FastAPI 엔트리.

라우터 6종(auth·profile·spec·backtest·portfolio·admin) + jobs(202/SSE 패턴)를
붙인다. 본체는 auth/login·refresh, spec/compile, jobs/stream을 뺀 나머지가
전부 501이다 — JWT+RBAC 의존성 체인은 그 501 앞에서 실제로 걸린다.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import admin, auth, backtest, health, jobs, portfolio, profile, spec

API_DESCRIPTION = """
사용자가 말로 요청하면 그걸 **전략서(Spec)** 로 만들고, 그 전략서대로 ETF를 사고파는
모의투자 시스템이다. 실제 돈은 쓰지 않는다.

### 어디까지 AI가 하나

1. **전략 만들기** — AI가 한다. 사용자가 쓴 문장을 전략서로 바꾼다
2. **전략 검사** — AI 안 쓴다. 규칙에 어긋난 전략을 거른다
3. **사고팔 판단** — 학습한 모델이 한다. 챗봇이 아니다
4. **주문 내기** — AI 안 쓴다. 정해진 계산대로만 한다

### 지금 실제로 되는 것

로그인 · 토큰 갱신 · 서버 상태 확인 · 전략 만들기 요청(접수까지) · 작업 진행 상황 보기.

**나머지는 전부 501(아직 안 만듦)이다.** 주소와 주고받을 데이터 형식만 정해 둔 상태이고
내용은 담당자가 채운다. 501이 떠도 로그인 검사는 그보다 먼저 걸린다 — 토큰 없이 부르면 401이다.

### 로그인하는 법

`POST /auth/login` 으로 `access_token` 을 받아 우측 상단 **Authorize** 에 넣으면 로그인이 유지된다.
누가 보낸 요청인지는 이 토큰으로 판단한다 — 요청 내용에 사용자 번호를 적는 칸은 없다.
"""

TAGS_METADATA = [
    {
        "name": "health",
        "description": "서버가 살아 있는지, 데이터베이스 같은 것들이 잘 붙어 있는지 본다.",
    },
    {
        "name": "auth",
        "description": "로그인. **실제로 된다.** 회원가입은 아직 없어서 "
        "계정은 데이터베이스에 직접 넣어야 한다.",
    },
    {
        "name": "profile",
        "description": "투자 성향 설문. 성향(1~5)이 정해져야 "
        "어떤 ETF를 얼마나 담을 수 있는지가 정해진다.",
    },
    {
        "name": "spec",
        "description": "사용자 요청을 전략서로 바꾸는 곳. 요청 접수는 되고, 변환은 만드는 중이다.",
    },
    {
        "name": "jobs",
        "description": "오래 걸리는 작업이 어디까지 갔는지 실시간으로 본다. **실제로 된다.**",
    },
    {"name": "backtest", "description": "만든 전략을 과거 데이터에 돌려보고 성적을 본다."},
    {"name": "portfolio", "description": "지금 굴리고 있는 전략의 상태."},
    {"name": "admin", "description": "모든 전략에 공통으로 걸리는 상한선 관리. **관리자만** 들어간다."},
]

app = FastAPI(
    title="Spec — 국내 ETF 모의운용 API",
    version="0.1.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(spec.router)
app.include_router(backtest.router)
app.include_router(portfolio.router)
app.include_router(admin.router)
app.include_router(jobs.router)
