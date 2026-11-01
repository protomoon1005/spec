# 감성 기사 채점용 일회성 이미지. 사용법은 scripts/score_news.py 머리말.
FROM python:3.12-slim
COPY scripts/score_news.requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/req.txt
ENV OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
WORKDIR /repo
