# spec

자연어 → Strategy Spec(JSON) 컴파일 → 결정론적 집행. 국내 ETF 모의운용 졸업작품 공통 인프라.

> 이 README는 아직 전체가 아니다. 3·4단계(DB 스키마·시드) 작업 중 나온 "알려진 설계 구멍"만
> 먼저 기록해 둔다. clone 후 기동 방법, 팀원별 착수 가이드 등 나머지는 10단계에서 채운다.

## 알려진 설계 구멍

작업하면서 `docs/db-erd.md`(정본)와 실제 요구사항이 어긋나거나, 정본에 없어서
구현하지 못한 부분들이다. 컬럼을 임의로 추가하는 대신 여기에 기록만 해 둔다.

- **ETF_MASTER에 국가 컬럼이 없다.** `docs/db-erd.md`의 A06(종목별 위험등급·자산군·섹터·국가
  부여)과 1.7.3(국가 상한 0.50)은 종목의 국가를 요구하지만, ETF_MASTER 테이블에는
  `sector`/`group_id`만 있고 `country`가 없다. DB 스키마에는 임의로 컬럼을 추가하지
  않았다. `db/seeds/04_etf_master.csv`에는 `country` 컬럼을 넣어 뒀다 — CSV는
  스키마가 아니라 입력 파일이라 여기서만 우회했다. `GROUP_CAPS`의 국가 그룹
  (COUNTRY_KR/COUNTRY_US/COUNTRY_OTHER) 상한을 실제로 계산에 반영하려면, 이 컬럼을
  ETF_MASTER 정식 컬럼으로 승격하는 마이그레이션이 나중에 필요하다.

- **임베딩 벡터 차원(768)이 db-erd.md에 없다.** `NEWS_ARTICLES.embedding`과
  `BBL_BLOCKS.embedding`은 `vector` 타입으로만 적혀 있고 차원이 없는데, pgvector는
  차원이 없는 컬럼엔 HNSW 인덱스를 만들 수 없다(직접 테스트해서 확인:
  `ERROR: column does not have dimensions`). 사용자 확정으로 둘 다 `vector(768)`로
  잡았다(KF-DeBERTa 계열 통상 차원). 두 컬럼은 담당·모델이 달라(BBL 검색 M1 / 뉴스
  중복제거 M3) 마이그레이션에서 정의·인덱스를 따로 둬서 한쪽만 바꿀 수 있게 했다.
  **pgvector는 차원을 바꾸려면 컬럼 타입 변경 + 인덱스 재생성이 필요해서, 임베딩
  모델 확정이 늦어질수록 나중에 되돌릴 비용이 커진다.**

- **NEWS_ARTICLES에 원문 컬럼(title/body)이 있다.** `docs/db-erd.md` 4.2에는
  `title text`, `body text`가 있지만, 최상위(graduation-project) `CLAUDE.md`는
  "뉴스 원문은 저장하지 않는다. 메타정보·감성 점수·태그·임베딩만 저장한다"고
  못 박고 있다. 컬럼 자체는 정본을 따라 만들었지만(마이그레이션에서 삭제하지
  않음), 실제 수집기가 이 컬럼에 원문을 채워 넣으면 안 된다 — 이건 DB 스키마가
  아니라 수집기 구현 시점에 지켜야 하는 규칙이다.

- **STRATEGY_SPECS 상태 전이가 트리거와 어떻게 공존하는지 안 정해졌다.**
  004_triggers는 `status='approved'`가 된 행의 UPDATE를 무조건 막는다. 그런데
  status 컬럼 자체는 `draft → approved → running → closed`로 전이하게 되어 있다
  (CHECK 제약). approved 이후 running/closed로 넘어가는 절차가 이 트리거를 우회하는
  별도 권한 경로를 쓸지, 아니면 다른 방식으로 상태를 관리할지는 정해진 바가 없다.
  이번 단계(Validator/OrderBuilder 등 실구현 제외)에서는 손대지 않았다.
  지금 트리거는 status 컬럼까지 얼려서 approved → running → closed 전이 자체가
  불가능하다. 상세설계서 4.3 "Spec 불변" 원칙의 의도는 유니버스·비중 범위(수정하면
  새 spec_version을 발행해야 하는 대상)를 동결하는 것으로 보이고, status는 그
  대상이 아닐 가능성이 크다. 그렇다면 트리거를 status를 제외한 나머지 컬럼에만
  거는 안이 유력하다. 다만 이건 추정이고 팀 확정이 아니므로, 확정 전까지는
  004_triggers 코드를 바꾸지 않는다.

- **ETF_MASTER는 4/60행만 채워져 있다.** `069500`/`232080`/`133690`/`148070`
  네 종목만 문서에 있는 이름으로 채웠고, 나머지 56종목은 `scripts/build_etf_master.py`
  실행 결과로 채워야 한다(아직 실행 안 함). risk_tag/group_id는 이 스크립트도
  절대 자동으로 채우지 않는다 — 사람이 검토해서 채우는 컬럼이다. group_id가 비어
  있는 동안은 DB `etf_master` 테이블(FK NOT NULL)에 아예 적재하지 않는다; CSV
  단계에만 존재한다.

- **`scripts/build_etf_master.py`의 pykrx 호출부는 검증되지 않았다.** 이 세션에
  pykrx가 설치돼 있지 않아 `get_etf_ticker_list`/`get_etf_ticker_name`/
  `get_etf_ohlcv_by_date` 시그니처를 실제로 실행해 확인하지 못했다. 실행 전에
  pykrx 버전 문서를 확인할 것.

- **etf_master.group_id를 FK NOT NULL로 둔 결과, 수집기(Celery 태스크
  `ingest_market`, W4)가 신규 상장 ETF를 자동으로 못 넣는다.** 그룹 배정은
  사람이 검토하는 컬럼이라(위 "risk_tag/group_id 자동 채우기 금지" 항목), 그룹이
  없는 신규 종목은 지금 스키마로는 `etf_master`에 아예 적재가 안 된다. 사람이
  그룹을 배정하기 전까지 막히는 건 지금 단계(시드 4/60행, 수집기 미구현)에서는
  맞는 동작이지만, 실제 운용 단계에서 수집기가 매일 신규 상장을 반영하려면
  (a) 그룹 미배정 종목을 임시로 받아두는 스테이징 테이블, 또는 (b) `asset_groups`에
  "미분류" 그룹을 하나 두고 거기로 잠정 배정하는 방식 중 하나가 필요해진다.
  둘 다 스키마 변경이라 이번 단계에서는 손대지 않았다.

- **`rebalance`/`signal_rules`(계약 ① Spec JSON 스키마)는 기획서 3.2 확정본대로
  구조를 고정했지만, 그 안의 "값 집합"은 여전히 미확정이다.**
  `RebalanceRule.trigger`는 `TriggerSpec(type, freq, day)`로, `SignalRules`는
  `MarketAnalysisRule(indicators)` · `SentimentRule(target_sectors,
  lookback_hours)` · `MarketTemperatureRule(trend_index, trend_ma_window,
  volatility_index)`로 필드 이름과 타입을 고정했다(`app/contracts/spec.py`).
  다만 `trigger.type`이 어떤 문자열들을 가질 수 있는지, `indicators`/
  `target_sectors`/`trend_index`/`volatility_index`에 어떤 값이 허용되는지는
  BBL(Building Block Library) 확정 후 M1이 채운다 — 각 필드에
  `# TODO: 값 집합은 BBL 확정 후 M1` 주석을 달아 뒀다. `signal_rules`의 세
  블록(market_analysis/sentiment/market_temperature) 자체는 여전히 선택적
  (`None` 허용)이다 — 그래프 상위 CLAUDE.md가 감성·시장온도 관점을 "선택
  범위"로 명시해서다.

- **`constraint`(계약 ① Spec JSON 스키마)는 기획서 3.2 확정본대로 다섯 필드
  전부 float로 고정했다** (`max_weight_per_asset` · `min_weight_per_asset` ·
  `cash_min` · `max_loss_per_trade` · `max_drawdown`, `ConstraintSpec`).
  이름이 겹치는 HARDCAP_VERSIONS 컬럼과는 값의 성격이 다르다는 점을
  `app/contracts/spec.py`의 `ConstraintSpec`/모듈 docstring에 적어 뒀다 —
  여기 값은 사용자 요청값이고, 하드캡은 Validator 4계층이 이 값을 클램프하는
  시스템 상한이다. `min_interval_days`(rebalance 소유)와
  `leverage_allowed`(하드캡 전용, Spec 어디에도 없음)는 의도적으로 뺐다.

- **`app/routers/auth.py`에 회원가입 엔드포인트가 없다.** `docs/infra-spec.md`
  7단계는 "JWT 액세스/리프레시 + 3역할 RBAC 의존성은 실제로 동작하게 만든다"고만
  요구했고, 사용자 생성 경로는 명시하지 않았다. 로그인/리프레시만 실구현하고,
  users 행은 시드나 관리자 경로로 이미 존재한다고 가정했다 — 회원가입 플로우
  (이메일 인증, 초기 role 부여 규칙 등)는 비즈니스 로직 판단이 필요해 보여
  이번 단계에서 임의로 만들지 않았다.

- **vLLM 쪽은 GPU가 없어 이번 단계에서 직접 검증하지 못했다.** `app/llm/
  vllm_client.py`(OpenAI 호환 `/v1/chat/completions` + `guided_json`)는
  코드만 존재하고 실행한 적이 없다 — GPU가 있는 팀원이 `--profile gpu`로
  기동해 처음 검증해야 한다. Ollama(`qwen3:8b`) 쪽은
  `tests/test_llm_client.py`(`requires_ollama` 마커)로 실제 구조화 JSON
  응답을 받아 확인했다. `scripts/smoke_test.py`의 "--profile gpu/cpu 둘 다
  기동" 항목은 이 이유로 SKIP 처리했다(GitHub Actions에서는 LLM 백엔드
  판정 자체를 `SMOKE_REQUIRE_LLM_HEALTHY=false`로 뺀다 — 매 PR마다 5GB
  모델을 받을 수 없어서다).

- **frontend는 Next.js(App Router) + TypeScript 스캐폴드만 있다.** 확정
  스택(Tailwind + shadcn/ui + TanStack Query + ECharts)은 아직 안 붙였다
  — `/health` 페이지 하나뿐인 지금 단계에서는 필요가 없어서, 실제 대시보드
  작업이 시작될 때 붙이는 게 맞다고 판단했다. `next@15.5.25`로 고정해
  RCE 등 critical CVE는 피했지만, 번들된 `postcss`(moderate/high 등급,
  breaking major 업그레이드 없이는 못 없앰)는 남아 있다 — `npm audit`
  참고.

- **`tasks.ps1 migrate`가 원래 `docker compose exec api alembic ...`였는데
  실제로는 항상 실패했다.** api 컨테이너에는 `backend/`만 마운트돼 있어
  `alembic.ini`와 `db/migrations/`(둘 다 저장소 루트)를 컨테이너 안에서
  못 찾는다 — `scripts/smoke_test.py`를 작성하며 `docker compose exec api
  python scripts/smoke_test.py`도 같은 이유로 안 된다는 걸 먼저 발견하고
  고치면서 같이 발견했다. 지금은 둘 다 호스트(백엔드 venv)에서 돌고
  `DATABASE_URL`만 `localhost` 공개 포트로 오버라이드한다. `db/seeds/*.sql`을
  적재하는 절차도 그동안 스크립트가 없어서(직전 세션들에서 수동으로
  `psql`을 돌렸던 것으로 보인다) `tasks.ps1 seed`로 새로 추가했다 — 멱등이
  아니므로(01_hardcap_v0_1.sql의 users insert 한 줄만 `ON CONFLICT`) 새 DB에
  한 번만 돌리는 게 전제다.

- **501 스텁 라우터(profile/spec/backtest/portfolio/admin)의 응답 모델은
  `docs/db-erd.md` 확정 컬럼만으로 구성했다.** 실제 요청/응답 바디 설계(페이징,
  부분 업데이트, 에러 형식 등)는 각 라인이 실구현할 때 정할 문제라 지금은
  형태를 보여주는 최소 모델만 두었다. 이 모델들은 나중에 실구현 시 바뀔 수
  있다는 뜻이다 — Spec JSON 스키마(계약 ①)처럼 전원 합의가 필요한 고정
  계약이 아니다.
