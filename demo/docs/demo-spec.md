# 한국어 투자 전략 명세화 · 백테스트 — 데모 구축 프롬프트 (Claude Code용)

> 작성 2026-11-02 · 새 주제 데모를 **별도 저장소**에서 빠르게 만들기 위한 프롬프트
> 흐름: **자연어 입력 → 명세화(판정·추가 질문) → 검증 → 사용자 확인 → 백테스트**
> 메인 저장소(protomoon1005/spec)는 건드리지 않는다. 데모로 개념을 검증한 뒤 필요한 부분만 옮긴다.

---

## 사용법

1. 빈 폴더를 만들고 `git init` 한다. (예: `C:\Users\MHS\graduation-project\nl-strategy-demo`)
2. 이 문서를 `docs/demo-spec.md` 로 넣는다.
3. 아래 **§A CLAUDE.md** 를 루트에 `CLAUDE.md` 로 저장한다.
4. `claude` 실행 후 **§C 단계별 프롬프트**를 1단계부터 하나씩 붙여넣는다.
   단계마다 **작업 → 검증 → 커밋(사람이 확인) → `/clear`**.

호스트에 Ollama `qwen3:8b` 가 떠 있어야 한다 (`ollama list` 로 확인).

---

## §A. CLAUDE.md (루트에 저장)

```markdown
# 한국어 투자 전략 명세화 · 백테스트 — 데모

## 이 저장소
한국어 매매 전략 문장을 정해진 블록만 쓰는 JSON 명세로 바꾸고,
규칙 기반 검증 → 사용자 확인 → 결정론적 백테스트까지 보여주는 데모.
기준 문서는 docs/demo-spec.md 다. 작업 전에 반드시 읽는다.

## 절대 규칙
- LLM은 "명세화" 단계에서만 쓴다. 검증·재서술·백테스트에는 LLM을 쓰지 않는다.
- 명세는 docs/demo-spec.md §B의 스키마와 블록 사전에 있는 값만 쓴다.
  사전에 없는 지표/연산자를 임의로 추가하지 않는다. 필요하면 멈추고 제안한다.
- 백테스트는 T일 종가까지의 데이터로 신호를 계산하고 T+1일 시가에 체결한다.
  이 규칙을 깨는 코드는 쓰지 않는다. 미래 참조 테스트가 항상 통과해야 한다.
- 같은 명세 + 같은 데이터 스냅샷 = 같은 거래 내역. random 금지.
- 데모 수준만 만든다. 로그인, DB 서버, Docker, 실거래 연결은 만들지 않는다.
- 설계 판단이 필요하면 임의로 정하지 말고 선택지를 제시하고 멈춘다.

## 환경
- 주 개발기는 Windows PowerShell, 보조는 macOS. 두 곳에서 다 돌아야 한다.
  bash 전용 문법(&&, heredoc, make)을 검증 절차에 쓰지 않는다.
- Python 3.11+, 가상환경 .venv, 의존성은 requirements.txt.
- LLM: 호스트 Ollama qwen3:8b (http://localhost:11434), JSON Schema 구조화 출력, temperature 0.
- UI: Streamlit 한 개 앱 (app.py).

## 명령
- 실행: streamlit run app.py
- 테스트: pytest -q
- 평가: python -m eval.run
```

---

## §B. 데모 사양 (docs/demo-spec.md 본문)

### B1. 범위

| 넣는 것 | 빼는 것 |
|---|---|
| 국내 주식·ETF 일봉, 1~3종목 | 분봉, 해외, 선물·옵션 |
| 지표 조건 기반 매수/매도, 손절·익절·보유기간 | 머신러닝 신호, 뉴스 감성 |
| 종목별 비중 고정 분할("반반") | 포트폴리오 최적화, 성향 제약 |
| 수수료·세금 반영 | 슬리피지 모델링(고정 bps만) |
| Streamlit 화면 1개 | 로그인, 서버 배포 |

### B2. 데모 명세 스키마 (DemoSpec v0.1)

```jsonc
{
  "schema_version": "demo-0.1",
  "verdict": "ok",                 // ok | clarify | reject   (LLM이 채움)
  "questions": [],                 // verdict=clarify 일 때 사용자에게 물을 질문 (최대 3개)
  "reject_reason": null,           // verdict=reject 일 때 사유 코드 + 한국어 설명
  "universe": [
    { "ticker": "005930", "name": "삼성전자", "weight": 0.5 },
    { "ticker": "069500", "name": "KODEX 200", "weight": 0.5 }
  ],
  "entry": {                       // 매수 조건
    "logic": "all",                // all | any
    "conditions": [
      { "left": "RSI_14", "op": "cross_below", "right": 30 }
    ]
  },
  "exit": {                        // 매도 조건 (조건 or 손절/익절/보유기간 중 먼저 걸리는 것)
    "logic": "any",
    "conditions": [
      { "left": "CLOSE", "op": "cross_below", "right": "SMA_5" }
    ],
    "stop_loss": null,             // 0~1 소수. 예: 0.05 = 매수가 대비 -5%
    "take_profit": null,
    "max_hold_days": null
  },
  "backtest": { "start": "2021-01-01", "end": "2025-12-31", "initial_cash": 10000000 },
  "costs": { "commission_bps": 1.5, "tax_profile": "kr_default" },
  "provenance": {                  // 어느 값이 문장의 어디서 왔는지 (LLM이 채움)
    "input_text": "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘",
    "mappings": [
      { "phrase": "RSI 30 아래로 떨어지면", "field": "entry.conditions[0]" },
      { "phrase": "5일선 깨지면", "field": "exit.conditions[0]" }
    ],
    "defaults_applied": ["universe", "backtest", "costs"]
  }
}
```

### B3. 블록 사전 (이 값만 허용)

**지표** — 전부 T일 종가까지로 계산
| id | 의미 |
|---|---|
| `CLOSE`, `OPEN`, `HIGH`, `LOW`, `VOLUME` | 당일 값 |
| `SMA_{5,10,20,60,120}` | 단순 이동평균 (5일선 등) |
| `EMA_{12,26}` | 지수 이동평균 |
| `RSI_14` | Wilder 방식 RSI |
| `BB_UPPER_20`, `BB_LOWER_20` | 볼린저 밴드(20일, 2σ) |
| `VOL_SMA_20` | 거래량 20일 평균 |
| `HIGH_N_20`, `LOW_N_20` | 직전 20일 최고/최저 (당일 제외) |

**연산자**: `lt`, `gt`, `cross_above`, `cross_below`
**right**: 지표 id 또는 숫자 (숫자는 `VOLUME`과 비교 시 배수 표현을 위해 `{"ref": "VOL_SMA_20", "mul": 2}` 허용)

**한국어 표현 사전** (`data/expressions.json`) — 시드 예시

| 표현 | 매핑 | 비고 |
|---|---|---|
| 5일선, 20일선, 60일선 | `SMA_5`, `SMA_20`, `SMA_60` | |
| ~선 깨지면 / 이탈하면 | `CLOSE cross_below SMA_n` | 종가 기준으로 고정 (재서술에 명시) |
| ~선 돌파 / 올라타면 | `CLOSE cross_above SMA_n` | |
| 골든크로스 | `SMA_5 cross_above SMA_20` | 기간 언급 없으면 기본값 + 재서술에 명시 |
| 과매도 / 과매수 | `RSI_14 lt 30` / `gt 70` | |
| 반반 | 두 종목 weight 0.5씩 | 종목이 1개면 clarify |
| 거래량 터지면 | `VOLUME gt {VOL_SMA_20 × 2}` | |
| 눌림목 | **clarify** | 정의가 사람마다 다름 → 질문 |
| 바닥, 고점, 세력 | **clarify 또는 reject** | 정량 정의 없음 |

**종목 별칭** (`data/tickers.json`) — 시드 20개 (대형주 10 + 대표 ETF 10). 없는 종목은 clarify.

### B4. 판정 규칙

| verdict | 조건 | 예 |
|---|---|---|
| `ok` | 매수 조건과 매도 조건(또는 손절/보유기간)이 모두 사전 값으로 표현됨 | "RSI 30 아래면 사고 5일선 깨지면 팔아" |
| `clarify` | 기간·기준·종목이 모호하거나, 사전에 있지만 해석이 여러 개 | "RSI 낮을 때 사", "눌림목에서 사" |
| `reject` | 미래 데이터 참조, 사전에 없는 개념, 실행 불가 | "내일 오를 종목 사", "뉴스 좋으면 사", "공매도해줘" |

reject 사유 코드: `FUTURE_REF` / `UNSUPPORTED_CONCEPT` / `UNSUPPORTED_ACTION` / `NO_EXIT_RULE`

### B5. 검증기 (AI 미사용, 4단계)

1. **형식** — Pydantic으로 스키마 통과
2. **참조** — 모든 지표·연산자·티커가 사전에 존재
3. **논리** — 매수 조건 ≥1, 매도 수단 ≥1, weight 합 ≤ 1, start < end, 매수·매도 조건이 항상 동시에 참인 모순 없음(간단 규칙만)
4. **범위** — stop_loss/take_profit 0~0.5, max_hold_days 1~250, 종목 ≤ 3

실패 시 어느 단계·어느 필드인지 한국어 메시지로 반환. 실패하면 백테스트 버튼 비활성.

### B6. 재서술 (AI 미사용)

명세 → 한국어 문장을 **템플릿으로** 만든다. LLM으로 만들면 "명세가 실제로 뭘 하는지"가 아니라 "LLM이 생각한 것"을 보여주게 되므로 금지.

> 예: "**삼성전자**를 대상으로, **RSI(14일)가 30 아래로 내려가는 날** 다음 거래일 시가에 전액 매수합니다. **종가가 5일 이동평균 아래로 내려가는 날** 다음 거래일 시가에 전량 매도합니다. 기간 2021-01-01 ~ 2025-12-31, 초기 자금 1,000만 원, 수수료 0.015%와 매도 시 세금을 반영합니다."

기본값이 적용된 부분은 따로 표시("기간·자금은 기본값입니다").

### B7. 백테스트 엔진 (AI 미사용)

- 데이터: FinanceDataReader 일봉 → `data/cache/{ticker}.parquet` 캐시. 한 번 받으면 캐시만 사용(스냅샷 날짜 기록).
- 신호: T일 종가까지로 계산 → **T+1 시가 체결**. 종목별로 배정 자금(weight × 초기자금) 안에서 정수 주식 단위로 매매, 잔액은 현금.
- 비용: 수수료 매수·매도 각 `commission_bps`. 세금은 `config/costs.yaml`
  - 주식: 매도 시 0.20% (2026 코스피 기준 — **팀 확인 필요**)
  - 국내 ETF: 거래세 0 (**팀 확인 필요**)
- 출력: 일별 자산 곡선, 총수익률, CAGR, MDD, 거래 횟수, 승률, 거래 내역 표, 벤치마크(같은 종목 단순 보유) 곡선.
- 결과에 `spec_hash`, `data_snapshot`, `result_hash` 를 붙인다 → 같은 명세 재실행 시 해시 일치를 화면에 표시.

### B8. 화면 (Streamlit, 단일 페이지)

상단에 4단계 진행 표시: **① 입력 → ② 명세화 → ③ 검증·확인 → ④ 결과**

| 영역 | 내용 |
|---|---|
| ① 입력 | 큰 텍스트 입력창 + 예시 문장 버튼 6개 (ok 2 / clarify 2 / reject 2) |
| ② 명세화 | 판정 배지(초록 ok / 노랑 추가 질문 / 빨강 실행 불가). clarify면 질문을 채팅 형식으로 보여주고 답변 입력 → 다시 명세화. reject면 사유 코드와 설명 |
| ③ 검증·확인 | 왼쪽: 검증 4단계 체크리스트(통과 ✓ / 실패 ✗ + 메시지). 오른쪽: "AI가 이해한 전략" 재서술 카드, 문장 구절 ↔ 명세 필드 매핑 하이라이트. 아래 접힌 영역에 명세 JSON. "이대로 백테스트" 버튼 |
| ④ 결과 | 지표 카드 4개(총수익률, CAGR, MDD, 거래 횟수), 자산 곡선(전략 vs 단순 보유, 매수·매도 지점 표시), 거래 내역 표, 재현성 배지("같은 명세 재실행 결과 일치 ✓ result_hash …") |
| 사이드바 | 모드 전환: **본 서비스 / Baseline(LLM 직접 코드 생성)** — 같은 문장 결과 나란히 비교 (5단계에서 추가) |

디자인: 한 가지 강조색, 판정 배지 색만 의미 있게 사용. 한국어 폰트(Pretendard 또는 시스템 기본). 차트는 Plotly.

### B9. 디렉터리

```
app.py                     # Streamlit
core/
  schema.py                # DemoSpec Pydantic + JSON Schema export
  dictionary.py            # 지표/연산자/표현/티커 사전 로더
  specify.py               # LLM 명세화 (Ollama)
  validate.py              # 4단계 검증기
  narrate.py               # 템플릿 재서술
  indicators.py            # 지표 계산 (as_of 안전)
  backtest.py              # 엔진
  data.py                  # FDR + 캐시
baseline/
  codegen.py               # LLM 직접 코드 생성 baseline
data/  expressions.json  tickers.json  cache/
config/ costs.yaml
eval/  sentences.jsonl  run.py
tests/
```

---

## §C. 단계별 프롬프트

### 1단계 · 데이터와 백테스트 엔진 (LLM 없음)

```
docs/demo-spec.md 를 읽고 B7(백테스트 엔진)과 B3의 지표 계산만 만들어줘.
LLM, UI, 명세화는 아직 손대지 마.

- core/data.py: FinanceDataReader 일봉 + parquet 캐시
- core/indicators.py: B3 지표 전부. HIGH_N_20/LOW_N_20 은 당일 제외
- core/backtest.py: DemoSpec(일단 core/schema.py 에 Pydantic 모델만 먼저)을 받아 결과 반환
- config/costs.yaml

테스트를 먼저 쓰고 실패하는 걸 보여준 다음 구현해라:
1. 미래 참조: T+1 이후 가격을 임의로 바꿔도 T일까지의 신호가 그대로임을 단언
2. 체결 시점: 신호 다음 날 시가로 체결됨을 단언
3. 재현성: 같은 명세 2회 실행 시 거래 내역과 result_hash 일치
4. 비용: 수수료·세금 계산을 손으로 계산한 작은 예제와 대조
5. RSI_14 가 Wilder 방식으로 알려진 값과 일치 (작은 고정 시계열)

마지막에 "RSI 30 아래면 사고 5일선 깨지면 팔아"를 삼성전자로 직접 명세 JSON 써서 돌리고
총수익률, MDD, 거래 횟수를 보고해.
```

검증: `pytest -q` 전부 통과 → 커밋 → `/clear`

### 2단계 · 스키마, 사전, 검증기, 재서술 (LLM 없음)

```
docs/demo-spec.md B2~B6 를 만들어줘. 아직 LLM 연결은 하지 마.

- core/schema.py: DemoSpec 완성 + JSON Schema 파일 export (schema/demospec.json).
  지표/연산자/티커는 enum 으로 스키마에 박아라 — 구조화 출력에서 사전 밖 값이 못 나오게.
- data/expressions.json, data/tickers.json: B3 시드
- core/validate.py: 4단계 검증기, 실패 시 단계·필드·한국어 메시지
- core/narrate.py: 템플릿 재서술. LLM 금지. 기본값 적용 부분 표시

테스트: 검증기 단계별로 통과 1개·실패 1개씩, 재서술 스냅샷 테스트 3개.
```

### 3단계 · LLM 명세화

```
docs/demo-spec.md B3·B4 기준으로 core/specify.py 를 만들어줘.

- Ollama qwen3:8b, format 에 schema/demospec.json 을 넘겨 구조화 출력, temperature 0, seed 고정
- 시스템 프롬프트에 블록 사전과 expressions.json 을 넣고, B4 판정 규칙과 예시 6개(ok/clarify/reject 각 2)를 포함
- clarify 흐름: 이전 입력 + 질문 + 사용자 답을 합쳐 다시 명세화하는 함수 refine(spec, answers)
- LLM 출력은 반드시 validate.py 를 통과해야 다음 단계로. 형식 실패 시 1회만 재시도
- 입력 문장 해시로 결과 캐시 (데모 시연 안정성)
- 응답 시간 측정해서 결과에 포함

eval/sentences.jsonl 에 문장 30개를 만들어라: ok 12 / clarify 9 / reject 9.
각 줄에 정답 verdict 와 (ok면) 정답 명세를 넣되, 정답 명세는 네가 추측하지 말고
TODO 로 남겨서 사람이 채우게 해라. verdict 정답만 채워라.

마지막에 30문장을 돌려서 verdict 혼동행렬과 평균 응답 시간을 보고해.
```

### 4단계 · Streamlit UI

```
docs/demo-spec.md B8 대로 app.py 를 만들어줘. frontend-design 관점으로 깔끔하게.

- 상단 4단계 진행 표시, st.session_state 로 단계 상태 관리
- 예시 문장 버튼 6개 (eval/sentences.jsonl 에서 ok/clarify/reject 각 2개)
- ② clarify 는 st.chat_message 로 질문/답변
- ③ 왼쪽 검증 체크리스트, 오른쪽 재서술 카드 + 구절↔필드 매핑 하이라이트, JSON 은 expander
- ④ 지표 카드 4개, Plotly 자산 곡선(전략 vs 단순 보유, 매수 ▲ 매도 ▼ 마커), 거래 내역 표
- 재현성 배지: "다시 실행" 버튼으로 같은 명세 재실행 → result_hash 비교 결과 표시
- LLM 호출 중 스피너와 경과 시간 표시
- Ollama 가 꺼져 있으면 첫 화면에 안내 배너 (앱이 죽지 않게)

완성 후 예시 6개를 순서대로 눌렀을 때 각 화면이 어떻게 나오는지 스크린샷 대신 텍스트로 보고해.
```

검증: `streamlit run app.py` 로 직접 6개 예시를 눌러본다.

### 5단계 · Baseline 비교 + 평가 (선택)

```
baseline/codegen.py 와 eval/run.py 를 만들어줘.

- baseline: 같은 문장을 qwen3:8b 에 주고 "pandas DataFrame df 를 받아 signals 를 반환하는
  파이썬 함수"를 직접 생성하게 한다. 생성 코드는 제한된 네임스페이스에서 실행하고
  (import 금지, 타임아웃), 결과를 같은 백테스트 엔진에 넣는다.
- 미래 참조 탐지: 생성 코드에 shift(-k), iloc[i+1] 같은 패턴이 있거나,
  1단계의 미래 참조 테스트 방식(미래 가격 변경 시 과거 신호가 바뀌는지)으로 검사
- eval/run.py: 30문장 × (본 서비스, baseline) × 3회 반복
  지표: verdict 정확도, clarify 재현율, reject 정확도, 3회 결과 일치율,
       (정답 명세가 채워진 문장만) 거래 내역 일치 정확도, baseline 미래 참조 건수
  결과를 eval/report.md 표로 저장
- UI 사이드바에 Baseline 모드 추가: 같은 문장에 대해 생성된 코드와 결과를 나란히 표시

숫자를 좋게 보이게 만들려고 문장이나 프롬프트를 고치지 마라. 결과 그대로 보고해.
```

---

## §D. 데모 시연 시나리오 (3분)

1. **ok** — "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘" → 재서술 확인 → 결과 → "다시 실행"으로 해시 일치
2. **clarify** — "눌림목에서 사서 10% 오르면 팔아" → 질문 "눌림목을 어떻게 볼까요? (예: 20일선 근처까지 내려왔을 때)" → 답변 → 명세 완성
3. **reject** — "내일 오를 종목 사줘" → FUTURE_REF 사유 표시
4. **비교** — 같은 문장 Baseline 모드 → 생성 코드가 실행마다 다르거나 미래 참조가 섞이는 사례

## §E. 팀 확인 필요

- 세율 기본값(주식 0.20%, ETF 0) — 비용 설정 근거
- 데모 명세(demo-0.1)와 메인 저장소 명세 v0.1의 관계 — 데모 검증 후 v0.1에 신호 블록을 추가할지, 데모 형식을 흡수할지
- 표현 사전 시드 목록과 정의(특히 "깨지면" = 종가 기준)
