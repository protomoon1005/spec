\# Spec — 공통 인프라



\## 이 저장소

자연어 → Strategy Spec(JSON) 컴파일 → 결정론적 집행. 국내 ETF 모의운용 졸업작품.

지금 단계는 공통 인프라만 만든다. 비즈니스 로직은 범위 밖이다.



\## 절대 규칙

\- 인프라 작업의 기준 문서는 docs/infra-spec.md 다. 시작 전 반드시 읽는다.

\- 그 문서의 확정 수치(허용범위 프리셋 v0.1, 성향별 제약 기본값, 2계층 그룹캡)를

&#x20; 임의로 바꾸지 않는다. 2026-09-08 팀 확정본이다.

\- docs/infra-spec.md 9장 "하지 말 것"에 적힌 것은 구현하지 않는다.

&#x20; 인터페이스와 NotImplementedError 만 둔다.

\- 피처 / 거시지표 / 관점 가중치는 app/repositories/ 밖에서 직접 SELECT 하지 않는다.

\- 스키마 설계 판단이 필요하면 임의 결정하지 말고 선택지를 제시하고 멈춘다.

\- 사용자가 지정한 단계만 수행한다. 다음 단계로 넘어가지 않는다.



\## 환경

\- Windows PowerShell 환경이다. WSL도 Ubuntu도 없다.

\- bash 전용 명령(make, \&\& 체이닝, heredoc)을 검증 절차에 쓰지 마라.

\- 편의 명령은 Makefile 대신 tasks.ps1 로 만든다.

\- 컨테이너 안에서 도는 스크립트는 LF 줄바꿈으로 쓴다.

\- LLM 백엔드는 호스트에 떠 있는 Ollama(qwen3:8b)를 재사용한다.

&#x20; Docker Desktop for Windows는 host.docker.internal 이 기본 제공된다.

\- GPU가 없어 vLLM 기동 검증은 보류한다. 서비스 정의는 profile: gpu 로 만들어만 둔다.

