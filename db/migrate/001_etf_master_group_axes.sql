-- etf_master 분류축 3분리: 자산군 / 섹터 / 국가.
-- 기존 group_id 한 컬럼은 세 축 중 자산군만 담아 왔다(README "알려진 설계
-- 구멍" ① 국가 컬럼 부재). 세 축은 부모-자식 관계가 아니다 — 국가(예: 한국)는
-- EQUITY와 BOND에 모두 걸쳐 있어 asset_groups.parent_group_id 트리로 묶을 수
-- 없다(2026-09-08 확정 + 이후 보충 지시). 그래서 트리가 아니라 etf_master
-- 쪽에 축마다 독립된 FK 컬럼을 둔다.
--
-- 기존 group_id 컬럼은 지우지 않고 당분간 자산군용으로 남긴다.

ALTER TABLE etf_master
    ADD COLUMN asset_group_id   VARCHAR REFERENCES asset_groups (group_id),
    ADD COLUMN sector_group_id  VARCHAR REFERENCES asset_groups (group_id),
    ADD COLUMN country_group_id VARCHAR REFERENCES asset_groups (group_id);
