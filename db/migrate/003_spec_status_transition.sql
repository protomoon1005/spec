-- 승인한 전략서의 상태 칸만 앞으로 넘어갈 수 있게 한다 (2026-10-21 결정, docs/known-issues.md
-- "승인한 전략서의 상태를 바꿀 수 없음").
--
-- 전에는 approved 행의 UPDATE 를 통째로 막아 approved → running → closed 로 넘어가는 것도 막혔다.
-- 얼려야 하는 것은 내용(종목 · 비중 · 규칙 · 제약)이지 상태가 아니다. 그래서
--   - 승인 뒤(approved · running · closed)에는 status 말고 어떤 칸도 못 바꾼다
--   - status 는 approved → running → closed 순서로 한 칸씩만 간다. 되돌리기(→ draft 등)는 막는다
--   - spec_universe 잠금은 상위 전략서가 approved 일 때만 걸리던 것을 승인 뒤 세 상태 전부로 넓힌다.
--     넓히지 않으면 running 으로 넘긴 전략서의 종목 · 비중을 다시 고칠 수 있게 된다
-- draft 행은 전처럼 자유롭게 고친다(draft → approved 는 승인 API 가 한다).

CREATE OR REPLACE FUNCTION fn_strategy_specs_block_update() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('approved', 'running', 'closed') THEN
        IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
            RAISE EXCEPTION 'strategy_specs: 승인한 spec_id=% 는 상태 말고는 수정할 수 없다 (status=%)',
                OLD.spec_id, OLD.status;
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
            (OLD.status = 'approved' AND NEW.status = 'running')
            OR (OLD.status = 'running' AND NEW.status = 'closed')
        ) THEN
            RAISE EXCEPTION 'strategy_specs: spec_id=% 상태는 approved → running → closed 순서로만 간다 (% → %)',
                OLD.spec_id, OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fn_spec_universe_block_update() RETURNS trigger AS $$
DECLARE
    parent_status VARCHAR;
BEGIN
    SELECT status INTO parent_status FROM strategy_specs WHERE spec_id = OLD.spec_id;
    IF parent_status IN ('approved', 'running', 'closed') THEN
        RAISE EXCEPTION 'spec_universe: 상위 spec_id=% 가 승인된 상태(%)라 수정할 수 없다', OLD.spec_id, parent_status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
