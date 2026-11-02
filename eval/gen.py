# 평가 데이터셋 초안 생성기. dataset.jsonl, review.csv 를 만든다.
# 경로는 모두 이 파일 위치 기준(ref/, cache/, ../data/). 네트워크를 쓰지 않는다.
import csv
import json
import random
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 저장소에 __pycache__ 를 남기지 않는다
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve import dividend_flag, resolve  # noqa: E402

HERE = Path(__file__).resolve().parent
REF = HERE / "ref"
ETF_MASTER = HERE.parent / "data" / "listing.csv"

NAMES = {}
for m in ("KOSPI", "KOSDAQ"):
    for r in csv.DictReader(open(REF / f"sector_{m}_20230102.csv", encoding="utf-8-sig")):
        NAMES[r["종목코드"]] = r["종목명"]
for r in csv.DictReader(open(ETF_MASTER, encoding="utf-8-sig")):
    NAMES[r["ticker"]] = r["name"]

K, X, Q = "KOSPI200", "VIX", "KOSDAQ"


def I(n, w=None, of="self"):
    d = {"indicator": n}
    if w is not None:
        d["window"] = w
    d["of"] = of
    return d


def V(x):
    return {"value": x}


def C(l, op, r):
    return {"left": l, "op": op, "right": r}


def ALL(*c):
    return {"all": list(c)}


def ANY(*c):
    return {"any": list(c)}


def R(entry, exit, target="each"):
    return {"target": target, "entry": entry, "exit": exit}


def T(*c):
    return {"tickers": list(c)}


def SEL(kind, value, market, count, method):
    return {"selector": {"kind": kind, "value": value, "market": market, "count": count, "method": method}}


def BT(start="2023-01-01", end="2025-12-31", capital=10_000_000, div=False):
    return {"start": start, "end": end, "capital": capital, "include_dividends": div}


RECS = []


def ok(text, universe, weights, rebalance, rules, bt, ev, tags, note="", constraint=None, dflt=()):
    spec = {
        "universe": universe,
        "weights": weights,
        "rebalance": rebalance,
        "rules": rules,
        "constraint": constraint or {},
        "backtest": bt,
    }
    d = list(dflt)
    if bt["start"] == "2023-01-01" and bt["end"] == "2025-12-31":
        d.append("period")
    if bt["capital"] == 10_000_000:
        d.append("capital")
    d.append("costs")
    if dividend_flag(text) is False:
        d.append("dividends")
    if rules and "weights" not in d:
        d.insert(0, "weights")
    if "selector" in universe:
        pick, approx, missing = resolve(universe["selector"])
        extra = f"정답 종목[{','.join(pick)}] = " + ", ".join(NAMES[t] for t in pick) + "."
        if approx:
            extra += f" 시총 근사(2023-01-02 파일 값): {','.join(approx)}."
        if missing:
            extra += f" 기준 기간 데이터가 다 없어 후보에서 뺀 종목 {len(missing)}개."
        note = (note + " " if note else "") + extra
    RECS.append({
        "type": "ok", "text": text, "expected_spec": spec, "defaults_used": d,
        "expected_ask": None, "expected_reject": None, "evidence": ev,
        "tags": tags, "note": note,
    })


def ask(text, slots, ev, tags, note=""):
    RECS.append({
        "type": "ask", "text": text, "expected_spec": None, "defaults_used": None,
        "expected_ask": [{"slot": s, "why": w} for s, w in slots],
        "expected_reject": None, "evidence": ev, "tags": tags, "note": note,
    })


def rej(text, code, evidence, tags, note=""):
    RECS.append({
        "type": "reject", "text": text, "expected_spec": None, "defaults_used": None,
        "expected_ask": None, "expected_reject": {"code": code, "evidence": evidence},
        "evidence": {}, "tags": tags, "note": note,
    })


# ---------------------------------------------------------------- ok: 자산 배분형 25
ok("삼성전자랑 SK하이닉스, TIGER 미국S&P500 세 개 똑같이 나눠서 사두고 매달 초에 비중 다시 맞춰줘",
   T("005930", "000660", "360750"), "equal", "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "삼성전자랑 SK하이닉스, TIGER 미국S&P500", "weights": "똑같이 나눠서", "rebalance": "매달 초에"},
   ["mixed", "allocation", "casual"], "매달 초 = 매달 첫 거래일(RB_MONTHLY_FIRST)로 봄")
ok("KODEX 200 60%, KODEX 국고채3년 40%로 분기마다 리밸런싱",
   T("069500", "114260"), {"069500": 0.6, "114260": 0.4}, "RB_QUARTERLY", [], BT(),
   {"universe": "KODEX 200 60%, KODEX 국고채3년 40%", "weights": "60%, KODEX 국고채3년 40%", "rebalance": "분기마다"},
   ["etf", "allocation", "rebalance_quarterly"])
ok("ACE 미국배당다우존스 70 에이스 금현물 30 으로 해주세요",
   T("402970", "411060"), {"402970": 0.7, "411060": 0.3}, "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "ACE 미국배당다우존스 70 에이스 금현물 30", "weights": "70 에이스 금현물 30"},
   ["etf", "allocation", "casual", "defaults", "etf_name_dividend"],
   "'배당'은 종목명(ACE 미국배당다우존스) 속 낱말일 뿐 배당 요구가 아니라 배당 반영 안 함(기본값). '에이스 금현물'은 ACE KRX금현물 하나뿐. 숫자에 % 없음. 리밸런싱 기본값",
   dflt=["rebalance"])
ok("삼성전자 40% KODEX 200 30% KIWOOM 국고채10년 30% 매주 리밸런싱",
   T("005930", "069500", "148070"), {"005930": 0.4, "069500": 0.3, "148070": 0.3}, "RB_WEEKLY", [], BT(),
   {"universe": "삼성전자 40% KODEX 200 30% KIWOOM 국고채10년 30%", "weights": "40% KODEX 200 30% KIWOOM 국고채10년 30%", "rebalance": "매주"},
   ["mixed", "allocation", "rebalance_weekly"], "RB_WEEKLY = 매주 마지막 거래일(2026-11-02 확정)")
ok("TIGER 미국S&P500, TIGER 미국나스닥100, ACE KRX금현물, KIWOOM 국고채10년 네 개 25%씩 넣고 비중이 5%p 넘게 벗어나면 다시 맞춰줘",
   T("360750", "133690", "411060", "148070"), {"360750": 0.25, "133690": 0.25, "411060": 0.25, "148070": 0.25},
   "RB_THRESHOLD_5", [], BT(),
   {"universe": "TIGER 미국S&P500, TIGER 미국나스닥100, ACE KRX금현물, KIWOOM 국고채10년", "weights": "25%씩", "rebalance": "비중이 5%p 넘게 벗어나면"},
   ["etf", "allocation", "rebalance_threshold"])
ok("배당 많이 주는 KT&G, 기업은행, SK텔레콤, 삼성화재 네 종목 똑같이 담고 배당금도 포함해서 돌려봐",
   T("033780", "024110", "017670", "000810"), "equal", "RB_MONTHLY_FIRST", [], BT(div=True),
   {"universe": "KT&G, 기업은행, SK텔레콤, 삼성화재", "weights": "똑같이", "dividends": "배당금도 포함해서"},
   ["stock", "allocation", "dividend", "defaults"], "리밸런싱 언급 없음, 기본값", dflt=["rebalance"])
ok("월배당 받고 싶어서요. TIGER 리츠부동산인프라 50% ACE 미국배당다우존스 50%, 매달 중순에 리밸런싱",
   T("329200", "402970"), {"329200": 0.5, "402970": 0.5}, "RB_MONTHLY_MID", [], BT(div=True),
   {"universe": "TIGER 리츠부동산인프라 50% ACE 미국배당다우존스 50%", "weights": "50% ACE 미국배당다우존스 50%",
    "rebalance": "매달 중순에", "dividends": "월배당"},
   ["etf", "allocation", "dividend", "rebalance_mid"], "'월배당' 낱말로 배당 반영")
ok("코스피 주식 중에 시가총액 상위 10종목 균등하게 담고 분기 리밸런싱",
   SEL("asset_type", "stock", "KOSPI", 10, "market_cap"), "equal", "RB_QUARTERLY", [], BT(),
   {"universe": "코스피 주식 중에", "count": "상위 10종목", "method": "시가총액", "weights": "균등하게", "rebalance": "분기 리밸런싱"},
   ["stock", "allocation", "selector", "rebalance_quarterly"],
   "'주식'이라 우선주·스팩 제외(삼성전자우 빠지고 카카오가 들어옴). 시총은 2022-12-29 값")
ok("코스닥 종목 중 거래대금 많은 순으로 5개 골라서 똑같이 사고 매달 첫 거래일에 비중 조정",
   SEL("asset_type", "stock", "KOSDAQ", 5, "trading_value"), "equal", "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "코스닥 종목 중", "count": "5개", "method": "거래대금 많은 순으로", "weights": "똑같이", "rebalance": "매달 첫 거래일에"},
   ["stock", "allocation", "selector", "rebalance_monthly_first"],
   "'종목'이라고만 해서 주식으로 봄(ETF 라는 말 없음). 거래대금은 직전 20거래일 평균")
ok("코스피 보험 업종에서 시총 큰 순으로 5개, 같은 비중, 매주 리밸런싱",
   SEL("sector", "보험", "KOSPI", 5, "market_cap"), "equal", "RB_WEEKLY", [], BT(),
   {"universe": "코스피 보험 업종에서", "count": "5개", "method": "시총 큰 순으로", "weights": "같은 비중", "rebalance": "매주 리밸런싱"},
   ["stock", "allocation", "selector", "rebalance_weekly"],
   "2023-01-02 보험 시총 3위 메리츠화재(000060)는 2023년 상장폐지라 가격 데이터가 없어 후보에서 빠짐. 그 다음 한화생명이 들어옴")
ok("코스피 은행 업종 종목 전부 같은 비중으로 담아줘",
   SEL("sector", "은행", "KOSPI", None, None), "equal", "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "코스피 은행 업종 종목 전부", "weights": "같은 비중으로"},
   ["stock", "allocation", "selector", "defaults"],
   "2023-01-02 코스피 은행 업종은 기업은행·제주은행·카카오뱅크 3개뿐(4대 금융지주는 기타금융). 5개 미만이고 '전부'라 되묻지 않음. 사용자가 금융지주를 기대했을 수 있음",
   dflt=["rebalance"])
ok("KODEX 200 50, KODEX 단기채권 30, 삼성전자 20. 리밸런싱은 매달 15일쯤으로",
   T("069500", "153130", "005930"), {"069500": 0.5, "153130": 0.3, "005930": 0.2}, "RB_MONTHLY_MID", [], BT(),
   {"universe": "KODEX 200 50, KODEX 단기채권 30, 삼성전자 20", "weights": "50, KODEX 단기채권 30, 삼성전자 20", "rebalance": "매달 15일쯤"},
   ["mixed", "allocation", "rebalance_mid"],
   "'KODEX 단기채권'은 153130, 'KODEX 단기채권PLUS'(214980)와 헷갈릴 수 있음. 15일쯤 = 매달 중순 블록")
ok("삼성전자랑 KODEX 200 반반 투자하면 어때? 배당은 빼고 계산해줘",
   T("005930", "069500"), {"005930": 0.5, "069500": 0.5}, "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "삼성전자랑 KODEX 200", "weights": "반반", "dividends": "배당은 빼고"},
   ["mixed", "allocation", "defaults", "casual", "half_half", "dividend_negated"],
   "'반반' = 0.5/0.5(표현 표). 배당 낱말이 있지만 부정문이라 배당 반영 안 함. 리밸런싱 기본값", dflt=["rebalance"])
ok("네이버 카카오 크래프톤 엔씨소프트 하이브 똑같이 사고 한 종목이 25% 넘지 않게 해줘, 분기마다 리밸런싱",
   T("035420", "035720", "259960", "036570", "352820"), "equal", "RB_QUARTERLY", [], BT(),
   {"universe": "네이버 카카오 크래프톤 엔씨소프트 하이브", "weights": "똑같이", "constraint": "한 종목이 25% 넘지 않게", "rebalance": "분기마다"},
   ["stock", "allocation", "max_weight", "rebalance_quarterly", "casual"],
   "균등 20%라 상한 25%는 리밸런싱 시점엔 묶이지 않음. 리밸런싱 사이 25% 초과를 막는지는 규칙에 없음",
   constraint={"max_weight_per_asset": 0.25})
ok("5천만원으로 2024년 1월부터 2025년 12월까지 TIGER 미국나스닥100 40 KODEX 국고채3년 40 ACE KRX금현물 20",
   T("133690", "114260", "411060"), {"133690": 0.4, "114260": 0.4, "411060": 0.2}, "RB_MONTHLY_FIRST", [],
   BT(start="2024-01-01", capital=50_000_000),
   {"capital": "5천만원으로", "period": "2024년 1월부터 2025년 12월까지",
    "universe": "TIGER 미국나스닥100 40 KODEX 국고채3년 40 ACE KRX금현물 20", "weights": "40 KODEX 국고채3년 40 ACE KRX금현물 20"},
   ["etf", "allocation", "custom_period", "custom_capital"], "기간·금액 직접 지정, 리밸런싱 기본값", dflt=["rebalance"])
ok("삼성전자 50% LG에너지솔루션 25% 현대차 25%, 비중이 5%포인트 이상 틀어지면 리밸런싱",
   T("005930", "373220", "005380"), {"005930": 0.5, "373220": 0.25, "005380": 0.25}, "RB_THRESHOLD_5", [], BT(),
   {"universe": "삼성전자 50% LG에너지솔루션 25% 현대차 25%", "weights": "50% LG에너지솔루션 25% 현대차 25%", "rebalance": "비중이 5%포인트 이상 틀어지면"},
   ["stock", "allocation", "rebalance_threshold"], "'5%p 이상'과 블록의 '5%p 이탈' 경계(=5.0%p) 처리 일치 여부 확인")
ok("kodex200 50퍼 kodex미국나스닥100 50퍼 매월초 리밸",
   T("069500", "379810"), {"069500": 0.5, "379810": 0.5}, "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "kodex200 50퍼 kodex미국나스닥100 50퍼", "weights": "50퍼 kodex미국나스닥100 50퍼", "rebalance": "매월초 리밸"},
   ["etf", "allocation", "casual", "typo", "rebalance_monthly_first"], "소문자·띄어쓰기 없음·'퍼' 줄임말")
ok("TIGER 미국나스닥100이랑 KIWOOM 국고채10년 반반, 매주 맞춰주고 분배금은 빼고 계산",
   T("133690", "148070"), {"133690": 0.5, "148070": 0.5}, "RB_WEEKLY", [], BT(),
   {"universe": "TIGER 미국나스닥100이랑 KIWOOM 국고채10년", "weights": "반반", "rebalance": "매주 맞춰주고",
    "dividends": "분배금은 빼고"},
   ["etf", "allocation", "half_half", "dividend_negated", "rebalance_weekly"],
   "'반반' = 0.5/0.5. '분배금은 빼고'는 부정문이라 배당 반영 안 함. 매주 = 매주 마지막 거래일")
ok("코스닥 반도체 업종 시총 상위 10개 균등, 매달 1일 리밸런싱",
   SEL("sector", "반도체", "KOSDAQ", 10, "market_cap"), "equal", "RB_MONTHLY_FIRST", [], BT(),
   {"universe": "코스닥 반도체 업종", "count": "상위 10개", "method": "시총", "weights": "균등", "rebalance": "매달 1일"},
   ["stock", "allocation", "selector", "kosdaq", "rebalance_monthly_first"],
   "코스닥 2023-01-02 에는 '반도체' 업종이 있어 ok. '매달 1일'은 휴장일이면 첫 거래일로 봄")
ok("PLUS 고배당주 40% KODEX 종합채권(AA-이상)액티브 40% TIGER CD금리투자KIS(합성) 20%, 분배금 재투자 포함하고 5%p 벗어나면 리밸런싱",
   T("161510", "273130", "357870"), {"161510": 0.4, "273130": 0.4, "357870": 0.2}, "RB_THRESHOLD_5", [], BT(div=True),
   {"universe": "PLUS 고배당주 40% KODEX 종합채권(AA-이상)액티브 40% TIGER CD금리투자KIS(합성) 20%",
    "weights": "40% KODEX 종합채권(AA-이상)액티브 40% TIGER CD금리투자KIS(합성) 20%",
    "dividends": "분배금 재투자 포함", "rebalance": "5%p 벗어나면"},
   ["etf", "allocation", "dividend", "rebalance_threshold"], "'분배금'으로 배당 반영. 재투자 방식(현금 보유 후 다음 리밸런싱 vs 즉시)은 엔진 규칙")
ok("삼성전자 30%, TIGER 미국S&P500 30% 넣고 나머지는 현금으로 들고 있을래. 분기마다 맞춰",
   T("005930", "360750"), {"005930": 0.3, "360750": 0.3}, "RB_QUARTERLY", [], BT(),
   {"universe": "삼성전자 30%, TIGER 미국S&P500 30%", "weights": "30%, TIGER 미국S&P500 30% 넣고 나머지는 현금", "rebalance": "분기마다"},
   ["mixed", "allocation", "cash", "rebalance_quarterly"],
   "비중 합 60%, 나머지 40% 현금(합 < 100% 는 나머지 현금으로 허용)")
ok("코스피 전기가스 업종에서 덜 출렁이는 순으로 5개 골라 똑같이, 분기 리밸",
   SEL("sector", "전기·가스", "KOSPI", 5, "low_vol"), "equal", "RB_QUARTERLY", [], BT(),
   {"universe": "코스피 전기가스 업종에서", "count": "5개", "method": "덜 출렁이는 순으로", "weights": "똑같이", "rebalance": "분기 리밸"},
   ["stock", "allocation", "selector", "casual", "rebalance_quarterly"],
   "'전기가스'를 업종명 '전기·가스'로 맞춤(10종목). 변동성은 직전 20거래일 일간 수익률 표준편차")
ok("코스피 주식 중에 최근 많이 오른 순으로 10개 뽑아서 균등하게, 매달 중순 리밸런싱",
   SEL("asset_type", "stock", "KOSPI", 10, "momentum"), "equal", "RB_MONTHLY_MID", [], BT(),
   {"universe": "코스피 주식 중에", "count": "10개", "method": "최근 많이 오른 순으로", "weights": "균등하게", "rebalance": "매달 중순"},
   ["stock", "allocation", "selector", "rebalance_mid"],
   "상승률은 직전 60거래일. 고르기는 시작할 때 한 번이라 매달 종목을 다시 고르지 않음(사용자 기대와 다를 수 있음). 소형 급등주가 많이 뽑힘")
ok("TIGER 200 IT 30 KODEX 2차전지산업 30 TIGER 미국필라델피아반도체나스닥 40 매주 리밸런싱",
   T("139260", "305720", "381180"), {"139260": 0.3, "305720": 0.3, "381180": 0.4}, "RB_WEEKLY", [], BT(),
   {"universe": "TIGER 200 IT 30 KODEX 2차전지산업 30 TIGER 미국필라델피아반도체나스닥 40",
    "weights": "30 KODEX 2차전지산업 30 TIGER 미국필라델피아반도체나스닥 40", "rebalance": "매주 리밸런싱"},
   ["etf", "allocation", "rebalance_weekly"], "'TIGER 200 IT 30'에서 숫자 붙어 있어 종목명/비중 경계 혼동 가능")
ok("고배당주로 KB금융 신한지주 하나금융지주 우리금융지주 똑같이, 배당 포함, 분기 리밸런싱, 시작금액 1억",
   T("105560", "055550", "086790", "316140"), "equal", "RB_QUARTERLY", [], BT(capital=100_000_000, div=True),
   {"universe": "KB금융 신한지주 하나금융지주 우리금융지주", "weights": "똑같이", "dividends": "배당 포함",
    "rebalance": "분기 리밸런싱", "capital": "시작금액 1억"},
   ["stock", "allocation", "dividend", "custom_capital", "rebalance_quarterly"])

# ---------------------------------------------------------------- ok: 매매 조건형 35
ok("삼성전자 20일 이평선 뚫으면 사고 다시 깨지면 팔아",
   T("005930"), "equal", None,
   [R(C(I("close"), "cross_above", I("ma", 20)), C(I("close"), "cross_below", I("ma", 20)))], BT(),
   {"universe": "삼성전자", "entry": "20일 이평선 뚫으면 사고", "exit": "다시 깨지면 팔아"},
   ["stock", "rules", "cross"], "'다시 깨지면'의 대상은 앞의 20일 이평선")
ok("KODEX 200 과매도면 사고 과매수면 팔아",
   T("069500"), "equal", None,
   [R(C(I("rsi", 14), "<", V(30)), C(I("rsi", 14), ">", V(70)))], BT(),
   {"universe": "KODEX 200", "entry": "과매도면 사고", "exit": "과매수면 팔아"},
   ["etf", "rules", "level", "defaults"], "과매도/과매수 = RSI14 30/70 기본값. 상태 표현이라 < / > 로 둠(<= 와 차이는 RSI 정확히 30일 때뿐)",
   dflt=["rsi_window", "rsi_level"])
ok("SK하이닉스랑 TIGER 반도체TOP10, RSI 30 밑으로 내려가면 사고 70 넘어가면 팔기",
   T("000660", "396500"), "equal", None,
   [R(C(I("rsi", 14), "cross_below", V(30)), C(I("rsi", 14), "cross_above", V(70)))], BT(),
   {"universe": "SK하이닉스랑 TIGER 반도체TOP10", "entry": "RSI 30 밑으로 내려가면 사고", "exit": "70 넘어가면 팔기"},
   ["mixed", "rules", "cross"], "'내려가면/넘어가면'을 사건(cross)으로 봄. '밑으로'가 섞여 상태(<)로 읽을 여지 있음",
   dflt=["rsi_window", "subject"])
ok("코스피가 60일 이동평균 위에 있으면 KODEX 200 사고, 60일 이평 아래로 떨어지면 팔아",
   T("069500"), "equal", None,
   [R(C(I("close", of=K), ">", I("ma", 60, K)), C(I("close", of=K), "cross_below", I("ma", 60, K)))], BT(),
   {"universe": "KODEX 200", "subject": "코스피가", "entry": "60일 이동평균 위에 있으면", "exit": "60일 이평 아래로 떨어지면 팔아"},
   ["etf", "rules", "market_condition", "level", "cross"], "'코스피' = KOSPI200 규약. 팔 조건의 주어도 앞 문장의 코스피로 봄")
ok("VIX 30 넘으면 TIGER 미국S&P500 사고 VIX 20 밑으로 내려오면 팔아",
   T("360750"), "equal", None,
   [R(C(I("vix", of=X), "cross_above", V(30)), C(I("vix", of=X), "cross_below", V(20)))], BT(),
   {"universe": "TIGER 미국S&P500", "entry": "VIX 30 넘으면", "exit": "VIX 20 밑으로 내려오면 팔아"},
   ["etf", "rules", "market_condition", "cross", "vix"], "국내 t일 판단에는 미국 t-1일 VIX 종가를 씀")
ok("현대차 RSI 30 이하에서 사고, 산 가격보다 10% 오르면 익절 5% 빠지면 손절",
   T("005380"), "equal", None,
   [R(C(I("rsi", 14), "<=", V(30)),
      ANY(C(I("entry_return"), ">=", V(0.10)), C(I("entry_return"), "<=", V(-0.05))))], BT(),
   {"universe": "현대차", "entry": "RSI 30 이하에서 사고", "exit": "산 가격보다 10% 오르면 익절 5% 빠지면 손절"},
   ["stock", "rules", "level", "stop_loss", "take_profit"], "entry_return 은 상태 비교(>=, <=)로 둠. 매수 다음날 바로 갭으로 넘는 경우 cross 와 결과가 다를 수 있음",
   dflt=["rsi_window"])
ok("TIGER 미국S&P500 20일선 위에 있고 RSI 50 넘으면 사, 20일선 아래로 빠지면 팔아",
   T("360750"), "equal", None,
   [R(ALL(C(I("close"), ">", I("ma", 20)), C(I("rsi", 14), "cross_above", V(50))),
      C(I("close"), "cross_below", I("ma", 20)))], BT(),
   {"universe": "TIGER 미국S&P500", "entry": "20일선 위에 있고 RSI 50 넘으면 사", "exit": "20일선 아래로 빠지면 팔아"},
   ["etf", "rules", "all", "level", "cross", "n_day_line", "new_verb"],
   "'20일선' = ma20(표현 표). '빠지면' = cross_below(동사 표). '20일선 위'의 비교 대상은 종가", dflt=["rsi_window"])
ok("NAVER 5일선이 20일선 돌파하면 사고, 다시 아래로 깨지거나 산값 대비 7% 손실이면 팔아",
   T("035420"), "equal", None,
   [R(C(I("ma", 5), "cross_above", I("ma", 20)),
      ANY(C(I("ma", 5), "cross_below", I("ma", 20)), C(I("entry_return"), "<=", V(-0.07))))], BT(),
   {"universe": "NAVER", "entry": "5일선이 20일선 돌파하면 사고", "exit": "다시 아래로 깨지거나 산값 대비 7% 손실이면 팔아"},
   ["stock", "rules", "cross", "any", "stop_loss", "n_day_line"], "기간을 밝힌 골든크로스라 ok(골든크로스라는 낱말만 쓰면 되묻기). 5일선·20일선 = ma5·ma20")
ok("에코프로 거래량이 20일 평균의 3배 넘게 터지고 종가가 20일 이평 위면 사, 20일 이평 깨지면 팔고",
   T("086520"), "equal", None,
   [R(ALL(C(I("volume_ratio", 20), "cross_above", V(3)), C(I("close"), ">", I("ma", 20))),
      C(I("close"), "cross_below", I("ma", 20)))], BT(),
   {"universe": "에코프로", "entry": "거래량이 20일 평균의 3배 넘게 터지고 종가가 20일 이평 위면 사", "exit": "20일 이평 깨지면 팔고"},
   ["stock", "rules", "all", "volume", "cross", "kosdaq", "new_verb"],
   "'터지면' = cross_above(동사 표). 에코프로는 코스닥 업종상 '금융'(지주사)")
ok("PLUS 고배당주 20일 수익률이 -10% 이하면 사고 +5% 이상이면 팔아",
   T("161510"), "equal", None,
   [R(C(I("ret", 20), "<=", V(-0.10)), C(I("ret", 20), ">=", V(0.05)))], BT(),
   {"universe": "PLUS 고배당주", "entry": "20일 수익률이 -10% 이하면 사고", "exit": "+5% 이상이면 팔아"},
   ["etf", "rules", "level", "return", "etf_name_dividend"],
   "'고배당'은 종목명 속 낱말이라 배당 반영 안 함(기본값)")
ok("코스닥 반도체 업종에서 거래대금 많은 순으로 5개 골라서 RSI 30 밑이면 사고 RSI 70 위면 팔아",
   SEL("sector", "반도체", "KOSDAQ", 5, "trading_value"), "equal", None,
   [R(C(I("rsi", 14), "<", V(30)), C(I("rsi", 14), ">", V(70)))], BT(),
   {"universe": "코스닥 반도체 업종에서", "count": "5개", "method": "거래대금 많은 순으로", "entry": "RSI 30 밑이면 사고", "exit": "RSI 70 위면 팔아"},
   ["stock", "rules", "selector", "level", "kosdaq"], "거래대금은 직전 20거래일 평균. 상장폐지 종목은 후보에서 빠짐", dflt=["rsi_window", "subject"])
ok("코스피 지수가 20일 이평 위일 때만 삼성전자 RSI 40 밑이면 사고 RSI 65 넘으면 팔아",
   T("005930"), "equal", None,
   [R(ALL(C(I("close", of=K), ">", I("ma", 20, K)), C(I("rsi", 14), "<", V(40))),
      C(I("rsi", 14), "cross_above", V(65)))], BT(),
   {"universe": "삼성전자", "entry": "코스피 지수가 20일 이평 위일 때만 삼성전자 RSI 40 밑이면 사고", "exit": "RSI 65 넘으면 팔아"},
   ["stock", "rules", "market_condition", "all", "level", "cross"],
   "'코스피 지수' = KOSPI200 로 매핑(실제 KOSPI 종합지수와 다름). 시장 필터가 팔 조건에도 걸리는지는 문장상 아님",
   dflt=["rsi_window"])
ok("삼성전자는 RSI 30 아래면 사서 70 위면 팔고, KODEX 국고채3년은 60일 이평 뚫으면 사서 깨지면 팔아",
   T("005930", "114260"), "equal", None,
   [R(C(I("rsi", 14), "<", V(30)), C(I("rsi", 14), ">", V(70)), target="005930"),
    R(C(I("close"), "cross_above", I("ma", 60)), C(I("close"), "cross_below", I("ma", 60)), target="114260")], BT(),
   {"universe": "삼성전자는 RSI 30 아래면 사서 70 위면 팔고, KODEX 국고채3년은",
    "entry": "RSI 30 아래면 사서", "exit": "70 위면 팔고"},
   ["mixed", "rules", "per_asset_rule", "level", "cross"], "종목별로 다른 규칙. 자리는 1/2씩", dflt=["rsi_window"])
ok("하이닉스 이십일 이평선 돌파시 매수 이탈시 매도ㅇㅇ",
   T("000660"), "equal", None,
   [R(C(I("close"), "cross_above", I("ma", 20)), C(I("close"), "cross_below", I("ma", 20)))], BT(),
   {"universe": "하이닉스", "entry": "이십일 이평선 돌파시 매수", "exit": "이탈시 매도"},
   ["stock", "rules", "cross", "casual", "new_verb"], "'하이닉스'=SK하이닉스, 숫자를 한글(이십일)로 씀. '이탈' = cross_below(동사 표)")
ok("VIX가 25 이상이면 KODEX 200 다 팔고, VIX가 20 아래로 내려오면 다시 사",
   T("069500"), "equal", None,
   [R(C(I("vix", of=X), "cross_below", V(20)), C(I("vix", of=X), ">=", V(25)))], BT(),
   {"universe": "KODEX 200", "exit": "VIX가 25 이상이면 KODEX 200 다 팔고", "entry": "VIX가 20 아래로 내려오면 다시 사"},
   ["etf", "rules", "market_condition", "vix", "cross", "level"],
   "파는 조건이 먼저 나옴. '다시 사'는 처음엔 들고 있다는 뜻일 수 있으나 규칙상 조건형은 현금에서 시작해 20 하향 돌파 때 처음 삼. 시작 보유 여부 확정 필요")
ok("삼성전자랑 SK하이닉스 60일 이평 위로 올라서면 사고 아래로 내려가면 팔아. 한 종목에 30% 넘게 넣지 마",
   T("005930", "000660"), "equal", None,
   [R(C(I("close"), "cross_above", I("ma", 60)), C(I("close"), "cross_below", I("ma", 60)))], BT(),
   {"universe": "삼성전자랑 SK하이닉스", "entry": "60일 이평 위로 올라서면 사고", "exit": "아래로 내려가면 팔아", "constraint": "한 종목에 30% 넘게 넣지 마"},
   ["stock", "rules", "cross", "max_weight", "new_verb"],
   "자리 1/2=50% 가 상한 30% 에 걸려 종목당 30% 만 사고 나머지 현금(남는 돈 현금 규칙). '올라서면' = cross_above(동사 표)",
   constraint={"max_weight_per_asset": 0.3}, dflt=["subject"])
ok("TIGER 미국나스닥100 120일 이평 위면 들고 있고 아래면 팔아",
   T("133690"), "equal", None,
   [R(C(I("close"), ">", I("ma", 120)), C(I("close"), "<", I("ma", 120)))], BT(),
   {"universe": "TIGER 미국나스닥100", "entry": "120일 이평 위면 들고 있고", "exit": "아래면 팔아"},
   ["etf", "rules", "level"], "'들고 있고'를 사는 조건으로 해석")
ok("코스피 RSI가 30 밑이면 KODEX 레버리지 사고 코스피 RSI 50 넘으면 팔아",
   T("122630"), "equal", None,
   [R(C(I("rsi", 14, K), "<", V(30)), C(I("rsi", 14, K), "cross_above", V(50)))], BT(),
   {"universe": "KODEX 레버리지", "subject": "코스피 RSI가", "entry": "30 밑이면", "exit": "코스피 RSI 50 넘으면 팔아"},
   ["etf", "rules", "market_condition", "level", "cross", "leverage"], dflt=["rsi_window"])
ok("셀트리온 거래량이 20일 평균 2배 넘으면 사고 5거래일 뒤에 팔아",
   T("068270"), "equal", None,
   [R(C(I("volume_ratio", 20), "cross_above", V(2)), C(I("holding_days"), ">=", V(5)))], BT(),
   {"universe": "셀트리온", "entry": "거래량이 20일 평균 2배 넘으면 사고", "exit": "5거래일 뒤에 팔아"},
   ["stock", "rules", "cross", "volume", "holding_days"],
   "holding_days >= 5. 체결일(t+1 시가)을 0일로 셈. 체결 후 5번째 거래일 종가에 판정, 그다음 날 시가에 팖")
ok("삼성바이오로직스나 셀트리온, RSI 30 밑이거나 60일 수익률이 -20% 이하면 사고 RSI 60 넘으면 팔아",
   T("207940", "068270"), "equal", None,
   [R(ANY(C(I("rsi", 14), "<", V(30)), C(I("ret", 60), "<=", V(-0.20))), C(I("rsi", 14), "cross_above", V(60)))], BT(),
   {"universe": "삼성바이오로직스나 셀트리온", "entry": "RSI 30 밑이거나 60일 수익률이 -20% 이하면 사고", "exit": "RSI 60 넘으면 팔아"},
   ["stock", "rules", "any", "level", "cross"],
   "'A나 B'를 두 종목 모두로 해석(팀 확정). 같은 날 사고 팔 조건이 함께 참이면 팔기 우선",
   dflt=["rsi_window", "subject"])
ok("삼성전자, KODEX 200, TIGER 미국S&P500 각각 20일 이평 위로 올라오면 사고 20일 이평 밑으로 빠지면 팔아. 단 VIX 30 넘으면 무조건 팔아",
   T("005930", "069500", "360750"), "equal", None,
   [R(C(I("close"), "cross_above", I("ma", 20)),
      ANY(C(I("close"), "cross_below", I("ma", 20)), C(I("vix", of=X), ">", V(30))))], BT(),
   {"universe": "삼성전자, KODEX 200, TIGER 미국S&P500", "entry": "20일 이평 위로 올라오면 사고",
    "exit": "20일 이평 밑으로 빠지면 팔아. 단 VIX 30 넘으면 무조건 팔아"},
   ["mixed", "rules", "cross", "any", "market_condition", "vix", "new_verb"],
   "'올라오면' = cross_above, '빠지면' = cross_below(동사 표). '무조건'이라 VIX 는 상태(>30)로 봄. VIX 30 위에서 이평 돌파가 나면 사고 팔 조건이 함께 참이라 팔기 우선(사지 않음)",
   dflt=["subject"])
ok("LG화학 RSI 25 이하 매수 / 75 이상 매도, 배당금 포함",
   T("051910"), "equal", None,
   [R(C(I("rsi", 14), "<=", V(25)), C(I("rsi", 14), ">=", V(75)))], BT(div=True),
   {"universe": "LG화학", "entry": "RSI 25 이하 매수", "exit": "75 이상 매도", "dividends": "배당금 포함"},
   ["stock", "rules", "level", "dividend"], "조건형에서 배당 반영", dflt=["rsi_window"])
ok("코스닥 지수가 20일선 위로 올라서면 KODEX 코스닥150 사고 20일선 이탈하면 팔아",
   T("229200"), "equal", None,
   [R(C(I("close", of=Q), "cross_above", I("ma", 20, Q)), C(I("close", of=Q), "cross_below", I("ma", 20, Q)))], BT(),
   {"universe": "KODEX 코스닥150", "subject": "코스닥 지수가", "entry": "20일선 위로 올라서면", "exit": "20일선 이탈하면 팔아"},
   ["etf", "rules", "cross", "market_condition", "kosdaq_index", "new_verb", "n_day_line"],
   "조건 주체 KOSDAQ(코스닥 지수). '올라서면' = cross_above, '이탈' = cross_below. 팔 조건 주어도 코스닥 지수로 봄")
ok("한화에어로스페이스 종가가 120일 이동평균보다 높으면 사고 낮으면 팔아",
   T("012450"), "equal", None,
   [R(C(I("close"), ">", I("ma", 120)), C(I("close"), "<", I("ma", 120)))], BT(),
   {"universe": "한화에어로스페이스", "entry": "종가가 120일 이동평균보다 높으면 사고", "exit": "낮으면 팔아"},
   ["stock", "rules", "level"], "120일 이평 워밍업에 시작 전 데이터를 써서 2023-01-02 부터 판정")
ok("ACE KRX금현물, VIX 25 넘으면 사고 15 밑으로 내려가면 팔아",
   T("411060"), "equal", None,
   [R(C(I("vix", of=X), "cross_above", V(25)), C(I("vix", of=X), "cross_below", V(15)))], BT(),
   {"universe": "ACE KRX금현물", "entry": "VIX 25 넘으면 사고", "exit": "15 밑으로 내려가면 팔아"},
   ["etf", "rules", "market_condition", "vix", "cross"], "2023~2025 VIX 가 15 밑으로 가는 일이 드물어 오래 보유할 수 있음(정답 자체는 결정됨)")
ok("KIWOOM 국고채10년, 코스피200이 60일 이평 깨지면 사고 다시 60일 이평 뚫으면 팔아",
   T("148070"), "equal", None,
   [R(C(I("close", of=K), "cross_below", I("ma", 60, K)), C(I("close", of=K), "cross_above", I("ma", 60, K)))], BT(),
   {"universe": "KIWOOM 국고채10년", "subject": "코스피200이", "entry": "60일 이평 깨지면 사고", "exit": "다시 60일 이평 뚫으면 팔아"},
   ["etf", "rules", "market_condition", "cross", "bond"])
ok("코스피 제약 업종 시총 큰 순으로 5개 골라서 각각 20일 이평 돌파하면 사고 산 가격 대비 -8%면 손절 +15%면 익절",
   SEL("sector", "제약", "KOSPI", 5, "market_cap"), "equal", None,
   [R(C(I("close"), "cross_above", I("ma", 20)),
      ANY(C(I("entry_return"), "<=", V(-0.08)), C(I("entry_return"), ">=", V(0.15))))], BT(),
   {"universe": "코스피 제약 업종", "count": "5개", "method": "시총 큰 순으로", "entry": "20일 이평 돌파하면 사고",
    "exit": "산 가격 대비 -8%면 손절 +15%면 익절"},
   ["stock", "rules", "selector", "cross", "stop_loss", "take_profit"],
   "2023-01-02 기준 삼성바이오로직스·셀트리온·SK바이오사이언스·유한양행·한미약품 예상")
ok("코스피에서 거래대금 상위 10종목 뽑아서 RSI 30 이하에서 사고 50 이상이면 팔아, 종목당 최대 15%",
   SEL("asset_type", "stock", "KOSPI", 10, "trading_value"), "equal", None,
   [R(C(I("rsi", 14), "<=", V(30)), C(I("rsi", 14), ">=", V(50)))], BT(),
   {"universe": "코스피에서", "count": "상위 10종목", "method": "거래대금", "entry": "RSI 30 이하에서 사고", "exit": "50 이상이면 팔아",
    "constraint": "종목당 최대 15%"},
   ["stock", "rules", "selector", "level", "max_weight"],
   "'상위 N종목'은 주식만(ETF 라는 말이 없음, 팀 확정). 거래대금은 직전 20거래일 평균. 자리 10% 라 상한 15% 는 묶이지 않음",
   constraint={"max_weight_per_asset": 0.15}, dflt=["rsi_window"])
ok("포스코홀딩스 rsi35밑 사고 rsi65위 팔고 손절 -10%",
   T("005490"), "equal", None,
   [R(C(I("rsi", 14), "<", V(35)), ANY(C(I("rsi", 14), ">", V(65)), C(I("entry_return"), "<=", V(-0.10))))], BT(),
   {"universe": "포스코홀딩스", "entry": "rsi35밑 사고", "exit": "rsi65위 팔고 손절 -10%"},
   ["stock", "rules", "level", "stop_loss", "casual", "typo"], "공식명 POSCO홀딩스를 한글로 씀", dflt=["rsi_window"])
ok("2024년 한 해만 SK하이닉스 20일 이평 위면 보유, 밑이면 현금",
   T("000660"), "equal", None,
   [R(C(I("close"), ">", I("ma", 20)), C(I("close"), "<", I("ma", 20)))], BT(start="2024-01-01", end="2024-12-31"),
   {"period": "2024년 한 해만", "universe": "SK하이닉스", "entry": "20일 이평 위면 보유", "exit": "밑이면 현금"},
   ["stock", "rules", "level", "custom_period"])
ok("셀트리온 거래량이 20일 평균의 2배 이상이고 하루 수익률 5% 이상이면 사고, 산 값 대비 5% 오르거나 3% 내리면 팔아",
   T("068270"), "equal", None,
   [R(ALL(C(I("volume_ratio", 20), ">=", V(2)), C(I("ret", 1), ">=", V(0.05))),
      ANY(C(I("entry_return"), ">=", V(0.05)), C(I("entry_return"), "<=", V(-0.03))))], BT(),
   {"universe": "셀트리온", "entry": "거래량이 20일 평균의 2배 이상이고 하루 수익률 5% 이상이면 사고", "exit": "산 값 대비 5% 오르거나 3% 내리면 팔아"},
   ["stock", "rules", "all", "any", "volume", "stop_loss", "take_profit"])
ok("KODEX 인버스는 코스피200이 20일 이평 아래면 사고 위로 올라가면 팔아",
   T("114800"), "equal", None,
   [R(C(I("close", of=K), "<", I("ma", 20, K)), C(I("close", of=K), "cross_above", I("ma", 20, K)))], BT(),
   {"universe": "KODEX 인버스", "subject": "코스피200이", "entry": "20일 이평 아래면 사고", "exit": "위로 올라가면 팔아"},
   ["etf", "rules", "market_condition", "level", "cross", "inverse"])
ok("KODEX 200은 VIX 20 밑이면 사고 30 넘으면 팔고, KODEX 국고채3년은 VIX 30 넘으면 사고 20 밑이면 팔아",
   T("069500", "114260"), "equal", None,
   [R(C(I("vix", of=X), "<", V(20)), C(I("vix", of=X), "cross_above", V(30)), target="069500"),
    R(C(I("vix", of=X), "cross_above", V(30)), C(I("vix", of=X), "<", V(20)), target="114260")], BT(),
   {"universe": "KODEX 200은 VIX 20 밑이면 사고 30 넘으면 팔고, KODEX 국고채3년은",
    "entry": "VIX 20 밑이면 사고", "exit": "30 넘으면 팔고"},
   ["etf", "rules", "per_asset_rule", "market_condition", "vix", "level", "cross"],
   "주식/채권 스위칭. 두 자리 1/2씩이라 둘 다 비는 구간(VIX 20~30, 채권 미보유)엔 현금")
ok("코스닥 RSI 30 밑이면 KODEX 코스닥150레버리지 사고, 코스닥 60일 수익률이 0 밑으로 내려가거나 산 지 10거래일 지나면 팔아",
   T("233740"), "equal", None,
   [R(C(I("rsi", 14, Q), "<", V(30)),
      ANY(C(I("ret", 60, Q), "cross_below", V(0)), C(I("holding_days"), ">=", V(10))))], BT(),
   {"universe": "KODEX 코스닥150레버리지", "subject": "코스닥 RSI", "entry": "30 밑이면",
    "exit": "코스닥 60일 수익률이 0 밑으로 내려가거나 산 지 10거래일 지나면 팔아"},
   ["etf", "rules", "level", "cross", "any", "market_condition", "kosdaq_index", "holding_days", "leverage"],
   "조건 주체 KOSDAQ. '10거래일 지나면' = holding_days >= 10(체결일 0일, 팀 확정). '0'은 수익률 0%",
   dflt=["rsi_window"])
ok("TIGER 2차전지TOP10 RSI가 20보다 낮으면 사고 RSI가 60보다 높으면 팔아요",
   T("364980"), "equal", None,
   [R(C(I("rsi", 14), "<", V(20)), C(I("rsi", 14), ">", V(60)))], BT(),
   {"universe": "TIGER 2차전지TOP10", "entry": "RSI가 20보다 낮으면 사고", "exit": "RSI가 60보다 높으면 팔아요"},
   ["etf", "rules", "level"], "'2차전지'가 들어가도 ETF 종목명이라 업종 되묻기 아님", dflt=["rsi_window"])

# ---------------------------------------------------------------- ask 30
ask("RSI 30 밑에서 사고 70 위에서 파는 전략 백테스트 해줘",
    [("universe", "어떤 종목인지 없음")], {"entry": "RSI 30 밑에서 사고", "exit": "70 위에서 파는"}, ["rules", "level"])
ask("삼성전자 50% KODEX 200 50%로 담는데 한 종목은 40%까지만 넣어줘",
    [("weight_vs_cap", "지정 비중 50% 가 종목당 상한 40% 와 부딪힘")],
    {"universe": "삼성전자 50% KODEX 200 50%", "constraint": "한 종목은 40%까지만"}, ["mixed", "allocation", "max_weight"],
    "상한을 우선해 40/40/현금 20 으로 둘 수도 있으나 규칙상 되묻기")
ask("삼성전자 20일 이평선 뚫으면 사줘",
    [("exit", "파는 조건 없음")], {"universe": "삼성전자", "entry": "20일 이평선 뚫으면 사줘"}, ["stock", "rules", "cross"])
ask("KODEX 200 RSI 30 아래로 내려가면 매수",
    [("exit", "파는 조건 없음")], {"universe": "KODEX 200", "entry": "RSI 30 아래로 내려가면 매수"}, ["etf", "rules"])
ask("SK하이닉스 갖고 있는데 20일 이평 깨지면 팔래",
    [("entry", "사는 조건 없음")], {"universe": "SK하이닉스", "exit": "20일 이평 깨지면 팔래"}, ["stock", "rules"],
    "이미 보유 중이라는 말. 시작일에 사서 들고 가다 판다는 뜻일 수 있으나 규칙상 사는 조건을 되묻는다")
ask("에코프로비엠 산 가격보다 10% 떨어지면 손절해줘",
    [("entry", "사는 조건 없음")], {"universe": "에코프로비엠", "exit": "산 가격보다 10% 떨어지면 손절해줘"}, ["stock", "rules", "stop_loss"])
ask("현대차 이평선 깨지면 팔고 다시 올라오면 사",
    [("ma_window", "이동평균 기간 없음")], {"universe": "현대차"}, ["stock", "rules"])
ask("카카오 이동평균선 위에 있으면 보유 아래면 현금",
    [("ma_window", "이동평균 기간 없음")], {"universe": "카카오"}, ["stock", "rules"])
ask("NAVER RSI 낮을 때 사고 높을 때 팔아",
    [("threshold", "RSI 기준 숫자 없음")], {"universe": "NAVER"}, ["stock", "rules"],
    "'낮을 때'를 과매도(30)로 채울 수도 있으나 '과매도' 낱말이 아니라 되묻기")
ask("kodex 코스닥150 많이 빠졌을 때 사서 좀 오르면 팔아",
    [("threshold", "얼마나 빠지고 얼마나 오르는지 숫자 없음")], {"universe": "kodex 코스닥150"}, ["etf", "rules", "casual"],
    "지표(수익률 기간)도 모호. threshold 하나로 묶음")
ask("삼성전자 골든크로스 나면 사고 데드크로스 나면 팔아",
    [("cross_windows", "골든/데드크로스 이평 기간 갈림")], {"universe": "삼성전자"}, ["stock", "rules", "cross"])
ask("tiger200 골크 매수 데크 매도",
    [("cross_windows", "골든/데드크로스 이평 기간 갈림")], {"universe": "tiger200"}, ["etf", "rules", "cross", "casual", "typo"],
    "골크/데크 줄임말")
ask("기아 7일선 뚫으면 사고 깨지면 팔아",
    [("window_not_allowed", "7일 이평은 허용 기간(5,10,20,60,120) 밖")], {"universe": "기아"}, ["stock", "rules"])
ask("TIGER 미국S&P500 60 KODEX 국고채3년 40, 종목당 최대 50%",
    [("weight_vs_cap", "지정 비중 60% 가 종목당 상한 50% 와 부딪힘")],
    {"universe": "TIGER 미국S&P500 60 KODEX 국고채3년 40", "constraint": "종목당 최대 50%"}, ["etf", "allocation", "max_weight"])
ask("삼성SDI 200일 이동평균 위면 사고 아래면 팔아",
    [("window_not_allowed", "200일 이평은 허용 기간 밖")], {"universe": "삼성SDI"}, ["stock", "rules"],
    "가장 가까운 120일로 바꿔 제안할지는 시스템 몫. 정답은 되묻기")
ask("삼성전자 50% SK하이닉스 40% KODEX 200 30%",
    [("weights_over_100", "비중 합 120%")], {"universe": "삼성전자 50% SK하이닉스 40% KODEX 200 30%"}, ["mixed", "allocation"])
ask("TIGER 미국S&P500 70 ACE KRX금현물 20 KODEX 국고채3년 20 분기 리밸",
    [("weights_over_100", "비중 합 110%")], {"universe": "TIGER 미국S&P500 70 ACE KRX금현물 20 KODEX 국고채3년 20", "rebalance": "분기 리밸"},
    ["etf", "allocation"])
ask("삼성전자 RSI 30 밑이면 사고 40 밑이면 팔아",
    [("always_both_true", "RSI<30 이면 RSI<40 도 참이라 사자마자 파는 조건 성립")], {"universe": "삼성전자"}, ["stock", "rules"])
ask("현대차 20일 수익률이 -5% 이하면 사고 0% 이하면 팔아",
    [("always_both_true", "-5% 이하면 0% 이하도 참")], {"universe": "현대차"}, ["stock", "rules"])
ask("코스피 반도체주 시총 상위 5개 균등 매수 분기 리밸런싱",
    [("sector_not_in_list", "2023-01-02 코스피 업종에 '반도체' 없음(전기·전자에 섞여 있음)")],
    {"count": "상위 5개", "method": "시총", "rebalance": "분기 리밸런싱"}, ["stock", "allocation", "sector"])
ask("2차전지주 거래대금 상위 5개로 RSI 30 이하 사고 70 이상 팔아",
    [("sector_not_in_list", "코스피·코스닥 어느 업종 목록에도 '2차전지' 없음")],
    {"count": "상위 5개", "method": "거래대금", "entry": "RSI 30 이하 사고", "exit": "70 이상 팔아"}, ["stock", "rules", "sector"],
    "시장도 안 밝힘. 'KODEX 2차전지산업' 같은 ETF 를 뜻했을 가능성도 있음")
ask("KODEX 200 KODEX 국고채3년 반반으로 1년에 한 번 리밸런싱",
    [("rebalance_not_allowed", "연 1회는 리밸런싱 블록에 없음")],
    {"universe": "KODEX 200 KODEX 국고채3년", "weights": "반반"}, ["etf", "allocation", "half_half"])
ask("코스닥 반도체 업종 종목들 똑같이 담아서 매달 리밸런싱",
    [("selection_needed", "해당 127종목, 개수·고르는 방식 없음")], {"universe": "코스닥 반도체 업종", "weights": "똑같이", "rebalance": "매달 리밸런싱"},
    ["stock", "allocation", "selector"], "'종목들'을 '전부'로 읽을 여지가 있음. '전부'가 명시되지 않아 되묻기로 둠")
ask("코스피 보험주 몇 개 골라서 배당 포함 분기 리밸런싱",
    [("selection_needed", "보험 15종목, 개수·방식 없음")], {"universe": "코스피 보험주", "dividends": "배당 포함", "rebalance": "분기 리밸런싱"},
    ["stock", "allocation", "selector", "dividend"])
ask("삼성전자 하이닉스 네이버 똑같이 담고 반기마다 비중 맞춰",
    [("rebalance_not_allowed", "반기(6개월)는 리밸런싱 블록에 없음")],
    {"universe": "삼성전자 하이닉스 네이버", "weights": "똑같이"}, ["stock", "allocation", "casual"],
    "분기 블록으로 바꿔 제안할지는 시스템 몫. 정답은 되묻기")
ask("삼성전자 눌림목에서 사고 20일 이평 깨지면 팔아",
    [("entry", "'눌림목'은 사전에 정의 없음, 사는 조건을 정할 수 없음")], {"universe": "삼성전자", "exit": "20일 이평 깨지면 팔아"},
    ["stock", "rules", "slang"], "slot 을 entry 로 둠(threshold 로 볼 여지)")
ask("반도체 ETF 하나 골라서 이평선 돌파하면 사",
    [("universe", "반도체 ETF 가 여러 개(9개)라 종목 미정"), ("ma_window", "이평 기간 없음"), ("exit", "파는 조건 없음")],
    {}, ["etf", "rules", "multi_slot"], "종목 칸은 universe 로 확정(팀 결정). selection_needed 아님")
ask("HMM RSI 낮아지면 사",
    [("threshold", "기준 숫자 없음"), ("exit", "파는 조건 없음")], {"universe": "HMM"}, ["stock", "rules", "multi_slot"])
ask("주식 반 채권 반으로 해줘",
    [("universe", "어떤 주식·채권(ETF)인지 없음")], {}, ["allocation", "slang", "multi_slot"],
    "'반'(50:50)은 사전에 없으나 비중보다 종목 미정이 먼저. 개별 채권이면 거절 대상이지만 채권 ETF 를 뜻했을 가능성이 커서 되묻기")
ask("코스피 화학 업종에서 많이 오른 순으로 골라서 20일 이평 뚫으면 사고 깨지면 팔아",
    [("selection_needed", "화학 121종목, 방식은 있으나 개수 없음")], {"universe": "코스피 화학 업종", "method": "많이 오른 순으로",
                                                          "entry": "20일 이평 뚫으면 사고", "exit": "깨지면 팔아"},
    ["stock", "rules", "selector"])

# ---------------------------------------------------------------- reject 30
rej("내일 종가가 오늘보다 오를 것 같은 날만 삼성전자 사서 다음날 팔아", "FUTURE_REF", "내일 종가가 오늘보다 오를 것 같은 날만", ["stock"])
rej("다음 달 실적 발표에서 서프라이즈 나는 종목만 미리 사두는 전략", "FUTURE_REF", "다음 달 실적 발표에서 서프라이즈 나는 종목만",
    ["stock"], "종목 미정(되묻기 거리)도 있지만 거절 우선")
rej("SK하이닉스 20일 뒤 수익률이 10% 넘을 날에 사고 RSI 70 넘으면 팔아", "FUTURE_REF", "20일 뒤 수익률이 10% 넘을 날에", ["stock"],
    "ret20(과거 20일)과 헷갈리기 쉬운 문장")
rej("엑셀에서 VLOOKUP 쓰는 법 알려줘", "OUT_OF_DOMAIN", "엑셀에서 VLOOKUP 쓰는 법", [])
rej("삼성전자 MACD 골든크로스에 사고 데드크로스에 팔아", "UNKNOWN_INDICATOR", "MACD", ["stock"], "골든크로스 되묻기 거리도 있으나 거절 우선")
rej("KODEX 200 볼린저밴드 하단 터치하면 매수 상단 터치하면 매도", "UNKNOWN_INDICATOR", "볼린저밴드", ["etf"])
rej("카카오 일목균형표 구름대 위로 올라가면 사", "UNKNOWN_INDICATOR", "일목균형표 구름대", ["stock"], "팔 조건 없음(되묻기 거리)보다 거절 우선")
rej("SK하이닉스 보유일 5일 이상이면 매수, 20일선 깨지면 매도", "VALIDATION", "보유일 5일 이상이면 매수", ["stock"],
    "holding_days 를 살 때 조건에 씀")
rej("테슬라랑 애플 반반 사서 분기마다 리밸런싱", "UNSUPPORTED_ASSET", "테슬라랑 애플", ["stock"], "해외 직접 상장 주식")
rej("비트코인 20일 이평 돌파하면 사고 깨지면 팔아", "UNSUPPORTED_ASSET", "비트코인", [])
rej("코스피200 선물 RSI 30 밑이면 매수 70 위면 매도", "UNSUPPORTED_ASSET", "코스피200 선물", [],
    "KODEX 200선물인버스2X 같은 선물 ETF 와 구분 필요")
rej("삼성전자 60% 국고채 10년물 채권 직접 40% 사서 매달 리밸런싱", "UNSUPPORTED_ASSET", "국고채 10년물 채권 직접", ["mixed"],
    "개별 채권. 'KIWOOM 국고채10년' ETF 를 뜻한 것으로 볼 여지가 있어 '직접'을 넣음")
rej("SK하이닉스 RSI 70 넘으면 공매도 치고 50 밑이면 청산", "UNSUPPORTED_ACTION", "공매도", ["stock"])
rej("삼성전자 20일 이평 뚫으면 신용 끌어서 두 배로 사고 깨지면 팔아", "UNSUPPORTED_ACTION", "신용 끌어서 두 배로", ["stock"])
rej("현대차 RSI 30 밑에서 사고 산 값보다 10% 빠질 때마다 물타기 해줘", "UNSUPPORTED_ACTION", "물타기", ["stock"])
rej("KODEX 200 RSI 30 밑일 때마다 1/3씩 세 번 나눠서 분할매수, RSI 70 넘으면 전부 팔아", "UNSUPPORTED_ACTION", "1/3씩 세 번 나눠서 분할매수", ["etf"])
rej("삼성전자 장중에 2% 빠지면 바로 사고 3% 오르면 팔아", "INTRADAY", "장중에 2% 빠지면 바로", ["stock"])
rej("KODEX 레버리지 5분봉 RSI 30 이하 매수 70 이상 매도", "INTRADAY", "5분봉", ["etf"])
rej("에코프로 아침 시초가 갭상승하면 사고 오후 3시에 팔아", "INTRADAY", "시초가 갭상승하면 사고 오후 3시에 팔아", ["stock"],
    "시가 갭은 일봉으로도 판정 가능하지만 당일 장중 청산이 들어가 장중으로 봄")
rej("두산로보틱스 20일 이평 돌파시 매수, 이탈시 매도", "NO_DATA", "두산로보틱스", ["stock"], "2023-10-05 상장. 기본 기간 2023-01-01 시작이라 상장 전 구간 포함")
rej("에이피알 50% 삼성전자 50% 분기 리밸런싱", "NO_DATA", "에이피알", ["stock"], "2024-02-27 상장")
rej("HD현대마린솔루션이랑 시프트업 RSI 30 밑에서 사고 70 위에서 팔아", "NO_DATA", "HD현대마린솔루션이랑 시프트업", ["stock"],
    "2024-05-08, 2024-07-11 상장")
rej("오늘 점심 뭐 먹을까", "OUT_OF_DOMAIN", "오늘 점심 뭐 먹을까", [])
rej("삼성전자 주가 내일 오를까요?", "FUTURE_REF", "내일 오를까요", ["stock"],
    "예측 질문도 미래 정보 요구로 FUTURE_REF(팀 확정)")
rej("파이썬으로 네이버 뉴스 크롤러 짜줘", "OUT_OF_DOMAIN", "파이썬으로 네이버 뉴스 크롤러", [], "'네이버'가 종목명과 겹침")
rej("삼성전자 RSI가 5일 이동평균보다 낮으면 사고 70 넘으면 팔아", "VALIDATION", "RSI가 5일 이동평균보다 낮으면", ["stock"], "단위가 다른 비교")
rej("KODEX 200 RSI 150 넘으면 팔고 30 밑이면 사", "VALIDATION", "RSI 150", ["etf"], "범위 밖 숫자")
rej("NAVER 20일 이평이 20일 이평을 뚫으면 사고 깨지면 팔아", "VALIDATION", "20일 이평이 20일 이평을", ["stock"], "자기 자신 비교")
rej("SK하이닉스 산 가격보다 5% 싸지면 사고 20일 이평 깨지면 팔아", "VALIDATION", "산 가격보다 5% 싸지면 사고", ["stock"],
    "entry_return 을 살 때 조건에 씀. '직전에 판 가격보다'로 읽을 여지 있음")
rej("코스피 거래량이 20일 평균 두 배 넘으면 KODEX 200 사고 RSI 70 넘으면 팔아", "VALIDATION", "코스피 거래량이 20일 평균 두 배", ["etf"],
    "volume_ratio 는 지수(KOSPI200·KOSDAQ)에 못 씀. VALIDATION 확정")

# ---------------------------------------------------------------- 배정
assert len(RECS) == 120, len(RECS)
# v0.2: 셀트리온 보유일 건(ok, 코드상 43번째)과 weight_vs_cap 건(ask, 61번째)을 맞바꿔
# 셀트리온 건이 v0.1 의 E016 자리, weight_vs_cap 건이 v0.1 의 E048 자리에 오게 한다. id·split 유지용.
RECS[43], RECS[61] = RECS[61], RECS[43]
for i, r in enumerate(RECS):
    r["split"] = "dev" if i % 3 == 0 else "test"

order = list(range(120))
random.Random(20261102).shuffle(order)
out = []
for n, i in enumerate(order, 1):
    r = RECS[i]
    out.append({
        "id": f"E{n:03d}", "split": r["split"], "type": r["type"], "text": r["text"],
        "expected_spec": r["expected_spec"], "defaults_used": r["defaults_used"],
        "expected_ask": r["expected_ask"], "expected_reject": r["expected_reject"],
        "evidence": r["evidence"], "tags": r["tags"], "note": r["note"],
    })

with open(HERE / "dataset.jsonl", "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------- review.csv
IND = {"close": "종가", "rsi": "RSI", "ret": "수익률", "vol": "변동성", "volume_ratio": "거래량비율", "vix": "VIX",
       "entry_return": "매수가대비수익률", "holding_days": "보유거래일"}
OP = {"<": "<", "<=": "<=", ">": ">", ">=": ">=", "cross_below": "하향돌파", "cross_above": "상향돌파"}
RB = {"RB_MONTHLY_FIRST": "매달 첫 거래일", "RB_MONTHLY_MID": "매달 중순", "RB_QUARTERLY": "분기",
      "RB_WEEKLY": "매주", "RB_THRESHOLD_5": "5%p 이탈"}
MET = {"market_cap": "시총순", "trading_value": "거래대금순", "momentum": "상승률순", "low_vol": "저변동순", None: "전부"}


def opnd(o):
    if "value" in o:
        return str(o["value"])
    n = o["indicator"]
    s = f"{o['window']}일 이평" if n == "ma" else (IND[n] + (str(o["window"]) if "window" in o and n != "rsi" else ""))
    if o["of"] == "KOSPI200":
        s = "K200 " + s
    elif o["of"] == "KOSDAQ":
        s = "코스닥지수 " + s
    elif o["of"] not in ("self", "VIX"):
        s = NAMES.get(o["of"], o["of"]) + " " + s
    return s


def cond(c):
    if "all" in c:
        return "(" + " 그리고 ".join(cond(x) for x in c["all"]) + ")"
    if "any" in c:
        return "(" + " 또는 ".join(cond(x) for x in c["any"]) + ")"
    return f"{opnd(c['left'])} {OP[c['op']]} {opnd(c['right'])}"


def summary(r):
    if r["type"] == "ask":
        return "되묻기: " + "; ".join(f"{a['slot']}({a['why']})" for a in r["expected_ask"])
    if r["type"] == "reject":
        e = r["expected_reject"]
        return f"거절: {e['code']} (근거: {e['evidence']})"
    s = r["expected_spec"]
    u = s["universe"]
    if "tickers" in u:
        us = ", ".join(NAMES[t] for t in u["tickers"])
    else:
        x = u["selector"]
        us = f"{x['market'] or ''} {x['value']} {x['count'] or ''}{'개 ' if x['count'] else ''}{MET[x['method']]}".strip()
        us += " (" + ", ".join(NAMES[t] for t in resolve(x)[0]) + ")"
    w = "균등" if s["weights"] == "equal" else "/".join(f"{NAMES[k]} {round(v*100)}%" for k, v in s["weights"].items())
    parts = [f"종목: {us}"]
    if s["rules"]:
        parts.append("비중: 종목당 1/N")
        for ru in s["rules"]:
            tg = "각 종목" if ru["target"] == "each" else NAMES[ru["target"]]
            parts.append(f"[{tg}] 사기 {cond(ru['entry'])} / 팔기 {cond(ru['exit'])}")
    else:
        parts += [f"비중: {w}", f"리밸런싱: {RB[s['rebalance']]}"]
    if s["constraint"]:
        parts.append(f"종목당 최대 {round(s['constraint']['max_weight_per_asset']*100)}%")
    b = s["backtest"]
    parts.append(f"기간 {b['start']}~{b['end']}, {b['capital']:,}원, 배당 {'포함' if b['include_dividends'] else '미포함'}")
    return " | ".join(parts)


with open(HERE / "review.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "split", "type", "text", "정답 요약", "라벨러A 판정", "라벨러B 판정", "비고"])
    for r in out:
        w.writerow([r["id"], r["split"], r["type"], r["text"], summary(r), "", "", r["note"]])
print("written", len(out))
