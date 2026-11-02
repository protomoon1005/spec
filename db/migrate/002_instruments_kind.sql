-- 종목 원장에 상품 종류(stock 주식, etf ETF) 칸을 더한다 (2026-11-02 결정)
-- 백테스트 세금 계산이 주식과 ETF 를 구분해야 한다. 지금까지는 group_id 의 EQUITY 에 주식과 주식형 ETF 가 섞여
-- 있어 구분할 수 없었다
-- 값은 scripts/load_etf_master.py 가 db/seeds/04_etf_master.csv(원천 data/listing.csv 의 kind)에서 채운다
-- 이미 종목이 들어 있는 DB 에서도 적용되도록 NOT NULL 은 걸지 않는다. 적재 스크립트가 빈 값을 막는다
ALTER TABLE instruments ADD COLUMN kind VARCHAR CHECK (kind IN ('stock', 'etf'));
