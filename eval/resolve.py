# selector 를 실제 종목 목록으로 푼다. gen.py(정답 기록)와 check.py(대조)가 같이 쓴다.
#
# 규칙(2026-11-02 팀 결정)
# - 업종 판정: 2023-01-02 업종 파일.
# - 후보: 가격 데이터가 있는 종목만. 프록시로 2025-12-30 업종 파일에 있는 종목.
# - "주식"/업종 고르기: 우선주·스팩 제외.
# - 기준값: 시가총액 = 2022-12-29(시작 직전 거래일) 값, 거래대금·변동성 = 직전 20거래일,
#   상승률 = 직전 60거래일. 값은 cache/ (2026-11-02 에 한 번 받은 사본). 시총 사본에 없으면 2023-01-02 파일 값으로 근사.
# - 기준 기간 데이터가 빠진 종목(거래정지 등)은 후보에서 뺀다.
# 경로는 모두 이 파일 위치 기준. 네트워크를 쓰지 않는다.
import csv
import gzip
import statistics
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE / "ref"
CACHE = HERE / "cache"
ETF_MASTER = HERE.parent / "db" / "seeds" / "04_etf_master.csv"  # 저장소 eval/ 기준 ../db/seeds


def _load(market, day):
    return {r["종목코드"]: r for r in csv.DictReader(open(REF / f"sector_{market}_{day}.csv", encoding="utf-8-sig"))}


@lru_cache(None)
def sector_files():
    s23 = {m: _load(m, "20230102") for m in ("KOSPI", "KOSDAQ")}
    s25 = {m: _load(m, "20251230") for m in ("KOSPI", "KOSDAQ")}
    return s23, s25


def is_pref_or_spac(ticker, name):
    return ticker[-1] != "0" or "스팩" in name or "기업인수목적" in name or name.endswith(("우", "우B", "우C"))


@lru_cache(None)
def _caps():
    out = {}
    for m in ("KOSPI", "KOSDAQ"):
        p = CACHE / f"cap_{m}_20221229.csv"
        if p.exists():
            for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                out[r["티커"]] = float(r["시가총액"])
    return out


@lru_cache(None)
def _px():
    days = (CACHE / "days.txt").read_text().split()
    close, value = {}, {}
    # 첫 날(60거래일 수익률 기준)과 마지막 21거래일만 담은 사본
    for r in csv.DictReader(gzip.open(CACHE / "ohlcv_pre.csv.gz", "rt", encoding="utf-8")):
        close.setdefault(r["ticker"], {})[r["date"]] = float(r["close"])
        value.setdefault(r["ticker"], {})[r["date"]] = float(r["value"])
    return days, close, value


def candidates(sel):
    s23, s25 = sector_files()
    m = sel["market"]
    markets = [m] if m else ["KOSPI", "KOSDAQ"]
    out = []
    for mk in markets:
        for t, r in s23[mk].items():
            if sel["kind"] == "sector" and r["업종명"] != sel["value"]:
                continue
            if t not in s25["KOSPI"] and t not in s25["KOSDAQ"]:
                continue
            if is_pref_or_spac(t, r["종목명"]):
                continue
            out.append(t)
    return out


def metric(t, method):
    """(값, 근사여부). 큰 값이 앞 순위가 되도록 부호를 맞춘다. 값을 못 구하면 None."""
    if method == "market_cap":
        caps = _caps()
        if t in caps:
            return caps[t], False
        s23, _ = sector_files()
        for mk in s23.values():
            if t in mk:
                return float(mk[t]["시가총액"]), True
        return None, False
    days, close, value = _px()
    c, v = close.get(t, {}), value.get(t, {})
    if method == "trading_value":
        xs = [v[d] for d in days[-20:] if d in v]
        return (sum(xs) / len(xs), False) if len(xs) == 20 else (None, False)
    if method == "momentum":
        a, b = c.get(days[0]), c.get(days[-1])
        return ((b / a - 1), False) if a and b else (None, False)
    if method == "low_vol":
        ps = [c.get(d) for d in days[-21:]]
        if any(not p for p in ps):
            return None, False
        rets = [ps[i] / ps[i - 1] - 1 for i in range(1, len(ps))]
        return -statistics.pstdev(rets), False
    raise ValueError(method)


def resolve(sel):
    """정답 종목 목록, 근사로 채운 종목, 값이 없어 뺀 종목."""
    cand = candidates(sel)
    if sel["count"] is None:
        return sorted(cand), [], []
    scored, approx, missing = [], [], []
    for t in cand:
        v, ap = metric(t, sel["method"])
        if v is None:
            missing.append(t)
            continue
        if ap:
            approx.append(t)
        scored.append((v, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    pick = [t for _, t in scored[: sel["count"]]]
    return pick, [t for t in approx if t in pick], missing


def names():
    s23, _ = sector_files()
    return {t: r["종목명"] for mk in s23.values() for t, r in mk.items()}


# ---------------------------------------------------------------- 배당 낱말
import re  # noqa: E402

DIV_WORDS = ("배당", "고배당", "분배금", "월배당")
# 배당 낱말 뒤 12자 안에 빼다/제외/없이/말고/미포함/신경 쓰지 등이 오면 부정문으로 본다.
_NEG = re.compile(r"(배당|분배금)[^,.]{0,12}?(빼|제외|없이|말고|미포함|안 넣|넣지 마|신경 ?쓰지|무시)")


@lru_cache(None)
def etf_names():
    rows = csv.DictReader(open(ETF_MASTER, encoding="utf-8-sig"))
    return tuple(sorted((r["name"] for r in rows), key=len, reverse=True))


def strip_etf_names(text):
    # 종목명 속 배당 낱말(ACE 미국배당다우존스, PLUS 고배당주)은 판정에서 뺀다. 대소문자·띄어쓰기 무시.
    for n in etf_names():
        pat = r"\s*".join(re.escape(ch) for ch in n.replace(" ", ""))
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    return text


def dividend_flag(text):
    """True(반영), False(낱말 없음), 'neg'(낱말은 있으나 부정문 = 반영 안 함). 종목명 속 낱말은 세지 않는다."""
    text = strip_etf_names(text)
    if not any(w in text for w in DIV_WORDS):
        return False
    return "neg" if _NEG.search(text) else True
