# 조각 찾기 (전략서 v0.2 명세화 1번째 일). 규칙만 쓰고 AI 는 쓰지 않는다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 설계 문서 "문장을 빈칸으로 나누는 방법"(2026-11-02).
#   1. 문장을 정규화한다(NFKC, 공백 하나로)
#   2. 표현 표 · 거절 사전(db/seeds/expressions.yaml)의 정규식과 종목 이름으로 후보 조각을 모두 찾는다
#   3. 겹치면 가장 긴 조각이 이긴다. 길이가 같으면 앞에 있는 것, 그다음 종류 순서(KIND_ORDER)
#   4. 남은 조각에 문장 순서대로 1부터 번호를 붙인다. AI 는 이 번호로만 빈칸을 짝짓는다
# 조각 찾기는 값을 옮겨 적기만 한다. 기본값, 단위 환산(% 를 소수로), 부정어 처리 같은 해석은
# 값 바꾸기 단계가 한다.
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LEFT_BOUNDARY = r"(?<![가-힣A-Za-z0-9])"
# 종목 이름 뒤에 올 수 있는 것: 한글이 아닌 글자, 또는 조사가 시작하는 글자.
# "삼성전자우" 의 "삼성전자" 처럼 이름 뒤에 다른 낱말이 붙은 경우를 막는다
NAME_FOLLOWERS = "이가은는을를과와랑도만의에로으하께까부보처마다및엔"
NAME_RIGHT_BOUNDARY = rf"(?![가-힣A-Za-z0-9])|(?=[{NAME_FOLLOWERS}])"

KIND_ORDER = (
    "reject", "instrument", "indicator", "rebalance", "verb", "action", "subject", "weight",
    "dividend", "selection", "sector", "product", "period", "capital", "number", "negation",
)
KINDS = frozenset(KIND_ORDER)


def default_dictionary_path() -> Path:
    # 호스트에서는 저장소 루트, 컨테이너에서는 /repo 에 db/ 가 있다(앱은 /app)
    here = Path(__file__).resolve()
    for root in (here.parents[3], Path("/repo")):
        path = root / "db" / "seeds" / "expressions.yaml"
        if path.exists():
            return path
    raise FileNotFoundError("db/seeds/expressions.yaml 을 찾지 못했다")


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    pattern: re.Pattern
    meaning: Mapping
    examples: tuple[str, ...]


@dataclass(frozen=True)
class Dictionary:
    entries: tuple[Entry, ...]
    instrument_aliases: Mapping[str, str]
    brand_aliases: Mapping[str, tuple[str, ...]]
    instrument_stopwords: frozenset[str]
    version: str  # 파일 내용의 sha256 앞 12자리. 전략서 meta.dictionary_version 에 적는다


@dataclass(frozen=True)
class Span:
    id: int
    start: int
    end: int
    text: str
    kind: str
    entry_id: str
    meaning: Mapping = field(default_factory=dict)

    def label(self) -> str:
        # AI 에게 주는 선택지 값. 번호와 글자를 같이 써서 번호를 밀려 적어도 글자가 안 맞아 잡힌다
        return f"{self.id}:{self.text}"


@dataclass(frozen=True)
class SpanResult:
    text: str  # 정규화한 문장. 조각의 start · end 는 이 문장 기준이다
    spans: tuple[Span, ...]
    dictionary_version: str

    def by_kind(self, *kinds: str) -> list[Span]:
        return [s for s in self.spans if s.kind in kinds]


def load_dictionary(path: Path | None = None) -> Dictionary:
    path = path or default_dictionary_path()
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    entries = []
    seen: set[str] = set()
    for item in data["entries"]:
        if item["id"] in seen:
            raise ValueError(f"표현 표 id 가 겹친다: {item['id']}")
        if item["kind"] not in KINDS or item["kind"] == "instrument":
            raise ValueError(f"표현 표 kind 를 모른다: {item['id']} {item['kind']}")
        seen.add(item["id"])
        entries.append(
            Entry(
                id=item["id"],
                kind=item["kind"],
                pattern=re.compile(item["pattern"].replace("{L}", LEFT_BOUNDARY), re.IGNORECASE),
                meaning=dict(item.get("meaning") or {}),
                examples=tuple(str(e) for e in item.get("examples") or ()),
            )
        )
    return Dictionary(
        entries=tuple(entries),
        instrument_aliases={str(k): str(v) for k, v in (data.get("instrument_aliases") or {}).items()},
        brand_aliases={str(k): tuple(v) for k, v in (data.get("brand_aliases") or {}).items()},
        instrument_stopwords=frozenset(data.get("instrument_stopwords") or ()),
        version=hashlib.sha256(raw).hexdigest()[:12],
    )


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


class InstrumentIndex:
    # 종목 이름 찾기. 정식 이름(종목 원장)과 줄임말(표현 표), 운용사 한글 표기를 하나의 정규식으로 묶는다.
    # 이름 글자 사이의 띄어쓰기는 있어도 없어도 된다("KODEX 200" = "KODEX200"). 대소문자를 가리지 않는다
    def __init__(self, records: Iterable[tuple[str, str]], dictionary: Dictionary):
        names: dict[str, str] = {}
        for ticker, name in records:
            for variant in _name_variants(name, dictionary.brand_aliases):
                names.setdefault(_key(variant), ticker)
        for alias, ticker in dictionary.instrument_aliases.items():
            names.setdefault(_key(alias), ticker)
        for word in dictionary.instrument_stopwords:
            names.pop(_key(word), None)
        self._ticker_by_key = names
        if names:
            alternatives = sorted(names, key=len, reverse=True)
            body = "|".join(r"\s*".join(re.escape(ch) for ch in key) for key in alternatives)
            self._pattern = re.compile(
                rf"{LEFT_BOUNDARY}(?:{body})(?:{NAME_RIGHT_BOUNDARY})", re.IGNORECASE
            )
        else:
            self._pattern = None

    def find(self, text: str) -> list[tuple[int, int, str]]:
        if self._pattern is None:
            return []
        return [
            (m.start(), m.end(), self._ticker_by_key[_key(m.group(0))])
            for m in self._pattern.finditer(text)
        ]


def _key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).upper()


def _name_variants(name: str, brand_aliases: Mapping[str, Sequence[str]]) -> list[str]:
    out = [name]
    first, _, rest = name.partition(" ")
    for korean in brand_aliases.get(first, ()):
        out.append(f"{korean} {rest}" if rest else korean)
    return out


def find_spans(text: str, dictionary: Dictionary, instruments: InstrumentIndex | None = None) -> SpanResult:
    text = normalize(text)
    order = {kind: i for i, kind in enumerate(KIND_ORDER)}
    candidates: list[tuple[int, int, str, str, Mapping]] = []
    for entry in dictionary.entries:
        for m in entry.pattern.finditer(text):
            start, end = _trim(text, m.start(), m.end())
            if end > start:
                candidates.append((start, end, entry.kind, entry.id, _fill_meaning(entry.meaning, m)))
    if instruments is not None:
        for start, end, ticker in instruments.find(text):
            candidates.append((start, end, "instrument", "instrument", {"ticker": ticker}))

    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0], order[c[2]]))
    taken: list[tuple[int, int, str, str, Mapping]] = []
    for cand in candidates:
        if all(cand[1] <= t[0] or cand[0] >= t[1] for t in taken):
            taken.append(cand)
    taken.sort(key=lambda c: c[0])
    spans = tuple(
        Span(id=i, start=s, end=e, text=text[s:e], kind=k, entry_id=eid, meaning=meaning)
        for i, (s, e, k, eid, meaning) in enumerate(taken, start=1)
    )
    return SpanResult(text=text, spans=spans, dictionary_version=dictionary.version)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _fill_meaning(meaning: Mapping, match: re.Match) -> dict:
    groups = match.groupdict()
    out: dict = {}
    for key, value in meaning.items():
        if isinstance(value, str) and value.startswith("$"):
            out[key] = _as_number(groups.get(value[1:]))
        else:
            out[key] = value
    return out


def _as_number(raw: str | None):
    if raw is None:
        return None
    cleaned = re.sub(r"\s+", "", raw).replace(",", "")
    try:
        number = float(cleaned)
    except ValueError:
        return raw
    return int(number) if number.is_integer() and "." not in cleaned else number


def negated_span_ids(result: SpanResult, kinds: Sequence[str] = ("reject", "dividend")) -> set[int]:
    # "MACD 말고", "배당은 빼고" 처럼 부정어 바로 앞의 조각을 없앤다(설계 문서 "거절 사전").
    # 사이에는 공백과 조사(은 · 는 · 도 · 을 · 를 · 이 · 가)만 올 수 있다
    out: set[int] = set()
    spans = result.spans
    for i, span in enumerate(spans):
        if span.kind != "negation" or i == 0:
            continue
        before = spans[i - 1]
        gap = result.text[before.end : span.start]
        if before.kind in kinds and re.fullmatch(r"\s*(?:은|는|도|을|를|이|가)?\s*", gap):
            out.add(before.id)
    return out
