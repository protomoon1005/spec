"""DB·모델을 읽는 관점 스코어러와 판단 결선.

app/views/ 는 stdlib 순수 함수만 둔다(CLAUDE.md). 가격·국면·모델을 저장소에서
as_of 로 읽어 그 순수 함수에 넘기는 일은 여기서 한다. 스코어러는 bridge 의
ScorerFn 시그니처 (tickers, *, as_of, features) -> list[ViewScore] 를 따른다.
"""
