"""scripts/check_asof_guard.py 단위 테스트.

리포지토리 밖 위반을 잡아내고, 리포지토리 안 참조와 무관한 코드는 통과시키는지 확인한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = REPO_ROOT / "scripts" / "check_asof_guard.py"

_spec = importlib.util.spec_from_file_location("check_asof_guard", _SCRIPT_PATH)
check_asof_guard = importlib.util.module_from_spec(_spec)
sys.modules["check_asof_guard"] = check_asof_guard
_spec.loader.exec_module(check_asof_guard)


def test_real_app_tree_has_no_violations():
    errors = check_asof_guard.check()
    assert errors == []


def test_flags_direct_reference_outside_repositories(tmp_path):
    app_root = tmp_path / "app"
    (app_root / "routers").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "routers" / "spec.py").write_text(
        'from sqlalchemy import text\n'
        'q = text("SELECT * FROM feature_store WHERE ticker = %s")\n',
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert len(errors) == 1
    assert "feature_store" in errors[0]
    assert "routers" in errors[0]


def test_allows_reference_inside_repositories(tmp_path):
    app_root = tmp_path / "app"
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "repositories" / "feature_store.py").write_text(
        'SQL = "SELECT features FROM feature_store WHERE ticker = %s"\n',
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert errors == []


def test_flags_fstring_reference(tmp_path):
    app_root = tmp_path / "app"
    (app_root / "workers").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "workers" / "ingest.py").write_text(
        'code = "cpi"\n'
        'q = f"SELECT * FROM macro_indicators WHERE indicator_code = {code}"\n',
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert any("macro_indicators" in error for error in errors)


def test_flags_orm_class_import_and_usage_outside_repositories(tmp_path):
    """select(FeatureStore) 처럼 테이블명 문자열이 전혀 등장하지 않는 ORM 우회를 잡는다."""
    app_root = tmp_path / "app"
    (app_root / "routers").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "models").mkdir(parents=True)
    (app_root / "models" / "feature_store.py").write_text(
        "class FeatureStore:\n    pass\n",
        encoding="utf-8",
    )
    (app_root / "routers" / "spec.py").write_text(
        "from sqlalchemy import select\n"
        "from app.models.feature_store import FeatureStore\n"
        "q = select(FeatureStore)\n",
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert len(errors) == 2  # import 1건 + 사용(select) 1건
    assert all("feature_store" in error and "routers" in error for error in errors)


def test_flags_orm_class_import_with_alias(tmp_path):
    """`import ... as` 로 이름을 바꿔 들여와도 잡는다."""
    app_root = tmp_path / "app"
    (app_root / "workers").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "workers" / "ingest.py").write_text(
        "from app.models.macro_indicators import MacroIndicators as MI\n",
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert any("macro_indicators" in error for error in errors)


def test_flags_orm_class_attribute_reference(tmp_path):
    """models.ViewWeights 처럼 모듈 경유 속성 접근도 잡는다."""
    app_root = tmp_path / "app"
    (app_root / "workers").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "workers" / "recalc.py").write_text(
        "from app import models\n"
        "q = select(models.ViewWeights)\n",
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert any("view_weights" in error for error in errors)


def test_allows_orm_class_definition_in_models(tmp_path):
    """app/models/ 안에서의 클래스 '정의' 자체는 위반이 아니다 (ClassDef.name은 Name 노드가 아니다)."""
    app_root = tmp_path / "app"
    (app_root / "models").mkdir(parents=True)
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "models" / "feature_store.py").write_text(
        "class FeatureStore:\n    __tablename__ = 'feature_store'\n",
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    # __tablename__ 값 'feature_store' 문자열 리터럴은 여전히 걸린다 — models/는 allowed_dir가 아니므로.
    # 이 테스트는 그 사실을 명시하기 위한 것이지, models/를 허용 대상으로 승격하려는 것이 아니다.
    assert len(errors) == 1
    assert "feature_store" in errors[0]


def test_allows_orm_class_reference_inside_repositories(tmp_path):
    app_root = tmp_path / "app"
    (app_root / "repositories").mkdir(parents=True)
    (app_root / "repositories" / "view_weights.py").write_text(
        "from app.models.view_weights import ViewWeights\n"
        "q = select(ViewWeights)\n",
        encoding="utf-8",
    )

    errors = check_asof_guard.check(app_root=app_root, allowed_dir=app_root / "repositories")

    assert errors == []
