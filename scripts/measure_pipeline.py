#!/usr/bin/env python3
"""명세화 전체(조각 찾기, AI 짝짓기, 조립, 판정)를 평가 데이터셋 개발용(dev) 문장으로 잰다.

v0.2 구현 5단계부터 쓴다(docs/v0.2-plan.md). 최종 평가용(test) 문장은 읽지 않는다(eval/README.md).

  ok      조립한 전략서가 정답 전략서와 같은지(meta 는 빼고 비교)
  ask     되물을 거리 종류가 정답(expected_ask 의 slot)과 같은지
  reject  거절 사유 코드가 정답과 같은지
AI 응답은 scripts/measure_pairing.py 와 같은 캐시(/tmp/pairing-cache)를 쓴다.

실행 (api 컨테이너, Ollama 필요):
    docker compose exec api python /repo/scripts/measure_pipeline.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

from measure_pairing import CachedClient  # noqa: E402

from app.contracts.strategy_v0_2 import StrategyV0_2  # noqa: E402
from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.m1 import assemble, decide, pairing, spans  # noqa: E402
from app.repositories import etf_master  # noqa: E402

DATASET = REPO / "eval" / "dataset.jsonl"


def run(text, dictionary, index, client):
    # 조각 찾기, AI 전 판정, 짝짓기, 조립, 판정. 검사 1~3단계는 6단계에서 붙인다
    result = spans.find_spans(text, dictionary, index)
    early = decide.precheck(result)
    if early is not None:
        return "reject", early.code, None
    outcome = pairing.pair(result, client)
    built = assemble.assemble(result, outcome.pairing, unresolved_issues=outcome.issues)
    decision = decide.decide(built, rounds_asked=0)
    if decision.status == "reject":
        return "reject", decision.rejection.code, built
    if decision.status == "ask":
        return "ask", sorted({a.kind for a in decision.all_asks}), built
    return "ok", built.spec, built


def _comparable(spec_dict: dict) -> dict:
    data = StrategyV0_2.model_validate(spec_dict).model_dump(mode="json")
    data.pop("meta", None)
    return data


def main() -> int:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    dev = [r for r in rows if r["split"] == "dev"]
    dictionary = spans.load_dictionary()
    index = spans.InstrumentIndex([(r.ticker, r.name) for r in etf_master.list_all(include_leveraged=True)],
                                  dictionary)
    client = CachedClient(OllamaClient())
    score = {"ok": [0, 0], "ask": [0, 0], "reject": [0, 0]}
    type_ok = 0
    for row in dev:
        kind, value, built = run(row["text"], dictionary, index, client)
        expected = row["type"]
        type_ok += kind == expected
        score[expected][1] += 1
        if expected == "ok":
            got = _comparable(value.model_dump(mode="json")) if kind == "ok" else None
            good = got == _comparable(row["expected_spec"])
        elif expected == "ask":
            want = sorted({a["slot"] for a in row["expected_ask"]})
            good = kind == "ask" and value == want
        else:
            good = kind == "reject" and value == row["expected_reject"]["code"]
        score[expected][0] += good
        mark = "OK  " if good else "MISS"
        print(f"{mark} {row['id']} 정답 {expected:6} 결과 {kind:6} | {row['text']}")
        if not good:
            if expected == "ok" and kind == "ok":
                got, want = _comparable(value.model_dump(mode="json")), _comparable(row["expected_spec"])
                for key in want:
                    if got.get(key) != want.get(key):
                        print(f"       {key}: 결과 {json.dumps(got.get(key), ensure_ascii=False)}")
                        print(f"       {key}: 정답 {json.dumps(want.get(key), ensure_ascii=False)}")
            elif kind == "ask":
                print(f"       되물을 거리 {[(a.kind, a.path) for a in built.asks]}")
                if expected == "ask":
                    print(f"       정답 {[a['slot'] for a in row['expected_ask']]}")
            else:
                print(f"       결과 {value} / 정답 {row.get('expected_reject') or row.get('expected_ask')}")
    print()
    for kind, (good, total) in score.items():
        print(f"{kind:6} {good}/{total}")
    print(f"판정 종류만 {type_ok}/{len(dev)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
