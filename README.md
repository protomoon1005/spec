# 한국어 투자 전략 명세화 · 백테스트 서비스

- 자동매매를 원하지만 코딩을 못 하는 사용자가 한국어로 전략을 말하면, 전략 요소 사전에 있는 요소만 쓰는 전략서(JSON)로 바꾸고 백테스트 결과를 보여 준다
- 졸업작품이고 실제 돈은 쓰지 않는다
- AI 는 명세화에만 쓴다
  - 전략서 만들기: AI 가 한다. 사용자가 쓴 문장을 전략서로 바꾼다
  - 전략서 검사: AI 안 쓴다. 형식 · 종목 · 논리가 맞는지 본다
  - 백테스트: AI 안 쓴다. 전략서대로만 계산한다
- 지금은 주제 변경 기반 정리 중이다. 계획은 `docs/pivot-plan.md`

---

## 띄우는 법

```sh
cp .env.example .env
docker compose up -d --build
```

- 표 구조는 `db/init/` 의 SQL 로 컨테이너 최초 기동 시 자동 생성된다
- LLM 은 호스트에 떠 있는 Ollama(qwen3:8b)를 쓴다

### 접속 주소

- http://localhost:8000/docs — API 문서(Swagger)
- http://localhost:8000/health — 서버와 붙어 있는 것들 상태
- http://localhost:3000 — 프론트 화면

### 데이터 넣기

```sh
docker compose exec api python /repo/scripts/apply_migrations.py   # 스키마 변경분 적용
docker compose exec api python /repo/scripts/seed_all.py           # 기준표 + 종목 원장 + 전략 요소 사전
docker compose exec -w /repo api python scripts/ingest_prices.py   # ETF 가격(data/prices.csv)
```

- `seed_all` 의 `01` 부터 `03` 은 한 번만 넣는다. 두 번 넣으면 중복으로 쌓인다
- 기반 정리 2 · 3단계에서 위 세 줄이 빌드 명령 하나로 바뀌고, 가격은 국내 상장 개별주와 ETF 의 3년치 스냅샷으로 바뀐다

### 표 구조가 바뀌었을 때

- `db/migrate/` 에 새 파일이 들어왔다면 `apply_migrations.py` 만 돌리면 된다
- `db/init/` 이 바뀌었다면 볼륨을 지우고 다시 띄운다. 계정과 전략서는 같이 사라진다(시험용이라 다시 만들면 된다)

```sh
docker compose down
docker volume rm spec_pgdata
docker compose up -d
```

- 그 뒤 위 "데이터 넣기" 를 다시 한다

### 자주 겪는 문제

- 컨테이너가 데이터베이스에 못 붙음
  - 터미널에 `DATABASE_URL` · `REDIS_URL` 을 `localhost` 로 export 해 두면 그 값이 `.env` 보다 우선한다
  - 컨테이너 입장에서 `localhost` 는 자기 자신이라 못 붙는다. `docker compose config` 로 실제 값을 확인한다
- 코드를 고쳤는데 반영이 안 됨
  - `api` 는 `--reload` 가 붙어 있지만 `worker` 에는 없다. 태스크를 고쳤으면 `docker compose restart worker`

---

## 지금 되는 것

- 회원가입 · 로그인 · 토큰 갱신
- 서버 상태 확인
- 한국어 문장으로 전략서 만들기. 정보가 모자라면 추가 질문을 하고, 만든 전략서를 저장한다
- 진행 상황 실시간 보기
- 전략서 검증(스키마 · 참조 · 논리)과 비중 범위 실현가능성 보정
- 백테스트 — `POST /backtest/runs` 로 접수하고 `GET /backtest/runs/{run_id}` 로 결과를 본다. 리밸런싱은 전략 요소 사전의 블록 5개(매달 첫 거래일 · 매달 15일 · 분기 · 매주 · 비중 이탈 5%p)만 돈다

---

## API 테스트

- `http://localhost:8000/docs` 에서 눌러서 바로 호출한다
- 대부분의 주소는 토큰이 필요하다. 우측 상단 `Authorize` 에 토큰을 넣으면 로그인이 유지된다

### 1. 계정 만들기

`POST /auth/signup` 에 username 하나만 넣는다. 형식 제한 없음, 비밀번호 없음.

```json
{ "username": "m1" }
```

- 응답의 `access_token` 을 `Authorize` 에 붙여넣는다
- 같은 username 으로 또 가입하면 409. 이미 만든 계정은 `POST /auth/login` 에 같은 본문으로 토큰을 다시 받는다
- 만료가 짧으면 `.env` 의 `JWT_ACCESS_TTL_MINUTES` 를 올린다(예: `43200` 은 30일). 값 뒤에 주석을 달지 않는다

### 2. 전략서 만들기

`POST /specs/compile` 에 요청 문장을 넣는다.

```json
{ "input_prompt": "안전하게 채권 위주로 굴리고 매달 정리해줘" }
```

작업 번호가 바로 나온다. 진행 상황은 터미널에서 본다.

```sh
curl -N -H "Authorization: Bearer <토큰>" http://localhost:8000/jobs/<작업번호>/stream
```

- 결과는 셋 중 하나
  - `completed` — 전략서를 만들어 저장했다. `spec_id` 가 함께 온다
  - `need_answer` — 추가 질문 중. `session_id` · `question` · `choices` 가 온다. 같은 주소에 `{"session_id": "...", "answer": "채권으로"}` 를 보낸다
  - `failed` — 만들 수 없다. `reason` 에 이유가 담긴다
- 한 번에 몇 분씩 걸린다. `.env` 의 `LLM_REQUEST_TIMEOUT_SECONDS` 가 300초로 맞춰져 있다

---

## 테스트

```sh
docker compose exec api pytest -m "not requires_ollama and not requires_backfill and not requires_backtest" -q
docker compose exec -w /repo/backend api pytest -m requires_backtest -q
docker compose exec api ruff check .
docker compose exec api python /repo/scripts/check_asof_guard.py
docker compose exec frontend npx tsc --noEmit
```

## 문서

- `docs/pivot-plan.md` — 기준 문서. 주제 변경 기반 정리 계획과 결정 사항
- `docs/known-issues.md` — 알려진 설계 구멍
- `docs/db-erd.md` — 표 구조 정본
- `docs/archive/` — 옛 주제(국내 ETF 모의운용) 문서. 기록으로만 남긴다
