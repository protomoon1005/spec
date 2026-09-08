"""Pydantic 모델 -> JSON Schema 파일 export.

계약 ①(Spec JSON 스키마 v0.1)만 대상이다. M1의 structured outputs가 이
파일을 직접 입력으로 쓰므로, Pydantic 모델과 별도로 저장소에 커밋해 둔다
(docs/infra-spec.md 6단계). 다른 세 계약(②③④)은 M2 내부 모듈 간 형식 계약일
뿐 LLM 입력이 아니므로 export 대상이 아니다.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.contracts.spec import SpecV0_1

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
SPEC_SCHEMA_PATH = SCHEMA_DIR / "spec_v0_1.schema.json"


def build_spec_schema() -> dict:
    schema = SpecV0_1.model_json_schema()
    # Pydantic v2는 draft 2020-12 방언을 쓰지만 $schema 키를 안 채운다.
    # M1이 어느 방언인지 추측하지 않도록 명시한다.
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def export_spec_schema(path: Path = SPEC_SCHEMA_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = build_spec_schema()
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    written = export_spec_schema()
    print(f"schema_export: {written} 갱신")
