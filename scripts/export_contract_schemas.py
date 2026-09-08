#!/usr/bin/env python3
"""backend/app/contracts/schema/spec_v0_1.schema.json 을 Pydantic 모델에서 재생성한다.

Spec 모델(app/contracts/spec.py)을 고칠 때마다 이 스크립트를 실행해 커밋된
JSON Schema 파일을 최신 상태로 맞춘다. backend/tests/test_contracts.py의
드리프트 테스트가 둘이 어긋나면 실패시킨다.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.contracts.schema_export import export_spec_schema  # noqa: E402


def main() -> int:
    written = export_spec_schema()
    print(f"export_contract_schemas: {written} 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
