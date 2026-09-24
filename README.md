# spec — 국내 ETF 모의운용

- 사용자가 말로 요청하면 그걸 **전략서(Spec)** 로 만들고, 그 전략서대로 ETF를 사고파는 모의투자 시스템
- 졸업작품이고 **실제 돈은 쓰지 않음**
- AI는 앞단에서만 사용
  - 1단계 **전략 만들기** — AI가 함. 사용자가 쓴 문장을 전략서로 변환
  - 2단계 **전략 검사** — AI 안 씀. 규칙에 어긋난 전략을 거름
  - 3단계 **사고팔 판단** — 학습한 모델이 함. 챗봇 아님
  - 4단계 **주문 내기** — AI 안 씀. 정해진 계산대로만

---

## 띄우는 법

```sh
cp .env.example .env
docker compose up -d --build
```

- 표 구조는 `db/init/` 의 SQL 로 **컨테이너 최초 기동 시 자동 생성.** 따로 실행할 것 없음

### 접속 주소

- **http://localhost:8000/docs** — API 문서(Swagger). 화면 없이 API를 직접 두드려 볼 수 있음
- http://localhost:8000/health — 서버와 붙어 있는 것들 상태
- http://localhost:3000 — 프론트 화면

### 데이터 넣기

표 구조만 생기고 안은 비어 있다. **두 줄이면 채워진다.**

```sh
docker compose exec api python /repo/scripts/apply_migrations.py   # 스키마 변경분 적용
docker compose exec api python /repo/scripts/seed_all.py           # 기준표 + 종목 원장
```

- `apply_migrations` — `db/migrate/` 의 변경분을 **아직 안 넣은 것만** 적용. 여러 번 돌려도 안전
- `seed_all` — `db/seeds/` 를 정해진 순서로 넣는다
  - `01_hardcap` 모든 전략에 공통으로 걸리는 상한선 (한 종목 30%, 현금 최소 5% 등)
  - `02_preset` 투자 성향 5단계 × ETF 위험등급 6단계 = 30칸
  - `03_asset_groups` 자산군·업종·국가 묶음과 각 묶음의 상한
  - `04_etf_master` ETF 71종목 — 레버리지·상장폐지 종목 포함
  - `05_bbl` 전략서에 들어갈 수 있는 값들의 목록 (블록 32개 + 태그 91개)

- **순서가 중요하다.** 종목이 자산군을 참조해서, 뒤섞으면 외래키 때문에 실패한다.
  그래서 `seed_all` 이 목록을 들고 있다
- **`01`~`03` 은 한 번만.** 두 번 넣으면 중복으로 쌓인다. `04`·`05` 는 여러 번 돌려도 된다
- 형식이 둘인 이유
  - `.sql` 은 **규칙표** — 팀이 회의에서 정한 값. 손으로 쓰고 잘 안 바뀜
  - `.csv` 는 **데이터** — 종목 원장. 스프레드시트로 분류 작업을 하고, 한글 분류값
    (`반도체`)을 묶음 이름(`SECTOR_SEMICONDUCTOR`)으로 옮기는 매핑이 필요해 전용 적재기를 씀
  - 형식을 통일하면 둘 중 하나가 나빠져서, **형식 대신 넣는 방법을 통일**했다

- **왜 파일로 두나** — 데이터베이스 내용은 git 으로 공유되지 않음. 팀원마다 자기 PC에
  postgres 가 따로 뜨고 각자 비어 있어서, 데이터 대신 **넣는 방법을 공유**하는 것

### 개발용 테스트 계정 (선택)

```sh
docker compose exec api python /repo/scripts/dev_seed.py
```

- `retail@test.local` · `pro@test.local` · `admin@test.local` — 비밀번호 전부 `test1234`
- `retail` 계정에는 위험중립형 성향까지 붙는다 — **설문을 건너뛰고 바로 전략서를 만들 수 있다**

### 표 구조가 바뀌었을 때 (기존 사용자)

- **`db/migrate/` 에 새 파일이 들어왔다면** `apply_migrations.py` 만 돌리면 된다.
  볼륨을 지울 필요 없고 데이터도 그대로 남는다
- **`db/init/02_schema.sql` 이 바뀌었다면** 기존 DB에는 반영되지 않는다
  - `db/init/` 은 볼륨이 빈 채로 처음 뜰 때만 실행됨
  - 이 경우에만 볼륨을 지워야 한다
- 내 DB가 낡았는지 확인

```sh
docker compose exec -T postgres psql -U spec -d spec -c "\dt" | tail -3
```

  - `Did not find any relations` → 표가 아예 없음
  - 표가 나오는데 최근에 추가된 표가 안 보임 → 낡은 것

#### 뉴스가 없는 사람 (대부분)

```sh
docker compose down
docker volume rm spec_pgdata
docker compose up -d
docker compose exec api python /repo/scripts/apply_migrations.py
docker compose exec api python /repo/scripts/seed_all.py
```

- 계정과 전략서는 같이 사라짐. 테스트용이라 다시 만들면 됨

#### 뉴스를 쌓고 있는 사람

- **지우기 전에 반드시 백업할 것.** 지나간 뉴스는 다시 받을 수 없음

```sh
# 1) 백업 — 표 구조는 빼고 데이터만
docker compose exec -T postgres pg_dump -U spec -d spec \
  --data-only -t news_articles -t news_sentiment > news_backup.sql

# 2) 위 "뉴스가 없는 사람" 절차를 그대로 수행

# 3) 복원
docker compose exec -T postgres psql -U spec -d spec < news_backup.sql
```

- 기사 표와 감성 표는 **한 파일에 같이** 뽑을 것. 감성이 기사 번호를 참조하므로 따로 뽑으면 어긋남
- **백업 파일을 저장소에 커밋하지 말 것** — 기사 원문이 들어 있음

### 자주 겪는 문제

- **컨테이너가 데이터베이스에 못 붙음**
  - 터미널에 `DATABASE_URL`·`REDIS_URL` 을 `localhost` 로 export 해 두면 그 값이 `.env` 보다 **우선**
  - 컨테이너 입장에서 `localhost` 는 자기 자신이라 못 붙음
  - `docker compose config` 로 실제로 뭐가 들어갔는지 확인 가능
- **코드를 고쳤는데 반영이 안 됨**
  - `api` 는 `--reload` 가 붙어 있지만 **`worker` 에는 없음.** 태스크를 고쳤으면
    `docker compose restart worker`
  - 새 폴더를 만들었을 때도 `api` 가 못 잡는 경우가 있음 → `docker compose restart api`

---

## 지금 되는 것 / 안 되는 것

- **되는 것**
  - 회원가입 · 로그인 · 토큰 갱신
  - 서버 상태 확인
  - 투자 성향 설문 → 성향 확정 → 조회
  - **자연어 → 전략서 만들기.** 정보가 모자라면 되묻고, 만든 전략서를 저장
  - 진행 상황 실시간 보기
- **안 되는 것**
  - 하드캡 적용 — Validator 담당인데 본체가 아직 없음. **하드캡을 넘는 값이 그대로 저장됨**
  - 백테스트 — 스크립트로만 됨. API로는 아직
  - 나머지 API — 501. 주소와 데이터 형식만 정해 둔 상태
- 501이 떠도 로그인 검사는 그보다 먼저 걸림. 토큰 없이 부르면 401

---

## API 테스트

- `http://localhost:8000/docs` 에서 눌러서 바로 호출
- 대부분의 주소는 토큰이 필요함. **우측 상단 `Authorize` 에 토큰을 넣으면 로그인이 유지됨**

### 1. 계정 만들기

`POST /auth/signup` 에 이메일과 비밀번호를 넣는다.

```json
{ "email": "m1@test.local", "password": "test1234" }
```

- 응답의 `access_token` 을 복사해 **`Authorize`** 에 붙여넣으면 끝. 따로 로그인할 필요 없음
- 비밀번호는 **최소 8자, UTF-8 72바이트까지**(한글 24자). 이메일 인증은 안 함
- 같은 이메일로 또 가입하면 409
- 시드 계정(`system@spec.internal`)으로는 **로그인할 수 없음** — 비밀번호가 해시가 아님
- 이미 만든 계정은 `POST /auth/login` 으로 토큰을 다시 받는다
- 만료가 짧으면 `.env` 의 `JWT_ACCESS_TTL_MINUTES` 를 올릴 것 (예: `43200` = 30일)
  - **값 뒤에 주석을 달지 말 것.** 주석은 줄 위에

### 2. 투자 성향 정하기

전략서를 만들려면 성향이 먼저 확정돼야 한다. **성향에 따라 담을 수 있는 종목과 비중이 달라진다.**

1. `GET /profile/survey/questions` — 문항 7개와 선택지 코드를 받는다
2. `POST /profile/survey` — 답을 **배열로** 보낸다. 7문항 전부 필요

```json
[
  {"question_code": "AGE",            "answer_code": "A1"},
  {"question_code": "HORIZON",        "answer_code": "A5"},
  {"question_code": "EXPERIENCE",     "answer_code": "A4"},
  {"question_code": "KNOWLEDGE",      "answer_code": "A4"},
  {"question_code": "INCOME_SOURCE",  "answer_code": "A5"},
  {"question_code": "ASSET_RATIO",    "answer_code": "A1"},
  {"question_code": "LOSS_TOLERANCE", "answer_code": "A5"}
]
```

3. `POST /profile` — **본문 없이** 실행. 답을 합산해 성향 1~5를 확정
4. `GET /profile/me` — 확인

- 같은 문항에 다시 답하면 마지막 답이 이김
- 다시 확정해도 덮어쓰지 않고 새로 쌓임. 조회는 최신 것을 봄
- 성향을 바꿔가며 보려면
  - `A5,A1,A1,A1,A2,A5,A1` → 1 안정투자형
  - `A3,A3,A3,A3,A3,A3,A3` → 3 위험중립형
  - `A1,A5,A5,A5,A5,A1,A5` → 5 공격투자형

### 3. 전략서 만들기

`POST /specs/compile` 에 요청 문장을 넣는다.

```json
{ "input_prompt": "안전하게 채권 위주로 굴리고 매달 정리해줘" }
```

작업 번호가 바로 나온다. 진행 상황은 터미널에서 본다.

```sh
curl -N -H "Authorization: Bearer <토큰>" http://localhost:8000/jobs/<작업번호>/stream
```

- 결과는 셋 중 하나
  - `completed` — 전략서를 만들어 저장함. `spec_id` 가 함께 옴
  - `need_answer` — 되묻는 중. `session_id` · `question` · `choices` 가 옴.
    **같은 주소를 다시 부르되 `{"session_id": "...", "answer": "채권으로"}`** 를 보냄
  - `failed` — 만들 수 없음. `reason` 에 이유가 담김
- 진행 상황 스트림은 **Swagger 화면에서 잘 안 보임** — 계속 흘려보내는 방식이라 위 curl 이 나음
- ⚠ **한 번에 몇 분씩 걸림.** 종목 후보마다 형식을 강제하느라 느리다.
  `.env` 의 `LLM_REQUEST_TIMEOUT_SECONDS` 가 300초로 맞춰져 있음

---

## 그 밖에

### 판단 모델 쪽 라이브러리 설치

- 평소 개발에는 필요 없음. 모델 학습·추론이나 뉴스 감성 분류를 돌릴 때만 설치

```sh
pip install -e "backend[dev,ml]" --extra-index-url https://download.pytorch.org/whl/cpu
```

- **뒤의 `--extra-index-url` 을 빼지 말 것.** 빼면 GPU용 무거운 버전(2.5GB+)이 깔리고,
  api·worker·beat 가 이미지 하나를 같이 써서 세 개가 한꺼번에 부풀음
- 확인: `python -c "import torch; print(torch.__version__, torch.version.cuda)"` →
  뒤가 `None` 이어야 CPU 빌드
- 이 라이브러리가 필요한 테스트에는 `requires_ml` 표시를 달 것

### 테스트

```sh
docker compose exec api pytest -m "not requires_ollama and not requires_ml and not requires_backfill and not requires_backtest" -q
docker compose exec api ruff check .
docker compose exec api python /repo/scripts/check_asof_guard.py
```

### 뉴스 수집은 지금 없다

- 수집 파이프라인이 2026-09-19 에 제거됐다 (`jun`, 커밋 `ee08952`, 11개 파일 1,178줄)
- **표(`news_articles`·`news_sentiment`)는 스키마에 그대로 남아 있다.** 넣는 코드만 없다
- 감성 관점을 살리려면 다시 만들어야 하고, **지나간 날은 받을 수 없다**
  (`docs/news-archive-feasibility.md` — 과거 아카이브 확보 불가 판정)
- 다시 시작할 계획이 있는지 M3 담당에게 확인할 것

### 문서

- `docs/known-issues.md` — **알려진 설계 구멍.** 표 구조를 바꾸는 대신 여기 적어 둠
- `docs/data-status.md` — 데이터가 어디까지 준비됐는지, 무엇이 아직 가짜인지
- `docs/m1_milestone.md` — M1(전략 컴파일) 단계별 진행과 결정 기록
- `docs/infra-spec.md` — 기준 문서. 확정 수치와 하지 말 것
- `docs/db-erd.md` — 표 구조 정본
