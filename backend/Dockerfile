FROM python:3.11-slim
WORKDIR /app

# api/worker/beat 세 서비스가 이 이미지를 공유한다 (docker-compose.yml
# x-backend-common). curl은 api 서비스의 healthcheck(GET /health)에 쓴다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ml extra(.[dev,ml])는 아직 넣지 않는다. 이미지 안에서 ML 의존성을 쓰는 코드가
# 하나도 없기 때문이다 — train_model·daily_judge는 스텁이고, Hedge와
# SignalIntegrator는 math만 쓴다. 개발도 호스트 venv에서 한다.
# CPU 휠이어도 torch가 769MB라(site-packages 933MB 실측) 지금 넣으면 ci.yml의
# `docker compose up` 이 PR마다 그만큼을 새로 받는다 — buildx 레이어 캐시가
# 없어서다. T3(LightGBM)에서 실제로 필요해질 때 buildx 캐시 도입과 함께 넣는다.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
