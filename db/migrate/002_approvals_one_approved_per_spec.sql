-- 전략서 하나에 "승인함" 기록은 하나만 (2026-10-21 결정, docs/m2-체인통합-승인-결정기록.md 6장 1번).
--
-- 승인 API(app/m2/approval.py)는 전략서 행 FOR UPDATE 잠금 + draft 확인으로 중복 승인을 이미
-- 막는다. 이 인덱스는 그 코드가 바뀌거나 우회돼도 DB 가 마지막으로 막게 하는 안전장치다.
-- 부분 인덱스(WHERE approved)라 거절 기록(approved = false)은 여러 번 남길 수 있다.
-- portfolios (spec_id) 쪽에 걸지 않은 이유: 같은 전략서로 포트폴리오를 다시 여는 기능을 막지
-- 않으려고. 승인 → 포트폴리오는 한 트랜잭션이라 승인 하나가 포트폴리오 하나를 낳는다.

CREATE UNIQUE INDEX uq_approvals_spec_approved ON approvals (spec_id) WHERE approved;
