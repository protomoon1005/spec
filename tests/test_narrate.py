"""v0.2 재서술: 문장 틀에 값을 끼운다 (LLM 미사용)."""
from conftest import cmp, ind, strategy_dict
from core.narrate import narrate
from core.schema import Strategy


def _samsung(**kw):
    kw.setdefault("assets", [{"ticker": "005930", "name": "삼성전자", "weight": None}])
    d = strategy_dict(**kw)
    d["backtest"] = {"start": "2023-01-01", "end": "2025-12-31", "initial_cash": 10_000_000,
                     "commission_rate": 0.00015, "slippage_rate": 0.0005}
    return Strategy.model_validate(d)


def test_v02_sample_sentence():
    text = narrate(_samsung()).text
    assert "**삼성전자**" in text
    assert "RSI(14일)가 30 아래로 내려가는 날" in text
    assert "종가가 5일 이동평균 아래로 내려가는 날" in text
    assert "다음 거래일 시가" in text
    assert "팔기를 우선" in text
    assert "2023-01-01 ~ 2025-12-31" in text and "1,000만 원" in text
    assert "수수료 0.015%" in text and "체결 오차 0.05%" in text
    assert "거래한 날짜의 세율" in text and "코스피 주식" in text
    assert "배당" in text   # 배당 미반영 안내


def test_slots_and_other_targets():
    s = _samsung(
        assets=[{"ticker": "005930", "name": "삼성전자", "weight": None},
                {"ticker": "000660", "name": "SK하이닉스", "weight": None}],
        max_weight=0.4,
        rules={"target": "each",
               "entry": {"all": [cmp(ind("close", of="KOSPI200"), ">", ind("ma", 20, of="KOSPI200")),
                                 cmp(ind("ret", 20), "<=", -10)]},
               "exit": {"any": [cmp(ind("entry_return"), "<=", -10), cmp(ind("holding_days"), ">=", 5)]}},
    )
    text = narrate(s).text
    assert "1/2" in text and "40%" in text
    assert "코스피200 종가가 코스피200 20일 이동평균 위인" in text
    assert "20일 수익률이 -10% 이하" in text
    assert "산 가격 대비 수익률이 -10% 이하" in text
    assert "보유 일수가 5거래일 이상" in text
    assert "그리고" in text and "또는" in text


def test_allocation_only_sentence():
    s = _samsung(rules=None, rebalance="quarterly",
                 assets=[{"ticker": "069500", "name": "KODEX 200", "weight": 0.7},
                         {"ticker": "153130", "name": "KODEX 단기채권", "weight": 0.3}])
    text = narrate(s).text
    assert "**KODEX 200** 70%" in text and "**KODEX 단기채권** 30%" in text
    assert "분기마다" in text
    assert "차익의 15.4%" in text            # 그 밖의 ETF


def test_default_tag():
    d = _samsung().model_dump(mode="json")
    d["defaults_applied"] = ["backtest"]
    text = narrate(Strategy.model_validate(d)).text
    assert "(기본값)" in text
