# 감성 렌즈 정의 — 빅카인즈 기사 범위 · 묶음 매핑 · 채점 모델 · 긍정/부정 판정. 정본은 여기 하나다.
# scripts/score_news.py · scripts/ingest_news.py · app/scoring/sentiment.py 가 이 값을 그대로 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/views 모듈과 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴에서 가드 대상 테이블명을 찾는다.
# 이 파일은 오프라인 채점 컨테이너가 패키지 없이 파일로 읽는다 — stdlib 만, app import 금지.
#
# ── 고정 원칙 (데이터 스누핑 금지) ───────────────────────────────────
# 아래 매핑 · 모델 · 임계값은 2026-10-27, 수익률을 하나도 보기 전에 정했다. 결과를 보고 바꾸지
# 않는다. 바꾸려면 LENS_ID 를 올리고 전 구간을 다시 채점 · 적재하고 상태 경계 측정을 다시 한다.
#
# ── 범위 ─────────────────────────────────────────────────────────────
# 통합분류1~3 중 하나라도 아래 분류면 포함. 빅카인즈 "분석제외" 칸(중복 · 예외)이 비어 있지 않으면 뺀다.
# 한 기사가 여러 묶음이면 묶음마다 한 행(news_sentiment.sector = 묶음).
#   EQUITY    <- 경제>증권_증시
#   BOND      <- (경제>금융_재테크 또는 경제>외환) 이면서 **제목**에 BOND_TITLE_WORDS 중 하나가 든 기사
#   COMMODITY <- 경제>자원
# BOND 규칙(2026-10-27 사용자 확정): 두 분류 모두 채권과 무관한 기사(대출 · 보험 · 환율)가 대부분이라
# 분류만으로는 채권 기사가 묽다(2023: 채권 · 금리 단어가 든 제목 비율 외환 27% · 금융_재테크 9%).
# 원장 BOND 15종목은 국고채 · 회사채 · 미국채 · 단기채 · CD/KOFR 금리형이다.
# 낱말 목록은 결과를 보기 전에 고정했다. 부분 문자열 일치다(예: '국채금리' 는 '국채' 로도 걸린다).
# 표기 변형으로 '크레디트'(크레딧의 다른 표기)만 더했다.
#
# ── 채점 ─────────────────────────────────────────────────────────────
# snunlp/KR-FinBert-SC 사전학습 그대로(긍정/중립/부정 3분류, PLM — LLM 아님). 커밋 고정.
# 입력 = 제목 + 줄바꿈 + 본문 앞 BODY_CHARS 자. 본문은 채점에만 쓰고 저장하지 않는다.
# polarity = p_pos − p_neg, confidence = 세 확률 중 최댓값.
# 기사 한 건의 긍정/부정은 polarity 로 가른다: >= +POLARITY_EDGE 긍정, <= −POLARITY_EDGE 부정, 그 사이 중립.
# (저장 칸이 polarity · confidence 둘뿐이라 argmax 라벨을 되살릴 수 없다 — known-issues 참고.)
from __future__ import annotations

LENS_ID = "bigkinds-market-v1"

CATEGORY_GROUPS: dict[str, str] = {
    "경제>증권_증시": "EQUITY",
    "경제>자원": "COMMODITY",
}
BOND_CATEGORIES: tuple[str, ...] = ("경제>금융_재테크", "경제>외환")
BOND_TITLE_WORDS: tuple[str, ...] = (
    "채권", "금리", "국채", "국고채", "회사채", "기준금리", "금통위", "국채금리", "미국채",
    "장기물", "단기물", "크레딧",
    "크레디트",  # 표기 변형
)
GROUPS: tuple[str, ...] = ("EQUITY", "BOND", "COMMODITY")

MODEL_NAME = "snunlp/KR-FinBert-SC"
MODEL_REVISION = "f8586286cc3161fb648e9fee09a456069fd846d0"
MAX_LEN = 128
BODY_CHARS = 200
MODEL_VERSION = f"{MODEL_NAME}@{MODEL_REVISION[:12]}/len{MAX_LEN}/body{BODY_CHARS}"

POLARITY_EDGE = 0.5


def groups_for(categories, *, title: str) -> tuple[str, ...]:
    # 통합분류 문자열들 + 제목 -> 묶음(중복 없이, GROUPS 순서).
    found = {CATEGORY_GROUPS[c] for c in categories if c in CATEGORY_GROUPS}
    if any(c in BOND_CATEGORIES for c in categories) and any(w in (title or "") for w in BOND_TITLE_WORDS):
        found.add("BOND")
    return tuple(g for g in GROUPS if g in found)


def score_text(title: str, body: str) -> str:
    return f"{title.strip()}\n{(body or '').strip()[:BODY_CHARS]}"


def label_of(polarity: float) -> int:
    # +1 긍정, −1 부정, 0 중립.
    if polarity >= POLARITY_EDGE:
        return 1
    if polarity <= -POLARITY_EDGE:
        return -1
    return 0
