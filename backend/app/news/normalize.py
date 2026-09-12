# 기사 본문 정규화와 중복제거 해시. stdlib 만 쓴다 —
# 중복제거 1단계(정확 일치)는 CI 에서 상시 돌아야 하기 때문이다.
#
# 정규화가 하는 일은 "같은 기사인지"를 판정 가능한 형태로 본문을 깎는 것이다.
# 언론사 보일러플레이트(기자명·이메일·저작권 문구·"무단전재 및 재배포 금지")는
# 같은 기사가 재게재될 때마다 조금씩 달라져서, 남겨 두면 해시가 흔들린다.
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

_TAG = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_WHITESPACE = re.compile(r"\s+")

# 꼬리표(문서 끝에 붙는 저작권·배포 금지 문구). 등장 지점부터 끝까지 자른다.
#
# 주의: 이 패턴들은 NFKC 정규화 **뒤**에 적용된다. NFKC 는 ⓒ(U+24D2)를 "c" 로
# 바꾸므로 ⓒ 를 그대로 쓴 패턴은 빗나간다(테스트가 잡았다). 그래서 기호가 아니라
# 낱말로 잡는다 — "c" 하나를 패턴에 넣으면 본문의 평범한 글자를 먹는다.
_TRAILERS = (
    re.compile(r"저작권자.*$", re.DOTALL),
    re.compile(r"무단\s*전재.*$", re.DOTALL),
    re.compile(r"all\s+rights\s+reserved.*$", re.DOTALL | re.IGNORECASE),
)

# 본문 어디에나 나타나는 조각.
_INLINE_NOISE = (
    re.compile(r"[가-힣]{2,4}\s*기자"),  # 기자명
    re.compile(r"^\[[^\]]{1,20}\]\s*"),  # 앞머리 [서울=연합뉴스] 같은 태그
    re.compile(r"\(사진\s*=[^)]*\)"),
    re.compile(r"사진\s*=\s*\S+"),
)


def normalize_body(text: str | None) -> str:
    """본문을 중복 판정 가능한 형태로 깎는다. 같은 기사면 같은 문자열이 나와야 한다."""
    if not text:
        return ""

    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _unescape_entities(cleaned)
    cleaned = _TAG.sub(" ", cleaned)

    for pattern in _TRAILERS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _EMAIL.sub(" ", cleaned)
    for pattern in _INLINE_NOISE:
        cleaned = pattern.sub(" ", cleaned)

    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned.casefold()


def _unescape_entities(text: str) -> str:
    from html import unescape

    # RSS 는 본문을 이스케이프해서 싣는 경우가 많아 두 번 풀어야 태그가 드러난다.
    return unescape(unescape(text))


def dedup_hash(normalized_body: str, *, title: str | None = None) -> str:
    """정규화 본문의 sha256 hex.

    본문이 비어 있는 피드(제목만 주는 소스)를 위해 제목을 보조로 섞는다 —
    본문이 있으면 본문만으로 판정한다. 같은 기사가 소스마다 다른 제목을 달고
    오는 경우가 흔해서, 제목을 항상 섞으면 오히려 중복을 놓친다.
    """
    material = normalized_body if normalized_body else normalize_body(title)
    if not material:
        raise ValueError("본문과 제목이 모두 비어 있어 해시를 만들 수 없다")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_published_at(raw: str | None) -> datetime | None:
    """발행 시각을 tz-aware UTC 로 파싱한다. 실패하면 None.

    RSS 는 소스마다 KST(+0900)와 GMT 가 섞여 온다. naive datetime 을 그대로
    넣으면 lookback_hours 계산이 소스별로 최대 9시간 어긋나고, 그 어긋남이
    그대로 시점 경계를 넘는다. 그래서 **tz 가 없으면 실패로 본다** — 시각을
    모르는 기사는 쓸 수 없다는 쪽이 조용히 틀리는 것보다 낫다.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None

    parsed = _try_rfc822(text) or _try_iso8601(text)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


# 실측(2026-09-12): 매일경제 RSS 의 pubDate 가 "Sat, 12 Sep 2026 18:10:40 +09:00" 처럼
# 오프셋에 콜론을 넣어 온다. RFC 822 는 +0900 이라 표준 파서가 실패한다. 소스가
# 규격을 안 지키는 흔한 경우라, 시각을 버리는 대신 오프셋만 고쳐 다시 시도한다.
_OFFSET_WITH_COLON = re.compile(r"([+-]\d{2}):(\d{2})\s*$")


def _try_rfc822(text: str) -> datetime | None:
    # parsedate_to_datetime 은 관대해서, 규격 밖 오프셋(+09:00)을 만나면 예외를
    # 내지 않고 "-0000"(알 수 없는 tz)으로 읽어 **naive** datetime 을 돌려준다.
    # 그러면 실패한 줄 모르고 9시간을 잃는다. 그래서 예외 여부가 아니라
    # tz-aware 인지로 성공을 판정하고, 아니면 오프셋을 고쳐 다시 시도한다.
    fallback: datetime | None = None
    for candidate in (text, _OFFSET_WITH_COLON.sub(r"\1\2", text)):
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is not None:
            return parsed
        fallback = parsed
    return fallback


def _try_iso8601(text: str) -> datetime | None:
    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None
