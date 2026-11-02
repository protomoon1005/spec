-- 전략서 v0.1 을 지우며 쓰지 않게 된 테이블과 칸을 지운다 (2026-11-02 결정, docs/v0.2-plan.md 9단계 d)
-- 전략 요소 사전(strategy_elements · strategy_element_tags)은 v0.1 명세화만 썼다.
-- v0.2 는 표현 표(db/seeds/expressions.yaml)를 쓴다
-- strategies 의 rebalance · signal_rules · constraint_user 는 v0.1 전략서를 쪼개 담던 칸이다.
-- v0.2 는 전략서를 spec 칸에 통째로 둔다(003)
DROP TABLE IF EXISTS strategy_element_tags;
DROP TABLE IF EXISTS strategy_elements;
ALTER TABLE strategies
    DROP COLUMN IF EXISTS rebalance,
    DROP COLUMN IF EXISTS signal_rules,
    DROP COLUMN IF EXISTS constraint_user;
