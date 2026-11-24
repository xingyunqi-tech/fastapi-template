import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli
from app.cli.generate import ResourceNames, generate_resource, pluralize

ROOT = Path(__file__).resolve().parents[2]
BIN = Path(sys.executable).parent


@pytest.mark.parametrize(
    ("word", "plural"),
    [("product", "products"), ("category", "categories"), ("box", "boxes"), ("day", "days")],
)
def test_pluralize(word: str, plural: str) -> None:
    assert pluralize(word) == plural


@pytest.mark.parametrize("name", ["ProductCategory", "product-category", "product_category"])
def test_names_from_any_spelling(name: str) -> None:
    assert ResourceNames.parse(name) == ResourceNames(
        class_name="ProductCategory",
        class_plural="ProductCategories",
        camel="productCategory",
        snake="product_category",
        package="product_categories",
        url="product-categories",
        table="product_category",
        tag="Product categories",
    )


def test_plural_override() -> None:
    names = ResourceNames.parse("Person", plural="People")
    assert (names.package, names.url, names.class_plural) == ("people", "people", "People")


@pytest.mark.parametrize("name", ["1abc", "bad name", "", "Class"])
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match=r"\S"):
        ResourceNames.parse(name)


def test_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    generate_resource("Widget", tmp_path)
    with pytest.raises(FileExistsError):
        generate_resource("Widget", tmp_path)
    generate_resource("Widget", tmp_path, force=True)


def test_cli_reports_invalid_name(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["generate", "resource", "1x", "--root", str(tmp_path)])
    assert result.exit_code == 1


@pytest.mark.parametrize(("name", "table"), [("Users", "users"), ("Sessions", "sessions")])
def test_rejects_table_used_by_existing_model(tmp_path: Path, name: str, table: str) -> None:
    with pytest.raises(ValueError, match=f"'{table}'"):
        generate_resource(name, tmp_path)
    result = CliRunner().invoke(cli, ["generate", "resource", name, "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert f"'{table}'" in result.output
    assert not (tmp_path / "src").exists()


def test_allows_table_owned_by_target_module(tmp_path: Path) -> None:
    generate_resource("Region", tmp_path)
    assert (tmp_path / "src" / "app" / "modules" / "regions" / "models.py").exists()


def _project_copy(target: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src", target / "src", ignore=ignore)
    shutil.copytree(ROOT / "tests", target / "tests", ignore=ignore)
    shutil.copy2(ROOT / "pyproject.toml", target / "pyproject.toml")
    (target / "openapi").symlink_to(ROOT / "openapi")
    return target


def _run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_generated_module_passes_lint_types_and_registers_routes(tmp_path: Path) -> None:
    project = _project_copy(tmp_path / "project")
    result = CliRunner().invoke(
        cli, ["generate", "resource", "ProductCategory", "--root", str(project)]
    )
    assert result.exit_code == 0, result.output
    files = [
        "src/app/modules/product_categories",
        "tests/integration/test_product_categories.py",
    ]
    ruff = str(BIN / "ruff")
    _run(ruff, "check", "--no-cache", *files, cwd=project)
    _run(ruff, "format", "--check", "--no-cache", *files, cwd=project)
    _run(
        str(BIN / "mypy"),
        "--config-file",
        "pyproject.toml",
        "--cache-dir",
        str(tmp_path / "mypy-cache"),
        *files,
        cwd=project,
    )
    script = textwrap.dedent(
        """
        from fastapi.testclient import TestClient

        from app.common.openapi import build_openapi
        from app.config import Settings
        from app.factory import create_app
        from app.modules.product_categories.router import router

        app = create_app(Settings.load(), extra_routers=[(router, "/api")])
        routes = sorted(
            (method, route.path)
            for route in app.routes
            if "product-categories" in getattr(route, "path", "")
            for method in route.methods
        )
        print("routes:", routes)
        paths = build_openapi(app)["paths"]
        assert paths["/api/v1/product-categories"]["get"]["operationId"] == "listProductCategories"
        response = TestClient(app).get("/api/v1/product-categories")
        assert response.status_code == 401
        assert response.json()["errorKey"] == "common.unauthorized"
        """
    )
    output = _run(
        sys.executable,
        "-c",
        script,
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(project / "src")},
    )
    lines = [line for line in output.splitlines() if line.startswith("routes: ")]
    assert lines == [
        "routes: "
        + str(
            [
                ("DELETE", "/api/v1/product-categories/{id}"),
                ("GET", "/api/v1/product-categories"),
                ("GET", "/api/v1/product-categories/{id}"),
                ("PATCH", "/api/v1/product-categories/{id}"),
                ("POST", "/api/v1/product-categories"),
            ]
        )
    ]
