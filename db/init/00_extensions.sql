-- postgres 컨테이너 최초 기동 시 자동 실행 (docker-entrypoint-initdb.d)
-- 도메인 DDL(3단계, Alembic)이 아니라 인프라 부트스트랩이다.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
