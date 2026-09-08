-- 시스템 하드캡 v0.1 (docs/infra-spec.md 4.4)
-- 수치는 아직 팀 확정 전이라 보수적 기본값을 넣는다.
-- -- TODO: 팀 확정 필요 (max_weight_per_asset / cash_min / max_loss_per_trade / max_drawdown / min_interval_days / leverage_allowed)
--
-- hardcap_versions.created_by는 NOT NULL FK라 시드 시점에 참조할 사용자가 있어야 한다.
-- users 시드 파일이 없으므로(4단계 산출물에 없음) 부트스트랩용 system 사용자를 여기서
-- 만든다. 로그인용이 아니라 시드 데이터의 created_by를 채우기 위한 용도다.
INSERT INTO users (email, password_hash, role)
VALUES ('system@spec.internal', 'seed-only-no-login', 'admin')
ON CONFLICT (email) DO NOTHING;

INSERT INTO hardcap_versions (
    hardcap_version, max_weight_per_asset, cash_min, max_loss_per_trade,
    max_drawdown, min_interval_days, leverage_allowed, created_by, activated_at
)
SELECT
    'v0.1', 0.30, 0.05, 0.05, 0.25, 5, false,
    (SELECT user_id FROM users WHERE email = 'system@spec.internal'),
    now()
WHERE NOT EXISTS (SELECT 1 FROM hardcap_versions WHERE hardcap_version = 'v0.1');
