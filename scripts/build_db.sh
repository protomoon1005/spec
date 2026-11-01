#!/usr/bin/env bash
# DB 를 처음부터 다시 만든다 (기반 정리 2단계: 볼륨 삭제, 기동, 스키마 생성).
#
# - 이 compose 프로젝트의 볼륨을 전부 지운다. 개발 DB(spec)와 테스트 DB(spec_test)의 데이터가 사라진다
# - 스키마는 빈 볼륨에서 postgres 가 처음 뜰 때 db/init/ 이 만든다. 이 스크립트는 그걸 기다렸다가
#   두 DB 의 테이블 목록을 확인한다
# - 공용 데이터(기준 시드, 종목 원장, 가격 스냅샷) 적재는 기반 정리 3단계에서 붙인다.
#   그전까지는 끝난 뒤 scripts/seed_all.py 와 scripts/ingest_prices.py 를 따로 돌린다(CLAUDE.md)
# - 프로젝트 이름과 포트는 .env(COMPOSE_PROJECT_NAME 등)를 따른다
#
# 사용 (저장소 루트에서):
#   scripts/build_db.sh        지울지 한 번 묻는다
#   scripts/build_db.sh -y     묻지 않는다
set -euo pipefail

cd "$(dirname "$0")/.."

expected_tables=11

if [[ "${1:-}" != "-y" ]]; then
    read -r -p "이 compose 프로젝트의 볼륨(DB 데이터)을 지우고 새로 만든다. 계속할까? [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "build_db: 그만둠"; exit 1; }
fi

echo "build_db: 1/3 컨테이너와 볼륨 삭제"
docker compose down -v --remove-orphans

echo "build_db: 2/3 기동 (빈 볼륨이라 db/init/ 이 스키마를 만든다)"
docker compose up -d --build

# pg_isready 는 초기화 중인 임시 서버에도 준비됐다고 답할 수 있어서, 두 DB 의 테이블 수가
# 맞을 때까지 기다린다.
echo "build_db: 3/3 스키마 확인"
count_tables() {
    docker compose exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB$1\" -tAc \"SELECT count(*) FROM pg_tables WHERE schemaname = 'public'\"" \
        2>/dev/null || true
}
for db_suffix in "" "_test"; do
    for _ in $(seq 1 60); do
        found="$(count_tables "$db_suffix" | tr -d '[:space:]')"
        [[ "$found" == "$expected_tables" ]] && break
        sleep 1
    done
    if [[ "$found" != "$expected_tables" ]]; then
        echo "build_db: 실패 — DB 접미사 '${db_suffix:-없음}' 에서 테이블 ${found:-?}개 (기대 ${expected_tables}개)" >&2
        exit 1
    fi
    echo "build_db: DB 접미사 '${db_suffix:-없음}' 테이블 ${found}개 확인"
done

echo "build_db: 완료"
