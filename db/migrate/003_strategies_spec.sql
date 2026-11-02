-- 전략서 v0.2 를 통째로 저장하는 칸을 더한다 (2026-11-02 결정, A안)
-- 지금 strategies 는 rebalance · signal_rules · constraint_user 칸과 strategy_assets 로 전략서를 쪼개 저장해서,
-- v0.2 의 rules · backtest · meta 를 둘 자리가 없다(docs/v0.2-plan.md 5단계)
-- v0.1 전략서는 이 칸이 비어 있다. 옛 칸은 v0.1 을 지울 때(9단계) 정리한다
ALTER TABLE strategies ADD COLUMN spec JSONB;
