# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 한국어 투자 전략 명세화 · 백테스트 서비스 — 졸업작품

- 자동매매를 원하지만 코딩을 못 하는 한국인 일반 투자자가 한국어로 전략을 말하면, 전략 요소 사전에 있는 요소만 쓰는 전략서(JSON)로 바꾸고 백테스트 결과를 보여 준다
- 지금은 주제 변경 기반 정리 중이다(2026-11-01 시작). 계획과 결정 사항은 `docs/pivot-plan.md`
  - 옛 주제(국내 ETF 모의운용, 투자 성향, 3개 신호원)의 코드는 1단계에서 지웠다(2026-11-01)
  - DB 테이블은 2단계에서 남는 11개만 새 이름으로 다시 만들었다(2026-11-02). 공용 데이터 적재와 가격 3년치 스냅샷은 3단계다

## 절대 규칙

- 사용자와의 대화는 한국어로 한다
- 기준 문서는 `docs/pivot-plan.md`. 시작 전 반드시 읽는다
  - `docs/infra-spec.md` 는 옛 주제의 기준 문서다. 두 문서가 다르면 `docs/pivot-plan.md` 를 따른다
- 사용자가 지정한 단계만 수행한다. 다음 단계로 넘어가지 않는다
- 스키마 설계 판단이 필요하면 임의 결정하지 말고 선택지를 제시하고 멈춘다
- 칸이나 테이블을 임의로 추가하지 않는다. 필요하면 먼저 묻는다
- 가격 같은 시점 데이터는 `app/repositories/` 밖에서 직접 SELECT 하지 않는다
- `db/init/` 은 빈 DB 부트스트랩 전용이라 고치지 않는다. 예외는 기반 정리 2단계에서 한 번 새로 쓰는 것뿐이다
- 옛 주제 기능(3개 신호원, 예측 모델, 거시 · 국면 · 뉴스 데이터, 투자 성향, 프리셋, 하드캡, 그룹캡, 모의운용)은 새 작업에서 쓰거나 고치지 않는다. 지우는 대상이다
- 문서(markdown 파일 형식)
  - 테이블 구조 금지
  - 화살표 흐름도 표현 금지
  - 이모티콘 금지. 동그라미 번호 같은 기호도 쓰지 않고 1 같은 숫자로 쓴다
  - 개조식 표현
  - 볼드체 같은 강조 표현 금지

## 환경

- `.gitattributes`: 전부 LF
- LLM 은 호스트 Ollama(qwen3:8b) 하나만 쓴다. Docker Desktop 은 host.docker.internal 로 접근한다
- GPU 없음

## 명령 — 전부 Docker 안에서 실행

```sh
scripts/build_db.sh                         # DB 새로 빌드 (볼륨 삭제, 기동, 스키마 생성 확인. -y 면 묻지 않음)
docker compose up -d --build                # 전 스택 기동
docker compose exec api ruff check .        # lint
docker compose exec api ruff check /repo/scripts --config /app/pyproject.toml   # scripts/ lint (위 명령 범위 밖)
docker compose exec api pytest -m "not requires_ollama and not requires_backfill and not requires_backtest" -q  # 테스트
docker compose exec api pytest tests/test_m2_feasibility.py::test_same_input_twice_same_result -q        # 단일 테스트
docker compose exec api python /repo/scripts/check_asof_guard.py                                          # as_of 가드
docker compose exec frontend npx tsc --noEmit                                                             # 프론트 타입 검사
docker compose logs -f                      # 로그
```

- 저장소 루트가 `/repo` 로, `backend/` 가 `/app` 으로 마운트된다. 앱 코드는 `/app`(uvicorn WORKDIR), 스크립트는 `/repo` 에서 찾는다
- DB
  - 스키마는 `db/init/` 이 빈 볼륨에서 postgres 컨테이너 최초 기동 시 만든다. 개발 DB `spec` 과 테스트 전용 DB `spec_test`(`db/init/03_test_db.sh`)가 같은 스키마로 생긴다
  - `scripts/build_db.sh` 가 이 compose 프로젝트의 볼륨을 지우고 다시 띄운 뒤 두 DB 의 테이블 11개를 확인한다. 프로젝트 이름과 포트는 `.env` 를 따른다
  - 스키마 변경은 `db/migrate/NNN_설명.sql` 추가 후 `docker compose exec api python /repo/scripts/apply_migrations.py`. 멱등이고 `schema_migrations` 에 이력이 남는다. 테스트 DB 에도 `DATABASE_URL` 을 바꿔 한 번 돌린다. Alembic 은 쓰지 않는다
  - 시드는 `docker compose exec api python /repo/scripts/seed_all.py`. 분류표, 종목 원장, 전략 요소 사전을 넣는다. 03 은 두 번 넣을 수 없어 빈 DB 에 한 번만 돌린다
  - 3단계에서 빌드 명령에 공용 데이터 적재(기준 시드, 종목 원장, 가격 스냅샷)가 붙는다
- 가격
  - 3단계 전까지는 ETF 71종목 가격 파일 `data/prices.csv` 를 `scripts/ingest_prices.py` 로 `price_daily` 에 적재한다. `build_universe.py` 는 `data/universe.csv` 를 덮어쓰므로 돌리지 않는다
  - 3단계에서 국내 상장 개별주와 ETF 의 3년치 가격 스냅샷 파일로 바뀐다. 한 사람이 받아 공유하고, 각자 빌드 때 적재한다
- 테스트
  - `tests/conftest.py` 가 `CELERY_TASK_ALWAYS_EAGER=true` 를 넣는다
  - 테스트는 테스트 전용 DB 만 쓴다. `tests/conftest.py` 가 `DATABASE_URL` 의 DB 이름을 `<이름>_test` 로 바꿔 강제로 넣는다(`TEST_DATABASE_URL` 로 따로 줄 수 있다). 개발 DB 는 테스트로 바뀌지 않는다
  - 테스트 DB 에 기준 시드가 없으면 conftest 가 세션 시작 때 `seed_all.py` 로 한 번 넣는다. 가격은 넣지 않는다
  - `requires_backfill` 테스트용 가격은 테스트 DB 에 따로 적재한다: `docker compose exec -e DATABASE_URL=postgresql+psycopg://spec:spec_dev_password@postgres:5432/spec_test api python /repo/scripts/ingest_prices.py`
  - 마커: `requires_ollama` · `requires_backfill` · `requires_backtest`. 로컬 테스트 시 제외한다
  - `requires_backtest` 는 `docker compose exec -w /repo/backend api pytest -m requires_backtest -q` 로 따로 돌린다. `-w` 가 없으면 루트 `data/` 를 못 찾는다
- 프론트
  - `frontend/` 에서 `npm run dev`. 검사는 `docker compose exec frontend npx tsc --noEmit`. `npm run lint` 는 ESLint 미설정이라 대화형으로 멈춘다
  - 화면 확인: `mcr.microsoft.com/playwright` 컨테이너(`--network host`)에 `localStorage['spec.session']` 을 주입해 연다. Recharts 차트는 fullPage 스크린샷에서 선이 빠지므로 요소 스크린샷으로 본다

## 아키텍처

- 흐름: 한국어 문장 입력, 명세화, 판정, 검증, 백테스트, 결과 확인
  - 명세화만 LLM 을 쓴다. 전략 요소 사전에 있는 값만 고를 수 있게 좁힌 스키마를 LLM 에 준다
  - 판정은 명세화 가능 / 추가 질문 필요 / 실행 불가 세 가지다. 판정기는 기반 이후에 만든다
  - 검증과 백테스트는 AI 없이 결정론적으로 돈다
- 사용자가 말한 대로만 거래한다. 사용자가 말하지 않은 외부 판단(시장 국면, 뉴스 감성, 예측 모델)이나 시스템 상한(성향, 하드캡, 그룹캡)이 거래에 끼어들지 않는다. 계산이 성립하지 않는 것(현금 초과 매수, 상장 기간 밖 거래)만 검증기가 막는다
- 대상 종목은 국내 상장 개별주와 ETF 다
- 보장할 성질: 시점 무결성(point-in-time, 룩어헤드 방지)과 재현성(같은 입력이면 같은 결과)
- 남는 모듈
  - 명세화 파이프라인: `app/m1/`(의도 파악, 추가 질문, 후보 선정, 스키마 좁히기, 후처리)
  - 전략 요소 사전: `db/seeds/05_bbl.sql`, `app/repositories/bbl.py`(테이블 `strategy_elements`, `strategy_element_tags`)
  - 전략서 계약: `app/contracts/spec.py`(Pydantic v2)
  - 검증기: `app/m2/validator.py`, `checks.py`, `stages.py`, `feasibility.py`
  - 백테스트: `app/backtest/`
  - 지표 계산: `app/backtest/features.py`. stdlib 순수 함수이고 결측은 None
- as_of 규약(`app/repositories/`)
  - `as_of` 는 키워드 필수, 기본값 금지. 가격 조회는 `as_of` 이하만 돌려준다
  - `scripts/check_asof_guard.py` 가 `app/` 전체 AST 에서 가드 테이블 이름 문자열을 잡는다. docstring 도 걸리므로 해당 파일은 설명을 `#` 주석으로 쓴다
- 데이터 저장 원칙(`docs/pivot-plan.md` 3.2)
  - DB 는 빌드할 때 처음부터 만든다. 공용 데이터(기준 시드, 종목 원장, 가격 3년치 스냅샷)는 그때 한 번에 적재한다
  - 개인 데이터(계정, 전략서, 검증 기록, 백테스트 결과)만 각자 DB 에 쌓인다
  - dev DB 서버는 두지 않는다
- 스키마 정본은 `docs/db-erd.md`. 테이블 11개: users, strategies, strategy_assets, validation_logs, backtest_runs, backtest_metrics, instruments, asset_groups, price_daily, strategy_elements, strategy_element_tags
  - 2단계에서 테이블 · 칸 이름을 바꿨다(`docs/pivot-plan.md` 3.2). 모듈 · 파일 이름(`repositories/etf_master.py`, `bbl.py`, `specs.py`, `scripts/load_etf_master.py`, `db/seeds/04_etf_master.csv`)은 그대로다
- 알려진 설계 구멍은 `docs/known-issues.md` 에 기록한다
