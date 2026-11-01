#!/usr/bin/env bash
# 테스트 전용 DB(${POSTGRES_DB}_test, 기본 spec_test)를 만들고 개발 DB 와 같은 스키마를 깐다.
#
# 개발 DB 가 테스트 찌꺼기로 더러워지지 않게 테스트는 이 DB 만 쓴다(backend/tests/conftest.py).
# 이 폴더의 *.sql 을 이름 순서대로 그대로 다시 돌리므로 두 DB 의 스키마가 어긋나지 않는다.
# docker-entrypoint 가 이 파일을 source 할 수도 있어서 set -u 나 exit 를 쓰지 않는다.

test_db="${POSTGRES_DB}_test"
init_dir="$(dirname "${BASH_SOURCE[0]}")"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE \"$test_db\""

for sql in "$init_dir"/*.sql; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$test_db" -f "$sql"
done
