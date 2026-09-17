# 공통 인프라 구축 프롬프트 (M2 선행 과제)

> 사용법: 아래 `---` 아래 전체를 코딩 에이전트(Claude Code 등)에 그대로 붙여넣는다.
> 근거 문서: 2026-09-06 회의록 하위 3개 페이지(기획서 / 상세설계서 / 데모 계획), 2026-09-08 개정본 기준.
> 관문: **10월 1~2주 — "docker-compose로 전 스택 기동"**. 이게 늦으면 A·C·D 세 라인이 동시에 막힌다.

---

# 역할

너는 이 프로젝트의 **공통 인프라 담당(M2)** 이다. 지금 만드는 것은 비즈니스 로직이 아니라, 나머지 세 라인(M1 전략 컴파일 / M3 3관점 판단 / M4 백테스트·프론트)이 **각자 자기 모듈 개발을 시작할 수 있게 하는 바닥**이다. 판단 알고리즘, Validator 본체, 비중 매퍼는 이 작업의 범위가 아니다. **인터페이스와 스텁만 두고 비워둔다.**

# 프로젝트 배경 (요약)

**Spec** — 투자자의 자연어 요청을 검증 가능한 전략 명세(Spec JSON)로 컴파일하고, 검증을 통과한 명세만 결정론적 코드가 집행하는 국내 ETF 자동운용 시스템(모의투자 한정, 졸업작품).

계층 경계가 곧 **AI 사용의 경계**다.

| 계층 | 역할 | AI |
|---|---|---|
| ① 전략 생성 | 성향 판정 → 되묻기 → BBL 검색 → Spec 조합 생성 | 생성형 AI 사용 |
| ② 검증 | 스키마 · 참조 · 논리 · 하드캡 4단 | **미사용** |
| ③ 판단 | 시장분석 · 감성 · 시장온도 3관점 + 신호 통합 | 지도학습/PLM (LLM 아님) |
| ④ 통제 | 리스크 한도 → 선형매핑 → 그룹캡 → 주문 생성 | **미사용** |

인프라가 반드시 보장해야 하는 두 가지 성질:

1. **재현성** — 같은 입력에 같은 주문. 동일 입력 2회 실행 시 결과가 같아야 한다.
2. **시점 무결성(as-of)** — 어떤 판단도 그 시점 이후에 확정된 데이터를 볼 수 없다. **이 규약이 깨지면 백테스트 결과 전체가 무의미해진다.**

# 만들 것 (산출물)

1. 모노레포 디렉터리 구조
2. `docker-compose.yml` — 전 스택 1회 기동
3. DB 스키마 DDL (`db/init/` SQL로 자동 생성)
4. 시드 데이터 (허용범위 프리셋 v0.1 · 그룹/그룹캡 · 하드캡 v0.1 · ETF 마스터)
5. 저장소 계층 `as_of` 질의 규약 구현
6. 모듈 계약 4종 스텁 (Pydantic 모델 + 목업 응답)
7. 헬스체크 · 스모크 테스트
8. `README.md` — 팀원이 clone 후 3개 명령으로 뜨게

# 확정 기술 스택 (변경 금지)

| 영역 | 선택 |
|---|---|
| Frontend | Next.js(App Router) + TypeScript + TanStack Query + ECharts + shadcn/ui + Tailwind |
| Backend | FastAPI + Pydantic v2 |
| 비동기 | Celery + Redis (broker/result) |
| LLM 서빙 | vLLM (structured outputs) — **GPU 없는 팀원용 Ollama 폴백 병행** |
| DB | PostgreSQL 16 + TimescaleDB + pgvector |
| 객체저장 | MinIO (모델 아티팩트, 리포트) |
| 라이브러리 | vectorbt, quantstats, LightGBM, SHAP, scikit-learn(isotonic), pandas-ta, KF-DeBERTa, FinanceDataReader, pykrx, fredapi |
| Infra | 온프레미스 Docker Compose + GitHub Actions + Prometheus/Grafana |

# 1. 디렉터리 구조

```
spec/
├── docker-compose.yml
├── .env.example
├── README.md
├── db/
│   ├── init/
│   │   ├── 00_extensions.sql       # timescaledb, vector, pg_trgm
│   │   ├── 01_roles.sql
│   │   └── 02_schema.sql           # 전체 DDL (테이블·인덱스·하이퍼테이블·트리거)
│   └── seeds/
│       ├── 01_hardcap_v0_1.sql
│       ├── 02_preset_v0_1.sql      # 성향5 x 위험등급6 매트릭스
│       ├── 03_asset_groups.sql     # 2계층 그룹 + 그룹캡
│       └── 04_etf_master.csv       # 60종목 (초기 스켈레톤)
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── app/
│       ├── main.py                 # FastAPI 엔트리
│       ├── core/                   # settings, logging, db session
│       ├── models/                 # SQLAlchemy ORM
│       ├── contracts/              # ★ 모듈 계약 4종 (전 라인 공유)
│       ├── repositories/           # ★ as_of 질의 규약 — 유일한 데이터 진입점
│       ├── routers/                # auth profile spec backtest portfolio admin (스텁)
│       ├── workers/                # Celery tasks (스텁)
│       └── llm/                    # vLLM/Ollama 클라이언트 추상화
├── frontend/
│   ├── Dockerfile
│   └── (Next.js 스캐폴드 + /health 페이지)
└── scripts/
    ├── smoke_test.py               # 전 스택 기동 검증
    └── check_asof_guard.py         # 저장소 계층 우회 검사
```

# 2. docker-compose

## 서비스

| 서비스 | 이미지/빌드 | 포트 | 비고 |
|---|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg16` (pgvector 포함본) | 5432 | `db/init` 자동 실행, named volume |
| `redis` | `redis:7-alpine` | 6379 | Celery broker + result backend |
| `minio` | `quay.io/minio/minio` (digest 고정, 2026-09-13 Docker Hub 익명 pull 거부로 이전) | 9000/9001 | 버킷 `models`, `reports` 자동 생성 |
| `api` | `./backend` | 8000 | uvicorn, `--reload` (dev) |
| `worker` | `./backend` | — | `celery -A app.workers worker` |
| `beat` | `./backend` | — | `celery -A app.workers beat` (일일 스케줄) |
| `frontend` | `./frontend` | 3000 | Next.js dev |
| `vllm` | `vllm/vllm-openai:latest` | 8001 | **profile: `gpu`** |
| `ollama` | `ollama/ollama` | 11434 | **profile: `cpu`** |

## 요구사항

- 모든 서비스에 **healthcheck**를 붙이고 `depends_on: condition: service_healthy` 로 기동 순서를 강제한다. `postgres` 준비 전에 `api`가 뜨면 안 된다.
- **profiles로 LLM 백엔드를 분기**한다. GPU 있는 팀원은 `docker compose --profile gpu up`, 없는 팀원은 `--profile cpu up`. 어느 쪽이든 `api`는 동일한 `LLMClient` 인터페이스만 본다.
  - `LLM_BACKEND=vllm|ollama` 환경변수로 구현체 선택
  - vLLM: OpenAI 호환 엔드포인트 + `guided_json` (structured outputs). 모델은 EXAONE 계열, `.env`로 교체 가능
  - Ollama: 기존 데모가 쓰던 `qwen3:8b`. structured output은 `format: json_schema`로 대응하되, **제약 디코딩 강도가 다르다는 것을 README에 명시**한다 (M1의 10월 실측은 반드시 vLLM 쪽에서 한다)
- 모든 설정은 `.env`로 뺀다. `.env.example`을 커밋하고 `.env`는 gitignore.
- GPU 서비스는 `deploy.resources.reservations.devices` 로 지정하고, GPU 없는 환경에서 `--profile cpu`가 아무 경고 없이 뜨는지 확인한다.
- 이름 있는 볼륨: `pgdata`, `miniodata`, `ollama_models`, `hf_cache`.

# 3. DB 스키마

**전략·운용 도메인 (관계형)**

`USERS`, `RISK_PROFILES`, `SURVEY_RESPONSES`, `PRESET_VERSIONS`, `ASSET_BOUND_PRESETS`, `STRATEGY_SPECS`, `SPEC_UNIVERSE`, `HARDCAP_VERSIONS`, `VALIDATION_LOGS`, `BACKTEST_RUNS`, `BACKTEST_METRICS`, `APPROVALS`, `PORTFOLIOS`, `VIEW_WEIGHTS`, `POSITIONS`, `ORDERS`, `EXECUTIONS`, `DECISION_RECORDS`, `VIEW_SCORES`, `VIEW_PERFORMANCE`, `ASSET_GROUPS`, `GROUP_CAPS`, `GROUP_CAP_APPLICATIONS`, `REPORTS`, `ETF_MASTER`, `AUDIT_LOGS`

**데이터·지식 도메인**

`PRICE_DAILY`, `FEATURE_STORE`, `MACRO_INDICATORS`, `REGIME_SNAPSHOTS`, `NEWS_ARTICLES`, `NEWS_SENTIMENT`, `BBL_BLOCKS`, `BBL_TAGS`, `ML_MODELS`

컬럼 정의는 상세설계서 4.1~4.2 ER 다이어그램을 그대로 따른다. 아래는 **놓치면 안 되는 설계 원칙**이다.

| 원칙 | 적용 |
|---|---|
| **Spec 3분할** | `STRATEGY_SPECS`에는 가변 컬럼도 가중치 컬럼도 두지 않는다. 채점 기준은 `VIEW_WEIGHTS`, 가변값은 `DECISION_RECORDS` |
| **재현 조건 위치** | `random_seed` · `data_snapshot_asof` · `feature_set_version` 은 `BACKTEST_RUNS`와 `DECISION_RECORDS`에만 존재. Spec에는 없다 |
| **2단 범위 추적** | `SPEC_UNIVERSE`가 `preset_id` FK를 들고, LLM 원출력을 `weight_min_raw` · `weight_max_raw` 에 보존 + `was_adjusted` 플래그 |
| **버전 고정** | `STRATEGY_SPECS.hardcap_version`, `RISK_PROFILES.preset_version` — 운영자가 값을 바꿔도 기존 Spec의 생성 근거가 흔들리지 않는다 |
| **시점 태그 이중화** | `MACRO_INDICATORS(as_of, released_at)`, `PRICE_DAILY(trade_date, ingested_at)` |
| **Spec 불변** | `STRATEGY_SPECS` · `SPEC_UNIVERSE`는 승인 후 UPDATE 금지 → **DB 트리거로 물리적으로 막는다** (`status='approved'` 이후 UPDATE 시 raise) |
| **가중치 이력 보존** | `VIEW_WEIGHTS`는 `as_of` 단위 append-only. UPDATE 금지 트리거 |
| **복합 기본키** | `PRICE_DAILY(ticker, trade_date)`, `FEATURE_STORE(ticker, as_of, feature_set_version)`, `MACRO_INDICATORS(indicator_code, as_of)`, `REGIME_SNAPSHOTS(as_of, trend_index)` — 중복 적재를 구조적으로 차단 |
| **2계층 그룹** | `ASSET_GROUPS.parent_group_id` 자기참조 + `group_level` (1 자산군 / 2 섹터·국가) |

**인덱스와 하이퍼테이블** — 아래를 그대로 넣는다.

```sql
CREATE INDEX idx_feature_ticker_asof   ON feature_store    (ticker, as_of DESC);
CREATE INDEX idx_price_ticker_date     ON price_daily      (ticker, trade_date DESC);
CREATE INDEX idx_macro_code_asof       ON macro_indicators (indicator_code, as_of DESC, released_at DESC);
CREATE UNIQUE INDEX uq_preset_lookup   ON asset_bound_presets (preset_version, risk_level, risk_tag);
CREATE UNIQUE INDEX uq_view_weight     ON view_weights (portfolio_id, as_of, view_type);
CREATE INDEX idx_decision_spec_asof    ON decision_records (spec_id, as_of DESC);
CREATE INDEX idx_viewscore_decision    ON view_scores      (decision_id, view_type);
CREATE INDEX idx_groupcap_decision     ON group_cap_applications (decision_id);
CREATE INDEX idx_news_published        ON news_articles (published_at DESC);
CREATE UNIQUE INDEX uq_news_dedup      ON news_articles (dedup_hash);
CREATE INDEX idx_bbl_embedding  ON bbl_blocks    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_news_embedding ON news_articles USING hnsw (embedding vector_cosine_ops);

SELECT create_hypertable('price_daily',      'trade_date', if_not_exists => TRUE);
SELECT create_hypertable('feature_store',    'as_of',      if_not_exists => TRUE);
SELECT create_hypertable('macro_indicators', 'as_of',      if_not_exists => TRUE);
```

**주의** — TimescaleDB 하이퍼테이블은 파티션 키가 PK에 포함되어야 한다. 위 복합 PK 설계가 이미 이를 만족하는지 확인하고, 충돌하면 하이퍼테이블 전환을 마이그레이션 후반부로 분리한다.

Alembic으로 관리하되 초기 리비전 하나에 전부 넣지 말고 `001_core` / `002_timeseries` / `003_hypertables` / `004_triggers` 로 쪼갠다.

# 4. 시드 데이터

## 4.1 허용범위 프리셋 v0.1 (`preset_version = 'v0.1'`, `is_active = true`)

축은 **금융투자협회 표준투자권유준칙**의 투자자성향 5단계 × 상품위험등급 6단계(G1 초고위험 ~ G6 초저위험). 값은 **종목 하나당 상한(`allowed_max`)** 이며 `allowed_min`은 전 셀 `0.00`.

| risk_level | 성향 | G1 | G2 | G3 | G4 | G5 | G6 |
|---|---|---|---|---|---|---|---|
| 1 | 안정투자형 | 0.00 | 0.00 | 0.00 | 0.10 | 0.30 | 1.00 |
| 2 | 안정추구형 | 0.00 | 0.00 | 0.10 | 0.25 | 0.40 | 1.00 |
| 3 | 위험중립형 | 0.00 | 0.10 | 0.25 | 0.35 | 0.50 | 1.00 |
| 4 | 성장투자형 | 0.10 | 0.25 | 0.35 | 0.40 | 0.60 | 1.00 |
| 5 | 공격투자형 | 0.25 | 0.35 | 0.40 | 0.50 | 0.70 | 1.00 |

G1 열의 0.00 세 칸은 **레버리지·인버스 ETF가 그 성향에게는 생성 자체로 불가능**하다는 뜻이다. 각 셀에 `rationale` 텍스트를 채운다.

## 4.2 성향별 제약 기본값 (`RISK_PROFILES` 기본값 유도용)

| risk_level | cash_min_default | max_drawdown_default | max_loss_per_trade_default |
|---|---|---|---|
| 1 안정투자형 | 0.20 | 0.10 | 0.02 |
| 2 안정추구형 | 0.15 | 0.15 | 0.03 |
| 3 위험중립형 | 0.10 | 0.20 | 0.05 |
| 4 성장투자형 | 0.05 | 0.28 | 0.07 |
| 5 공격투자형 | 0.05 | 0.35 | 0.10 |

## 4.3 2계층 그룹과 그룹캡

**상위(level 1) 자산군 — 성향별 상한**

| group_id | 라벨 | 안정투자 | 안정추구 | 위험중립 | 성장투자 | 공격투자 |
|---|---|---|---|---|---|---|
| EQUITY | 주식계 | 0.25 | 0.40 | 0.60 | 0.75 | 0.85 |
| BOND | 채권계 | 0.70 | 0.60 | 0.50 | 0.35 | 0.25 |
| COMMODITY | 원자재계 | 0.05 | 0.10 | 0.15 | 0.20 | 0.25 |

**하위(level 2) — 성향 무관 고정**

- 섹터 상한 `0.30` : 반도체 · 2차전지 · 바이오와헬스케어 · 금융 · 인터넷과플랫폼 · 소비재 · 기타
- 국가 상한 `0.50` : 한국 · 미국 · 기타

`GROUP_CAPS.risk_level` 은 level 2 그룹에 대해 `0`(성향 무관)으로 넣는다.

## 4.4 시스템 하드캡 v0.1

`HARDCAP_VERSIONS` 에 활성 버전 1건. `max_weight_per_asset`, `cash_min`, `max_loss_per_trade`, `max_drawdown`, `min_interval_days`, `leverage_allowed`. **수치는 아직 미확정이므로 보수적 기본값을 넣고 `-- TODO: 팀 확정 필요` 주석을 단다.**

## 4.5 ETF 마스터

`db/seeds/04_etf_master.csv` — 60종목 스켈레톤. 컬럼: `ticker, name, sector, group_id, risk_tag(G1~G6), mdd_3y, volatility_1y, expense_ratio, listed_date, delisted_date, is_leveraged, active`.

- 최소한 검산 예제의 4종목은 실제 값으로 채운다: `069500 KODEX 200`(국내주식형), `232080 TIGER 코스닥150`(국내주식형), `133690 TIGER 미국나스닥100`(해외주식형), `148070 KOSEF 국고채10년`(채권형)
- **상장폐지 종목을 최소 1건 일부러 남긴다** (`delisted_date` 채움, `active=false`). 데모 "실패 장면 3 · 상장폐지 종목이 참조 검증에서 차단"에 그대로 쓴다
- 나머지는 `pykrx`/`FinanceDataReader`로 티커·종목명을 긁어 채우는 스크립트를 함께 만들되, `risk_tag`와 `group_id` 배정은 사람이 검토하도록 CSV로 남긴다 (자동 추정 금지)

**이 테이블이 없으면 프리셋 조회도 그룹캡도 동작하지 않는다.** M1과 M2가 둘 다 여기서 멈추므로 최우선이다.

# 5. 저장소 계층 as_of 질의 규약 ★

**가장 중요한 산출물이다.** 피처·거시지표·관점 가중치 조회는 반드시 `app/repositories/` 의 단일 함수를 거친다. 응용 코드가 이 테이블들을 직접 SELECT 하는 것은 금지한다.

```python
def get_features(ticker: str, as_of: date, feature_set_version: str) -> dict:
    """as_of 이후의 데이터는 어떤 경우에도 반환하지 않는다."""
    # WHERE ticker=%s AND feature_set_version=%s AND as_of <= %(as_of)s
    # ORDER BY as_of DESC LIMIT 1

def get_macro(indicator_code: str, as_of: date) -> float:
    """거시지표는 공표 시차가 있으므로 released_at 기준으로도 걸러야 한다."""
    # WHERE indicator_code=%s AND as_of <= %(as_of)s AND released_at <= %(as_of)s
    # ORDER BY as_of DESC LIMIT 1

def get_view_weights(portfolio_id: int, as_of: date) -> dict[str, float]:
    """오늘 성과가 오늘 판단에 쓰이면 미래를 미리 보는 것이다.
    as_of '미만'(strict <) 으로 계산된 행만 읽는다."""
    # SELECT DISTINCT ON (view_type) ... WHERE portfolio_id=%s AND as_of < %(as_of)s
```

부수 요구사항:

- `as_of`는 **키워드 인자 필수**로 만든다. 기본값 `date.today()` 를 절대 두지 않는다 (실운용에서만 오늘을 주입).
- 세 함수 각각에 **경계 테스트**를 작성한다: `as_of` 당일 데이터, 하루 뒤 데이터, `released_at`이 미래인 거시지표를 넣고 반환되지 않음을 단언한다. `get_view_weights`는 `as_of` 당일 행이 반환되지 **않는지** 확인한다 (부등호 방향이 다르다).
- `scripts/check_asof_guard.py` — `app/` 전체를 AST로 훑어 `repositories/` 밖에서 `feature_store` · `macro_indicators` · `view_weights` 를 문자열로 참조하는 코드를 찾아 실패시킨다.

# 6. 모듈 계약 4종 (스텁)

병렬 개발의 전제 조건이다. 이 네 가지를 `app/contracts/` 에 Pydantic v2 모델로 정의하고, **각각 목업 구현을 함께 준다.** A·C·D 라인은 인프라 완성을 기다리지 않고 이 목업에 붙어 단독 개발을 시작한다.

| # | 계약 | 내용 |
|---|---|---|
| ① | **Spec JSON 스키마 v0.1** | `spec_id`, `spec_version`, `user_id`, `name`, `created_at`, `universe[]`(ticker · name · weight_min · weight_max), `rebalance`(trigger · min_interval_days), `signal_rules`(market_analysis · sentiment · market_temperature), `constraint`. **`objective`와 `reproducibility` 블록은 넣지 않는다.** JSON Schema 파일로도 export (M1의 structured outputs 입력) |
| ② | **관점별 스코어 출력 형식** | `ViewScore { view_type: "market"\|"sentiment"\|"regime", ticker, raw_score: [-1,1], calibrated_prob: [0,1], evidence: dict }` — **세 관점 모두 보정 확률을 낸다** (Hedge가 Brier score를 쓰므로 점수만으로는 부족) |
| ③ | **관점 가중치 조회 인터페이스** | `get_view_weights(portfolio_id, as_of) -> {view_type: weight}`, Σw=1, 하한 0.10. 읽기 전용 — 쓰기 API를 노출하지 않는다 |
| ④ | **목표 비중 벡터 형식** | `TargetWeights { as_of, weights: {ticker: float}, cash: float, mapped_weights, group_cap_applications[] }` — 매핑 직후와 그룹캡 적용 후를 둘 다 보존 |

목업 구현은 고정 시드로 결정론적인 값을 돌려준다 (랜덤 금지).

# 7. FastAPI 라우터 스켈레톤

`auth` / `profile` / `spec` / `backtest` / `portfolio` / `admin` 6개 라우터를 **경로와 응답 모델만** 만든다. 본체는 `501 Not Implemented`.

- JWT 액세스/리프레시 + `retail` · `pro` · `admin` 3역할 RBAC 의존성은 실제로 동작하게 만든다 (다른 라인이 여기 붙는다)
- 장시간 작업은 `202 Accepted` + `job_id` 반환 → `GET /jobs/{job_id}/stream` SSE 구독 패턴. **이 패턴만 실제로 뚫어 둔다** (`compile_spec` 더미 태스크가 3초 뒤 완료 이벤트를 흘리는 수준)
- `/health`

Celery 태스크 이름도 미리 박는다: `compile_spec`, `run_backtest`, `daily_judge`, `ingest_market`, `train_model`, `update_view_weights`.

# 8. 검증 (수용 기준)

`scripts/smoke_test.py` 가 아래를 전부 통과해야 완료다.

- [ ] `git clone` 후 `cp .env.example .env && make up` 세 명령으로 전 스택 기동
- [ ] `docker compose ps` 에서 전 서비스 `healthy`
- [ ] `GET /health` 200, DB·Redis·MinIO·LLM 백엔드 연결 상태를 각각 보고
- [ ] `--profile gpu` 와 `--profile cpu` 가 **둘 다** 기동되고, 양쪽에서 동일한 프롬프트로 JSON 응답 1건을 받는다
- [ ] Alembic `upgrade head` → `downgrade base` → `upgrade head` 왕복 성공
- [ ] 시드 적재 후 `ASSET_BOUND_PRESETS` 30행(5×6), `GROUP_CAPS` 상위 15행(3×5) + 하위 10행, `HARDCAP_VERSIONS` 1행 활성
- [ ] 하이퍼테이블 3종 전환 확인 (`SELECT * FROM timescaledb_information.hypertables`)
- [ ] pgvector HNSW 인덱스 2종 생성 확인
- [ ] 승인된 Spec에 UPDATE 시도 → 트리거가 거부
- [ ] `VIEW_WEIGHTS` UPDATE 시도 → 트리거가 거부
- [ ] as_of 경계 테스트 3종 통과 (미래 데이터 미반환, `released_at` 미래 미반환, 당일 가중치 미반환)
- [ ] `check_asof_guard.py` 통과
- [ ] 프론트 `localhost:3000/health` 가 API를 호출해 상태를 렌더
- [ ] Grafana에 API 요청 지연 대시보드 1장이 프로비저닝되어 뜸
- [ ] GitHub Actions: lint(ruff) + 마이그레이션 왕복 + smoke test 가 PR에서 돈다

# 9. 하지 말 것

- Validator 본체, `BoundFeasibilityFixer`, `WeightMapper`, `GroupCapEnforcer`, `OrderBuilder` 구현 — **인터페이스와 `NotImplementedError`만**
- 3관점 스코어러 실구현, 모델 학습 코드
- 백테스트 러너 실구현
- 데이터 수집기 본체 (테이블과 태스크 이름만; ETF 마스터 채우기 스크립트는 예외)
- 화면 구현 (U02R 등은 M4 담당)
- 하드캡·프리셋 **수치를 임의로 바꾸는 것**. 위 표는 2026-09-08 확정본이다
- 스키마에 없는 컬럼을 "편의상" 추가하는 것. 필요하면 먼저 물어본다

# 10. 작업 순서와 보고

1. 디렉터리 구조 + `.env.example`
2. `docker-compose.yml` (postgres/redis/minio 먼저) → `docker compose up` 으로 3종 healthy 확인
3. DDL (`db/init/` SQL) → 컨테이너 최초 기동 시 자동 생성
4. 시드 4종 → 행 수 검증
5. `repositories/` as_of 규약 + 경계 테스트 + `check_asof_guard.py`
6. `contracts/` 4종 + 목업
7. FastAPI 스켈레톤 + Celery + SSE 패턴
8. vLLM/Ollama profile 분기 + `LLMClient` 추상화
9. frontend 스캐폴드, Prometheus/Grafana
10. `smoke_test.py` + GitHub Actions + README

각 단계가 끝날 때마다 **무엇이 돌아가는지 한 줄로 보고**하고 다음으로 넘어간다. 막히면 우회하지 말고 멈춰서 묻는다. 특히 스키마 설계 판단이 필요한 지점(하이퍼테이블 PK 충돌 등)은 임의로 결정하지 말고 선택지를 제시한다.

README에는 팀원 4명이 각자 무엇부터 붙이면 되는지를 **라인별 3줄 요약**으로 적는다 (A 재원 / B 현서 / C 강준 / D 하늘).
