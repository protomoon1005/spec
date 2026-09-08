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
