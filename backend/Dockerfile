FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# libgomp1: LightGBM 이 OpenMP 런타임을 요구한다. slim 이미지에 없으면
# import lightgbm 이 "libgomp.so.1: cannot open shared object file" 로 실패한다.
# backtest: 워커의 run_backtest 가 app/backtest/ 러너(vectorbt)를 부른다.
# 워커를 따로 두지 않고 단일 이미지에 넣는다(2026-09-28 결정, 실측 +230MB).
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev,ml,backtest]"
COPY backend/app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
