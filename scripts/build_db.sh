#!/usr/bin/env bash
# DB 를 처음부터 다시 만든다 (기반 정리 2 · 3단계: 볼륨 삭제, 기동, 스키마 생성, 공용 데이터 적재).
#
# - 이 compose 프로젝트의 볼륨을 전부 지운다. 개발 DB(spec)와 테스트 DB(spec_test)의 데이터가 사라진다
# - 스키마는 빈 볼륨에서 postgres 가 처음 뜰 때 db/init/ 이 만든다. 이 스크립트는 그걸 기다렸다가
#   두 DB 의 테이블 목록을 확인한다
# - 그 뒤 두 DB 에 db/migrate/ 를 순서대로 적용한다(scripts/apply_migrations.py). db/init/ 뒤에 쌓인
#   스키마 변경이 새로 빌드한 DB 에도 들어간다
# - 이어서 두 DB 에 공용 데이터를 넣는다: 기준 시드(분류표 · 종목 원장 · 전략 요소 사전, scripts/seed_all.py)와
#   가격 스냅샷(scripts/ingest_prices.py). 스냅샷 파일은 data/snapshot/ 에 있으면 그걸, 없으면
#   data/prices_snapshot.json 의 url(GitHub Release)에서 받는다. 파일 해시와 적재한 내용 해시를 맞춰 본다
#   — 누가 빌드해도 같은 DB 가 나온다(docs/pivot-plan.md 3.2)
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

echo "build_db: 1/6 컨테이너와 볼륨 삭제"
docker compose down -v --remove-orphans

echo "build_db: 2/6 기동 (빈 볼륨이라 db/init/ 이 스키마를 만든다)"
docker compose up -d --build

# pg_isready 는 초기화 중인 임시 서버에도 준비됐다고 답할 수 있어서, 두 DB 의 테이블 수가
# 맞을 때까지 기다린다.
echo "build_db: 3/6 스키마 확인"
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

# 컨테이너 안의 DATABASE_URL(개발 DB) 끝에 _test 를 붙이면 테스트 DB 다(db/init/03_test_db.sh).
echo "build_db: 4/6 마이그레이션 (db/migrate/)"
for db_suffix in "" "_test"; do
    docker compose exec -T api sh -c "DATABASE_URL=\"\${DATABASE_URL}${db_suffix}\" python /repo/scripts/apply_migrations.py"
done

load_public_data() {
    local db_suffix="$1"
    docker compose exec -T api sh -c "DATABASE_URL=\"\${DATABASE_URL}${db_suffix}\" python /repo/scripts/seed_all.py"
    docker compose exec -T api sh -c "DATABASE_URL=\"\${DATABASE_URL}${db_suffix}\" python /repo/scripts/ingest_prices.py"
}

echo "build_db: 5/6 공용 데이터 — 개발 DB"
load_public_data ""
echo "build_db: 6/6 공용 데이터 — 테스트 DB"
load_public_data "_test"

echo "build_db: 완료"
