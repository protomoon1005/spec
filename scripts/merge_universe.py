"""ETF 원장(04_etf_master.csv)과 가격 유니버스(universe.csv)를 하나로 합친다.

M2_답변_2026-09-20.md 2절: 두 목록의 합집합에 3년 커버리지 95% 필터를 적용하고
그 결과로 원장을 확정한다. 정본은 db/seeds/04_etf_master.csv 이고, 확정 후
data/universe.csv 는 원장의 파생본이 된다.

사용:
    python scripts/merge_universe.py review   # 후보 목록 → (fetch_prices) → 검토 파일
    python scripts/merge_universe.py apply    # 검토 파일(risk_tag 채운 뒤) → 원장·universe.csv
    python scripts/merge_universe.py derive   # 평소: 원장 → universe.csv 만 다시 만든다

review·apply 는 두 목록을 합칠 때만 쓴다. apply 는 검토 파일에서 통과한 종목만 원장에 남기므로,
원장에 종목을 직접 추가했다면 apply 가 아니라 derive 를 돌린다.
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from load_etf_master import COUNTRY_GROUP_MAP, SECTOR_GROUP_MAP, check_g1_leveraged_consistency

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "db" / "seeds" / "04_etf_master.csv"
UNIVERSE = ROOT / "data" / "universe.csv"
PRICES = ROOT / "data" / "prices.csv"
CANDIDATES = ROOT / "Claude outputs" / "etf_union_candidates.csv"
REVIEW = ROOT / "Claude outputs" / "etf_merge_review.csv"

WINDOW = (date(2023, 1, 2), date(2025, 12, 31))
CALENDAR_TICKER = "069500"
MIN_COVERAGE = 0.95

SECTOR_LABEL = {v: k for k, v in SECTOR_GROUP_MAP.items()}
COUNTRY_LABEL = {v: k for k, v in COUNTRY_GROUP_MAP.items()}


def read_rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["ticker"]: row for row in csv.DictReader(handle)}


def write_rows(path: Path, fields: list[str], rows: list[dict], encoding: str = "utf-8") -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def is_active(master_row: dict) -> bool:
    return master_row["active"].lower() == "true"


def universe_axes(universe_row: dict) -> dict[str, str]:
    # universe.csv 의 묶음 이름을 원장의 한글 분류값으로 되돌린다.
    return {"asset_group": universe_row["asset_group"],
            "sector": SECTOR_LABEL[universe_row["sector_group"]],
            "country": COUNTRY_LABEL[universe_row["country_group"]]}


def coverage_by_ticker() -> dict[str, float]:
    # 분모는 같은 구간 CALENDAR_TICKER 의 거래일 수다.
    dates: dict[str, set[str]] = {}
    start, end = (d.isoformat() for d in WINDOW)
    with PRICES.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if start <= row["date"] <= end:
                dates.setdefault(row["ticker"], set()).add(row["date"])
    calendar = dates[CALENDAR_TICKER]
    return {t: len(d & calendar) / len(calendar) for t, d in dates.items()}


def review() -> int:
    master, universe = read_rows(MASTER), read_rows(UNIVERSE)
    delisted = {t for t, r in master.items() if not is_active(r)}
    order = list(master) + [t for t in universe if t not in master]
    candidates = [t for t in order if t not in delisted]
    # 판정 구간 뒤에 상장한 종목은 구간 가격이 없어 커버리지 0 이 정의상 확정이다. 받지 않는다.
    unlisted = {t for t in candidates if t in master and master[t]["listed_date"] > WINDOW[1].isoformat()}
    to_fetch = [t for t in candidates if t not in unlisted]
    write_rows(CANDIDATES, ["ticker", "name"],
               [{"ticker": t, "name": (master.get(t) or universe[t])["name"]} for t in to_fetch])
    print(f"[merge] 합집합 {len(order)} · 상장폐지 제외 {len(candidates)} · 구간 뒤 상장 {len(unlisted)}"
          f" · 가격 받을 후보 {len(to_fetch)} → {CANDIDATES}")

    coverage = {**coverage_by_ticker(), **dict.fromkeys(unlisted, 0.0)}
    missing = [t for t in candidates if t not in coverage]
    if missing:
        print(f"[merge] 가격이 없는 후보 {len(missing)}개 — fetch_prices.py --universe 로 먼저 받는다")
        return 1

    rows = []
    for t in candidates:
        m, u = master.get(t), universe.get(t)
        if m:
            axes = {"asset_group": m["group_id"], "sector": m["sector"], "country": m["country"]}
        else:
            axes = universe_axes(u)
        diff = []
        if m and u:
            source = "both"
            theirs = universe_axes(u)
            diff = [f"{k}: {axes[k]}≠{theirs[k]}" for k in axes if axes[k] != theirs[k]]
        elif m:
            source = "master"
        else:
            source = "universe"
        rows.append({
            "ticker": t, "name": (m or u)["name"], "source": source,
            "coverage": f"{coverage[t]:.4f}", "pass": str(coverage[t] >= MIN_COVERAGE).lower(),
            "risk_tag": m["risk_tag"] if m else "",
            "is_leveraged": m["is_leveraged"] if m else "false",
            **axes, "axis_diff": "; ".join(diff),
        })
    write_rows(REVIEW, list(rows[0]), rows)

    failed = [r for r in rows if r["pass"] == "false"]
    new_ok = [r for r in rows if r["source"] == "universe" and r["pass"] == "true"]
    listing = ", ".join(f"{r['ticker']} {r['name']} {r['coverage']}" for r in failed)
    diffs = sum(1 for r in rows if r["axis_diff"])
    print(f"[merge] 탈락 {len(failed)}: {listing}")
    print(f"[merge] 통과한 새 종목 {len(new_ok)} (risk_tag 빈칸) · axis_diff {diffs}")
    print(f"[merge] 검토 파일 → {REVIEW}")
    return 0


def apply() -> int:
    master = read_rows(MASTER)
    review_rows = [r for r in read_rows(REVIEW).values() if r["pass"] == "true"]
    blank = [r["ticker"] for r in review_rows if not r["risk_tag"]]
    if blank:
        print(f"[merge] risk_tag 빈칸 {len(blank)}개 — 파일을 쓰지 않는다: {blank}")
        return 1

    fields = list(next(iter(master.values())))
    passed = {r["ticker"]: r for r in review_rows}
    rows = []
    for t, m in master.items():  # 기존 순서 유지. 통과했거나 상장폐지면 남긴다.
        if t in passed or not is_active(m):
            rows.append({**m, "risk_tag": passed[t]["risk_tag"] if t in passed else m["risk_tag"]})
    for t, r in passed.items():
        if t not in master:
            rows.append({**dict.fromkeys(fields, ""), "ticker": t, "name": r["name"], "sector": r["sector"],
                         "group_id": r["asset_group"], "risk_tag": r["risk_tag"], "is_leveraged": "false",
                         "active": "true", "country": r["country"]})
    check_g1_leveraged_consistency(rows)
    write_rows(MASTER, fields, rows, encoding="utf-8-sig")  # 원장은 BOM 을 유지한다
    print(f"[merge] 원장 {len(rows)}행 → {MASTER}")
    return derive()


def derive() -> int:
    # 원장에서 universe.csv(백테스트·가격 수집 대상)를 다시 만든다. 원장을 고친 뒤 평소에 쓰는 모드다.
    # 원장에 없는 ann_vol·marcap_eok·first_date 는 기존 universe.csv 값이 있으면 옮긴다.
    master, universe = read_rows(MASTER), read_rows(UNIVERSE)
    derived = []
    for t, r in master.items():
        if not is_active(r):
            continue
        previous = universe.get(t, {})
        derived.append({
            "ticker": t, "name": r["name"], "risk_tag": r["risk_tag"],
            "risk_tag_method": "product (04_etf_master.csv)", "ann_vol": previous.get("ann_vol", ""),
            "asset_group": r["group_id"], "sector_group": SECTOR_GROUP_MAP[r["sector"]],
            "country_group": COUNTRY_GROUP_MAP[r["country"]],
            "marcap_eok": previous.get("marcap_eok", ""),
            "first_date": previous.get("first_date", ""),
        })
    write_rows(UNIVERSE, list(derived[0]), derived)
    print(f"[merge] universe.csv {len(derived)}행 → {UNIVERSE}")
    return 0


if __name__ == "__main__":
    modes = {"review": review, "apply": apply, "derive": derive}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit(f"사용: merge_universe.py {{{'|'.join(modes)}}}")
    sys.exit(modes[sys.argv[1]]())
