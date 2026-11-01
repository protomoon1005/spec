"""dataset.jsonl 검증. 실패가 하나라도 있으면 종료 코드 1.

python3 check.py [dataset.jsonl]
경로는 모두 이 파일 위치 기준(ref/, cache/, ../db/seeds/). 어느 디렉터리에서 실행해도 된다. 네트워크를 쓰지 않는다.
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # 저장소에 __pycache__ 를 남기지 않는다
sys.path.insert(0, str(HERE))
from resolve import DIV_WORDS, ETF_MASTER, candidates, dividend_flag, resolve  # noqa: E402

REF = HERE / "ref"
DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dataset.jsonl"

ERR = []
WARN = []


def err(rid, msg):
    ERR.append(f"{rid}: {msg}")


# ---------------------------------------------------------------- 참조 데이터
def load_sector(market, day):
    return {r["종목코드"]: r for r in csv.DictReader(open(REF / f"sector_{market}_{day}.csv", encoding="utf-8-sig"))}


S23 = {m: load_sector(m, "20230102") for m in ("KOSPI", "KOSDAQ")}
S25 = {m: load_sector(m, "20251230") for m in ("KOSPI", "KOSDAQ")}
STOCK23 = {**S23["KOSPI"], **S23["KOSDAQ"]}
STOCK25 = {**S25["KOSPI"], **S25["KOSDAQ"]}
ETF = {r["ticker"]: r for r in csv.DictReader(open(ETF_MASTER, encoding="utf-8-sig"))}
ETF_OK = {t for t, r in ETF.items() if r["active"] == "true" and (not r["listed_date"] or r["listed_date"] < "2023-01-02")}
KIND = list(csv.DictReader(open(REF / "kind_listing.csv", encoding="utf-8")))
SECTORS = {m: Counter(r["업종명"] for r in S23[m].values()) for m in S23}

TYPES = {"ok": 60, "ask": 30, "reject": 30}
SPLIT = {"dev": {"ok": 20, "ask": 10, "reject": 10}, "test": {"ok": 40, "ask": 20, "reject": 20}}
KEYS = ["id", "split", "type", "text", "expected_spec", "defaults_used", "expected_ask", "expected_reject", "evidence", "tags", "note"]
SLOTS = {"exit", "universe", "ma_window", "threshold", "cross_windows", "window_not_allowed", "weights_over_100",
         "always_both_true", "sector_not_in_list", "selection_needed", "entry", "weight_vs_cap", "rebalance_not_allowed"}
CODES = {"FUTURE_REF", "UNKNOWN_INDICATOR", "UNSUPPORTED_ASSET", "UNSUPPORTED_ACTION", "INTRADAY", "NO_DATA",
         "OUT_OF_DOMAIN", "VALIDATION"}
RB = {"RB_MONTHLY_FIRST", "RB_MONTHLY_MID", "RB_QUARTERLY", "RB_WEEKLY", "RB_THRESHOLD_5"}
DEFAULTS = {"weights", "rebalance", "period", "capital", "costs", "dividends", "rsi_window", "rsi_level", "subject"}
OPS = {"<", "<=", ">", ">=", "cross_below", "cross_above"}
WINDOWS = {"close": None, "ma": {5, 10, 20, 60, 120}, "rsi": {14}, "ret": {1, 5, 20, 60}, "vol": {20},
           "volume_ratio": {20}, "vix": None, "entry_return": None, "holding_days": None}
EXIT_ONLY = {"entry_return", "holding_days"}
INDEX_OK = {"close", "ma", "rsi", "ret", "vol"}  # KOSPI200·KOSDAQ 에 쓸 수 있는 지표
# 같은 단위끼리만 비교. price 계열은 지표끼리, 나머지는 숫자와만.
UNIT = {"close": "price", "ma": "price", "rsi": "rsi", "ret": "ratio", "vol": "vol", "volume_ratio": "vratio",
        "vix": "vix", "entry_return": "ratio", "holding_days": "days"}
RANGE = {"rsi": (0, 100), "ret": (-1, 10), "entry_return": (-1, 10), "volume_ratio": (0, 100), "vix": (0, 100), "vol": (0, 10), "holding_days": (1, 1000)}
NEW_VERBS = ("이탈", "빠지", "빠져", "올라서", "올라오", "터지", "터져")
OK_MIX = {"stock": (26, 30), "etf": (22, 26), "mixed": (6, 10)}
FIRST_TAGS = {"stock", "etf", "mixed"}


def ticker_ok(t):
    if not (isinstance(t, str) and len(t) == 6):
        return False
    if t in ETF_OK:
        return True
    return t in STOCK23 and t in STOCK25


# ---------------------------------------------------------------- 조건 검사
def check_operand(rid, o, side_exit, universe):
    if "value" in o:
        if set(o) != {"value"} or not isinstance(o["value"], (int, float)):
            err(rid, f"value 피연산자 형식 {o}")
        return None
    n = o.get("indicator")
    if n not in WINDOWS:
        err(rid, f"허용 밖 지표 {n}")
        return None
    extra = set(o) - {"indicator", "window", "of"}
    if extra:
        err(rid, f"피연산자에 모르는 칸 {extra}")
    allowed = WINDOWS[n]
    if allowed is None:
        if "window" in o:
            err(rid, f"{n} 에 기간이 있으면 안 됨")
    elif o.get("window") not in allowed:
        err(rid, f"{n} 기간 {o.get('window')} 허용 밖")
    of = o.get("of")
    if of not in ("self", "KOSPI200", "KOSDAQ", "VIX") and not (of in universe or ticker_ok(of)):
        err(rid, f"of 값 {of} 이상")
    if of in ("KOSPI200", "KOSDAQ") and n not in INDEX_OK:
        err(rid, f"{n} 은 지수({of})에 못 씀")
    if n == "vix" and of != "VIX":
        err(rid, "vix 는 of VIX 만")
    if n != "vix" and of == "VIX":
        err(rid, f"{n} 은 of VIX 불가")
    if n in EXIT_ONLY:
        if of != "self":
            err(rid, f"{n} 은 of self 만")
        if not side_exit:
            err(rid, f"{n} 을 살 때 조건에 씀")
    return n


def check_cond(rid, c, side_exit, universe, depth=0):
    if not isinstance(c, dict):
        err(rid, f"조건 형식 {c}")
        return
    if "all" in c or "any" in c:
        k = "all" if "all" in c else "any"
        if set(c) != {k} or not isinstance(c[k], list) or len(c[k]) < 2:
            err(rid, f"{k} 형식 이상")
            return
        for x in c[k]:
            check_cond(rid, x, side_exit, universe, depth + 1)
        return
    if set(c) != {"left", "op", "right"}:
        err(rid, f"비교식 칸 {set(c)}")
        return
    if c["op"] not in OPS:
        err(rid, f"연산자 {c['op']}")
    ln = check_operand(rid, c["left"], side_exit, universe)
    rn = check_operand(rid, c["right"], side_exit, universe)
    if ln is None and "value" in c["left"]:
        err(rid, "왼쪽이 숫자")
        return
    if c["left"] == c["right"]:
        err(rid, "자기 자신 비교")
    if ln and rn:
        if UNIT[ln] != UNIT[rn] or UNIT[ln] != "price":
            err(rid, f"단위가 다른/허용 안 된 지표끼리 비교 {ln} vs {rn}")
    elif ln and "value" in c["right"]:
        if UNIT[ln] == "price":
            err(rid, f"가격 지표 {ln} 를 숫자와 비교")
        lo, hi = RANGE.get(ln, (None, None))
        v = c["right"]["value"]
        if lo is not None and not (lo <= v <= hi):
            err(rid, f"{ln} 숫자 {v} 범위 밖")
        if ln == "holding_days" and not isinstance(v, int):
            err(rid, "holding_days 는 정수 거래일 수")


def atoms(c):
    if "all" in c:
        return [a for x in c["all"] for a in atoms(x)]
    if "any" in c:
        return [a for x in c["any"] for a in atoms(x)]
    return [c]


def implies_simple(entry, exit_):
    """단일 '지표 vs 숫자' 상태 비교끼리 entry 가 exit 를 함의하면 True."""
    if "left" not in entry or "left" not in exit_:
        return False
    if "value" not in entry["right"] or "value" not in exit_["right"] or entry["left"] != exit_["left"]:
        return False
    a, b = entry["right"]["value"], exit_["right"]["value"]
    if entry["op"] in ("<", "<=") and exit_["op"] in ("<", "<="):
        return a <= b
    if entry["op"] in (">", ">=") and exit_["op"] in (">", ">="):
        return a >= b
    return False


# ---------------------------------------------------------------- 레코드 검사
def check_spec(rid, s, text, note=""):
    if set(s) != {"universe", "weights", "rebalance", "rules", "constraint", "backtest"}:
        err(rid, f"expected_spec 칸 {set(s)}")
        return
    u = s["universe"]
    tickers = []
    if "tickers" in u:
        tickers = u["tickers"]
        if not tickers or len(set(tickers)) != len(tickers):
            err(rid, "tickers 비었거나 중복")
        for t in tickers:
            if not ticker_ok(t):
                err(rid, f"종목코드 {t} 가 목록에 없음(또는 비활성·상장폐지·2023 이후 상장)")
    elif "selector" in u:
        x = u["selector"]
        if set(x) != {"kind", "value", "market", "count", "method"}:
            err(rid, f"selector 칸 {set(x)}")
        if x["kind"] not in ("sector", "asset_type", "etf_category"):
            err(rid, f"selector kind {x['kind']}")
        if x["market"] not in ("KOSPI", "KOSDAQ", None):
            err(rid, f"market {x['market']}")
        if x["method"] not in ("market_cap", "trading_value", "momentum", "low_vol", None):
            err(rid, f"method {x['method']}")
        if x["kind"] == "sector" and SECTORS.get(x["market"], {}).get(x["value"], 0) == 0:
            err(rid, f"업종 {x['market']} {x['value']} 가 2023-01-02 목록에 없음")
        cand = candidates(x)
        if x["count"] is None or x["method"] is None:
            if not (x["count"] is None and x["method"] is None and (len(cand) < 5 or "전부" in text)):
                err(rid, "selector 개수/방식 누락(후보 5개 이상인데 '전부'도 아님)")
        elif x["count"] >= len(cand):
            err(rid, f"개수 {x['count']} >= 후보 수 {len(cand)}")
        mt = re.search(r"정답 종목\[([0-9A-Z,]*)\]", note)
        if not mt:
            err(rid, "selector 건 note 에 '정답 종목[...]' 없음")
        elif mt.group(1).split(",") != resolve(x)[0]:
            err(rid, "note 의 정답 종목이 resolve.py 결과와 다름")
    else:
        err(rid, "universe 형식")
    w = s["weights"]
    if w != "equal":
        if not isinstance(w, dict) or "tickers" not in u or set(w) != set(tickers):
            err(rid, "weights 키가 tickers 와 다름")
        else:
            tot = sum(w.values())
            if tot > 1 + 1e-9 or any(v <= 0 for v in w.values()):
                err(rid, f"비중 합 {tot}")
            if tot < 1 - 1e-9:
                WARN.append(f"{rid}: 비중 합 {tot:.2f} (나머지 현금)")
    rules = s["rules"]
    if not isinstance(rules, list):
        err(rid, "rules 는 리스트")
        rules = []
    if rules:
        if s["rebalance"] is not None:
            err(rid, "조건형인데 rebalance 가 있음")
        if w != "equal":
            err(rid, "조건형 비중은 equal(1/N)")
        for ru in rules:
            if set(ru) != {"target", "entry", "exit"}:
                err(rid, f"rule 칸 {set(ru)}")
                continue
            if ru["target"] != "each" and ru["target"] not in tickers:
                err(rid, f"target {ru['target']} 가 universe 에 없음")
            check_cond(rid, ru["entry"], False, tickers)
            check_cond(rid, ru["exit"], True, tickers)
            for ea in atoms(ru["entry"]):
                if "all" not in ru["entry"] and implies_simple(ea, ru["exit"]):
                    err(rid, "사는 조건이 파는 조건을 함의(항상 같이 참)")
    else:
        if s["rebalance"] not in RB:
            err(rid, f"자산배분형 rebalance {s['rebalance']}")
    c = s["constraint"]
    if c not in ({},) and (set(c) != {"max_weight_per_asset"} or not 0 < c["max_weight_per_asset"] <= 1):
        err(rid, f"constraint {c}")
    if c and isinstance(w, dict) and any(v > c["max_weight_per_asset"] + 1e-9 for v in w.values()):
        err(rid, "지정 비중이 종목당 상한을 넘음(weight_vs_cap 되묻기 대상)")
    b = s["backtest"]
    if set(b) != {"start", "end", "capital", "include_dividends"}:
        err(rid, f"backtest 칸 {set(b)}")
        return
    if not ("2023-01-01" <= b["start"] < b["end"] <= "2025-12-31"):
        err(rid, f"기간 {b['start']}~{b['end']}")
    if not isinstance(b["capital"], int) or b["capital"] <= 0:
        err(rid, "capital")
    flag = dividend_flag(text)
    if b["include_dividends"] != (flag is True):
        err(rid, f"배당 반영({b['include_dividends']})이 문장 판정({flag})과 다름")


def main():
    lines = DATA.read_text(encoding="utf-8").splitlines()
    recs = []
    for i, ln in enumerate(lines, 1):
        try:
            recs.append(json.loads(ln))
        except json.JSONDecodeError as e:
            err(f"line{i}", f"JSON 오류 {e}")
    if len(recs) != 120:
        err("ALL", f"건수 {len(recs)} != 120")
    ids = [r.get("id") for r in recs]
    if ids != [f"E{n:03d}" for n in range(1, len(recs) + 1)]:
        err("ALL", "id 가 E001 부터 연속이 아님")
    tc = Counter(r.get("type") for r in recs)
    if dict(tc) != TYPES:
        err("ALL", f"유형 분포 {dict(tc)}")
    for sp, want in SPLIT.items():
        got = Counter(r["type"] for r in recs if r.get("split") == sp)
        if dict(got) != want:
            err("ALL", f"{sp} 분포 {dict(got)} != {want}")
    texts = Counter(r.get("text") for r in recs)
    for t, n in texts.items():
        if n > 1:
            err("ALL", f"중복 문장 {t}")

    slot_c, code_c, tag_c, rb_c, cov = Counter(), Counter(), Counter(), Counter(), Counter()
    for r in recs:
        rid = r.get("id", "?")
        if list(r) != KEYS:
            err(rid, f"칸 순서/이름 {list(r)}")
            continue
        if r["split"] not in SPLIT:
            err(rid, "split")
        if not r["text"].strip():
            err(rid, "text 비었음")
        if not isinstance(r["tags"], list):
            err(rid, "tags")
        tag_c.update(r["tags"])
        for k, v in (r["evidence"] or {}).items():
            if v not in r["text"]:
                err(rid, f"evidence[{k}] '{v}' 가 문장에 없음")
        t = r["type"]
        if t == "ok":
            if r["expected_spec"] is None or r["expected_ask"] is not None or r["expected_reject"] is not None:
                err(rid, "ok 인데 spec 없음/다른 칸 있음")
                continue
            if not isinstance(r["defaults_used"], list) or not set(r["defaults_used"]) <= DEFAULTS:
                err(rid, f"defaults_used {r['defaults_used']}")
            if not r["tags"] or r["tags"][0] not in FIRST_TAGS:
                err(rid, "ok 의 첫 태그는 stock/etf/mixed")
            kind = "rules" if r["expected_spec"].get("rules") else "allocation"
            if kind not in r["tags"]:
                err(rid, f"태그에 {kind} 없음")
            check_spec(rid, r["expected_spec"], r["text"], r["note"])
            js = json.dumps(r["expected_spec"], ensure_ascii=False)
            if '"holding_days"' in js:
                cov["holding_days"] += 1
            if '"of": "KOSDAQ"' in js:
                cov["kosdaq_subject"] += 1
            if any(v in r["text"] for v in NEW_VERBS):
                cov["new_verb"] += 1
            if dividend_flag(r["text"]) == "neg":
                cov["dividend_negated"] += 1
            if any(wd in r["text"] for wd in DIV_WORDS) and dividend_flag(r["text"]) is False:
                cov["etf_name_dividend"] += 1
            if "반반" in r["text"]:
                cov["half_half"] += 1
            if re.search(r"(5|10|20|60|120)일선", r["text"]):
                cov["n_day_line"] += 1
            rb_c[r["expected_spec"].get("rebalance")] += 1
            if not r["evidence"]:
                err(rid, "ok 인데 evidence 없음")
        elif t == "ask":
            if r["expected_ask"] is None or r["expected_spec"] is not None or r["expected_reject"] is not None or r["defaults_used"] is not None:
                err(rid, "ask 칸 구성")
                continue
            for a in r["expected_ask"]:
                if a.get("slot") not in SLOTS or not a.get("why"):
                    err(rid, f"ask slot {a}")
                slot_c[a.get("slot")] += 1
        elif t == "reject":
            e = r["expected_reject"]
            if e is None or r["expected_spec"] is not None or r["expected_ask"] is not None or r["defaults_used"] is not None:
                err(rid, "reject 칸 구성")
                continue
            if e.get("code") not in CODES:
                err(rid, f"거절 코드 {e.get('code')}")
            if not e.get("evidence") or e["evidence"] not in r["text"]:
                err(rid, "거절 근거가 문장에 없음")
            code_c[e.get("code")] += 1
            if e.get("code") == "NO_DATA":
                hits = [k for k in KIND if len(k["회사명"]) >= 2 and k["회사명"] in r["text"]]
                if not hits:
                    err(rid, "NO_DATA 인데 문장 속 종목을 KIND 상장 목록에서 못 찾음")
                # 다른 이름 속에 들어 있는 짧은 이름(두산로보틱스 속 두산)은 뺀다
                names_ = [k["회사명"] for k in hits]
                hits = [k for k in hits if not any(k["회사명"] != o and k["회사명"] in o for o in names_)]
                if hits and not any(k["상장일"] > "2023-01-02" for k in hits):
                    err(rid, "NO_DATA 인데 문장 속 종목이 모두 기간 시작 전 상장")
        else:
            err(rid, f"type {t}")

    for c in CODES:
        if code_c[c] < 3:
            err("ALL", f"거절 코드 {c} {code_c[c]}건 < 3")
    for s in SLOTS:
        if slot_c[s] < 2:
            err("ALL", f"되묻기 slot {s} {slot_c[s]}건 < 2")
    for k in ("holding_days", "kosdaq_subject", "new_verb", "dividend_negated", "half_half", "n_day_line", "etf_name_dividend"):
        if cov[k] < 2:
            err("ALL", f"새 규칙 적용 ok {k} {cov[k]}건 < 2")
    mix = Counter(r["tags"][0] for r in recs if r["type"] == "ok")
    for k, (lo, hi) in OK_MIX.items():
        if not lo <= mix[k] <= hi:
            err("ALL", f"ok 자산 구성 {k} {mix[k]} 가 {lo}~{hi} 밖")
    for b in RB:
        if rb_c[b] < 2:
            err("ALL", f"리밸런싱 블록 {b} {rb_c[b]}건 < 2")
    ok_kind = Counter("rules" if r["expected_spec"]["rules"] else "allocation" for r in recs if r["type"] == "ok" and r["expected_spec"])
    if dict(ok_kind) != {"allocation": 25, "rules": 35}:
        err("ALL", f"ok 유형 {dict(ok_kind)}")

    print(f"건수 {len(recs)}  유형 {dict(tc)}  ok {dict(ok_kind)}")
    for sp in SPLIT:
        print(f"  {sp}: {dict(Counter(r['type'] for r in recs if r['split'] == sp))}")
    print("거절 코드", dict(sorted(code_c.items())))
    print("되묻기 slot", dict(sorted(slot_c.items())))
    print("리밸런싱", dict(sorted(rb_c.items(), key=lambda x: str(x[0]))))
    print("ok 자산 구성", dict(Counter(r["tags"][0] for r in recs if r["type"] == "ok")))
    print("새 규칙 적용(ok)", dict(cov))
    print("태그", dict(tag_c.most_common()))
    for w in WARN:
        print("경고", w)
    if ERR:
        print(f"\n실패 {len(ERR)}건")
        for e in ERR:
            print(" -", e)
        sys.exit(1)
    print("\n통과")


if __name__ == "__main__":
    main()
