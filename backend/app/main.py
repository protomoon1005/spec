"""FastAPI 엔트리 (docs/infra-spec.md 7단계).

라우터 6종(auth·profile·spec·backtest·portfolio·admin) + jobs(202/SSE 패턴)를
붙인다. 본체는 auth/login·refresh, spec/compile, jobs/stream을 뺀 나머지가
전부 501이다 — JWT+RBAC 의존성 체인은 그 501 앞에서 실제로 걸린다.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.routers import admin, auth, backtest, health, jobs, portfolio, profile, spec

app = FastAPI(title="spec-backend", version="0.1.0")

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

Instrumentator().instrument(app).expose(app)
