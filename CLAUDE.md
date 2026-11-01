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
