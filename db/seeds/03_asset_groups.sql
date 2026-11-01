-- 2계층 자산 그룹 (분류표). 그룹캡 시드는 그룹캡 기능과 함께 지웠다(기반 정리 2단계).
-- 레벨2(섹터·국가) 그룹의 parent_group_id는 NULL로 둔다 — 섹터·국가는 자산군의
-- 하위가 아니라 독립된 분류축이다 (사용자 확정, 2026-09-08 이후 보충 지시).
-- 국가(예: 한국)는 EQUITY와 BOND에 걸쳐 있어 트리로 묶을 수 없다.

-- 레벨1: 자산군 (group_id는 4.3 표에 리터럴로 주어진 값 그대로 사용)
INSERT INTO asset_groups (group_id, parent_group_id, group_level, label)
VALUES
    ('EQUITY', NULL, 1, '주식계'),
    ('BOND', NULL, 1, '채권계'),
    ('COMMODITY', NULL, 1, '원자재계');

-- 레벨2: 섹터 (7) — group_id는 4.3에 없어서 라벨을 영문 스네이크로 옮긴 식별자를 붙였다
INSERT INTO asset_groups (group_id, parent_group_id, group_level, label)
VALUES
    ('SECTOR_SEMICONDUCTOR', NULL, 2, '반도체'),
    ('SECTOR_BATTERY', NULL, 2, '2차전지'),
    ('SECTOR_BIOHEALTH', NULL, 2, '바이오와헬스케어'),
    ('SECTOR_FINANCE', NULL, 2, '금융'),
    ('SECTOR_INTERNET_PLATFORM', NULL, 2, '인터넷과플랫폼'),
    ('SECTOR_CONSUMER', NULL, 2, '소비재'),
    ('SECTOR_OTHER', NULL, 2, '기타');

-- 레벨2: 국가 (3)
INSERT INTO asset_groups (group_id, parent_group_id, group_level, label)
VALUES
    ('COUNTRY_KR', NULL, 2, '한국'),
    ('COUNTRY_US', NULL, 2, '미국'),
    ('COUNTRY_OTHER', NULL, 2, '기타');
