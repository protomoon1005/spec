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

## M3 ML 의존성 설치 (판단 계층)

3관점 판단 계층(학습·추론·PLM)의 의존성은 `backend/pyproject.toml` 의 `ml` extra에 있다.
평소 개발·CI에는 필요 없고, 모델 학습·추론과 KF-DeBERTa 감성 분류를 돌릴 때만 필요하다.

```
pip install -e "backend[dev,ml]" --extra-index-url https://download.pytorch.org/whl/cpu
```

**`--extra-index-url` 을 빼지 마라.** linux x86_64에서 `pip install torch` 는 기본이
CUDA 휠(2.5GB+)이다. api·worker·beat가 `backend/Dockerfile` 하나를 공유하므로
(쪼개지 않기로 확정, 2026-09-12) CUDA 휠이 잡히면 세 서비스 이미지가 한꺼번에 부푼다.
`pyproject.toml` 로는 패키지별 인덱스를 지정할 수 없어서, 이 플래그가 Dockerfile과
이 문서 양쪽에 적혀 있어야 한다. 설치 후 확인:

```
python -c "import torch; print(torch.__version__, torch.version.cuda)"
# 2.x.x+cpu None   <- cuda가 None이어야 CPU 빌드다
```

**worker 이미지에 ML 의존성을 넣는 것은 T3(LightGBM)에서 실제로 필요해질 때.
그 시점에 buildx 캐시 도입을 함께 검토한다.** 지금 `backend/Dockerfile` 은 `.[dev]` 만
설치한다 — 이미지 안에서 ML을 쓰는 코드가 아직 없고(`train_model`·`daily_judge` 는 스텁),
CPU 휠이어도 torch가 769MB라 레이어 캐시 없이는 PR마다 그만큼을 다시 받게 된다.

ML이 필요한 테스트에는 `requires_ml` 마커를 단다. CI는
`pytest -m "not requires_ollama and not requires_ml" -q` 로 그 테스트들을 빼고 돈다
(현서가 만들어 둔 `requires_ollama` 선례와 같은 방식). 따라서 **CI는 `ml` extra를
설치하지 않으며, 신호 통합·Hedge 같은 순수 함수 회귀 테스트는 ML 의존성 없이 항상 돈다.**

## M3 피처셋 버전 (`feature_set_version`)

시장분석 모델이 쓰는 가격 기반 피처의 버전 문자열이다. `decision_records.feature_set_version`
과 `backtest_runs` 에 그대로 기록되고, 저장소 계층의 피처 조회가 이 값으로 찾는다.
**재현성의 축이라 규칙이 두 개 있다.**

1. **피처 목록이나 계산식이 바뀌면 버전을 올린다.** 같은 버전 문자열에 다른 정의가
   섞이면 과거 결정을 다시 만들어낼 수 없다.
2. 문자열만 보고 무엇이 들었는지 짐작할 수 있게 한다.

형식은 `v<major>.<minor>-<피처셋 슬러그>`. 버전별 피처 목록은
`backend/app/views/market/features.py` 의 `FEATURE_SETS` 상수가 정본이다
(테이블을 새로 만드는 것은 스키마 변경이라 하지 않았다).

### `v0.1-ta9` 지표 정의

`C` = 종가, `H`/`L` = 고가/저가, `V` = 거래량, `t` = `as_of` 이하의 마지막 행.
"필요 행수"보다 이력이 짧으면 `None`(결측)이다.

| 피처 | 정의 | 필요 행수 |
|---|---|---|
| `ret_1` | `C_t / C_{t-1} − 1` | 2 |
| `ret_5` | `C_t / C_{t-5} − 1` | 6 |
| `ret_20` | `C_t / C_{t-20} − 1` | 21 |
| `ma_gap_20` | `C_t / SMA20(C) − 1` (SMA = 단순평균) | 20 |
| `ma_gap_60` | `C_t / SMA60(C) − 1` | 60 |
| `rsi_14` | **Wilder** RSI(14) | 15 |
| `atr_14_pct` | **Wilder** ATR(14) `/ C_t` | 15 |
| `vol_20` | 최근 20개 일간 단순수익률의 **표본**표준편차(n−1) | 21 |
| `volume_ratio_20` | `V_t / SMA20(V)` | 20 |
| **워밍업** | `FEATURE_WARMUP_ROWS = 120` — `as_of` 직전 120행을 입력으로 준다 | 120 |

**워밍업 120행은 권고가 아니라 `v0.1-ta9` 정의의 일부다.** Wilder 계열(`rsi_14`·
`atr_14_pct`)은 기억이 무한해서 입력 이력 길이가 달라지면 같은 버전 문자열로 다른 값이
나온다. 그러면 재현성의 축이 거짓말을 한다. **적재 배치와 백테스트 러너는 `as_of` 마다
직전 120행을 입력으로 준다 — 이건 계약이다.** 값을 바꾸려면 `feature_set_version` 을
올려야 한다. (M4 전달 사항: 시점 주입 러너가 피처를 재계산하는 경로를 만들면 같은
규칙을 따라야 한다.)

**RSI(14)** — 구현체마다 가장 많이 갈리는 지표다. `delta_i = C_i − C_{i-1}`,
`gain = max(delta,0)`, `loss = max(−delta,0)`. seed 는 앞 14개의 **단순평균**,
이후 `avg = (avg×13 + 값)/14` (**Wilder smoothing**). `RS = avgGain/avgLoss`,
`RSI = 100 − 100/(1+RS)`. `avgLoss == 0` 이면 `avgGain > 0` 은 100, 완전 평탄은 50.
gain/loss 단순이동평균을 쓰는 구현과는 값이 다르다.

**ATR(14)** — `TR_i = max(H_i−L_i, |H_i−C_{i-1}|, |L_i−C_{i-1}|)`, seed 는 앞 14개의
단순평균, 이후 Wilder smoothing. SMA 기반 ATR 구현과는 값이 다르다.

> **재현성 주의 — Wilder 계열은 기억이 무한하다.** `rsi_14`·`atr_14_pct` 는 seed 이후
> 매 행을 지수적으로 섞으므로, **같은 `as_of` 라도 입력 이력을 어디서부터 줬는지에 따라
> 값이 달라진다.** 200행 랜덤워크 실측: 이력 201행 → RSI 61.7797, 141행 → 61.7807,
> 31행 → 65.0775. 윈도우 기반 피처(`ret_*`·`ma_gap_*`·`vol_20`·`volume_ratio_20`)는
> 이력 길이와 무관하게 같다. **재현하려면 `as_of` 뿐 아니라 입력 가격 구간의 시작일도
> 고정해야 한다.**

### `indicators` 는 적재 목록이 아니라 모델 입력 마스크다 (2026-09-12 확정)

피처 저장소에는 **`feature_set_version` 이 정의한 전체 지표를 항상 계산해 적재**한다.
`signal_rules.market_analysis.indicators` 는 **그중 모델 입력으로 무엇을 쓸지 고르는
마스크**로 해석한다.

적재가 Spec 과 무관해지므로 배치가 한 번이면 되고, 샘플 10건이 각기 다른 지표를
지목해도 피처 저장소가 갈라지지 않는다. 재현 조건도 둘로 깔끔히 나뉜다 —
`feature_set_version` 이 "무엇이 계산되어 있는가"를, Spec 의 `indicators` 가
"그중 무엇을 봤는가"를 책임진다. FN-401·403 의 "지표는 `signal_rules` 에서 파생된다"는
이 해석으로 성립한다.

`v0.1-ta9` 는 데모 계획의 "소규모 구간과 적은 피처로 먼저 돌아가게 만들고 정확도는
12월에 올린다"에 맞춘 출발점이다. 계산은 **pandas-ta 가 아니라 stdlib** 로 했다 —
pandas 가 base dependencies 에 없어서, pandas 로 계산하면 미래 참조 금지 테스트에
`requires_ml` 이 붙어 CI 에서 빠진다. 그 성질은 매 PR 에서 검증돼야 한다.
DataFrame 은 어댑터가 받으므로 호출부는 그대로 pandas 를 쓸 수 있다.

**결측은 `None`(JSON null)으로 남긴다. 0으로 채우지 않는다** — 윈도우보다 이력이
짧은 것과 지표값이 실제로 0인 것을 모델이 구분하지 못하게 되기 때문이다.
