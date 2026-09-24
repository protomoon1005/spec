#!/usr/bin/env python3
"""ml extra 가 깔린 이미지에서 LightGBM 이 실제로 도는지 확인한다.

import 만으로는 부족하다 — libgomp(OpenMP)는 학습 스레드가 뜰 때 쓰이므로
합성 데이터로 fit → predict_proba 까지 한 번 돌린다.

    docker compose exec api python /repo/scripts/verify_ml_env.py
"""
from __future__ import annotations

import sys


def main() -> int:
    import lightgbm
    import numpy as np
    import sklearn
    from lightgbm import LGBMClassifier

    print(f"lightgbm     {lightgbm.__version__}")
    print(f"scikit-learn {sklearn.__version__}")
    print(f"numpy        {np.__version__}")

    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 5))
    y = (x[:, 0] + 0.5 * x[:, 1] + rng.normal(scale=0.5, size=200) > 0).astype(int)

    model = LGBMClassifier(n_estimators=20, random_state=0, verbose=-1)
    model.fit(x, y)
    proba = model.predict_proba(x[:5])

    assert proba.shape == (5, 2), proba.shape
    assert np.allclose(proba.sum(axis=1), 1.0)
    print(f"fit 200x5 → predict_proba[:5, 1] = {np.round(proba[:, 1], 4).tolist()}")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
