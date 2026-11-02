# 우리 방식 vs LLM 코드 직접 생성 (개발용 문장)

실행: 2026-11-02 11:07 · `python -m eval.run --baseline` · 문장 30개 · 각 3회 (캐시 없이 실제 LLM 호출)

> LLM 실행: 2026-11-02 10:45~11:06 (위 '실행' 시각은 보고서를 다시 쓴 시각). 보고서 작성 뒤 우리 쪽 미래 참조 검사가 기간 끝 비교 버그로 건너뛰어진 것을 고쳐, 저장된 전략서로 그 검사만 다시 계산했다 (LLM 은 다시 부르지 않음). 실패 이유 분류(출력 상한 도달·허용 안 된 import)도 이때 추가.

## 조건

- 모델·설정 (두 방식 같음): qwen3:8b, options {"temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 2048}, think false
- baseline 프롬프트: 데이터 형식·출력 형식·실행 환경(Python/pandas/numpy 판)만. 우리 사전·규칙·판정은 넣지 않음. 모호하면 `NEED_CLARIFICATION`, 구현 불가면 `CANNOT_IMPLEMENT` 로 답할 수 있게 함 (전문은 아래). 구조화 출력은 쓰지 않음 (코드를 자유롭게)
- baseline 실행: 별도 프로세스, import 는 pandas·numpy 만, 파일·네트워크 금지, 타임아웃 30초 (Docker 아님 — baseline/sandbox.py 의 한계 참고). 실행 실패는 오답
- baseline 의 기간 인자: 항상 기본 기간 2023-01-01 ~ 2025-12-31, 데이터는 그 끝날까지 (종목 20 + KS11·KS200·KQ11·VIX)
- 미래 참조: 기간 거래일 20·40·60·80% 지점 t 마다 t 이후 가격(모든 데이터)을 바꿔 다시 실행, 판단일 ≤ t 주문이 달라지면 미래 참조 (tests/test_lookahead.py 와 같은 섭동 함수). 우리 방식도 바로 진행된 전략서에 같은 검사를 함
- 응답 시간: 우리 = run() 전체 (빈칸 채우기 + 판정 + 검사 1~5단계, 5단계 시험 백테스트 포함). baseline = 코드 생성 + 샌드박스 실행 1회 (미래 참조 검사 제외)
- 판정 대응 (baseline): 코드를 냄 → ok(진행), NEED_CLARIFICATION → clarify, CANNOT_IMPLEMENT → reject, 그 밖의 응답 → error. 코드를 냈지만 실행에 실패하면 fail (오답)

## 요약

| | 우리 방식 | LLM 코드 직접 생성 |
|---|---|---|
| 판정 정확도 (ok/clarify/reject, 첫 회) | 25/30 (83%) | 5/30 (17%) |
| 되묻기·거절이 정답인데 그냥 진행 | 0/20 (0%) | 19/20 (95%) (코드를 만듦) |
| 진행(전략서 / 코드) 건수 | 5 | 29 |
| 실행 실패 (진행한 것 중) | — (검사 5단계 시험 실행) | 19/29 (66%) |
| 미래 참조 (검사한 것 중, 첫 회) | 0/5 | 0/10 |
| 미래 참조 (3회 중 한 번이라도) | — | 0건 |
| 3회 반복 결과 일치 | 29/30 (97%) | 23/30 (77%) |
| 3회 반복 코드 글자까지 같음 | — | 10/29 (34%) (코드를 낸 문장 중) |
| 응답 시간 (모든 회) | 평균 6.7s · 중앙값 6.6s · 최대 8.4s | 평균 6.5s · 중앙값 5.9s · 최대 19.4s |
| 주문 일치 정확도 | 정답 미작성 | 정답 미작성 |

주문 일치 정확도: 정답 전략서(expected_spec)가 채워진 문장 0개 — 지금은 모두 TODO 라 **정답 미작성**. 채우면 같은 명령으로 계산된다.

### baseline 실행 실패 이유 (첫 회)

| 이유 | 건수 |
|---|---|
| KeyError | 10 |
| 출력 상한 도달 (num_predict 2048, 같은 줄 반복 생성) | 4 |
| 허용 안 된 import (정적 검사) | 3 |
| NameError | 1 |
| AttributeError | 1 |

## 판정 혼동행렬

**우리 방식**

| 정답 \ 결과 | ok | clarify | reject | error |
|---|---|---|---|---|
| ok (10) | 5 | 2 | 3 | 0 |
| clarify (11) | 0 | 11 | 0 | 0 |
| reject (9) | 0 | 0 | 9 | 0 |

**LLM 코드 직접 생성**

| 정답 \ 결과 | ok | clarify | reject | error | fail |
|---|---|---|---|---|---|
| ok (10) | 5 | 0 | 0 | 0 | 5 |
| clarify (11) | 4 | 0 | 0 | 0 | 7 |
| reject (9) | 1 | 1 | 0 | 0 | 7 |

## 문장별

| id | 정답 | 우리 판정 | 우리 3회 | 우리 시간 | baseline 결과 | 주문 수 | 미래 참조 | baseline 3회 | baseline 시간 | 문장 |
|---|---|---|---|---|---|---|---|---|---|---|
| ok-01 | clarify | clarify | 일치 | 6.4s | 코드·**실행 실패** | — | — | 일치 | 6.0s | RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘 |
| ok-02 | clarify | clarify | 일치 | 6.3s | 코드·**실행 실패** | — | — | 일치 | 5.5s | RSI 30 아래면 사고 5일선 깨지면 팔아 |
| ok-03 | ok | clarify | 일치 | 7.0s | 코드·**실행 실패** | — | — | **불일치** code_fail/code_ok/code_ok | 6.1s | 삼성전자 골든크로스 나오면 사고 데드크로스 나오면 팔아 |
| ok-04 | ok | proceed | 일치 | 7.2s | 코드·**실행 실패** | — | — | **불일치** code_fail/code_fail/code_fail | 10.6s | SK하이닉스 20일선 위로 올라가면 사고 10% 떨어지면 손절해줘 |
| ok-05 | ok | reject UNKNOWN_INDICATOR | 일치 | 7.2s | 코드·실행 성공 | 368 | 없음 | **불일치** code_ok/code_ok/code_ok | 6.5s | KODEX 200 볼린저 하단 아래로 내려가면 사고 볼린저 상단 위로 올라가면 팔아 |
| ok-06 | ok | proceed | **불일치** proceed/reject/reject | 7.4s | 코드·실행 성공 | 1 | 없음 | 일치 | 5.0s | 현대차랑 기아 반반, 과매도면 사고 과매수면 팔아 |
| ok-07 | ok | clarify | 일치 | 6.8s | 코드·실행 성공 | 0 | 없음 | 일치 | 6.6s | 네이버 거래량 터지면 사고 5일 보유 후 팔아 |
| ok-08 | ok | proceed | 일치 | 7.9s | 코드·실행 성공 | 0 | 없음 | 일치 | 6.1s | 카카오 60일선 돌파하면 사고 15% 오르면 익절, 7% 떨어지면 손절 |
| ok-09 | ok | proceed | 일치 | 7.1s | 코드·**실행 실패** | — | — | 일치 | 4.7s | 셀트리온 RSI 25 이하일 때 사고 RSI 60 이상이면 팔아 |
| ok-10 | ok | reject UNKNOWN_INDICATOR | 일치 | 8.0s | 코드·실행 성공 | 0 | 없음 | **불일치** code_ok/code_fail/code_fail | 6.2s | KODEX 레버리지 종가가 직전 20일 최고가 위로 올라가면 사고 20일선 깨지면 팔아, 2023년부터 2025년까지 |
| ok-11 | ok | reject UNKNOWN_INDICATOR | 일치 | 7.5s | 코드·**실행 실패** | — | — | 일치 | 6.6s | KB금융 12일 지수이평이 26일 지수이평 위로 올라가면 사고 아래로 떨어지면 팔아, 초기 자금 3천만 원 |
| ok-12 | ok | proceed | 일치 | 7.3s | 코드·**실행 실패** | — | — | 일치 | 7.6s | 삼성전자에 70%만 넣고 RSI 30 아래면 사고 20거래일 지나면 팔아 |
| clarify-01 | clarify | clarify | 일치 | 6.6s | 코드·실행 성공 | 35 | 없음 | 일치 | 5.0s | 눌림목에서 사서 10% 오르면 팔아 |
| clarify-02 | clarify | clarify | 일치 | 6.5s | 코드·**실행 실패** | — | — | 일치 | 5.2s | RSI 낮을 때 사고 높을 때 팔아 |
| clarify-03 | reject | reject UNKNOWN_INDICATOR | 일치 | 6.3s | 코드·**실행 실패** | — | — | 일치 | 5.8s | 바닥에서 사서 고점에서 팔아 |
| clarify-04 | clarify | clarify | 일치 | 6.7s | 코드·**실행 실패** | — | — | 일치 | 6.4s | LG에너지솔루션 20일선 돌파하면 사고 20일선 깨지면 팔아 |
| clarify-05 | clarify | clarify | 일치 | 7.2s | 코드·**실행 실패** | — | — | **불일치** code_fail/code_fail/code_fail | 10.2s | 삼성전자 반반으로 RSI 30 아래면 사고 5일선 깨지면 팔아 |
| clarify-06 | clarify | clarify | 일치 | 6.9s | 코드·실행 성공 | 759 | 없음 | 일치 | 7.3s | 이평선 돌파하면 사고 이평선 깨지면 팔아 |
| clarify-07 | clarify | clarify | 일치 | 6.3s | 코드·**실행 실패** | — | — | 일치 | 6.2s | 거래량 좀 늘면 사고 5일선 깨지면 팔아 |
| clarify-08 | clarify | clarify | 일치 | 6.8s | 코드·**실행 실패** | — | — | **불일치** code_fail/code_fail/code_fail | 10.4s | 하이닉스 9일 RSI가 20 아래면 사고 5일선 깨지면 팔아 |
| clarify-09 | clarify | clarify | 일치 | 6.5s | 코드·실행 성공 | 0 | 없음 | 일치 | 6.6s | RSI 30 아래로 떨어지면 사고 적당한 때 팔아 |
| reject-01 | reject | reject FUTURE_REF | 일치 | 5.6s | 코드·**실행 실패** | — | — | 일치 | 3.9s | 내일 오를 종목 사줘 |
| reject-02 | reject | reject FUTURE_REF | 일치 | 6.4s | 코드·실행 성공 | 20 | 없음 | 일치 | 4.6s | 다음 주에 오를 것 같으면 사고 떨어질 것 같으면 팔아 |
| reject-03 | reject | reject UNKNOWN_INDICATOR | 일치 | 6.3s | 되묻기 | — | — | 일치 | 3.1s | 뉴스 좋으면 사고 나쁘면 팔아 |
| reject-04 | reject | reject UNKNOWN_INDICATOR | 일치 | 6.5s | 코드·**실행 실패** | — | — | 일치 | 18.4s | 세력 들어오면 따라 사고 빠지면 팔아 |
| reject-05 | reject | reject UNSUPPORTED_ACTION | 일치 | 5.4s | 코드·**실행 실패** | — | — | 일치 | 3.9s | 삼성전자 공매도해줘 |
| reject-06 | reject | reject UNKNOWN_INDICATOR | 일치 | 6.3s | 코드·**실행 실패** | — | — | 일치 | 4.4s | PER 10 아래면 사고 20 넘으면 팔아 |
| reject-07 | reject | reject INTRADAY | 일치 | 5.8s | 코드·**실행 실패** | — | — | 일치 | 4.9s | 5분봉 RSI 보고 단타 쳐줘 |
| reject-08 | clarify | clarify | 일치 | 5.8s | 코드·실행 성공 | 0 | 없음 | **불일치** code_ok/code_ok/code_ok | 5.7s | 삼성전자 RSI 30 아래면 사줘 |
| reject-09 | reject | reject UNSUPPORTED_ACTION | 일치 | 6.2s | 코드·**실행 실패** | — | — | 일치 | 4.1s | 신용 끌어서 하이닉스 3배로 사 |

## baseline 실행 실패·미래 참조 상세 (첫 회)

- ok-01 실행 실패 [KeyError] KeyError: Timestamp('2023-01-24 00:00:00')
- ok-02 실행 실패 [KeyError] KeyError: 1
- ok-03 실행 실패 [KeyError] KeyError: 1
- ok-04 실행 실패 [출력 상한 도달 (num_predict 2048, 같은 줄 반복 생성)] 정적 검사: 문법 오류: invalid syntax (1행)
- ok-09 실행 실패 [KeyError] KeyError: 1
- ok-11 실행 실패 [KeyError] KeyError: 1
- ok-12 실행 실패 [KeyError] KeyError: 0
- clarify-02 실행 실패 [KeyError] KeyError: 1
- clarify-03 실행 실패 [KeyError] KeyError: -1
- clarify-04 실행 실패 [KeyError] KeyError: 1
- clarify-05 실행 실패 [출력 상한 도달 (num_predict 2048, 같은 줄 반복 생성)] 정적 검사: 문법 오류: invalid syntax (1행)
- clarify-07 실행 실패 [KeyError] KeyError: 1
- clarify-08 실행 실패 [출력 상한 도달 (num_predict 2048, 같은 줄 반복 생성)] 정적 검사: 문법 오류: unterminated string literal (detected at line 58) (58행)
- reject-01 실행 실패 [허용 안 된 import (정적 검사)] 정적 검사: from datetime import … (pandas, numpy 만 허용)
- reject-03 되묻기: "뉴스 좋으면 사고 나쁘면 팔아"라는 전략에서 "좋은 뉴스"와 "나쁜 뉴스"의 정의를 구체적으로 설명해 주세요. 예를 들어, 어떤 기준으로 뉴스를 판단할지, 특정 종목에 대한 뉴스를 어떻게 수집하고 분석할지 등에 대해 설명해 주세요.
- reject-04 실행 실패 [출력 상한 도달 (num_predict 2048, 같은 줄 반복 생성)] 정적 검사: 문법 오류: invalid syntax (1행)
- reject-05 실행 실패 [NameError] NameError: name 'pd' is not defined
- reject-06 실행 실패 [허용 안 된 import (정적 검사)] 정적 검사: from datetime import … (pandas, numpy 만 허용)
- reject-07 실행 실패 [AttributeError] AttributeError: 'int' object has no attribute 'strftime'
- reject-09 실행 실패 [허용 안 된 import (정적 검사)] 정적 검사: from datetime import … (pandas, numpy 만 허용)

원본(생성 코드 포함): eval/out/baseline_run.jsonl (git 제외)

## baseline 프롬프트 전문

````text
너는 파이썬 개발자다. 사용자가 한국어로 말한 매매 전략을 백테스트용 파이썬 함수 하나로 구현하라.

## 함수
def strategy(prices: dict[str, pd.DataFrame], start: str, end: str) -> list[dict]:

## 입력
- prices: 키 → 일봉 DataFrame
  사고팔 수 있는 종목 (키는 6자리 종목코드):
  - "005930": 삼성전자 (주식)
  - "000660": SK하이닉스 (주식)
  - "207940": 삼성바이오로직스 (주식)
  - "005380": 현대차 (주식)
  - "000270": 기아 (주식)
  - "068270": 셀트리온 (주식)
  - "035420": NAVER (주식)
  - "105560": KB금융 (주식)
  - "005490": POSCO홀딩스 (주식)
  - "035720": 카카오 (주식)
  - "069500": KODEX 200 (ETF)
  - "102110": TIGER 200 (ETF)
  - "229200": KODEX 코스닥150 (ETF)
  - "122630": KODEX 레버리지 (ETF)
  - "114800": KODEX 인버스 (ETF)
  - "252670": KODEX 200선물인버스2X (ETF)
  - "233740": KODEX 코스닥150레버리지 (ETF)
  - "091160": KODEX 반도체 (ETF)
  - "091170": KODEX 은행 (ETF)
  - "153130": KODEX 단기채권 (ETF)
  지수 (참고용, 사고팔 수 없음):
  - "KS11": 코스피 지수
  - "KS200": 코스피200 지수
  - "KQ11": 코스닥 지수
  - "VIX": 미국 VIX 지수 (사고팔 수 없음). 인덱스가 미국 거래일 날짜다. 미국 장 마감은 한국 시간으로 다음 날 아침이다.
- 각 DataFrame: 인덱스는 거래일 DatetimeIndex(오름차순), 열은 "Open", "High", "Low", "Close", "Volume" (수정주가).
  end 까지의 데이터만 들어 있고, start 이전 과거 데이터도 들어 있다 (지표 계산용). 종목마다 첫 날짜가 다를 수 있다.
- start, end: 'YYYY-MM-DD' 문자열. 백테스트 기간이다. 문장에 기간이 따로 없으면 이 기간을 쓴다.

## 출력
주문 목록 (dict 의 list, 날짜 오름차순). 주문 하나는:
- "date": 'YYYY-MM-DD' — 주문을 판단한 거래일. 그날 종가까지의 데이터로 판단하고, 다음 거래일 시가에 체결된다.
  기간 첫 거래일 시가에 체결하려면 그 전 거래일 날짜로 적는다.
- "ticker": 사고팔 수 있는 종목의 종목코드
- "side": "buy" 또는 "sell"
- "weight": 체결 뒤 그 종목이 총자산에서 차지할 목표 비중 (0~1). 전부 팔면 0.
시작할 때는 전부 현금이다. 수수료·세금·체결 오차는 실행하는 쪽이 계산한다. 1주 단위로 체결되고 남는 돈은 현금으로 둔다.

## 답하는 법
- 실행 환경: Python 3.11, pandas 3.1.0, numpy 2.4.6.
- import 는 pandas, numpy 만 쓸 수 있다. 파일·네트워크는 쓸 수 없다.
- 함수 strategy(필요하면 보조 함수 포함)를 ```python 코드 블록 하나로만 답하라. 설명은 쓰지 마라.
- 문장의 뜻이 하나로 정해지지 않아 사용자에게 물어봐야 하면, 코드 대신 `NEED_CLARIFICATION: <질문>` 한 줄만 답하라.
- 위 데이터로 구현할 수 없는 전략이면, 코드 대신 `CANNOT_IMPLEMENT: <이유>` 한 줄만 답하라.
````
