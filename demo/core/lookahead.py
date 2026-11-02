"""미래 참조 검사용 섭동. t일 이후 가격을 결정론적으로 크게 바꾼다 (random 미사용).

tests/test_lookahead.py(우리 엔진)와 baseline(LLM 이 만든 코드)이 같은 함수를 쓴다:
t일 이후를 바꿨을 때 t일까지의 판단(지표·신호·주문)이 바뀌면 미래 참조다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def perturb_after(df: pd.DataFrame, t) -> pd.DataFrame:
    """t 이후(t 는 그대로) 행을 바꾼다. t: 위치(int) 또는 날짜(그 날짜보다 뒤의 행)."""
    out = df.copy()
    if isinstance(t, (int, np.integer)):
        tail = out.index[t + 1:]
    else:
        tail = out.index[out.index > pd.Timestamp(t)]
    if not len(tail):
        return out
    k = np.arange(len(tail))
    for col in ["Open", "High", "Low", "Close"]:
        out.loc[tail, col] = out.loc[tail, col] * (2.5 + 0.7 * np.sin(k))
    out.loc[tail, "Low"] = out.loc[tail, ["Open", "Close", "Low"]].min(axis=1) * 0.5
    out.loc[tail, "High"] = out.loc[tail, ["Open", "Close", "High"]].max(axis=1) * 1.5
    out.loc[tail, "Volume"] = out.loc[tail, "Volume"] * 37
    return out
