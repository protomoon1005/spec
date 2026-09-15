FROM python:3.11-slim
WORKDIR /app

# api/worker/beat 세 서비스가 이 이미지를 공유한다.
# curl은 api 서비스의 healthcheck(GET /health)에 쓴다.
# postgresql-client는 시드 적재(psql)에 쓴다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY backend/app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
