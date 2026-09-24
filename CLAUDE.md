# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Spec — 국내 ETF 모의운용 졸업작품

자연어 → Strategy Spec(JSON) 컴파일 → 결정론적 집행. 공통 인프라 + 3관점 판단 계층.

## 절대 규칙

- 기준 문서는 docs/infra-spec.md. 시작 전 반드시 읽는다.
- B 라인(M2 검증·통제) 구현의 알고리즘 정본은 docs/m2-algorithms.md 다.
  거기 적힌 수치와 실행 순서를 임의로 바꾸지 않는다.
- 확정 수치(프리셋 v0.1, 성향별 제약, 2계층 그룹캡)를 임의로 바꾸지 않는다. 2026-09-08 팀 확정본.
- docs/infra-spec.md 9장 "하지 말 것"은 구현하지 않는다. 인터페이스와 NotImplementedError만.
- 피처 / 거시지표 / 관점 가중치는 app/repositories/ 밖에서 직접 SELECT 하지 않는다.
- 스키마 설계 판단이 필요하면 임의 결정하지 말고 선택지를 제시하고 멈춘다.
- 사용자가 지정한 단계만 수행한다. 다음 단계로 넘어가지 않는다.

## 환경

- `.gitattributes`: 전부 LF.
- LLM 백엔드는 호스트 Ollama(qwen3:8b). Docker Desktop은 host.docker.internal로 접근.
- GPU 없음. vLLM은 `profiles: ["gpu"]`로 정의만 돼 있다.

## 명령 — 전부 Docker 안에서 실행

```sh
docker compose up -d --build                # 전 스택 기동
docker compose exec api ruff check .        # lint
docker compose exec api pytest -m "not requires_ollama and not requires_ml and not requires_backfill and not requires_backtest" -q  # 테스트
docker compose exec api pytest tests/test_hedge.py::test_confirmed_constants -q                           # 단일 테스트
docker compose exec api python /repo/scripts/check_asof_guard.py                                          # as_of 가드
docker compose logs -f                      # 로그
```

- 저장소 루트가 `/repo`로, `backend/`가 `/app`으로 마운트된다.
  앱 코드는 `/app`(uvicorn WORKDIR), 스크립트는 `/repo`에서 찾는다.
- DB 스키마는 `db/init/`의 SQL로 postgres 컨테이너 최초 기동 시 자동 생성된다.
  시드 데이터는 `docker compose exec -T postgres psql -U spec -d spec < db/seeds/01_hardcap_v0_1.sql` 식으로 넣는다.
- 가격 데이터 순서: `scripts/build_universe.py`(종목 선정) → `scripts/fetch_prices.py`(`data/prices.csv`를 쓰는 유일한 곳) → `scripts/ingest_prices.py`(`price_daily` upsert).
- `tests/conftest.py`가 `CELERY_TASK_ALWAYS_EAGER=true`를 넣는다.
  DB 픽스처를 쓰는 테스트는 seed된 상태여야 한다.
  `test_hedge`·`test_views_base` 등 순수 함수 테스트는 DB 없이 돈다.
- 마커: `requires_ollama` · `requires_ml` · `requires_backfill` · `requires_backtest`. 로컬 테스트 시 제외.
  `requires_backtest`(두 러너 대조)는 `pip install -e "backend[backtest]"` 뒤
  `pytest -m requires_backtest` 로 따로 돌린다. api 이미지는 `.[dev]` 만 깔린다.
- ML extra 설치 시 `--extra-index-url https://download.pytorch.org/whl/cpu` 필수(CUDA 휠 방지).
- 프론트: `frontend/`에서 `npm run dev`. TS 백테스트는 `node --experimental-strip-types scripts/run-backtest.ts`.

## 아키텍처

4계층 = AI 사용 경계: ① 전략 생성(LLM) → ② 검증(AI 없음) → ③ 판단(지도학습/PLM) → ④ 통제(AI 없음).
보장할 성질: **재현성**(같은 입력 → 같은 주문), **시점 무결성(as_of)**.

- **as_of 규약** (`app/repositories/`): `as_of`는 키워드 필수, 기본값 금지.
  `get_features`/`get_macro`는 `<=`, `get_view_weights`는 **strict `<`**.
  `scripts/check_asof_guard.py`가 `app/` 전체 AST에서 가드 테이블 참조를 잡는다 —
  **docstring도 걸린다.** `app/views/*`는 설명을 `#` 주석으로 쓴다.
- **계약** (`app/contracts/`, Pydantic v2): ① Spec ② ViewScore ③ view_weights ④ TargetWeights ⑤ IntegratedSignal.
- **판단 계층** (`app/views/`): `as_of`를 받는 순수 함수. stdlib만 사용(numpy/pandas 금지).
  `integrator.py`: tanh 스케일 0.5, 데드존 0.10, 연산 순서 고정.
  `hedge.py`: 롤링 재계산 + water-filling 하한. `app/llm/` import 금지.
- **피처셋** `v0.1-ta9` (`views/market/features.py`의 `FEATURE_SETS`가 정본): 워밍업 120행은 정의의 일부. 결측은 None.
- **백테스트 접합**: `backtest/runner.py` → `views/bridge.py` 단방향. 스코어러 교체는 `bridge.SCORERS` 한 곳.
- **정책 값 3중 사본**: `db/seeds/` ↔ `backtest/policy.py` ↔ `frontend/lib/policy.ts`. 수치가 같아야 한다.
- **DB**: 스키마는 `db/init/`의 SQL로 생성. 트리거가 approved Spec과 view_weights UPDATE를 막는다. 스키마 정본은 `docs/db-erd.md`.
- 알려진 설계 구멍은 README "알려진 설계 구멍"에 기록. 컬럼을 임의 추가하지 않는다.
