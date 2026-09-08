#!/usr/bin/env python3
"""app/ 전체를 AST로 훑어 app/repositories/ 밖에서 feature_store · macro_indicators ·
view_weights 를 문자열로 참조하거나, 그에 대응하는 ORM 모델 클래스
(FeatureStore · MacroIndicators · ViewWeights)를 import/참조하는 코드를 찾아
실패시킨다 (docs/infra-spec.md 5단계).

피처 / 거시지표 / 관점 가중치는 app/repositories/ 의 단일 함수를 거쳐서만
조회해야 한다는 규약(CLAUDE.md)을 코드 리뷰가 아니라 CI에서 강제하기 위한 스크립트다.

테이블명 문자열만 잡으면 ORM으로 우회할 수 있다 (예: select(FeatureStore)에는
'feature_store' 문자열이 없다). 그래서 테이블당 관례적 ORM 클래스명도 함께 감시한다.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

GUARDED_TABLES = ("feature_store", "macro_indicators", "view_weights")
_TABLE_PATTERN = re.compile(r"\b(" + "|".join(GUARDED_TABLES) + r")\b")

# 테이블명 -> 관례적 ORM 모델 클래스명 (snake_case -> PascalCase).
GUARDED_CLASSES: dict[str, str] = {
    "FeatureStore": "feature_store",
    "MacroIndicators": "macro_indicators",
    "ViewWeights": "view_weights",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APP_ROOT = REPO_ROOT / "backend" / "app"
DEFAULT_ALLOWED_DIR = DEFAULT_APP_ROOT / "repositories"


def _violations_in_file(path: Path) -> list[tuple[int, str]]:
    """path 안에서 가드 대상 테이블명 문자열 또는 대응 ORM 클래스 참조를 찾는다.

    - 문자열 리터럴: f-string 조각도 ast.Constant로 노출되므로 ast.walk만으로 잡힌다.
    - ORM 클래스: import(alias) 와 이름 참조(Name/Attribute) 양쪽 다 잡는다.
      클래스 정의(class FeatureStore(Base): ...) 자체는 ClassDef.name이 문자열
      속성일 뿐 Name 노드를 만들지 않으므로, 정의 파일(app/models/)은 오탐되지 않는다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = _TABLE_PATTERN.search(node.value)
            if match:
                violations.append((node.lineno, match.group(1)))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                # alias.name: 실제로 들여오는 이름 (as로 가려도 이 값은 원래 이름이다).
                # alias.asname: 로컬에서 부르는 이름. 둘 중 하나라도 가드 클래스명과
                # 일치하면 잡는다 (import ... as MI 로 가려도, 반대로 asname 자체를
                # 클래스명으로 지어도 잡기 위함).
                table = GUARDED_CLASSES.get(alias.name) or GUARDED_CLASSES.get(alias.asname or "")
                if table is not None:
                    violations.append((node.lineno, table))
        elif isinstance(node, ast.Name):
            table = GUARDED_CLASSES.get(node.id)
            if table is not None:
                violations.append((node.lineno, table))
        elif isinstance(node, ast.Attribute):
            table = GUARDED_CLASSES.get(node.attr)
            if table is not None:
                violations.append((node.lineno, table))
    return violations


def check(app_root: Path = DEFAULT_APP_ROOT, allowed_dir: Path = DEFAULT_ALLOWED_DIR) -> list[str]:
    """app_root 아래 .py 파일 중 allowed_dir 밖에서 발견된 위반을 문자열 목록으로 반환한다."""
    allowed_dir = allowed_dir.resolve()
    errors: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        resolved = path.resolve()
        if resolved == allowed_dir or allowed_dir in resolved.parents:
            continue
        for lineno, table in _violations_in_file(path):
            errors.append(f"{path}:{lineno}: repositories/ 밖에서 '{table}' 테이블을 직접 참조했다")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("check_asof_guard: 위반 발견", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("check_asof_guard: 위반 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
