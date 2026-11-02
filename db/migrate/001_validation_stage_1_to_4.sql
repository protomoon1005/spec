-- 검증 단계를 4개로 넓힌다 (2026-11-02 결정)
-- 1 모양, 2 존재, 3 말이 되는지, 4 근거. 시험 실행 단계(5)는 두지 않기로 했다
-- 4단계(근거 검사)는 전략서 저장 전에 돌고, 결과는 전략서를 저장할 때 같이 남긴다
-- 막힘 · 되묻기 · 경고는 칸을 늘리지 않고 violations 항목마다 종류(block, ask, warn)로 적는다
ALTER TABLE validation_logs DROP CONSTRAINT validation_logs_stage_check;
ALTER TABLE validation_logs ADD CONSTRAINT validation_logs_stage_check CHECK (stage BETWEEN 1 AND 4);
