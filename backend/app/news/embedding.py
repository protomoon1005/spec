# 중복제거용 문장 임베딩. transformers 가 필요하므로 ml extra 다 —
# 이 모듈을 쓰는 테스트에는 requires_ml 마커를 붙인다.
#
# ── 모델 선택 (2026-09-12, M3 확정) ──────────────────────────────────
# news_articles.embedding 은 VECTOR(768) 로 이미 고정돼 있다. 현서가 정본에
# 차원이 없어 잡은 값이고, 차원을 바꾸려면 컬럼 타입 변경 + HNSW 인덱스
# 재생성이라 되돌리는 비용이 크다. 그래서 768 을 내는 모델 중에서 고른다.
#
# 고른 것: jhgan/ko-sroberta-multitask
#   - 중복제거는 "같은 기사인가"를 재는 문장 유사도 과제다. 감성 분류
#     (KF-DeBERTa)와 목적이 달라 같은 모델일 필요가 없다.
#   - KLUE-STS/NLI 로 학습된 한국어 문장 임베딩이라 유사도 용도에 맞고,
#     hidden size 가 768 이라 컬럼을 건드리지 않는다.
#   - sentence-transformers 를 새로 넣지 않고 transformers 만으로 쓴다
#     (평균 풀링 + L2 정규화). 의존성을 하나라도 덜 늘리려는 것이다.
#
# 평균 풀링은 attention_mask 로 패딩을 빼고 평균한다. L2 정규화를 하면
# 코사인 유사도가 내적과 같아지고, pgvector 의 <=> (코사인 거리) 와도 맞는다.
from __future__ import annotations

import math

DEDUP_EMBEDDING_MODEL = "jhgan/ko-sroberta-multitask"
EMBEDDING_DIM = 768
MAX_TOKENS = 256

# 중복(재게재) 판정 임계값. **실측으로 정했다** — 2026-09-12, 실제 RSS 5개 소스에서
# 받은 기사 320건(약 2일치)의 51,040개 쌍을 전부 재고 상위 구간을 눈으로 확인했다.
#
#   유사도   쌍의 정체                                          판정
#   ------   ------------------------------------------------  ----------
#   1.0000   같은 기사가 소스 둘에 그대로 실림                   진짜 중복
#   0.9987   "(종합)" 꼬리만 다른 같은 기사                      진짜 중복
#   0.9792   국고채 금리 기사의 수치 갱신판(4.014% / 4.018%)     진짜 중복
#   0.9766   같은 사건의 [1보] / [속보]                          진짜 중복
#   0.9598   "[인사] 국세청" 서로 다른 인사 발표 두 건           **오탐**
#   0.9375   같은 사건을 두 소스가 다른 제목으로                 진짜 중복
#   0.9317   같은 사건의 [2보] / [속보]                          진짜 중복
#   0.9072   경기 진단 기사 두 건(관련은 있으나 다른 기사)       **오탐**
#
# 전체 분포는 중앙값 0.1854, 99분위 0.5119, 99.9분위 0.8177 이라 무관한 기사끼리는
# 충분히 낮다. 문제는 0.93~0.96 회색 구간으로, 진짜 중복과 오탐이 섞여 있다
# (0.9598 오탐이 0.9375 진짜 중복보다 높다 — 깔끔한 분리선이 없다).
#
# 그래서 **정밀도 우선으로 0.97** 을 쓴다. 서로 다른 기사를 지우는 쪽(오탐)이
# 재게재를 남기는 쪽(미탐)보다 나쁘다 — 지운 기사는 복구되지 않지만 남은
# 재게재는 감성 집계에서 가중치가 조금 치우칠 뿐이다. 미탐으로 넘어가는
# 0.9375·0.9317 은 1단계 해시가 잡지 못한 채 남는다는 뜻이고, 그건 감수한다.
#
# 표본이 5개 소스·2일치뿐이라 과거 아카이브를 확보하면 다시 재야 한다.
DEDUP_SIMILARITY_THRESHOLD = 0.97

_model = None
_tokenizer = None


def _load():
    global _model, _tokenizer
    if _model is None or _tokenizer is None:
        from transformers import AutoModel, AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(DEDUP_EMBEDDING_MODEL)
        _model = AutoModel.from_pretrained(DEDUP_EMBEDDING_MODEL)
        _model.eval()
    return _tokenizer, _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """문장 목록 -> L2 정규화된 768차원 벡터 목록. 추론에 난수가 개입하지 않는다."""
    if not texts:
        return []

    import torch

    tokenizer, model = _load()
    encoded = tokenizer(
        texts, padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt"
    )
    with torch.no_grad():
        output = model(**encoded)

    token_vectors = output.last_hidden_state
    mask = encoded["attention_mask"].unsqueeze(-1).type_as(token_vectors)
    pooled = (token_vectors * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
    normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
    return [[float(value) for value in row] for row in normalized]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """L2 정규화된 벡터끼리는 내적이 곧 코사인 유사도다. 검증용으로 길이도 본다."""
    if len(left) != len(right):
        raise ValueError(f"차원이 다르다: {len(left)} vs {len(right)}")
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, dot))
