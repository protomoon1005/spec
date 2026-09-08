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
