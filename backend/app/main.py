"""FastAPI 엔트리. 라우터: health · auth · spec · backtest · jobs(202/SSE 패턴)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, backtest, health, jobs, spec, strategy_v0_2

API_DESCRIPTION = """
사용자가 한국어로 투자 전략을 말하면 그걸 전략서로 만들고, 그 전략서로 과거 데이터에
백테스트를 돌려 결과를 보여 준다. 실제 돈은 쓰지 않는다.

### 어디까지 AI가 하나

1. 전략서 만들기 — AI 가 한다. 사용자가 쓴 문장을 전략 요소 사전에 있는 값만 쓰는 전략서로 바꾼다
2. 전략서 검사 — AI 안 쓴다. 형식 · 종목 · 논리가 맞는지 본다
3. 백테스트 — AI 안 쓴다. 전략서대로만 계산한다

### 로그인하는 법

`POST /auth/login` 으로 `access_token` 을 받아 우측 상단 Authorize 에 넣으면 로그인이 유지된다.
누가 보낸 요청인지는 이 토큰으로 판단한다 — 요청 내용에 사용자 번호를 적는 칸은 없다.
"""

TAGS_METADATA = [
    {
        "name": "health",
        "description": "서버가 살아 있는지, 데이터베이스 같은 것들이 잘 붙어 있는지 본다.",
    },
    {"name": "auth", "description": "회원가입 · 로그인 · 토큰 갱신."},
    {"name": "spec", "description": "사용자 요청을 전략서로 바꾸고, 전략서를 검사 · 조회 · 삭제한다."},
    {"name": "jobs", "description": "오래 걸리는 작업이 어디까지 갔는지 실시간으로 본다."},
    {"name": "strategy v0.2",
     "description": "한국어 문장으로 전략서 v0.2 를 만들고, 되묻기에 답하고, 백테스트한다."},
    {"name": "backtest", "description": "만든 전략을 과거 데이터에 돌려보고 성적을 본다."},
]

app = FastAPI(
    title="한국어 투자 전략 명세화 · 백테스트 API",
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
app.include_router(spec.router)
app.include_router(backtest.router)
app.include_router(jobs.router)
app.include_router(strategy_v0_2.router)
