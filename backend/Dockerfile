FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# backtest: 워커의 run_backtest 가 app/backtest/ 러너(vectorbt)를 부른다.
# 워커를 따로 두지 않고 단일 이미지에 넣는다(2026-09-28 결정, 실측 +230MB).
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev,backtest]"
COPY backend/app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
