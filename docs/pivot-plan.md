# 주제 변경 기반 정리 계획 — 한국어 투자 전략 명세화 · 백테스트 서비스

- 작성 2026-11-01. 기준 커밋 5edc604 (sub 브랜치)
- 이 문서의 최종 목표: 바뀔 주제를 개발하기 위한 깨끗한 기반을 만든다
  - 옛 주제(모의운용, 성향, 3개 신호원)의 코드, 데이터, 문서를 걷어내고, 새 주제 개발이 바로 올라탈 수 있는 상태로 만든다
  - 새 주제의 기능(전략서 v0.2, 판정기, 베이스라인, 평가, 화면)은 이 문서의 범위가 아니다. 기반이 끝난 뒤 별도 계획에서 다룬다. 8장에 참고로만 적었다
- 아직 코드는 하나도 바꾸지 않았다. 5장 결정 6건은 2026-11-01 확정. 단계 중 생기는 스키마 판단은 그 단계 시작 때 선택지를 내고 정한다

---

## 1. 무엇이 바뀌는가

- 주제
  - 기존: 자연어 전략 명세 컴파일 기반 국내 ETF 모의운용 시스템
  - 변경: 한국어 투자 전략 명세화 · 백테스트 서비스
- 대상
  - 기존: 투자 성향 기반 포트폴리오를 원하는 국내 ETF 투자자
  - 변경: 자동매매를 원하지만 코딩을 못 하는 한국인 일반 투자자
- 범위
  - 기존: 투자 성향 확인, 전략서 생성, 검증과 비중 통제, 3개 신호원 판단, 모의운용
  - 변경: 한국어 입력, 명세화, 백테스트 결과 확인. 모의운용은 범위에서 빠진다
- 핵심 기여
  - 기존: LLM 은 전략서 작성까지, 거래는 정해진 규칙대로
  - 변경: 한국어 전략 문장을 전략 요소 사전에 있는 요소만 쓰는 전략서로 변환
  - 사용자의 문장을 정확히 인식해 그대로 백테스트 거래를 하는 것이 핵심이다. 사용자가 말하지 않은 외부 판단(시장 국면, 뉴스 감성, 예측 모델)이 거래에 끼어들지 않는다
- 입력 판정
  - 기존: 성향별 허용 범위 · 비중 상한 준수 여부
  - 변경: 명세화 가능 / 추가 질문 필요 / 실행 불가 3분류
- 주 평가 지표
  - 기존: 재현성, 시점 무결성, 성향 준수
  - 변경: 변환 정확도, 판정 성능. 비교 대상은 LLM 이 백테스트 코드를 직접 생성하는 방식
  - 재현성과 시점 무결성은 주 지표에서 내려오지만 백테스트 신뢰성의 전제 조건으로 계속 지킨다

---

## 2. 용어 정리

- 문서 · 화면 · 주석에서 아래 용어로 통일한다. 코드 식별자(함수명, 테이블명, 파일명)는 바꾸지 않는다. 이름만 바꾸는 리팩터링은 비용 대비 얻는 게 없다
- Spec, 전략 명세는 전략서. 프로젝트 이름으로 쓰던 Spec 도 문서와 화면에서는 쓰지 않는다
  - 한국어 문장을 전략서로 바꾸는 과정은 명세화라 부른다. 주제명과 판정 라벨 명세화 가능은 그대로 둔다
  - 코드 식별자(`SpecV0_1`, `spec_id`, `strategy_specs`, `docs/infra-spec.md`)는 바꾸지 않는다
- BBL(블록 라이브러리)은 전략 요소 사전(허용값 카탈로그)
- 되묻기는 추가 질문(clarification)
- 범위 실행가능성 보정은 비중 범위 실현가능성 보정(feasibility)
- 4단 Validator 는 검증기. 하드캡 단계가 빠져 단계 수가 줄어든다
- OrderBuilder 는 주문 생성
- 검산 예제는 기준 계산 예제(회귀 테스트 기준값)
- 시점 무결성은 그대로. 설명에 point-in-time, 룩어헤드 방지를 덧붙인다
- 결정 3, 6 으로 기능이 사라져 더 이상 쓰지 않는 용어
  - 시장온도(시장 국면), 3관점 판단(3개 신호원), 관점 가중치(신호원 가중치), 통합 신호(앙상블 신호), 감성 렌즈
  - RiskSizer(포지션 사이징, ATR 기반), WeightMapper(신호-비중 매핑), GroupCapEnforcer(그룹 한도 적용)
  - 신호형 리밸런싱, same_policy(정책 버전 일치 검사), no_data 중립
  - 투자 성향, 허용범위 프리셋, 하드캡, 그룹캡

---

## 3. 버릴 것, 데이터 저장 구조, 챙길 것

### 3.1 버릴 것

- 모의운용 전체
  - 일일 판단과 실운용 결선: `backend/app/scoring/live.py`, Celery beat 의 `run_live_day`
  - 승인: `backend/app/m2/approval.py`, `backend/app/routers/portfolio.py`
  - 화면: `frontend/app/confirm`
  - 테스트: `test_live_judge.py`, `test_portfolio_approve.py`
- 3개 신호원과 외부 데이터 (결정 6)
  - 스코어러와 판단 계층: `backend/app/scoring/` 전체, `backend/app/views/` 중 `bridge.py`, `integrator.py`, `hedge.py`, `calibration.py`, `evidence.py`, `base.py`, `regime/`, `sentiment/`, `market/labels.py`, `market/dataset.py`
  - 예측 모델: LightGBM 학습과 저장(`scoring/market_model.py`, `scoring/model_store.py`), `backend/app/repositories/ml_models.py`
  - 거시 · 국면 · 뉴스 적재 기능 삭제 (2026-11-01 확정). 수집, 채점, 적재, 조회 경로를 전부 지운다
    - 뉴스: `scripts/score_news.py`, `scripts/score_news.Dockerfile`, `scripts/score_news.requirements.txt`, `scripts/ingest_news.py`, `scripts/eval_sentiment_states.py`, `backend/app/routers/news.py`(`backend/app/main.py` 의 라우터 등록과 태그 설명 포함), `backend/app/repositories/news.py`
    - 거시와 국면: `scripts/ingest_macro.py`, `scripts/ingest_index.py`, `scripts/build_regime.py`, `backend/app/macro/`, `backend/app/repositories/macro_indicators.py`, `backend/app/repositories/regime.py`
    - 작업 이름: `backend/app/workers/tasks.py` 의 `ingest_market`, `train_model`, `daily_judge`, `update_view_weights`, `run_live_day`
    - 설정: `.env.example` 의 `FRED_API_KEY`, `.gitignore` 의 `data/news/` 항목, 로컬 `data/news/` 폴더
    - 문서: `docs/news-archive-feasibility.md`, `docs/data-status.md` 의 거시 · 국면 · 뉴스 절, `CLAUDE.md` 의 거시 · 국면 적재 순서와 감성 기사 설명
  - 신호원 가중치: `backend/app/repositories/view_weights.py`, `feature_store.py`(피처 적재 저장소)
  - 계약: `backend/app/contracts/` 의 `view_score.py`, `view_weights.py`, `integrated_signal.py`, `target_weights.py` 중 신호 관련 부분
  - 화면: `frontend/app/views`
  - 의존성: pyproject 의 `ml` extra(lightgbm, shap, scikit-learn). FRED 키 등 환경변수
  - 관련 테스트: `test_hedge.py`, `test_integrator.py`, `test_calibration.py`, `test_bridge*.py`, `test_*_scorer.py`, `test_regime_*.py`, `test_sentiment_*.py`, `test_news_*.py`, `test_market_dataset.py`, `test_market_labels.py`, `test_market_model.py`, `test_ml_models_repo.py`, `test_macro_repo.py`, `test_view_weights_repo.py`, `test_feature_store_repo.py`, `test_scoring_schedule.py`, `test_views_base.py`
- 투자 성향, 프리셋, 하드캡, 그룹캡 (결정 3)
  - 설문과 성향: `backend/app/profile/`, `backend/app/routers/profile.py`, `backend/app/repositories/profiles.py`, `frontend/app/survey`, `frontend/app/profile`
  - 컴파일 파이프라인의 성향 관문(`backend/app/m1/pipeline.py`)과 후보 선정의 프리셋 · 하드캡 조회(`backend/app/m1/candidates.py`)
  - 프리셋과 하드캡 조회: `backend/app/repositories/presets.py`, `backend/app/routers/bounds.py`, `frontend/app/bounds`, `frontend/app/hardcap`
  - 검증기 하드캡 단계: `backend/app/m2/hardcap.py`
  - 비중 통제 체인: `backend/app/m2/weights.py`(포지션 사이징, 신호-비중 매핑, 그룹 한도 적용), `backend/app/backtest/m2_chain.py`
  - 정책 값 사본: `backend/app/backtest/policy.py`, `frontend/lib/policy.ts`. 사본을 맞출 대상 자체가 없어진다
  - 관련 테스트: `test_presets_repo.py`, `test_bounds_api.py`, `test_profile_survey.py`, `test_m2_weights.py`, `test_m2_chain.py`
- ETF 71종목 전용 가격 구조 (3.2 의 3년치 가격 스냅샷으로 대체, 3단계에서 지운다)
  - 종목 선정과 파생: `scripts/build_universe.py`, `scripts/merge_universe.py`, `data/universe.csv`, `data/meta.json`
  - 커밋된 ETF 가격 파일 `data/prices.csv`, `data/prices.meta.json`
  - `CLAUDE.md` 의 "가격을 다시 적재하면 worker 를 재시작" 규칙(스코어러 캐시가 사라져 필요 없다)
- 기존 DB 와 스키마 이력 (3.2)
  - 지금 각자의 DB 볼륨(`spec_pgdata`)은 전부 지운다
  - `db/init/02_schema.sql` 의 옛 주제 테이블과, 지운 테이블을 가리키는 칸
  - `db/migrate/001` 부터 `003`. 새 `db/init/` 에 흡수한다
- 자유 거래로 바뀌는 것의 뜻
  - 시스템이 비중이나 레버리지를 막지 않는다. 사용자가 문장으로 말한 한도(손절, 최대 비중 등)만 전략서에 들어간다
  - 다만 현금보다 많이 사는 주문, 상장 전이나 상장폐지 뒤 거래처럼 백테스트에서 물리적으로 불가능한 것은 검증기가 계속 막는다. 이것은 안전장치가 아니라 계산이 성립하기 위한 조건이다

### 3.2 데이터 저장 구조 (2026-11-01 확정)

- 문제: DB 구조가 바뀔 때마다 사람마다 DB 상태가 달라져 혼잡이 생겼다. 원인은 사람마다 적재 단계가 달랐던 것(시드, 가격, 거시, 뉴스, 모델 학습), 다시 돌리면 중복이 쌓이는 시드, 커밋할 수 없는 뉴스 데이터, 개발 DB 에 직접 쓰는 테스트다
- 원칙
  - 기존 DB 는 전부 지우고 처음부터 다시 만든다. 지금 DB 에 지켜야 할 데이터는 없다
  - DB 는 빌드할 때 처음부터 만들고, 공용 데이터는 그때 한 번에 적재한다. 누가 빌드해도 같은 DB 가 나온다
  - 개인 데이터만 각자 DB 에 쌓인다. 구조가 바뀌면 다시 빌드한다. 잃는 것은 자기 시험 데이터뿐이다
  - dev DB 서버는 두지 않는다. 공용 데이터는 git 과 공유 스냅샷 파일에 있다
- 공용 데이터 (빌드할 때 한 번에 적재)
  - 기준 데이터: 전략 요소 사전, 분류표, 종목 원장, 이후 생길 종목 이름 찾기 사전. 팀이 직접 만들거나 고친다. git 시드로 두고 PR 로 고친다
  - 가격: 최초 빌드 때 3년치 가격을 모두 같은 파일로 넣는다(아래)
- 가격 3년치 스냅샷 (가격만 특수하게 다룬다)
  - 한 사람이 국내 상장 개별주와 ETF 의 3년치 일별 가격을 한 번 받아 스냅샷 파일로 만든다. 나머지는 이 파일을 받아 빌드 때 적재만 한다. 각자 API 를 부르지 않으므로 모두 같은 가격을 본다
  - 스냅샷에는 받은 날, 소스, 기간, 종목 수, 해시를 같이 남긴다(지금 `data/prices.meta.json` 과 같은 방식)
  - 크기가 커서(종목 약 2,800개, 행 수백만) git 에 넣지 않는다. GitHub Release 같은 곳에 올리고 해시로 확인한다
  - 지금의 수집과 적재 분리(`fetch_prices.py` 가 파일을 쓰고 `ingest_prices.py` 가 파일만 읽어 적재)를 그대로 쓴다. 대상만 ETF 71종목에서 전 종목으로 넓힌다
  - 가격 조회 함수는 지금처럼 `as_of` 키워드 필수로 두고, `as_of` 이후 값은 돌려주지 않는다
  - 이 스냅샷이 평가용 가격도 겸한다. 평가와 베이스라인이 같은 파일을 본다
- 개인 데이터 (각자 DB 에만)
  - 계정, 전략서, 검증 기록, 백테스트 결과, 이후 생길 대화와 버전
- 새 `db/init/`
  - 남는 테이블만으로 스키마를 다시 쓴다. 옛 주제 테이블은 처음부터 만들지 않는다
  - 지운 테이블을 가리키던 칸도 없앤다: `strategy_specs.profile_id`, `strategy_specs.hardcap_version`, `spec_universe.preset_id`. 성향 없이 전략서를 저장할 수 없던 문제가 같이 풀린다
  - `db/migrate/001` 부터 `003` 의 내용을 흡수하고 `db/migrate/` 를 비운다. 이후 구조 변경은 다시 `db/migrate/` 에 순번으로 쌓는다
  - 지울 테이블
    - 모의운용: portfolios, positions, orders, executions, approvals, decision_records, reports
    - 성향과 하드캡: risk_profile_defaults, risk_profiles, survey_responses, preset_versions, asset_bound_presets, hardcap_versions, group_caps, group_cap_applications
    - 신호원과 외부 데이터: view_weights, view_scores, view_performance, feature_store, macro_indicators, regime_snapshots, news_articles, news_sentiment, ml_models
  - 남는 테이블: users, strategies, strategy_assets, validation_logs, backtest_runs, backtest_metrics, instruments, asset_groups, price_daily, strategy_elements, strategy_element_tags(이름 변경은 아래). audit_logs 는 쓰는 코드가 없어 2026-11-02 에 지우기로 했다
  - 테이블 · 칸 이름 변경 (2026-11-01 확정). 우리가 지어낸 말(Spec, BBL)과 이제 맞지 않는 말(ETF 원장)을 일반적인 단어로 바꾼다
    - etf_master 는 instruments. 개별주와 ETF 를 모두 담는다
    - strategy_specs 는 strategies, spec_universe 는 strategy_assets
    - bbl_blocks 는 strategy_elements, bbl_tags 는 strategy_element_tags
    - 칸 이름도 같이 바꾼다: spec_id 는 strategy_id(strategies, strategy_assets, validation_logs, backtest_runs), spec_version 은 strategy_version, spec_universe_id 는 strategy_asset_id, block_id 는 element_id, block_type 은 element_type
    - 코드의 저장소 함수, API 응답 키, 전략서 계약의 칸 이름(spec_id, spec_version)도 같이 바꾼다
    - 그대로 두는 이름: users, validation_logs, backtest_runs, backtest_metrics, asset_groups, price_daily, schema_migrations
  - 칸을 더하거나 새 테이블을 만드는 것은 이번 범위가 아니다. 지우는 것만 한다

### 3.3 챙길 것 (기반으로 남는 것)

- 전략 컴파일 파이프라인(`backend/app/m1/`) — 새 핵심 기여의 본체
  - `intent.py` 의도 파악, `schema_builder.py` 스키마 좁히기, `questions.py` 추가 질문, `excluded.py` 못 담은 것과 사유, `postprocess.py` 파싱과 검사, `session.py` Redis 대화 상태
  - `candidates.py` 는 프리셋 의존을 걷어내고 종목 이름 찾기와 상장 상태 확인만 남긴다
- 전략 요소 사전(`db/seeds/05_bbl.sql`, `backend/app/repositories/bbl.py`)
- 검증기의 스키마 · 참조 · 논리 단계(`backend/app/m2/validator.py`, `checks.py`, `stages.py`), 비중 범위 실현가능성 보정(`feasibility.py`)
- 가격 기반 지표 계산(`backend/app/views/market/features.py`) — stdlib 순수 함수이고 미래를 보지 않는 테스트가 붙어 있다. 이후 조건형 전략서의 지표 계산 코어가 된다
- 백테스트 러너의 신호와 무관한 부분(`backend/app/backtest/`)
  - 주기 일정(`weekly_dates`, `monthly_first` 등), 비중 이탈 계산, 매수 후 보유 비교군, 지표 계산(`service.compute_metrics`), 실행 기록 저장
- 가격 수집 · 적재 · 조회: `scripts/fetch_prices.py`, `scripts/ingest_prices.py`, `backend/app/repositories/price_daily.py`. 대상을 전 종목 3년치로 넓혀 스냅샷 방식으로 쓴다(3단계)
- 종목 원장(`backend/app/repositories/etf_master.py`, `scripts/load_etf_master.py`). 개별주까지 넓혀 빌드 때 적재한다
- as_of 규약과 가드: 가드 대상이던 피처 · 거시 · 가중치 테이블이 모두 사라진다. `price_daily` 를 저장소 함수 밖에서 직접 읽지 못하게 `scripts/check_asof_guard.py` 의 대상을 바꾼다
- 인증(JWT, 단일 역할), 작업 SSE 패턴(`backend/app/routers/jobs.py`)
- 전략 요청 화면(`frontend/app/compile`), 백테스트 결과 화면(`frontend/app/backtest`, `frontend/app/report`). 리포트는 실제 실행 결과만 그린다(3.4)

### 3.4 데모용으로 과하게 세팅한 것 (제외)

- 기준: 발표 시연을 위해 만들었고, 새 주제의 기능이나 평가 어느 쪽에도 쓰이지 않는 것
- TS 백테스트 러너와 두 러너 대조 체계
  - `frontend/scripts/run-backtest.ts`, `frontend/lib/backtest.ts`, `frontend/data/` 의 정적 결과 3종
  - `scripts/run_backtest_vbt.py`, `backend/tests/test_backtest_parity.py`, `test_backtest_drift.py` 중 TS 결과를 읽는 부분
  - 파이썬 러너 하나와 같은 입력 2회 실행 테스트로 충분하다. node 22 실행 절차도 같이 지운다
- 정적 데모 리포트: `/report` 가 run_id 없이 정적 결과를 그리는 경로(`frontend/app/report/page.tsx`, `frontend/components/report.tsx`, `frontend/lib/data.ts` 의 정적 분기)
- 리포트 연출
  - 숫자 애니메이션(`frontend/components/report/motion.tsx`), 맞춤 툴팁과 지표 안내(`tooltips.tsx`, `metric-guide.tsx`), 비중 띠 그래프(`weight-band.tsx`)
  - 수익 곡선, 핵심 지표, 거래 기록만 남긴다. 요약 문단 템플릿(`frontend/lib/report-narrative.ts`)에서는 신호원 문장을 뺀다
- 데모 샘플 일괄 실행: `scripts/run_demo_samples.py`, `frontend/public/data/demo-samples.json`, `frontend/lib/demo-samples.ts`, `frontend/components/demo-samples.tsx`, `frontend/app/samples`, `frontend/scripts/check-demo.ts`
- 재현성 비교 화면: `frontend/app/repro`, `frontend/lib/repro.ts`. 같은 입력 2회 실행 테스트로 대신한다
- 관점 브리핑: `frontend/scripts/check-views.ts`, `frontend/lib/views-briefing.ts`, `frontend/components/views-briefing.tsx`
- 3역할 권한(retail, pro, admin): `backend/app/core/security.py` 의 역할 구분, `backend/app/routers/admin.py`, `scripts/dev_seed.py` 의 역할별 계정. 로그인과 JWT 는 남긴다
- 쓰지 않는 인프라
  - vLLM `gpu` 프로필과 `vllm_client.py`. LLM 은 Ollama qwen3:8b 하나로 고정한다(결정 5)
  - `beat` 서비스: 일일 판단이 없어 할 일이 없다
  - MinIO 와 `minio-init`: 모델 저장에만 썼고 모델이 사라진다
  - Prometheus, Grafana: 기준 문서에만 있고 compose 에 없다. 문서에서 지운다
- `docs/infra-spec.md` 수용 기준 중 위 항목에 걸린 것을 지운다(아직 안 함, 1단계 결과 참조)

---

## 4. 기반이 끝났다고 말할 수 있는 조건

- 옛 주제의 코드, 테스트, 화면, 설정이 저장소에 남아 있지 않다(3.1, 3.4)
- 남은 기능이 끝까지 돈다: 문장 입력, 전략서 저장, 검증, 백테스트, 결과 화면
- 로컬 테스트 명령 전부 통과, as_of 가드 통과, `docker compose up` 이 줄어든 서비스로 정상 기동
- DB 에 옛 주제 테이블이 없다. `db/init/` 이 남는 테이블만 만든다
- 누구든 빌드 명령 하나로 같은 DB 를 만든다. 공용 데이터(기준 시드와 3년치 가격 스냅샷)가 그때 한 번에 들어간다. 두 번 연속 실행해도 같은 결과
- 테스트가 개발 DB 를 더럽히지 않는다
- `CLAUDE.md` 와 기준 문서가 새 주제와 새 구조를 설명한다. 옛 주제 문서는 기준 문서와 섞여 있지 않다

---

## 5. 결정 사항 (2026-11-01 확정)

- 무엇을 버리고 남길지의 근거다. 새 주제 기능의 세부는 8장 참고

### 결정 1 — 전략서 구조: C

- 전략 유형 필드로 비중형(v0.1 계열)과 조건형(v0.2)을 모두 허용한다
- 기반 정리에서는 비중형 v0.1 을 그대로 두고, 조건형은 기반 이후에 더한다

### 결정 2 — 종목 범위: 국내 상장 주식 (개별주와 ETF)

- 국내 증시(코스피, 코스닥)에 상장된 개별주와 ETF 를 대상으로 한다. 기존 ETF 에 개별주를 더한다
- 해외 거래소에 상장된 종목(예: 애플, 테슬라 직접 매매)은 대상이 아니다. 국내에 상장된 해외지수 ETF(예: TIGER 미국나스닥100)는 대상이다
- 성향을 없앴으므로(결정 3) 위험등급 배정은 하지 않는다
- 기반 정리에서는 개별주와 ETF 의 종목 원장과 3년치 가격 스냅샷까지 만든다. 종목 이름 찾기 사전 등은 기반 이후

### 결정 3 — 투자 성향: A (하드캡도 제거, 자유 거래)

- 성향 설문, 허용범위 프리셋, 성향별 제약, 그룹캡, 하드캡을 모두 쓰지 않는다
- 시스템은 사용자가 말한 대로만 거래한다. 사용자가 말한 손절이나 비중 한도는 전략서에 들어간다
- 계산이 성립하지 않는 것(현금 초과 매수, 상장 기간 밖 거래)만 검증기가 막는다
- `CLAUDE.md` 의 확정 수치 조항(2026-09-08 팀 확정본)은 주제 변경으로 효력을 잃는다

### 결정 4 — 판정 결과 저장 위치: 새 테이블

- 사용자가 자기 한국어 요청을 AI 채팅 세션창처럼 대화 단위로 남기고, 대화 안에서 버전별로 다시 볼 수 있어야 한다
- 기반 이후에 새 테이블로 만든다. 기반 정리에서는 테이블을 만들지 않는다

### 결정 5 — 베이스라인 모델: A

- 같은 qwen3:8b 로만 비교한다. 그래서 vLLM 과 외부 API 를 기반에서 지운다

### 결정 6 — 3개 신호원: 제거

- 사용자의 자연어 요청을 정확히 인식해 그대로 백테스트 거래를 하는 것이 핵심이므로, 시장 국면, 뉴스 감성, 예측 모델 같은 외부 데이터와 판단은 필요 없다
- 기술적 지표 자체(이동평균, RSI 등 가격으로 계산하는 값)는 남는다. 빠지는 것은 그 지표를 입력으로 쓰던 예측 모델이다

---

## 6. 단계별 진행

- 각 단계는 사용자가 지시할 때만 시작한다
- 순서: 0단계 규칙 정리, 1단계 코드 걷어내기, 2단계 DB 테이블 구성 다시 하기, 3단계 공용 데이터와 가격 3년치 스냅샷 적재

### 0단계 — 규칙 정리

- `CLAUDE.md` 를 새 주제에 맞게 고친다
  - 절대 규칙: 확정 수치 조항 삭제, `docs/m2-algorithms.md` 정본 조항 삭제, infra-spec 9장 "하지 말 것" 조항 재검토, as_of 대상 테이블을 가격 중심으로. "`db/init/` 은 고치지 않는다" 는 이번 한 번 예외로 새로 쓰고, 그 뒤로 다시 지킨다
  - 아키텍처: 4계층 설명을 명세화, 판정, 검증, 백테스트로
  - 명령: TS 백테스트, 거시 · 국면 · 뉴스 적재 절차, 가격 적재 순서, vLLM 프로필, `requires_ml` 마커 설명 삭제. DB 빌드 명령과 가격 스냅샷 받는 법으로 바꾼다
  - 데이터 저장 원칙(3.2): DB 는 빌드 때 처음부터 만든다. 공용 데이터는 git 시드와 3년치 가격 스냅샷, 개인 데이터만 각자 DB
- 마감 일정, 발표 형식, 팀 역할 재분배는 지금 정하지 않는다(2026-11-01)
- 끝난 기준: `CLAUDE.md` 개정 (2026-11-01 완료)

### 1단계 — 코드 걷어내기 (2026-11-01 완료)

- 원칙
  - 지우기만 하고 새 기능은 추가하지 않는다
  - 옛 주제를 위한 코드는 "안 쓰고 남겨 두기" 보다 지우는 쪽을 택한다. 남겨 두면 다음 단계에서 다시 읽고 피해야 한다
  - 지운 뒤에도 남는 기능(문장 입력, 전략서 저장, 검증, 백테스트, 결과 화면)은 끝까지 돌아야 한다
- 지울 것: 3.1 과 3.4. 순서는 아래대로 해서 한 번에 깨지는 범위를 줄인다
  - 화면과 데모 세팅(정적 리포트, 샘플, 재현성, 관점 브리핑, 성향, 범위, 하드캡, 확인 화면)
  - 모의운용과 3개 신호원(스코어러, 판단 계층, 예측 모델)
  - 거시 · 국면 · 뉴스 적재 기능(스크립트, 라우터, 저장소, 작업 이름, 설정)
  - 백테스트 러너에서 `views.bridge` 와 `policy` 떼어내기, 비중 통제 체인 제거
  - 성향, 프리셋, 하드캡 조회와 컴파일 파이프라인의 성향 관문
  - TS 러너, 3역할 권한, vLLM, beat, MinIO, `ml` extra
- 이번에 지우지 않는 것: ETF 가격 파일(`data/prices.csv`)과 종목 선정 스크립트. 3년치 스냅샷이 3단계에서 생긴다. 지금 지우면 백테스트가 멈춘다
- DB 는 2단계에서 통째로 새로 만든다. 1단계 동안은 기존 DB 를 그대로 둔다
- 지우면서 바뀌는 동작 (새 기능이 아니라 남는 코드로 버티는 동작)
  - 백테스트 비중: 신호 관련 인자와 분기(`signals`, `use_signals`, 신호형 리밸런싱, 신호원 가중치 기록)를 러너에서 지우고, 전략서에 적힌 비중 범위의 정중앙으로 돈다. 지금 `use_signals=False` 경로가 하는 계산과 같다
  - 검증기: 하드캡 단계를 빼고 스키마 · 참조 · 논리만 돈다
  - 승인 흐름이 없어져 전략서는 draft 상태로만 남는다
- 이 단계 시작 때 정할 것
  - 남는 코드의 자리: `views/` 를 지우면 그 안의 `views/market/features.py` 만 남는다. 사라지는 패키지 안에 두지 않고 백테스트 쪽으로 옮긴다
  - 옛 주제 문서: `docs/m2-algorithms.md`, `docs/m2-validator-api.md`, `docs/m2-체인통합-승인-결정기록.md`, `docs/강준_구현현황.md`, `docs/m1_milestone.md`, `docs/frontend_milestone.md`, `docs/llm-model-comparison.md`, `docs/data-status.md`, `docs/news-archive-feasibility.md`, `docs/spec_exam.json`. 지울지 `docs/archive/` 로 옮길지
- 성향 코드를 지우면 기존 DB 에서는 전략서 저장이 깨진다(`profile_id` 등 NOT NULL 외래키). 2단계에서 테이블을 새로 구성하면 풀린다. 1단계부터 3단계까지 이어서 끝내야 다시 끝까지 돈다
- `scripts/check_asof_guard.py` 의 대상 테이블을 고친다
- 버린 기능의 테스트는 지우고, 남는 기능의 테스트는 통과시킨다
- 지우는 양이 전체의 절반 가까이 된다. 한 커밋에 몰지 말고 위 순서대로 나눠 커밋한다
- 끝난 기준: 옛 주제 코드 · 테스트 · 화면 · 설정이 없음, 린트와 as_of 가드 통과, `docker compose up` 이 줄어든 서비스로 정상 기동. 전략서 저장과 테스트 전체 통과는 3단계 끝에서 본다

- 1단계 결과 (2026-11-01)
  - 정한 것: `features.py` 는 `backend/app/backtest/features.py` 로 옮겼다. 옛 주제 문서 10개는 `docs/archive/` 로 옮겼다
  - 3.1 과 3.4 의 코드 · 테스트 · 화면 · 설정을 지웠다. compose 에서 minio · minio-init · beat · vllm 이 빠졌다
  - 계획에서 더 나간 것
    - `backend/app/m2/orders.py` 와 `contracts/target_weights.py`: 3.3 에서 챙길 것으로 적었지만, 쓰던 곳이 비중 통제 체인뿐이라 쓰는 곳이 없어져 지웠다
    - `scripts/dev_seed.py`: 3역할 계정과 성향을 붙이는 스크립트라 통째로 지웠다
    - 저장소 루트의 `Claude outputs/` 와 `plans/` 도 옛 주제 문서라 `docs/archive/claude-outputs`, `docs/archive/plans` 로 옮겼다
    - 전략 요소 사전에서 `RB_SIGNAL`(신호 기반 리밸런싱) 블록을 지웠다. 러너가 더 이상 돌릴 수 없다
    - 컴파일 프롬프트의 성향 기본 제약 대신 "제약 없음" 값(현금 최소 0, 최대 낙폭 1, 1회 최대 손실 1)을 준다. 사용자가 말하지 않은 제약은 걸지 않는다
    - 리포트 화면은 수익 곡선 · 핵심 지표 · 종목과 비중 범위 · 리밸런싱 기록만 남긴 작은 화면으로 새로 썼다. 요약 문단 템플릿은 지웠다(한국어 역설명을 만들 때 새로 쓴다)
  - 바뀐 동작: 후보의 비중 범위는 전부 0 부터 1, 지목한 레버리지 종목은 담긴다, 검증기는 3단(스키마 · 참조 · 논리), 백테스트는 범위 정중앙 비중으로 전략과 시장 두 계열만 돈다
  - 확인: lint · as_of 가드 · 프론트 타입 검사 통과. 2단계 구조를 흉내 낸 임시 DB(옛 NOT NULL 칸을 푼 것)에서 로컬 테스트 230건, 백테스트 테스트 29건 통과. 개발 DB 에서는 `users.role` NOT NULL 때문에 저장 테스트 47건이 실패한다 — 2단계에서 풀린다
  - 남은 것
    - 전략서 계약의 `signal_rules`(시장분석 · 감성 · 시장온도 자리)와 전략 요소 사전의 `KOSPI200` · `VIX_CLOSE` 블록은 남아 있다. 계약을 바꾸는 일이라 기반 이후 전략서 v0.2 때 정리한다(8장)
    - `docs/infra-spec.md`, `docs/known-issues.md`, `docs/human_manual.md`, `docs/db-erd.md` 는 아직 옛 주제 내용이다
    - `frontend/components/flow.css` 에 지운 화면용 스타일이 남아 있다

### 2단계 — DB 테이블 구성 다시 하기

- 새 `db/init/` 을 쓴다(3.2)
  - 남는 테이블 11개만 만든다: users, strategies, strategy_assets, validation_logs, backtest_runs, backtest_metrics, instruments, asset_groups, price_daily, strategy_elements, strategy_element_tags
  - 3.2 의 테이블 · 칸 이름 변경을 이때 같이 한다. 코드(저장소 함수, 라우터, 테스트, 시드, 스크립트, 프론트의 API 키)도 같이 맞춘다
  - 지운 테이블을 가리키던 칸을 없앤다: `strategy_specs.profile_id`, `strategy_specs.hardcap_version`, `spec_universe.preset_id`
  - `users.role` 의 3역할 제약 등 지운 기능에 걸린 제약과 트리거를 정리한다
  - `db/migrate/001` 부터 `003` 을 흡수하고 `db/migrate/` 를 비운다
- 칸을 더하거나 새 테이블을 만들지 않는다. 지우고 정리만 한다
- 테이블을 읽고 쓰는 저장소 함수(`backend/app/repositories/`)와 모델을 새 구성에 맞춘다
- 테스트는 별도 DB(예: `spec_test`)를 쓰게 `backend/tests/conftest.py` 를 고친다. 개발 DB 가 테스트 찌꺼기로 더러워지지 않는다
- 빌드 명령의 앞부분: 볼륨 삭제, 기동, 스키마 생성까지 한 번에 한다. 데이터 적재는 3단계에서 붙인다
- `docs/db-erd.md` 를 남는 테이블 기준으로 고친다
- 끝난 기준: 빈 볼륨에서 빌드하면 테이블 11개만 새 이름으로 생김, 코드에 옛 테이블 · 칸 이름이 남지 않음, 두 번 연속 빌드해도 같은 스키마, DB 데이터가 필요 없는 테스트 통과

- 2단계 결과 (2026-11-02)
  - 새 `db/init/02_schema.sql` 이 테이블 11개를 새 이름으로 만든다. 옛 migrate 001 의 분류 세 칸은 instruments 에 넣었고, 002 · 003 과 승인 상태 트리거는 버렸다. `db/migrate/` 는 비었다
  - `db/init/03_test_db.sh` 가 같은 스키마로 테스트 전용 DB `spec_test` 를 만든다. `backend/tests/conftest.py` 는 DB 이름 끝이 `_test` 가 아니면 멈추고, 비어 있으면 `seed_all.py` 로 시드를 한 번 넣는다
  - 빌드 명령 앞부분: `scripts/build_db.sh` (볼륨 삭제, 기동, 두 DB 의 테이블 수 확인). 데이터 적재는 3단계에서 붙인다
  - 코드 · 테스트 · 시드 · 프론트 API 키를 새 테이블 · 칸 이름으로 맞췄다. 파일과 모듈 이름(`repositories/etf_master.py`, `bbl.py`, `specs.py`, `04_etf_master.csv`)은 2장 원칙대로 두었다
  - 계획에서 더 나간 것
    - `users.role` 은 NOT NULL 만 푸는 대신 칸을 지웠다. 읽는 코드가 없다
    - 지운 기능에 걸린 CHECK 를 좁혔다: `validation_logs.stage` 1~3, `strategy_elements.element_type` 에서 lens 제외
    - `db/seeds/03_asset_groups.sql` 의 그룹캡 INSERT 를 지웠다. group_caps 가 없어 시드와 테스트가 깨지기 때문이다. 시드 01 · 02 는 `seed_all.py` 에서만 뺐고 파일 삭제는 3단계 몫이다
  - 그대로 둔 것: `strategies.status` 의 CHECK 4값(draft, approved, running, closed). draft 가 아니면 막는 코드와 테스트가 approved 를 쓴다
  - 확인: 빈 볼륨 빌드 두 번 모두 11개, 스키마 덤프 동일. lint, as_of 가드, 프론트 타입 검사 통과. 로컬 테스트 229건, 테스트 DB 에 가격을 넣은 뒤 백테스트 테스트 29건 통과. 테스트 뒤 개발 DB 행 수 변화 없음
  - 팀원은 기존 볼륨을 지우고 `scripts/build_db.sh` 로 다시 빌드해야 한다

### 3단계 — 공용 데이터와 가격 3년치 스냅샷 적재

- 공용 데이터(3.2)를 빌드 때 한 번에 적재하게 한다
  - 기준 시드: 전략 요소 사전, 분류표. 성향 · 하드캡 · 그룹캡 시드(`db/seeds/01` 부터 `03` 중 해당 부분)는 지운다. 남는 시드는 두 번 돌려도 같은 결과가 나오게 한다
  - 종목 원장: 국내 상장 개별주와 ETF 전체로 넓힌다. 3년 안에 상장폐지된 종목은 넣지 않는다.
  - 가격: 3년치 스냅샷(아래)
- 가격 스냅샷의 첫 작업은 가격 소스 실측이다. FinanceDataReader(지금 쓰는 것), 공공데이터포털 주식시세정보, 증권사 Open API 중에서 수정주가, 상장폐지 종목, 전 종목을 받는 데 걸리는 시간과 호출 제한을 확인하고 하나를 고른다
- `fetch_prices.py` 를 전 종목 3년치로 넓혀 스냅샷 파일을 만든다. 받은 날, 소스, 기간, 종목 수, 해시를 메타 파일에 남긴다
- 스냅샷을 공유 위치(GitHub Release 등)에 올리고, 빌드 명령이 이 파일을 받아 `ingest_prices.py` 로 적재하게 한다
- 빌드 명령 완성: 2단계의 스키마 생성 뒤에 기준 시드, 종목 원장, 가격 스냅샷 적재를 붙인다
- 팀원 전원이 기존 볼륨을 지우고 새로 빌드한다. README 의 "표 구조가 바뀌었을 때" 절차(뉴스가 있는 사람, 없는 사람)를 빌드 명령 하나로 바꾼다
- ETF 71종목 전용 가격 구조(`build_universe.py`, `merge_universe.py`, 커밋된 `data/prices.csv` 등)를 지운다(3.1)
- 이 단계 시작 때 정할 것
  - 3년의 기준: 끝나는 날을 언제로 할지(예: 2025-12-31). 이동평균 같은 지표는 앞쪽 이력이 필요해서, 120일선을 쓰면 첫 반년은 신호가 안 나온다. 지표 준비 기간을 3년 안에서 쓸지 앞에 더 받을지
  - 스냅샷 밖의 요청(3년보다 앞선 기간, 스냅샷에 없는 종목): 실행 불가(데이터가 없는 기간 · 종목)로 둘지, 그때 API 로 더 받을지
  - 상장폐지 종목을 무료 소스로 못 받으면 어떻게 할지
  - 파일 형식(CSV 압축, Parquet 등)과 올릴 곳
- 끝난 기준: 빈 볼륨에서 빌드 명령 한 번으로 스키마와 공용 데이터가 다 들어감, 두 번 연속 빌드해도 같은 DB, 두 사람이 빌드한 DB 의 가격이 같음(해시 일치), 로컬 테스트 전부 통과, 테스트를 돌려도 개발 DB 의 행이 바뀌지 않음, 문장 입력부터 백테스트 결과까지 한 번 끝까지 돎, 개별주 하나와 ETF 하나로 백테스트가 돎, 상장폐지 종목의 가격이 폐지일까지 있음, `as_of` 경계 테스트 통과

- 3단계 결과 (2026-11-02, 진행 중)
  - 시작 때 정한 것
    - 가격 소스: 네이버 금융 차트 API(FinanceDataReader 가 국내 종목에 쓰는 소스). 수정주가임을 확인했다(에코프로 2024-04 5:1 분할 전후가 이어짐). 공공데이터포털 · 증권사 API 는 재지 않았다
    - 기간: 5년(2021-11-02 ~ 2026-10-30). 백테스트 3년 앞에 이동평균 같은 지표의 준비 기간 2년을 둔다
    - 상장폐지 종목: 이번 스냅샷은 뺀다. 목록이 받는 날 상장된 종목이라 기간 중 폐지된 종목이 없다(생존편향). 메타의 excluded 에 적었다. 끝난 기준의 "상장폐지 종목의 가격이 폐지일까지 있음" 은 채우지 못한다
    - 파일 형식: Parquet(zstd). 올릴 곳은 GitHub Release
    - 스냅샷 밖의 요청(5년보다 앞선 기간, 없는 종목)은 아직 정하지 않았다. 판정기를 만들 때 실행 불가 사유로 다룬다
  - 만든 것
    - `scripts/fetch_prices.py`: 수집과 스냅샷 만들기. 이미 받은 원천 CSV 가 있으면 `--from-raw` 로 API 없이 만든다. 같은 입력이면 같은 파일이 나온다(두 번 만들어 파일 해시 · 내용 해시 동일)
    - 스냅샷 2026-11-02: 보통주 2,667 · ETF 1,183 · 3,925,500행 · 45.4MB. 거래정지일(시가 · 고가 · 저가 0) 40,097행의 시가 · 고가 · 저가를 빈 값으로 바꿨다
    - `data/prices_snapshot.json`: 버전, 소스, 기간, 종목 수, 파일 해시, 내용 해시, 정리 규칙, 뺀 것
    - `scripts/ingest_prices.py`: 스냅샷을 내려받거나 로컬에서 찾아 파일 해시를 맞추고, COPY 로 한 번에 적재한 뒤 DB 의 내용 해시를 메타와 맞춘다(`price_daily.copy_price_bars`, `stored_price_content_sha256`)
    - 종목 원장 3,850종목: `scripts/build_etf_master.py` 가 `data/listing.csv` 를 분류하고 `load_etf_master.py` 가 바로 적재한다. 같은 내용을 두 번 들고 있던 `db/seeds/04_etf_master.csv` 는 지웠다(2026-11-02)
      - 보통주 업종은 KRX 상장법인 업종(KSIC 158가지)을 `db/seeds/industry_sector_map.csv` 로 업종 묶음 6개에 매핑하고 나머지는 기타. 매핑 중 "검토" 로 표시한 4줄은 팀 확인이 필요하다
      - ETF 는 예전에 사람이 분류한 73종목(`db/seeds/etf_curated.csv`)은 그 값을, 나머지는 목록 분류와 이름 키워드로 자산군 · 국가 · 업종 · 레버리지를 정한다. 분류가 "기타" 인 ETF 는 근사다
      - 위험등급 · 변동성 칸은 비운다(결정 3). `load_etf_master.py` 의 G1 과 레버리지 일치 검사를 뺐다
    - `scripts/build_db.sh` 에 두 DB 의 공용 데이터 적재(seed_all, ingest_prices)를 붙였다
    - 지운 것: `db/seeds/01_hardcap_v0_1.sql`, `02_preset_v0_1.sql`, `delisted_candidates.csv`, `scripts/build_universe.py`, `merge_universe.py`, `data/universe.csv`, `prices.csv`, `prices.meta.json`, `meta.json`, CSV 대조 테스트 1개
    - pyproject 의 backtest extra 에 pyarrow 를 더했다(이미지 재빌드 필요)
  - 남은 것
    - 빈 볼륨 빌드 두 번, 테스트 전체, 개별주 하나와 ETF 하나 백테스트, as_of 경계 테스트 확인
    - 스냅샷 파일을 GitHub Release 에 올리고 메타의 url 을 채운다
    - 팀원 전원 볼륨 삭제 후 새로 빌드

---

## 7. 주의할 점

- 걷어내기 규모: 1단계에서 지우는 코드와 테스트가 전체의 절반 가까이 된다. 순서대로 나눠 커밋하고, 커밋마다 테스트를 돌린다
- 브랜치: 팀원이 기존 `main` 에서 작업 중이면 대량 삭제와 충돌이 크다. 걷어내기를 어느 브랜치에서 할지 1단계 시작 전에 맞춘다
- 시점 무결성: 가격 대상을 넓힐 때 `as_of` 규약이 빠지지 않게 경계 테스트(`as_of` 당일, 다음 날)를 유지한다
- DB 를 새로 만드는 날: 팀원 전원이 기존 볼륨을 지워야 한다. 날짜를 맞추고, 지키고 싶은 시험 데이터가 있으면 각자 미리 백업한다
- 외부 API 의존: 스냅샷을 만드는 한 번만 API 에 기댄다. 무료 라이브러리는 예고 없이 깨질 수 있으니 스냅샷 파일과 메타를 두 곳 이상에 둔다
- 수정주가 재계산: 스냅샷을 다시 받으면 과거 값이 달라질 수 있다. 스냅샷은 버전을 붙여 새로 만들고 옛 버전을 덮어쓰지 않는다
- 개별 주식 데이터 함정: 수정주가를 안 쓰거나 상장폐지 종목을 빼면 결과가 체계적으로 틀린다. 3단계 소스 실측에서 반드시 확인한다

---

## 8. 기반 이후에 할 일 (이 문서 범위 밖, 참고)

- 기반이 끝난 뒤 별도 계획 문서에서 다룬다. 지금까지 논의된 것을 잊지 않게 목록만 남긴다
- 평가 문장 수집과 동결: 한국어 전략 문장 200건 안팎, 3분류와 두 전략 유형, 개별주와 ETF, 외부 데이터 · 해외 종목을 요구하는 실행 불가 문장. 2인 라벨링과 일치도. 시험용은 사전 조정 전에 동결. 라벨링 전에 "말하지 않은 정보를 물을지 기본값으로 채울지" 와 "일봉만 쓸지" 를 정한다
- 전략 요소 사전 확장과 전략서 v0.2(조건형), 비중형 목표 비중 정의, 전략서의 한국어 역설명
- 종목 이름 찾기 사전(약칭, 영문명, 옛 이름), ETN · 리츠 · 우선주 · 스팩 포함 여부
- 판정기: 명세화 가능 / 추가 질문 필요 / 실행 불가와 닫힌 사유 코드. 대화와 버전 테이블(결정 4)
- 컴파일러 개편: 두 유형 전략서와 판정기에 맞춤
- 조건형 백테스트: 체결 시점 규칙, 거래비용과 세금, 손절과 익절, 룩어헤드 테스트
- 베이스라인: 같은 qwen3:8b 로 백테스트 코드 직접 생성, 네트워크 없는 격리 컨테이너에서 실행
- 시험용 평가: 3년치 가격 스냅샷으로 돌림, 변환 정확도와 판정 성능, 실행 불가인데 실행해 버린 비율
- 화면 개편: 입력, 판정, 전략서 확인, 백테스트 결과, 채팅 세션창 같은 대화 목록
- 문서와 발표 자료
