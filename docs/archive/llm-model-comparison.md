# LLM 백엔드 교체 비교 — 로컬 qwen3:8b 대비

조사일 2026-10-04. 가격과 점수는 공개 자료 기준이며 수시로 바뀐다. 실제 도입 전에 각 공급자 가격표를 다시 확인한다.

## 1. 현재 상태

- 백엔드: 호스트 Ollama, qwen3:8b, `think: False`, `format` 에 JSON Schema 전달
- LLM 을 쓰는 곳은 M1 전략 컴파일 한 군데뿐 (`app/m1/`)
  - 의도 추출 1회 (`intent.py`)
  - 전략서 생성 1회 (`pipeline.py`, 후보 종목·블록 최대 6개·성향 기본 제약 포함)
  - 되묻기가 생기면 그만큼 호출이 늘어난다
- 판단 계층(③)과 통제(④)는 LLM 을 쓰지 않으므로 교체 영향 없음
- 알려진 약점: Ollama 의 형식 강제가 약해 스키마 위반이 실제로 난다 (m1_milestone 5단계)
- 비용: API 요금 0원. 대신 호출 1회가 수십 초, 정확도가 낮다

## 2. 비교 대상 정리

- Claude Opus 5.5 (Anthropic)
  - 정식 모델명 `claude-opus-5-5`
  - 생각(thinking)을 끌 수 없다. effort 로만 조절하며 기본값 medium
- Claude Sonnet 5.5 (Anthropic)
  - 2026-09-28 출시. 요금은 Sonnet 5 와 동일
- Muse Spark 1.3 (Meta)
  - 2026-09-02 출시. 비공개 가중치
  - 요금이 Standard 와 Contributor 두 등급. Contributor 는 Meta 가 입력·출력을 학습에 쓰는 조건
- minio-v2.6-pro
  - 같은 이름의 모델은 찾지 못했다. Xiaomi MiMo-V2.6-Pro 로 보고 조사했다 (이름이 다르면 알려 줄 것)
  - 2026-09-22 출시. MIT 라이선스 공개 가중치, 1.02T 파라미터(활성 42B) MoE
- DeepSeek V4.1
  - 공개된 것은 V4.1 Flash 뿐이다 (2026-09-10). V4.1 Pro 는 없고 V4 Pro 가 계속 서비스 중
  - 시간대별 요금. 피크 시간이 중국 주간이라 한국 주간 사용은 대부분 피크 요금

## 3. 단가 (USD, 100만 토큰당)

- Opus 5.5: 입력 4.00, 출력 20.00, 캐시 읽기 0.20
- Sonnet 5.5: 입력 2.00, 출력 10.00, 캐시 읽기 0.20, Batch 시 절반
- Muse Spark 1.3 Standard: 입력 1.25, 출력 4.25, 캐시 읽기 0.15
- Muse Spark 1.3 Contributor: 입력 0.10, 출력 0.20 (학습 데이터 제공 조건, 분당 100회 제한)
- MiMo-V2.6-Pro: 입력 0.435, 출력 0.87, 캐시 읽기 0.0036
- DeepSeek V4.1 Flash 피크: 입력 0.30, 출력 1.20
- DeepSeek V4.1 Flash 비피크: 입력 0.15, 출력 0.60
- 참고 DeepSeek V4 Pro 피크: 입력 1.32, 출력 3.96

## 4. 사용량별 예상 요금

### 4.1 가정

- 전략서 1건 = LLM 호출 2회 (의도 추출 + 전략서 생성), 되묻기 없음
- 1건당 입력 약 4,000 토큰 (시스템 프롬프트, 사용자 문장, 후보 목록, 블록, JSON Schema)
- 1건당 출력 약 2,000 토큰 (JSON 결과 약 1,000 + 낮은 effort 의 생각 토큰 약 1,000)
- 프롬프트 캐시 할인은 넣지 않았다. 시스템 프롬프트가 짧아 효과가 작다
- 한국어는 모델마다 토크나이저 효율이 달라 실제 토큰 수가 1.5배까지 차이 날 수 있다
- 생각 effort 를 high 이상으로 올리면 출력 토큰이 몇 배로 늘어 요금도 그만큼 는다

### 4.2 1건당 요금

- Opus 5.5: 약 0.056 달러
- Sonnet 5.5: 약 0.028 달러
- Muse Spark 1.3 Standard: 약 0.0135 달러
- Muse Spark 1.3 Contributor: 약 0.0008 달러
- MiMo-V2.6-Pro: 약 0.0035 달러
- DeepSeek V4.1 Flash: 피크 약 0.0036 달러, 비피크 약 0.0018 달러
- qwen3:8b 로컬: 0 달러 (전기료와 개발 PC 점유만)

### 4.3 월 100건 (팀 개발·테스트 수준)

- Opus 5.5: 약 5.6 달러
- Sonnet 5.5: 약 2.8 달러
- Muse Spark 1.3 Standard: 약 1.35 달러
- MiMo-V2.6-Pro: 약 0.35 달러
- DeepSeek V4.1 Flash: 약 0.36 달러 (피크)

### 4.4 월 1,000건 (시연·평가 실측 수준)

- Opus 5.5: 약 56 달러
- Sonnet 5.5: 약 28 달러
- Muse Spark 1.3 Standard: 약 13.5 달러
- MiMo-V2.6-Pro: 약 3.5 달러
- DeepSeek V4.1 Flash: 약 3.6 달러 (피크)

### 4.5 월 10,000건 (실서비스 가정)

- Opus 5.5: 약 560 달러
- Sonnet 5.5: 약 280 달러
- Muse Spark 1.3 Standard: 약 135 달러
- MiMo-V2.6-Pro: 약 35 달러
- DeepSeek V4.1 Flash: 약 36 달러 (피크), 약 18 달러 (비피크)

## 5. 성능 비교

### 5.1 공통 지표

Artificial Analysis Intelligence Index 기준 (추론·지식·코딩 종합, 높을수록 좋음).

- qwen3:8b (비추론): 6 (추정치, 현재 측정 중단된 모델)
- Opus 5.5: 58 (max effort), 54 (high), 51 (medium). 전체 1위
- Sonnet 5.5: 56 (max effort). 전체 2위
- Muse Spark 1.3: 48 (max effort)
- MiMo-V2.6-Pro: 46. 공개 가중치 모델 중 최상위권
- DeepSeek V4.1 Flash: 39 (max effort)

### 5.2 qwen3:8b 대비 증가량

- 지수 차이로 보면 Opus 5.5 는 약 52점, Sonnet 5.5 약 50점, Muse Spark 1.3 약 42점, MiMo 약 40점, DeepSeek V4.1 Flash 약 33점 높다
- 배수로는 6.5배에서 9.7배 사이. 단 qwen3:8b 점수가 바닥에 가까워 배수는 과장되기 쉽다
- 이 지수는 수학·코딩·지식 중심이다. 우리 작업(한국어 문장을 정해진 JSON 전략서로 옮기기)과 직접 대응하지 않는다
- 우리 작업에서 의미 있는 개선은 세 가지로 본다
  - 스키마 준수: 지금 파싱이 유일한 방어선인 문제가 줄어든다
  - 한국어 의도 파악: 후보 종목 중 왜 그것을 고르는지 판단하는 품질
  - 지연: 로컬 CPU 추론의 수십 초 대기가 줄어든다

### 5.3 형식 강제

- Claude (Opus 5.5, Sonnet 5.5): 구조화 출력(`output_config.format`)이 스키마를 제약 디코딩으로 강제한다. 현재 Ollama 보다 확실히 강하다
- Muse Spark 1.3, MiMo-V2.6-Pro, DeepSeek V4.1 Flash: JSON 모드 지원은 확인했으나 JSON Schema 를 제약 디코딩으로 강제하는지는 공개 자료로 확인하지 못했다. 도입 전 실측 필요
- 어느 쪽이든 `postprocess.py` 의 파싱과 범위 접기는 그대로 둔다

### 5.4 속도 (Artificial Analysis 기준 출력 속도)

- DeepSeek V4.1 Flash: 초당 약 209 토큰
- Muse Spark 1.3: 초당 약 152 토큰
- Sonnet 5.5: 초당 약 132~139 토큰
- Opus 5.5: 초당 약 93 토큰
- MiMo-V2.6-Pro: 초당 약 46 토큰, 첫 토큰까지 p95 약 10초
- 추론 모델은 max effort 에서 첫 토큰까지 수 분이 걸릴 수 있다. 우리 작업은 low 또는 medium effort 로 충분하다

## 6. 프로젝트 관점의 고려 사항

- 재현성
  - 보장 대상은 같은 Spec 에서 같은 주문이다. LLM 은 1계층이라 출력 Spec 을 저장하면 하류 재현성은 유지된다
  - Opus 5.5 와 Sonnet 5.5 는 temperature 를 지정할 수 없고 Opus 5.5 는 생각을 끌 수 없다. 같은 문장에서 같은 Spec 이 나오는 것은 보장되지 않는다
  - m1_milestone 4단계 완료 기준 "같은 문장 두 번에 같은 후보" 는 후보 선정이 LLM 앞단(결정론)이라 영향 없음
- 데이터
  - Muse Spark 1.3 Contributor 등급은 입력이 학습에 쓰인다. 사용자 투자 의도가 들어가므로 쓰지 않는 편이 맞다
  - DeepSeek, MiMo 공식 API 는 중국 소재 서버다. 학내 과제라도 개인정보 처리 방침을 확인한다
- 자체 호스팅
  - MiMo-V2.6-Pro 는 가중치가 공개돼 vLLM 으로 띄울 수 있지만 1T 파라미터라 팀 장비로는 불가능하다
- 코드 변경 범위
  - `LLMClient` Protocol 이 있으므로 구현체 하나와 `LLM_BACKEND` 값 하나만 추가하면 된다
  - 기존 `vllm_client.py` 의 `guided_json` 은 vLLM 전용이라 외부 API 에는 그대로 쓸 수 없다
  - API 키는 `.env` 로만 넣고 저장소에 올리지 않는다

## 7. 정리

- 성능 최우선: Opus 5.5. 월 1,000건에 약 56 달러
- 성능 대비 비용 균형: Sonnet 5.5. 성능 차이는 2점, 요금은 절반
- 저비용 실측용: DeepSeek V4.1 Flash 또는 MiMo-V2.6-Pro. 월 1,000건에 4 달러 이하이며 qwen3:8b 보다 지수가 30점 이상 높다. 단 형식 강제는 실측으로 확인해야 한다
- Muse Spark 1.3 Standard 는 중간 가격대이나 이 작업에서 Sonnet 5.5 대비 뚜렷한 이점은 찾지 못했다
- 학기 중 개발·시연 규모(월 수백 건)에서는 어느 모델을 골라도 월 수십 달러를 넘지 않는다

## 8. 종합 의견

### 8.1 결론

- 로컬 qwen3:8b 를 버리지 말고 역할을 나눈다
  - 일상 개발과 CI: qwen3:8b 유지. 무료이고 오프라인으로 돌며 `requires_ollama` 마커 체계가 이미 맞춰져 있다
  - 정식 실측과 시연: 외부 API 하나를 추가한다
- 외부 API 1순위는 Sonnet 5.5
  - 형식 강제가 확인된 모델 중 가장 싸다
  - Opus 5.5 와 지수 차이는 2점인데 요금은 절반이다
  - 우리 작업은 후보 목록 안에서 고르는 단순한 구조라 최상위 추론 능력이 필요한 일이 아니다
- 저가 모델(DeepSeek V4.1 Flash, MiMo-V2.6-Pro)은 실측으로 자격을 확인한 뒤 채택한다
  - 스키마를 지키기만 하면 Sonnet 5.5 의 8분의 1 요금이다
  - 형식 강제 여부가 확인되지 않은 상태에서 1순위로 두면 지금 Ollama 의 문제를 그대로 옮겨 오게 된다
- Opus 5.5 와 Muse Spark 1.3 은 당장 쓰지 않는다
  - Opus 5.5 는 Sonnet 5.5 가 샘플에서 반복해서 틀리는 경우가 생길 때만 검토한다
  - Muse Spark 1.3 은 가격과 성능 모두 중간이라 고를 이유가 없다

### 8.2 진행 순서 제안

- 1단계: m1_milestone 7단계의 샘플 10건을 평가 기준으로 먼저 확정한다
  - 모델 선택 기준은 공개 지수가 아니라 이 10건의 결과다
  - 볼 항목은 스키마 통과율, 기대 전략서와의 일치, 실패해야 하는 입력의 거부, 되묻기 발생 여부, 응답 시간
- 2단계: 같은 10건을 qwen3:8b, Sonnet 5.5, DeepSeek V4.1 Flash, MiMo-V2.6-Pro 로 각 3회 돌린다
  - 총 120건이라 요금은 1달러 안팎이다
  - 3회 반복으로 같은 문장에서 결과가 얼마나 흔들리는지도 같이 본다
- 3단계: 결과로 고른다
  - 저가 모델이 Sonnet 5.5 와 비슷하면 저가 모델을 기본으로 둔다
  - 차이가 크면 Sonnet 5.5 를 기본으로 둔다
  - 셋 다 qwen3:8b 와 차이가 작으면 외부 API 도입 자체를 보류한다
- 4단계: 결정을 docs/infra-spec.md 에 반영한다
  - 기준 문서는 정식 실측을 vLLM 으로 하라고 지시한다. GPU 확보가 어려워 외부 API 로 대신하려면 팀 합의로 이 지시를 고쳐야 한다

### 8.3 지켜야 할 것

- 구현은 `LLMClient` 구현체 하나 추가로 끝낸다. M1 파이프라인과 후처리는 건드리지 않는다
- 외부 모델을 쓰더라도 `postprocess.py` 의 파싱과 범위 접기는 방어선으로 남긴다
- LLM 원출력과 사용한 모델명을 함께 남겨, 나중에 어떤 모델이 만든 전략서인지 추적할 수 있게 한다. 기록할 컬럼이 없으면 스키마 변경 안건으로 올린다
- API 키는 `.env` 로만 다루고 팀원별 사용량 한도를 공급자 콘솔에서 걸어 둔다

## 출처

- Claude 요금: Anthropic API 가격표, https://ofox.ai/blog/claude-sonnet-5-5-api-pricing-task-cost/
- Sonnet 5.5: https://artificialanalysis.ai/models/claude-sonnet-5-5 , https://openrouter.ai/anthropic/claude-sonnet-5.5
- Opus 5.5: https://artificialanalysis.ai/models/claude-opus-5-5 , https://www.eesel.ai/blog/claude-opus-5-5-review
- Muse Spark 1.3: https://coursiv.io/blog/muse-spark-1-3 , https://openrouter.ai/meta/muse-spark-1.3
- MiMo-V2.6-Pro: https://llm-stats.com/models/mimo-v2.6-pro , https://venturebeat.com/technology/better-than-deepseek-xiaomis-mimo-v2-6-pro-debuts-as-the-top-open-weights-model-in-the-world-alongside-cheaper-v2-6-flash
- DeepSeek V4.1: https://www.digitalapplied.com/blog/deepseek-v4-1-flash-pro-routing-prices-early-tests , https://openrouter.ai/deepseek/deepseek-v4.1-flash
- 종합 지수: https://artificialanalysis.ai/leaderboards/models , https://artificialanalysis.ai/models/qwen3-8b-instruct
