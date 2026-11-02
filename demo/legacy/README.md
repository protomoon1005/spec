# legacy — demo-0.1 (v0.1) 전략서 일체

v0.2 로 교체하기 직전(커밋 807dc65)의 core/·data/·config/·schema/·tests/ 를 그대로 복사한 것.
평가 단계에서 이전 방식(demo-0.1)과 v0.2 를 비교하는 데 쓴다. 기능 수정은 하지 않는다.

- 바꾼 것: import 경로(core. → legacy.), 사전·설정·스키마 경로(legacy/data, legacy/config, legacy/schema).
- 가격·LLM 캐시는 저장소 공용 data/cache 를 쓴다. 스키마 `$comment` 는 캐시 키가 같도록 원문 그대로 둔다.
- 테스트: `pytest -q legacy/tests` (루트에서 `pytest -q` 하면 함께 돈다)
- 평가: `python -m eval.run` 은 이제 v0.2 파이프라인(core.pipeline.run)을 쓴다. legacy 결과 파일은 `--compare` 로 비교한다.
