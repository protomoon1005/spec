# 자연어 의도 추출 (M1 4단계).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 —
# as_of 가드 사정권이다.
#
# ── LLM 에게 자유 문자열을 받지 않는다 ───────────────────────────────
# 사용자가 쓰는 말은 무한하다. "배당" / "배당금" / "월배당" / "따박따박 나오는" 을
# 태그로 다 쫓으면 끝이 없다. 그래서 **LLM 의 일은 자유로운 말을 우리가 아는 낱말로
# 옮기는 것**이고, 고를 수 있는 낱말 목록을 스키마에 넣어 준다.
#
#   keywords      BBL 의 kw: 태그 값 중에서만
#   sectors       업종 묶음 이름 중에서만
#   asset_groups  자산군 이름 중에서만
#
# 이러면 뽑힌 값이 반드시 블록이나 종목에 맞는다. 사전에 없는 말이 들어와 아무것도
# 안 잡히는 일이 없다.
#
# ── 종목명만 자유 문자열이다 ─────────────────────────────────────────
# 종목은 176개라 목록으로 주기엔 많고, 사용자는 코드가 아니라 이름으로 말한다.
# 그래서 들린 대로 받아 적게 하고 **찾는 일은 우리가 한다**(mentioned_names).
# 코드를 지어내게 두면 없는 종목코드가 나온다.
#
# ── 성향은 여기서 안 정한다 ──────────────────────────────────────────
# "공격적으로" 라고 써 있어도 성향은 설문으로 확정된 값을 쓴다. 그 말은 keywords 에
# 남아 블록을 고르는 데만 쓰인다. 사용자가 말 한마디로 자기 한도를 올릴 수 있으면
# 성향 판정이 의미가 없다.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.repositories import bbl, etf_master, presets

SYSTEM_PROMPT = (
    "너는 투자 요청 문장에서 핵심을 뽑아내는 도구다. "
    "주어진 목록에 있는 값만 고른다. 목록에 없으면 고르지 않는다. "
    "확실하지 않으면 비워 둔다. 설명하지 말고 JSON 만 낸다."
)

MAX_NAME_MENTIONS = 5


@dataclass(frozen=True)
class Intent:
    keywords: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    asset_groups: tuple[str, ...] = ()
    mentioned_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedIntent:
    intent: Intent
    tag_queries: tuple[str, ...]  # BBL 검색에 넣을 태그
    requested_tickers: tuple[str, ...]  # 이름으로 찾아낸 종목
    unresolved_names: tuple[str, ...] = field(default=())  # 못 찾은 이름


def build_intent_schema() -> dict[str, Any]:
    # 고를 수 있는 값을 스키마에 박아 둔다. 목록은 시드에서 나오므로 블록이나 묶음이
    # 늘면 자동으로 따라간다.
    keywords = bbl.list_tags("kw:")
    sectors = [g for g in presets.list_group_ids(2) if g.startswith("SECTOR_")]
    asset_groups = presets.list_group_ids(1)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["keywords", "sectors", "asset_groups", "mentioned_names"],
        "properties": {
            "keywords": {"type": "array", "items": {"enum": keywords}},
            "sectors": {"type": "array", "items": {"enum": sectors}},
            "asset_groups": {"type": "array", "items": {"enum": asset_groups}},
            "mentioned_names": {
                "type": "array",
                "maxItems": MAX_NAME_MENTIONS,
                "items": {"type": "string"},
            },
        },
    }


def build_prompt(user_text: str) -> str:
    return (
        "아래 투자 요청에서 다음을 뽑아라.\n"
        "- keywords: 요청의 성격을 나타내는 낱말 (목록에서만)\n"
        "- sectors: 특정 업종을 원하면 그 업종 (목록에서만)\n"
        "- asset_groups: 주식·채권·원자재 중 원하는 것 (목록에서만)\n"
        "- mentioned_names: 사용자가 말한 종목 이름이나 코드를 들린 그대로\n\n"
        f"요청: {user_text}"
    )


def extract(user_text: str, client) -> Intent:
    # client 는 app.llm.client.LLMClient 다. 여기서 만들지 않고 받는다 —
    # 테스트가 가짜 클라이언트를 넣을 수 있어야 한다.
    raw = client.generate_json(
        build_prompt(user_text), build_intent_schema(), system=SYSTEM_PROMPT
    )
    return from_payload(raw)


def from_payload(raw: dict) -> Intent:
    # LLM 이 목록 밖의 값을 낼 수 있다(Ollama 는 형식 강제가 약하다). 여기서 한 번
    # 더 거른다 — 스키마를 믿고 그냥 쓰면 없는 업종으로 종목을 찾게 된다.
    allowed_keywords = set(bbl.list_tags("kw:"))
    allowed_sectors = {g for g in presets.list_group_ids(2) if g.startswith("SECTOR_")}
    allowed_groups = set(presets.list_group_ids(1))

    return Intent(
        keywords=_clean(raw.get("keywords"), allowed_keywords),
        sectors=_clean(raw.get("sectors"), allowed_sectors),
        asset_groups=_clean(raw.get("asset_groups"), allowed_groups),
        mentioned_names=_clean(raw.get("mentioned_names"), None)[:MAX_NAME_MENTIONS],
    )


def resolve(intent: Intent) -> ResolvedIntent:
    # 종목 이름을 코드로 바꾸고, 태그 질의를 만든다. LLM 이 안 들어가는 구간이라
    # 같은 의도면 항상 같은 결과가 나온다.
    tickers: list[str] = []
    unresolved: list[str] = []
    for mention in intent.mentioned_names:
        found = _find_ticker(mention)
        if found is None:
            unresolved.append(mention)
        elif found not in tickers:
            tickers.append(found)

    tags = [f"kw:{value}" for value in intent.keywords]
    tags += [f"sector:{value.removeprefix('SECTOR_').lower()}" for value in intent.sectors]
    tags += [f"asset:{value.lower()}" for value in intent.asset_groups]

    return ResolvedIntent(
        intent=intent,
        tag_queries=tuple(dict.fromkeys(tags)),
        requested_tickers=tuple(tickers),
        unresolved_names=tuple(unresolved),
    )


NAME_SEARCH_LIMIT = 10


def _find_ticker(mention: str) -> str | None:
    # 코드로 말했으면 그대로, 이름으로 말했으면 이름으로 찾는다.
    #
    # **이름이 정확히 일치하면 그걸 쓴다.** "KODEX 200" 은 부분일치로 네 개가 걸리지만
    # (200TR · 200IT TR · 200미국채혼합50) 사용자가 말한 건 앞의 하나다. 정확 일치를
    # 안 보면 멀쩡한 요청이 "못 찾았다" 로 떨어진다.
    #
    # 정확히 맞는 게 없고 부분일치가 여럿이면 고르지 않는다 — 엉뚱한 종목을 담는
    # 것보다 되묻는 편이 낫다.
    mention = mention.strip()
    if not mention:
        return None
    if mention in etf_master.get_by_tickers([mention]):
        return mention

    matches = etf_master.find_by_name(mention, limit=NAME_SEARCH_LIMIT)
    folded = _fold(mention)
    exact = [record for record in matches if _fold(record.name) == folded]
    if len(exact) == 1:
        return exact[0].ticker
    return matches[0].ticker if len(matches) == 1 else None


def _fold(value: str) -> str:
    # 띄어쓰기와 대소문자만 무시한다. "kodex200" 과 "KODEX 200" 은 같은 말이다.
    return "".join(value.split()).lower()


def _clean(values, allowed: set[str] | None) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value or (allowed is not None and value not in allowed):
            continue
        if value not in out:
            out.append(value)
    return tuple(out)
