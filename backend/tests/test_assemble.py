"""값 바꾸기와 전략서 조립(app/m1/assemble.py) 테스트. AI 없이 짝짓기 JSON 을 직접 넣는다."""

from __future__ import annotations

from app.m1 import assemble, pairing, spans

DICTIONARY = spans.load_dictionary()
INDEX = spans.InstrumentIndex(
    [
        ("005930", "삼성전자"),
        ("069500", "KODEX 200"),
        ("005380", "현대차"),
        ("000660", "SK하이닉스"),
        ("000270", "기아"),
    ],
    DICTIONARY,
)
EMPTY_BACKTEST = {"period": [], "capital": [], "dividends": []}


def _cond(actions, items, join="one"):
    return {
        "actions": actions,
        "join": join,
        "items": [dict(zip(("subject", "left", "op", "right"), item, strict=True)) for item in items],
    }


def _build(text, rules=(), *, overrides=None, issues=(), **fields):
    raw = {
        "universe": [],
        "weights": [],
        "rebalance": [],
        "max_weight": None,
        "selection": [],
        "backtest": dict(EMPTY_BACKTEST),
        "quotes": [],
        "rules": list(rules),
    }
    raw.update(fields)
    result = spans.find_spans(text, DICTIONARY, INDEX)
    p = pairing.normalize(pairing.parse(raw, result))
    return assemble.assemble(result, p, unresolved_issues=issues, overrides=overrides)


REPRESENTATIVE = (
    "삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘",
    [
        {
            "target": "each",
            "entry": _cond(["5:사고"], [(None, "2:RSI", "4:아래로 떨어지면", "3:30")]),
            "exit": _cond(["8:팔아줘"], [(None, None, "7:깨지면", "6:5일선")]),
        }
    ],
)


def test_representative_example_becomes_the_answer_spec():
    built = _build(*REPRESENTATIVE, universe=["1:삼성전자"])
    assert built.asks == [] and built.rejects == []
    spec = built.spec.model_dump(mode="json", exclude={"meta"})
    assert spec["rules"] == [
        {
            "target": "each",
            "entry": {
                "left": {"indicator": "rsi", "window": 14, "of": "self"},
                "op": "cross_below",
                "right": {"value": 30.0},
            },
            "exit": {
                "left": {"indicator": "close", "window": None, "of": "self"},
                "op": "cross_below",
                "right": {"indicator": "ma", "window": 5, "of": "self"},
            },
        }
    ]
    assert spec["universe"]["tickers"] == ["005930"] and spec["weights"] == "equal"
    assert built.defaults_used == [
        "weights",
        "period",
        "capital",
        "costs",
        "dividends",
        "rsi_window",
        "subject",
    ]
    assert built.evidence["entry"] == ["사고", "RSI", "아래로 떨어지면", "30"]


def test_take_profit_and_stop_loss_signs():
    text = "현대차 RSI 30 이하에서 사고, 산 가격보다 10% 오르면 익절 5% 빠지면 손절"
    built = _build(
        text,
        [
            {
                "target": "each",
                "entry": _cond(["5:사고"], [(None, "2:RSI", "4:이하에서", "3:30")]),
                "exit": _cond(
                    ["9:익절", "12:손절"],
                    [(None, "6:산 가격보다", "8:오르면", "7:10%"), (None, None, "11:빠지면", "10:5%")],
                    join="any",
                ),
            }
        ],
        universe=["1:현대차"],
    )
    exits = built.spec.model_dump(mode="json")["rules"][0]["exit"]["any"]
    assert [(c["left"]["indicator"], c["op"], c["right"]["value"]) for c in exits] == [
        ("entry_return", ">=", 0.1),
        ("entry_return", "<=", -0.05),
    ]


def test_missing_ma_window_is_asked_once_and_the_answer_fills_both_sides():
    text = "현대차 이평선 깨지면 팔고 다시 올라오면 사"
    rules = [
        {
            "target": "each",
            "exit": _cond(["4:팔고"], [(None, None, "3:깨지면", "2:이평선")]),
            "entry": _cond(["6:사"], [(None, None, "5:올라오면", "2:이평선")]),
        }
    ]
    built = _build(text, rules, universe=["1:현대차"])
    assert [(a.kind, a.path) for a in built.asks] == [("ma_window", "span2")]
    answered = _build(text, rules, universe=["1:현대차"], overrides={built.asks[0].key: 20})
    rule = answered.spec.model_dump(mode="json")["rules"][0]
    assert rule["entry"]["right"]["window"] == rule["exit"]["right"]["window"] == 20


def test_window_not_allowed_and_threshold_and_exit_asks():
    built = _build(
        "기아 7일선 뚫으면 사",
        [{"target": "each", "exit": None, "entry": _cond(["4:사"], [(None, None, "3:뚫으면", "2:7일선")])}],
    )
    kinds = {a.kind: a for a in built.asks}
    assert kinds["window_not_allowed"].context["window"] == 7
    assert "exit" in kinds and "universe" in kinds

    rsi = [{"target": "each", "exit": None, "entry": _cond(["4:사"], [(None, "2:RSI", "3:낮아지면", None)])}]
    built = _build("현대차 RSI 낮아지면 사", rsi, universe=["1:현대차"])
    assert sorted(a.kind for a in built.asks) == ["exit", "threshold"]
    threshold = next(a for a in built.asks if a.kind == "threshold")
    assert threshold.context == {"indicator": "rsi", "direction": "down"}
    done = _build(
        "현대차 RSI 낮아지면 사",
        rsi,
        universe=["1:현대차"],
        overrides={threshold.key: 30, "exit:rules[0].exit": "reverse"},
    )
    rule = done.spec.model_dump(mode="json")["rules"][0]
    assert rule["entry"]["right"] == {"value": 30.0} and rule["exit"]["op"] == ">="


def test_exit_answers():
    text = "현대차 RSI 30 아래로 내려가면 매수"
    rules = [
        {
            "target": "each",
            "exit": None,
            "entry": _cond(["5:매수"], [(None, "2:RSI", "4:아래로 내려가면", "3:30")]),
        }
    ]
    for answer, expected in (("reverse", "cross_above"), ("take_profit_10", ">="), ("hold", None)):
        built = _build(text, rules, universe=["1:현대차"], overrides={"exit:rules[0].exit": answer})
        exit_ = built.spec.model_dump(mode="json")["rules"][0]["exit"]
        assert (exit_ or {}).get("op") == expected


def test_weights():
    built = _build(
        "삼성전자 40% KODEX 200 30% 현대차 30% 매주 리밸런싱",
        universe=["1:삼성전자", "3:KODEX 200", "5:현대차"],
        rebalance=["7:매주"],
        weights=[
            {"asset": "1:삼성전자", "value": "2:40%"},
            {"asset": "3:KODEX 200", "value": "4:30%"},
            {"asset": "5:현대차", "value": "6:30%"},
        ],
    )
    spec = built.spec.model_dump(mode="json")
    assert spec["weights"] == {"005930": 0.4, "069500": 0.3, "005380": 0.3}
    assert spec["rebalance"] == "RB_WEEKLY"

    over = dict(
        universe=["1:삼성전자", "3:SK하이닉스"],
        weights=[{"asset": "1:삼성전자", "value": "2:70%"}, {"asset": "3:SK하이닉스", "value": "4:50%"}],
    )
    built = _build("삼성전자 70% SK하이닉스 50%", **over)
    assert [a.kind for a in built.asks] == ["weights_over_100"]
    scaled = _build("삼성전자 70% SK하이닉스 50%", overrides={"weights_over_100:weights": "scale"}, **over)
    assert round(sum(scaled.spec.weights.values()), 9) == 1

    half = _build(
        "삼성전자랑 KODEX 200 반반, 배당은 빼고",
        universe=["1:삼성전자", "2:KODEX 200"],
        weights=[{"asset": None, "value": "3:반반"}],
    )
    assert half.spec.weights == {"005930": 0.5, "069500": 0.5}
    assert half.spec.backtest.include_dividends is False and "dividends" not in half.defaults_used
    assert half.spec.rebalance == "RB_MONTHLY_FIRST" and "rebalance" in half.defaults_used


def test_rebalance_period_and_capital():
    built = _build(
        "삼성전자 KODEX 200 반기마다 리밸런싱",
        universe=["1:삼성전자", "2:KODEX 200"],
        rebalance=["3:반기마다", "4:리밸런싱"],
    )
    assert [(a.kind, a.context["said"]) for a in built.asks] == [("rebalance_not_allowed", "half_year")]
    built = _build(
        "2024년 한 해만 삼성전자, 시작금액 1억",
        universe=["2:삼성전자"],
        backtest={"period": ["1:2024년 한 해만"], "capital": ["3:시작금액", "4:1억"], "dividends": []},
    )
    bt = built.spec.backtest
    assert (bt.start.isoformat(), bt.end.isoformat(), bt.capital) == ("2024-01-01", "2024-12-31", 100_000_000)


def test_universe_quotes_and_unresolved_spans():
    built = _build(
        "RSI 30 밑에서 사고 70 위에서 파는 전략",
        [
            {
                "target": "each",
                "entry": _cond(["4:사고"], [(None, "1:RSI", "3:밑에서", "2:30")]),
                "exit": _cond(["7:파는"], [(None, None, "6:위에서", "5:70")]),
            }
        ],
    )
    assert [(a.kind, a.context["reason"]) for a in built.asks] == [("universe", "none")]
    answered = _build(
        "RSI 30 밑에서 사고 70 위에서 파는 전략",
        built_rules := [
            {
                "target": "each",
                "entry": _cond(["4:사고"], [(None, "1:RSI", "3:밑에서", "2:30")]),
                "exit": _cond(["7:파는"], [(None, None, "6:위에서", "5:70")]),
            }
        ],
        overrides={"universe:universe": ["069500"]},
    )
    assert built_rules and answered.spec.universe.tickers == ["069500"]

    quoted = _build(
        "삼성전자 많이 빠지면 사",
        universe=["1:삼성전자"],
        quotes=[
            {"text": "많이", "slot": "entry", "state": "ambiguous"},
            {"text": "삼성전자", "slot": "universe", "state": "unsupported"},
        ],
    )
    assert [a.kind for a in quoted.asks] == ["ambiguous_quote"]
    assert [r.code for r in quoted.rejects] == ["UNSUPPORTED_ASSET"]

    issue = pairing.EvidenceIssue("unused_span", "KODEX 200", 2)
    left = _build("삼성전자 KODEX 200", universe=["1:삼성전자"], issues=(issue,))
    assert [(a.kind, a.tier) for a in left.asks] == [("evidence_unused", 1)]
    ignored = _build(
        "삼성전자 KODEX 200",
        universe=["1:삼성전자"],
        issues=(issue,),
        overrides={"evidence_unused:span2": "ignore"},
    )
    assert ignored.asks == [] and ignored.spec is not None


def test_reverse():
    cond = {
        "all": [
            {"left": {"indicator": "rsi", "window": 14, "of": "self"}, "op": "<", "right": {"value": 30}},
            {
                "left": {"indicator": "close", "of": "self"},
                "op": "cross_above",
                "right": {"indicator": "ma", "window": 5, "of": "self"},
            },
        ]
    }
    flipped = assemble._reverse(cond)
    assert [c["op"] for c in flipped["any"]] == [">=", "cross_below"]
