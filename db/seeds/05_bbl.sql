-- BBL (Building Block Library) — 전략서에 들어갈 수 있는 값들의 목록.
--
-- 전략서 계약(backend/app/contracts/spec.py)에 "값 집합은 BBL 확정 후 M1" 주석이
-- 일곱 군데 달려 있다. 자리와 타입은 고정됐는데 어떤 값이 허용되는지가 비어 있었고,
-- 이 시드가 그 빈칸을 채운다. **여기 없는 값은 전략서에 들어갈 수 없다.**
--
-- 설계는 docs/m1_milestone.md 3단계 참고.
--
-- ── 식별자 규약 ──────────────────────────────────────────────────────
--   지표 블록은 block_id 가 곧 전략서에 들어갈 값이다. ret_20 블록을 고르면
--   indicators 에 "ret_20" 이 그대로 들어간다. 번역표를 한 겹 두면 언젠가 어긋난다.
--   나머지는 접두어를 붙인다 — RB_(리밸런싱) · FL_(필터) · ET_(ETF 특성).
--
-- ── params_schema 가 하는 일 ─────────────────────────────────────────
--   "이 블록을 고르면 무엇이 되는가" 를 담는다. 종류마다 모양이 다르다.
--     rebalance  전략서의 rebalance 자리에 통째로 들어갈 JSON
--     indicator  전략서의 어느 자리에 어떤 값으로 들어가는지
--     filter     종목 표의 어느 칸을 어떻게 거르는지 (전략서에 안 들어간다)
--     etf_trait  종목명을 어떤 규칙으로 판정하는지 (전략서에 안 들어간다)
--
-- ── 태그 규약 ────────────────────────────────────────────────────────
--   접두어 다섯 개로 고정한다 — kw: asset: sector: country: risk:
--   asset/sector/country 값은 asset_groups.group_id 를 소문자로 바꾼 것이다.
--   kw: 는 사용자 원문이 아니라 LLM 이 뽑아낸 표준 키워드와 맞춘다.
--   한 블록에 태그를 다섯 개 이상 달지 않는다.

-- ── 두 번 돌려도 안전하다 (비우고 다시 채운다) ──────────────────────
-- bbl_tags 에는 (block_id, tag) UNIQUE 제약이 없어서 ON CONFLICT 로는 중복을 막을 수
-- 없다. 두 벌이 되면 검색의 "맞은 태그 수" 가 부풀어 순서가 바뀐다 — 같은 입력에
-- 다른 전략서가 나온다는 뜻이다.
-- ON CONFLICT DO NOTHING 도 답이 아니다. 블록이 이미 있으면 건너뛰어서 **설명이나
-- 판정 규칙을 고쳐도 반영되지 않는다.** 실제로 그렇게 한 번 놓쳤다.
-- 두 표 모두 이 시드가 통째로 소유하므로 비우고 채운다.
--
-- ⚠ 나중에 임베딩 생성 스크립트를 돌린 뒤 이 시드를 다시 돌리면 임베딩이 날아간다.
--   그때는 시드를 돌린 다음 임베딩을 다시 만든다.

DELETE FROM bbl_tags;
DELETE FROM bbl_blocks;

-- =====================================================================
-- 지표 (indicator) — 11개
-- =====================================================================
-- 가격 지표 9개는 backend/app/views/market/features.py 의 피처셋 v0.1-ta9 그대로다.
-- 이름을 바꾸거나 없는 지표를 넣으면 판단 계층이 그 값을 찾지 못한다.
--
-- 거시지표는 수집 중인 네 개 중 **둘만** 넣는다. 전략서의 market_temperature 에는
-- trend_index 와 volatility_index 자리밖에 없어서다. USDKRW 와
-- CREDIT_SPREAD_BAA10Y 는 국면 판정이 내부에서 쓰는 입력이고 전략서에 자리가 없다.

INSERT INTO bbl_blocks (block_id, block_type, title, description, params_schema) VALUES
('ret_1', 'indicator', '1일 수익률',
 '직전 거래일 대비 종가 변화율. 하루 단위의 짧은 움직임을 본다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "ret_1"}'),
('ret_5', 'indicator', '5일 수익률',
 '한 주 정도의 수익률. 단기 흐름을 본다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "ret_5"}'),
('ret_20', 'indicator', '20일 수익률',
 '한 달 정도의 수익률. 중기 모멘텀을 본다 — 최근에 오른 것이 계속 오를지 판단할 때 쓴다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "ret_20"}'),
('ma_gap_20', 'indicator', '20일 이동평균 이격도',
 '종가가 20일 평균에서 얼마나 떨어져 있는지. 양수면 평균 위, 음수면 아래다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "ma_gap_20"}'),
('ma_gap_60', 'indicator', '60일 이동평균 이격도',
 '종가가 60일 평균에서 얼마나 떨어져 있는지. 20일보다 느리고 추세를 본다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "ma_gap_60"}'),
('rsi_14', 'indicator', '상대강도지수(14)',
 '0~100 사이 값. 낮으면 많이 내려 과매도, 높으면 많이 올라 과매수로 본다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "rsi_14"}'),
('atr_14_pct', 'indicator', '평균실체범위 비율(14)',
 '하루에 얼마나 크게 움직이는지를 종가 대비 비율로 본다. 변동성 지표다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "atr_14_pct"}'),
('vol_20', 'indicator', '20일 변동성',
 '최근 20거래일 수익률이 얼마나 흩어져 있는지. 클수록 오르내림이 심하다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "vol_20"}'),
('volume_ratio_20', 'indicator', '거래량 비율(20일)',
 '오늘 거래량이 20일 평균의 몇 배인지. 관심이 몰렸는지를 본다.',
 '{"slot": "signal_rules.market_analysis.indicators", "value": "volume_ratio_20"}'),
('KOSPI200', 'indicator', '코스피200 지수',
 '시장 전체가 오르는 추세인지 내리는 추세인지 판단하는 기준 지수.',
 '{"slot": "signal_rules.market_temperature.trend_index", "value": "KOSPI200"}'),
('VIX_CLOSE', 'indicator', '변동성 지수(VIX)',
 '시장이 얼마나 불안한지를 재는 지수. 높을수록 위험을 피하는 국면으로 본다.',
 '{"slot": "signal_rules.market_temperature.volatility_index", "value": "VIX_CLOSE"}');

-- =====================================================================
-- 리밸런싱 (rebalance) — 6개
-- =====================================================================
-- params_schema 가 전략서의 rebalance 자리에 **통째로** 들어간다.
-- min_interval_days 는 하드캡(5일)보다 짧게 두지 않는다 — 넣어도 나중에 접힌다.

INSERT INTO bbl_blocks (block_id, block_type, title, description, params_schema) VALUES
('RB_MONTHLY_FIRST', 'rebalance', '매달 첫 거래일',
 '매달 첫 거래일에 목표 비중으로 되돌린다. 거래가 잦지 않아 비용이 적고 가장 무난하다.',
 '{"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20}'),
('RB_MONTHLY_MID', 'rebalance', '매달 중순',
 '매달 15일 근처에 되돌린다. 월초에 몰리는 거래를 피하고 싶을 때 쓴다.',
 '{"trigger": {"type": "calendar", "freq": "monthly", "day": 15}, "min_interval_days": 20}'),
('RB_QUARTERLY', 'rebalance', '분기마다',
 '석 달에 한 번 되돌린다. 손이 가장 덜 가고 거래비용이 가장 적다.',
 '{"trigger": {"type": "calendar", "freq": "quarterly", "day": 1}, "min_interval_days": 60}'),
('RB_WEEKLY', 'rebalance', '매주 월요일',
 '주 1회 되돌린다. 시장 변화를 빨리 따라가지만 거래가 잦아 비용이 늘어난다.',
 '{"trigger": {"type": "calendar", "freq": "weekly", "day": 1}, "min_interval_days": 5}'),
('RB_THRESHOLD_5', 'rebalance', '비중이 5%p 벗어나면',
 '날짜와 무관하게, 목표 비중에서 5%p 이상 벌어진 종목이 생기면 되돌린다.',
 '{"trigger": {"type": "threshold", "freq": "daily"}, "min_interval_days": 5}'),
('RB_SIGNAL', 'rebalance', '신호가 바뀌면',
 '판단 계층의 신호 방향이 바뀔 때 되돌린다. 가장 자주 거래하게 된다.',
 '{"trigger": {"type": "signal", "freq": "daily"}, "min_interval_days": 5}');

-- =====================================================================
-- 필터 (filter) — 7개
-- =====================================================================
-- 종목 후보를 추릴 때만 쓴다. 전략서에는 들어가지 않는다.
-- 종목 표에 **실제로 값이 들어 있는 칸만** 쓴다 — 종목코드·이름·업종·자산군·위험등급·
-- 레버리지 여부·활성 여부. 보수율·최대낙폭·국가는 비어 있거나 칸이 없어서 못 거른다.
-- (국가 칸이 없는 것은 README 의 알려진 문제에 기록돼 있다.)

INSERT INTO bbl_blocks (block_id, block_type, title, description, params_schema) VALUES
('FL_NO_LEVERAGE', 'filter', '레버리지·인버스 제외',
 '지수 움직임을 몇 배로 따라가거나 거꾸로 가는 상품을 후보에서 뺀다.',
 '{"column": "is_leveraged", "op": "eq", "value": false}'),
('FL_ACTIVE_ONLY', 'filter', '상장폐지 종목 제외',
 '지금 거래되고 있는 종목만 남긴다.',
 '{"column": "active", "op": "eq", "value": true}'),
('FL_EQUITY_ONLY', 'filter', '주식형만',
 '주식에 투자하는 ETF 만 남긴다.',
 '{"column": "group_id", "op": "eq", "value": "EQUITY"}'),
('FL_BOND_ONLY', 'filter', '채권형만',
 '채권에 투자하는 ETF 만 남긴다. 가격이 덜 흔들린다.',
 '{"column": "group_id", "op": "eq", "value": "BOND"}'),
('FL_COMMODITY_ONLY', 'filter', '원자재만',
 '금·은·원유 같은 실물에 투자하는 ETF 만 남긴다.',
 '{"column": "group_id", "op": "eq", "value": "COMMODITY"}'),
('FL_LOW_RISK_ONLY', 'filter', '위험이 낮은 등급만',
 '위험등급 G4~G6 만 남긴다. 보수적인 성향이 담을 수 있는 구간이다.',
 '{"column": "risk_tag", "op": "in", "value": ["G4", "G5", "G6"]}'),
('FL_HIGH_RISK_ONLY', 'filter', '위험이 높은 등급만',
 '위험등급 G1~G2 만 남긴다. 크게 움직이는 것을 노릴 때 쓴다.',
 '{"column": "risk_tag", "op": "in", "value": ["G1", "G2"]}');

-- =====================================================================
-- ETF 특성 (etf_trait) — 8개
-- =====================================================================
-- 종목 이름에서 읽히는 성격. 전략서에는 들어가지 않고 후보를 고를 때 쓴다.
-- 판정은 종목명 정규식이다 — 종목 표에 칸을 추가하지 않기 위해서다.

INSERT INTO bbl_blocks (block_id, block_type, title, description, params_schema) VALUES
('ET_DIVIDEND', 'etf_trait', '배당',
 '배당을 많이 주는 종목을 모은 ETF. 정기적으로 현금이 나오기를 바랄 때 고른다.',
 '{"name_pattern": "배당"}'),
('ET_COVERED_CALL', 'etf_trait', '커버드콜',
 '옵션을 팔아 매달 분배금을 더 받는 대신, 크게 오를 때 덜 오르는 구조다.',
 '{"name_pattern": "커버드콜"}'),
('ET_HEDGED', 'etf_trait', '환헤지',
 '환율 변동을 막아 둔 상품. 이름 끝에 (H) 가 붙는다.',
 '{"name_pattern": "\\(H\\)|환헤지"}'),
('ET_TR', 'etf_trait', '분배금 재투자',
 '분배금을 현금으로 주지 않고 자동으로 다시 투자한다. 이름에 TR 이 붙는다.',
 '{"name_pattern": "(^|[[:space:]])[0-9A-Za-z]*TR($|[[:space:]])"}'),
('ET_ACTIVE', 'etf_trait', '액티브',
 '지수를 그대로 따라가지 않고 운용사가 종목을 조정한다.',
 '{"name_pattern": "액티브"}'),
-- 종목명의 **마지막 낱말**이 지수 이름으로 끝나야 한다. 그래야 "TIGER 200 IT" ·
-- "TIGER 200 중공업" 같은 업종 펀드와 "KODEX 200미국채혼합50" 같은 혼합형이 안 걸린다.
-- '다우존스' 는 뺐다 — 이 유니버스에 들어 있는 다우 계열이 전부 배당 전략 지수
-- (미국배당다우존스)라 시장 대표지수가 아니다.
('ET_MARKET_INDEX', 'etf_trait', '시장 대표지수',
 '코스피200·코스닥150·S&P500 처럼 시장 전체를 담는 ETF.',
 '{"name_pattern": "(^|[[:space:]])[^[:space:]]*(200|코스피|코스피100|코스닥150|S&P500|나스닥100)(TR)?(\\(H\\))?$"}'),
('ET_MONEY_MARKET', 'etf_trait', '초단기 금리',
 'CD금리·KOFR 같은 하루짜리 금리를 따라간다. 거의 예금처럼 움직인다.',
 '{"name_pattern": "CD금리|KOFR|머니마켓|단기통안채|(^|[[:space:]])단기채권"}'),
('ET_GOV_BOND', 'etf_trait', '국채',
 '나라가 발행한 채권에 투자한다. 채권 중에서도 가장 안전한 축이다.',
 '{"name_pattern": "국고채|국채"}');

-- =====================================================================
-- 렌즈 (lens) — 비워 둔다
-- =====================================================================
-- 감성 분석이 기사를 어느 관점으로 읽을지 가리키는 블록이다. 감성 점수 모델
-- 자체가 아직 없고 렌즈 목록 정의도 없어서, M3 와 조율 전까지 넣지 않는다.

-- =====================================================================
-- 태그
-- =====================================================================
INSERT INTO bbl_tags (block_id, tag) VALUES
-- 지표
('ret_1', 'kw:단기'), ('ret_1', 'risk:high'),
('ret_5', 'kw:단기'), ('ret_5', 'risk:mid'),
('ret_20', 'kw:모멘텀'), ('ret_20', 'kw:추세'), ('ret_20', 'risk:mid'),
('ma_gap_20', 'kw:이격도'), ('ma_gap_20', 'kw:평균회귀'), ('ma_gap_20', 'risk:mid'),
('ma_gap_60', 'kw:추세'), ('ma_gap_60', 'risk:low'),
('rsi_14', 'kw:과매도'), ('rsi_14', 'kw:반등'), ('rsi_14', 'risk:mid'),
('atr_14_pct', 'kw:변동성'), ('atr_14_pct', 'risk:high'),
('vol_20', 'kw:변동성'), ('vol_20', 'kw:안전하게'), ('vol_20', 'risk:low'),
('volume_ratio_20', 'kw:거래량'), ('volume_ratio_20', 'risk:high'),
('KOSPI200', 'kw:시장'), ('KOSPI200', 'kw:추세'), ('KOSPI200', 'country:kr'),
('VIX_CLOSE', 'kw:변동성'), ('VIX_CLOSE', 'kw:불안'), ('VIX_CLOSE', 'risk:low'),
-- 리밸런싱
('RB_MONTHLY_FIRST', 'kw:월간'), ('RB_MONTHLY_FIRST', 'kw:매달'), ('RB_MONTHLY_FIRST', 'risk:low'),
('RB_MONTHLY_MID', 'kw:월간'), ('RB_MONTHLY_MID', 'kw:매달'), ('RB_MONTHLY_MID', 'risk:low'),
('RB_QUARTERLY', 'kw:분기'), ('RB_QUARTERLY', 'kw:장기'), ('RB_QUARTERLY', 'risk:low'),
('RB_WEEKLY', 'kw:주간'), ('RB_WEEKLY', 'kw:자주'), ('RB_WEEKLY', 'risk:mid'),
('RB_THRESHOLD_5', 'kw:이탈'), ('RB_THRESHOLD_5', 'kw:비중유지'), ('RB_THRESHOLD_5', 'risk:mid'),
('RB_SIGNAL', 'kw:신호'), ('RB_SIGNAL', 'kw:자주'), ('RB_SIGNAL', 'risk:high'),
-- 필터
('FL_NO_LEVERAGE', 'kw:안전하게'), ('FL_NO_LEVERAGE', 'kw:보수적'), ('FL_NO_LEVERAGE', 'risk:low'),
('FL_ACTIVE_ONLY', 'kw:상장폐지'), ('FL_ACTIVE_ONLY', 'risk:low'),
('FL_EQUITY_ONLY', 'kw:주식'), ('FL_EQUITY_ONLY', 'asset:equity'), ('FL_EQUITY_ONLY', 'risk:high'),
('FL_BOND_ONLY', 'kw:채권'), ('FL_BOND_ONLY', 'asset:bond'), ('FL_BOND_ONLY', 'risk:low'),
('FL_COMMODITY_ONLY', 'kw:원자재'), ('FL_COMMODITY_ONLY', 'kw:금'), ('FL_COMMODITY_ONLY', 'asset:commodity'),
('FL_LOW_RISK_ONLY', 'kw:안전하게'), ('FL_LOW_RISK_ONLY', 'kw:보수적'), ('FL_LOW_RISK_ONLY', 'risk:low'),
('FL_HIGH_RISK_ONLY', 'kw:공격적'), ('FL_HIGH_RISK_ONLY', 'kw:수익'), ('FL_HIGH_RISK_ONLY', 'risk:high'),
-- ETF 특성
('ET_DIVIDEND', 'kw:배당'), ('ET_DIVIDEND', 'kw:분배금'), ('ET_DIVIDEND', 'risk:low'),
('ET_COVERED_CALL', 'kw:배당'), ('ET_COVERED_CALL', 'kw:월배당'), ('ET_COVERED_CALL', 'risk:mid'),
('ET_HEDGED', 'kw:환헤지'), ('ET_HEDGED', 'kw:환율'), ('ET_HEDGED', 'risk:low'),
('ET_TR', 'kw:재투자'), ('ET_TR', 'kw:복리'), ('ET_TR', 'risk:mid'),
('ET_ACTIVE', 'kw:액티브'), ('ET_ACTIVE', 'risk:high'),
('ET_MARKET_INDEX', 'kw:지수'), ('ET_MARKET_INDEX', 'kw:시장대표'), ('ET_MARKET_INDEX', 'risk:mid'),
('ET_MONEY_MARKET', 'kw:파킹'), ('ET_MONEY_MARKET', 'kw:예금'), ('ET_MONEY_MARKET', 'asset:bond'), ('ET_MONEY_MARKET', 'risk:low'),
('ET_GOV_BOND', 'kw:국채'), ('ET_GOV_BOND', 'kw:안전하게'), ('ET_GOV_BOND', 'asset:bond'), ('ET_GOV_BOND', 'risk:low');
