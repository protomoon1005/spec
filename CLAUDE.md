# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Spec — 국내 ETF 모의운용 졸업작품

자연어 → Strategy Spec(JSON) 컴파일 → 결정론적 집행. 공통 인프라 + 3관점 판단 계층.

## 절대 규칙

- 사용자와의 대화는 한국어로 한다.
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
docker compose exec api ruff check /repo/scripts --config /app/pyproject.toml   # scripts/ lint (위 명령 범위 밖)
docker compose exec api pytest -m "not requires_ollama and not requires_ml and not requires_backfill and not requires_backtest" -q  # 테스트
docker compose exec api pytest tests/test_hedge.py::test_confirmed_constants -q                           # 단일 테스트
docker compose exec api python /repo/scripts/check_asof_guard.py                                          # as_of 가드
docker compose logs -f                      # 로그
```

- 저장소 루트가 `/repo`로, `backend/`가 `/app`으로 마운트된다.
  앱 코드는 `/app`(uvicorn WORKDIR), 스크립트는 `/repo`에서 찾는다.
- DB 스키마는 `db/init/`의 SQL로 postgres 컨테이너 최초 기동 시 자동 생성된다.
  시드 데이터는 `docker compose exec -T postgres psql -U spec -d spec < db/seeds/01_hardcap_v0_1.sql` 식으로 넣는다.
- 가격 데이터 순서: `db/seeds/04_etf_master.csv`(종목 정본) → `scripts/merge_universe.py derive`(`data/universe.csv` 파생) → `scripts/fetch_prices.py`(`data/prices.csv`를 쓰는 유일한 곳) → `scripts/ingest_prices.py`(`price_daily` upsert). `build_universe.py`는 `universe.csv`를 덮어쓰므로 돌리지 않는다.
- 거시·국면 적재: `ingest_macro.py --start 2019-01-01`(FRED 키가 비면 `--public-csv`) → `ingest_index.py` → `build_regime.py`. 기본 시작일이 2023이라 백테스트 구간(2019~)을 쓰려면 `--start`를 준다.
- 가격을 다시 적재하면 worker를 재시작한다 — 스코어러가 cutoff별 모델·피처를 프로세스 메모리에 캐시한다.
- `tests/conftest.py`가 `CELERY_TASK_ALWAYS_EAGER=true`를 넣는다.
  DB 픽스처를 쓰는 테스트는 seed된 상태여야 한다.
  `test_hedge`·`test_views_base` 등 순수 함수 테스트는 DB 없이 돈다.
- 마커: `requires_ollama` · `requires_ml` · `requires_backfill` · `requires_backtest`. 로컬 테스트 시 제외.
  `requires_backtest`(두 러너 대조·백테스트 입력·재현성)는
  `docker compose exec -w /repo/backend api pytest -m requires_backtest -q` 로 따로 돌린다
  (`-w` 가 없으면 루트 `data/` 를 못 찾아 CSV 대조가 건너뛰어진다). api·worker 이미지는 `.[dev,ml,backtest]` 이 깔린다.
- ml extra 에는 torch 가 없다(2026-09-19 경량화). torch 를 되살리면 `--extra-index-url https://download.pytorch.org/whl/cpu` 가 다시 필요하다.
- 프론트: `frontend/`에서 `npm run dev`. 검사는 `docker compose exec frontend npx tsc --noEmit` (`npm run lint`는 ESLint 미설정이라 대화형으로 멈춘다).
- 화면 확인: `mcr.microsoft.com/playwright` 컨테이너(`--network host`)에 `localStorage['spec.session']`을 주입해 연다. Recharts 차트는 fullPage 스크린샷에서 선이 빠지므로 요소 스크린샷으로 본다.
- TS 백테스트는 node 22 이상이 필요하다(`--experimental-strip-types`). frontend 컨테이너는 node 20이라 안 된다. 저장소 루트에서:
  `docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w/frontend node:22-slim node --experimental-strip-types scripts/run-backtest.ts`
  (루트 `data/`를 읽고 `frontend/data/backtest-result.json`에 쓴다. `--user`가 없으면 결과 파일이 root 소유가 된다.)

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
- **라벨** (`views/market/labels.py`): 사건 = 20거래일 선행 수익률 > 0. 라벨·채점·보정이 이 정의 하나를 쓴다.
  `THETA`는 학습 표본 필터에만 쓴다 — 보정·채점은 전체 표본.
- **피처셋** `v0.1-ta9` (`views/market/features.py`의 `FEATURE_SETS`가 정본): 워밍업 120행은 정의의 일부. 결측은 None.
- **스코어러 실물** (`app/scoring/`): DB·LightGBM을 읽는 쪽은 views가 아니라 여기. market(LightGBM)·regime(국면 조건부 빈도)이 실물, 감성은 중립 고정.
  재학습은 `scoring/schedule.cutoff_for`의 고정 20거래일 격자 — 같은 as_of는 같은 모델이라 cutoff 캐시가 재현성을 깨지 않는다. 라이브 결선(`daily_judge`·`update_view_weights`)은 `scoring/live.py`.
- **백테스트 접합**: `backtest/runner.py` → `views/bridge.py` 단방향. 스코어러 교체는 `bridge.SCORERS` 한 곳(실물은 지연 import). DB 없는 테스트는 `bridge.MOCK_SCORERS`로 고정한다.
- **정책 값 3중 사본**: `db/seeds/` ↔ `backtest/policy.py` ↔ `frontend/lib/policy.ts`. 수치가 같아야 한다.
- **DB**: `db/init/`은 빈 DB 부트스트랩 전용 — 고치지 않는다. 스키마 변경은 `db/migrate/NNN_설명.sql` 추가 후
  `docker compose exec api python /repo/scripts/apply_migrations.py` (멱등, `schema_migrations`에 이력). Alembic은 쓰지 않는다. 트리거가 approved Spec과 view_weights UPDATE를 막는다. 스키마 정본은 `docs/db-erd.md`.
- 알려진 설계 구멍은 `docs/known-issues.md`에 기록. 컬럼을 임의 추가하지 않는다.
