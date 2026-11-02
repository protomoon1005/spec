# DB 설계

- 기준: 기반 정리 2단계(2026-11-02). 스키마 정본은 이 문서와 `db/init/02_schema.sql` 이다. 둘이 다르면 SQL 이 맞고 이 문서를 고친다
- 테이블은 11개다. 칸이나 테이블을 임의로 더하지 않는다(CLAUDE.md 절대 규칙)
- 옛 주제(모의운용, 투자 성향, 하드캡, 그룹캡, 3개 신호원)의 테이블 설계는 git 이력(커밋 fe1f8ce 이전 `docs/db-erd.md`)에 있다

---

## 1. 데이터 구분

- 개인 데이터: 각자 DB 에만 쌓인다. 구조가 바뀌면 다시 빌드하고 잃는 것은 자기 시험 데이터뿐이다
  - users, strategies, strategy_assets, validation_logs, backtest_runs, backtest_metrics
- 공용 데이터: 빌드할 때 git 시드와 가격 스냅샷에서 한 번에 적재한다. 누가 빌드해도 같아야 한다
  - instruments, asset_groups, price_daily, strategy_elements, strategy_element_tags
- 테스트 전용 DB `spec_test` 도 같은 스키마로 만들어진다(`db/init/03_test_db.sh`). 테스트는 이 DB 만 쓴다

---

## 2. 테이블 간 관계

- users 한 명이 strategies 여러 개를 가진다(strategies.user_id)
- strategies 한 개가 아래를 여러 개 가진다
  - strategy_assets(strategy_assets.strategy_id): 담은 종목과 종목별 비중 범위
  - validation_logs(validation_logs.strategy_id): 검증 단계별 기록
  - backtest_runs(backtest_runs.strategy_id): 백테스트 실행 기록
- backtest_runs 한 건에 backtest_metrics 가 많아야 한 건 붙는다(backtest_metrics.run_id 가 기본키이자 외래키)
- strategy_assets.ticker 는 instruments.ticker 를 가리킨다
- instruments 는 asset_groups 를 네 칸으로 가리킨다: group_id, asset_group_id, sector_group_id, country_group_id
- asset_groups.parent_group_id 는 asset_groups 자신을 가리킨다. 지금 시드에서는 전부 비어 있다
- strategy_element_tags.element_id 는 strategy_elements.element_id 를 가리킨다
- price_daily 는 외래키가 없다. ticker 로 instruments 와 이어지지만 제약은 걸지 않았다
- 외래키에 ON DELETE CASCADE 는 없다. 전략서를 지울 때는 저장소 함수가 하위 테이블부터 지운다(`app/repositories/specs.py` 의 `delete_draft_spec`)

---

## 3. 테이블별 칸

### 3.1 users

- 계정. 화면에서는 아이디 하나로 가입한다(email 칸에 아이디가 들어간다)
- user_id: bigint, 기본키, 자동 증가
- email: varchar, 필수, 유일(uq_users_email)
- password_hash: varchar, 필수. 프로토타입이라 빈 문자열이 들어간다
- created_at: timestamptz, 필수, 기본값 now()

### 3.2 strategies

- 전략서 한 건. 내용 블록은 JSONB 로 담는다. 계약은 `app/contracts/spec.py`
- strategy_id: varchar, 기본키. `STR-` 와 12자리 16진수
- user_id: bigint, 필수, users 외래키
- strategy_version: varchar, 필수. 전략서 계약 버전(지금 0.1)
- name: varchar, 필수
- input_prompt: text. 사용자가 쓴 한국어 문장
- rebalance: jsonb. 리밸런싱 규칙
- signal_rules: jsonb. 옛 신호원 자리가 남아 있다. 기반 이후 전략서 v0.2 때 정리한다
- constraint_user: jsonb. 사용자가 말한 제약
- status: varchar, 필수. draft, approved, running, closed 중 하나. 승인 기능이 없어져 지금은 draft 로만 저장한다
- created_at: timestamptz, 필수, 기본값 now()

### 3.3 strategy_assets

- 전략서가 담은 종목과 종목별 비중 범위. 전략서 한 건과 같은 트랜잭션으로 들어간다
- strategy_asset_id: bigint, 기본키, 자동 증가
- strategy_id: varchar, 필수, strategies 외래키
- ticker: varchar, 필수, instruments 외래키
- weight_min, weight_max: numeric. 확정 범위. 검증기의 비중 범위 실현가능성 보정이 고친다
- weight_min_raw, weight_max_raw: numeric. LLM 이 낸 원래 범위. 저장 뒤 바뀌지 않는다
- was_adjusted: boolean, 필수, 기본값 false. 확정 범위가 원래 범위와 달라졌는지
- rationale: text. 지금 쓰는 코드가 없다

### 3.4 validation_logs

- 검증기 실행 기록. 한 번 검증하면 단계마다 한 행이 같은 트랜잭션(같은 checked_at)으로 들어간다
- validation_id: bigint, 기본키, 자동 증가
- strategy_id: varchar, 필수, strategies 외래키
- stage: smallint, 필수, 1 부터 4(`db/migrate/001`). 지금 코드는 1 스키마, 2 참조, 3 논리와 범위 보정만 쓴다
  - 전략서 v0.2 검사 기준 뜻: 1 모양, 2 존재, 3 말이 되는지, 4 근거. 시험 실행 단계는 두지 않는다(2026-11-02)
  - 4 근거 검사는 전략서 저장 전에 돌고, 결과는 전략서를 저장할 때 같이 남긴다. 되묻기나 거절로 끝난 문장의 기록은 이후 대화 테이블(결정 4)에 남긴다
- passed: boolean, 필수
- violations: jsonb. 위반 목록. 전략서 v0.2 검사부터 항목마다 종류(block 막힘, ask 되묻기, warn 경고)를 적는다. 칸은 늘리지 않는다
- adjusted_bounds: jsonb. 범위 보정 내역
- clamped_fields: jsonb
- checked_at: timestamptz, 필수, 기본값 now()

### 3.5 backtest_runs

- 백테스트 실행 한 건과 재현 조건. 상태는 queued, running, done, failed 순서로 한 방향만 간다
- run_id: bigint, 기본키, 자동 증가
- strategy_id: varchar, 필수, strategies 외래키
- period_start, period_end: date
- seed_money, fee_rate, tax_rate, slippage_bp: numeric
- wf_windows: integer. 지금 쓰는 코드가 없다
- random_seed: integer
- data_snapshot_asof: date. 실행이 본 가격의 마지막 날
- feature_set_version: varchar. 지표 계산 버전
- status: varchar
- started_at: timestamptz

### 3.6 backtest_metrics

- 백테스트 결과 지표. 실패하면 지표를 비운 행을 만들고 window_results 에 실패 이유를 넣는다
- run_id: bigint, 기본키, backtest_runs 외래키
- cagr, mdd, sharpe, sortino, win_rate, benchmark_cagr: numeric
- window_results: jsonb. 자산곡선, 리밸런싱 기록, 실행이 쓴 종목과 분류. 칸 이름(워크포워드 창별 결과)과 뜻이 어긋난다(`docs/known-issues.md`)

### 3.7 instruments

- 종목 원장. 국내 상장 보통주 2,667종목과 ETF 1,183종목, 모두 3,850종목이다(3단계, `data/listing.csv` 를 적재할 때 분류)
- ticker: varchar, 기본키
- name: varchar, 필수
- sector: varchar. 한글 섹터 라벨(예: 반도체). 코드는 sector_group_id 를 읽는다
- group_id: varchar, 필수, asset_groups 외래키. 자산군을 담던 옛 칸이라 asset_group_id 와 값이 같다
- risk_tag: varchar. 위험등급 G1 부터 G6
- mdd_3y, volatility_1y, expense_ratio: numeric
- listed_date, delisted_date: date
- is_leveraged: boolean, 필수, 기본값 false
- active: boolean, 필수, 기본값 true
- kind: varchar, stock(주식) 또는 etf(ETF)(`db/migrate/002`). 백테스트 세금 계산이 주식과 ETF 를 구분하는 데 쓴다. 이미 종목이 들어 있는 DB 에도 적용되게 NOT NULL 은 걸지 않았고, `scripts/load_etf_master.py` 가 빈 값을 막는다
- asset_group_id, sector_group_id, country_group_id: varchar, asset_groups 외래키. 자산군, 섹터, 국가 세 축. 국가는 주식과 채권에 모두 걸쳐 있어 트리가 아니라 칸을 나눴다(옛 주제의 `db/migrate/001` 을 흡수. 지금의 `db/migrate/001` 과 다른 파일이다)

### 3.8 asset_groups

- 분류표. 레벨 1 은 자산군(EQUITY, BOND, COMMODITY), 레벨 2 는 섹터(SECTOR_*)와 국가(COUNTRY_*)
- group_id: varchar, 기본키
- parent_group_id: varchar, asset_groups 외래키
- group_level: smallint, 필수, 1 또는 2
- label: varchar, 필수. 한글 이름
- description: text

### 3.9 price_daily

- 일별 가격. 시점 데이터라 `app/repositories/` 밖에서 직접 읽지 않는다(5장)
- ticker: varchar, 복합 기본키
- trade_date: date, 복합 기본키. 하이퍼테이블 시간 축
- open, high, low, close: numeric
- volume: bigint
- nav: numeric. 적재기가 채우지 않는다
- atr_14: numeric. 적재기가 채우지 않는다. 지표는 app/backtest/features.py 가 계산한다
- ingested_at: timestamptz, 필수, 기본값 now(). 적재 시각

### 3.10 strategy_elements

- 전략 요소 사전. 전략서에 들어갈 수 있는 값의 목록이다. 시드는 `db/seeds/05_bbl.sql`
- element_id: varchar, 기본키. 지표는 이 값이 그대로 전략서에 들어간다(예: ret_20). 나머지는 RB_, FL_, ET_ 접두어
- element_type: varchar, 필수. indicator, filter, rebalance, etf_trait 중 하나
- title: varchar
- description: text
- params_schema: jsonb. 이 요소를 고르면 무엇이 되는지
- embedding: vector(768). 아직 채우지 않는다
- active: boolean, 필수, 기본값 true

### 3.11 strategy_element_tags

- 전략 요소의 검색 태그. 접두어는 kw:, asset:, sector:, country:, risk: 다섯 개
- tag_id: bigint, 기본키, 자동 증가
- element_id: varchar, 필수, strategy_elements 외래키
- tag: varchar, 필수
- (element_id, tag) 유일 제약이 없다. 시드가 두 테이블을 비우고 다시 채워 중복을 막는다

---

## 4. 인덱스, 하이퍼테이블, 확장

- 확장: timescaledb(price_daily 하이퍼테이블), vector(strategy_elements.embedding), pg_trgm(지금 쓰는 코드가 없다)
- idx_price_ticker_date: price_daily (ticker, trade_date DESC). 시점 경계 조회의 진입점
- idx_strategy_elements_embedding: strategy_elements, hnsw (embedding vector_cosine_ops)
- price_daily 는 trade_date 기준 하이퍼테이블이다
- 트리거는 없다. 옛 승인 후 수정 금지 트리거(옛 `db/migrate/003`)와 승인 기록 유일 인덱스(옛 `db/migrate/002`)는 승인 기능과 함께 버렸다

---

## 5. 시점 경계 질의 규약

- 가격은 `app/repositories/price_daily.py` 의 함수로만 읽는다
- 조회 함수는 `as_of` 를 키워드 필수 인자로 받고 기본값을 두지 않는다. `as_of` 이하 거래일만 돌려준다
- `scripts/check_asof_guard.py` 가 `app/` 전체에서 `app/repositories/` 밖의 price_daily 참조를 잡는다

---

## 6. 빌드와 변경

- `scripts/build_db.sh` 가 볼륨 삭제, 기동, 스키마 생성 확인을 한 번에 한다. 개발 DB(spec)와 테스트 DB(spec_test)가 같이 만들어진다
- 공용 데이터 적재는 기반 정리 3단계에서 빌드 명령에 붙인다. 그전까지는 `scripts/seed_all.py` 와 `scripts/ingest_prices.py` 를 따로 돌린다
- `db/init/` 은 빈 볼륨 부트스트랩 전용이다. 이후 구조 변경은 `db/migrate/NNN_설명.sql` 로 쌓고 `scripts/apply_migrations.py` 로 적용한다. 적용하면 이력 테이블 schema_migrations 가 생긴다. `scripts/build_db.sh` 가 빌드 때 두 DB 에 적용한다
