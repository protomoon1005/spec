"""모델 산출물을 MinIO `models` 버킷에 올리고 내린다.

클라이언트는 boto3 가 아니라 minio SDK 다(2026-10-04 실측): 새로 깔리는 것이 wheel
2.9MB · 설치 약 13MB · 새 패키지 6개로, boto3(wheel 16MB · 설치 약 30MB, botocore 만
27MB) 보다 작고, 버킷 하나에 put/get 만 하는 용도라 코드 길이는 같다.
접속 정보는 compose 가 넣는 MINIO_ENDPOINT · MINIO_ROOT_USER · MINIO_ROOT_PASSWORD 다.
"""
from __future__ import annotations

import io
import os

from minio import Minio

BUCKET = os.environ.get("MINIO_BUCKET_MODELS", "models")
_SCHEME = "s3://"


def _client() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", ""),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", ""),
        secure=False,
    )


def upload(key: str, blob: bytes) -> str:
    """blob 을 올리고 artifact_uri(s3://버킷/키)를 돌려준다."""
    _client().put_object(BUCKET, key, io.BytesIO(blob), len(blob), content_type="application/json")
    return f"{_SCHEME}{BUCKET}/{key}"


def download(artifact_uri: str) -> bytes:
    if not artifact_uri.startswith(_SCHEME):
        raise ValueError(f"artifact_uri 형식이 아니다: {artifact_uri!r}")
    bucket, key = artifact_uri[len(_SCHEME) :].split("/", 1)
    response = _client().get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
