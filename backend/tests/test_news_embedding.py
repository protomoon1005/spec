"""중복제거 2단계(임베딩 유사도) 테스트.

transformers 가 필요해 requires_ml 이다. 1단계(정확 일치)는
test_news_normalize.py 가 의존성 없이 지키므로, 이 파일이 빠져도 수집기의
뼈대는 CI 에서 계속 검증된다.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_ml

SAME_STORY_A = (
    "KB금융 차기 회장 후보에 이재근 전 국민은행장이 내정됐다. "
    "이사회는 회장후보추천위원회를 열어 최종 후보를 확정했다."
)
SAME_STORY_B = (
    "[속보] KB금융 차기 회장 후보로 이재근 전 국민은행장이 확정됐다. "
    "회장후보추천위원회가 이날 이사회를 열고 최종 후보를 결정했다."
)
UNRELATED = (
    "정부가 청소년 상담 사업 예산을 늘린다고 밝혔다. "
    "지난해 상담 건수는 4만여 건으로 대인관계 고민이 절반을 넘었다."
)


def test_embedding_dimension_matches_the_column() -> None:
    # news_articles.embedding 은 VECTOR(768) 로 고정돼 있다. 차원이 어긋나면
    # 컬럼 타입 변경 + HNSW 인덱스 재생성이라 현서 결정 사항이 된다.
    from app.news.embedding import EMBEDDING_DIM, embed_texts

    vectors = embed_texts([SAME_STORY_A, UNRELATED])
    assert len(vectors) == 2
    assert all(len(vector) == EMBEDDING_DIM == 768 for vector in vectors)


def test_vectors_are_l2_normalized() -> None:
    # 정규화돼 있어야 내적 = 코사인 유사도이고 pgvector 의 <=> 와도 맞는다.
    import math

    from app.news.embedding import embed_texts

    vector = embed_texts([SAME_STORY_A])[0]
    norm = math.sqrt(math.fsum(value * value for value in vector))
    assert norm == pytest.approx(1.0, abs=1e-5)


def test_similar_articles_clear_the_threshold_and_unrelated_ones_do_not() -> None:
    from app.news.embedding import DEDUP_SIMILARITY_THRESHOLD, cosine_similarity, embed_texts

    same_a, same_b, other = embed_texts([SAME_STORY_A, SAME_STORY_B, UNRELATED])

    duplicate_similarity = cosine_similarity(same_a, same_b)
    unrelated_similarity = cosine_similarity(same_a, other)

    assert duplicate_similarity > DEDUP_SIMILARITY_THRESHOLD
    assert unrelated_similarity < DEDUP_SIMILARITY_THRESHOLD
    assert duplicate_similarity > unrelated_similarity


def test_identical_text_is_similarity_one() -> None:
    from app.news.embedding import cosine_similarity, embed_texts

    left, right = embed_texts([SAME_STORY_A, SAME_STORY_A])
    assert cosine_similarity(left, right) == pytest.approx(1.0, abs=1e-5)


def test_dimension_mismatch_raises() -> None:
    from app.news.embedding import cosine_similarity

    with pytest.raises(ValueError, match="차원이 다르다"):
        cosine_similarity([0.1, 0.2], [0.1, 0.2, 0.3])
