-- postgres 컨테이너 최초 기동 시 자동 실행 (docker-entrypoint-initdb.d)
-- 테이블은 02_schema.sql, 테스트 전용 DB 는 03_test_db.sh 가 만든다.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
