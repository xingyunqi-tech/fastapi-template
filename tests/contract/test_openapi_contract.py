import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.common.openapi import build_openapi
from app.common.params import BODY_MODELS, QUERY_MODELS
from app.common.routing import ApiRoute, Auth
from app.common.validation import constraints, functions, metadata
from app.config import Settings
from app.factory import create_app

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "openapi" / "nestjs-baseline.json"
CONTRACT_DOC = ROOT / "docs" / "contract.md"
BASELINE: dict[str, Any] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
GLOBAL_HEADER = {"in": "header", "name": "Accept-Language"}
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
WHITELIST: set[tuple[str, str]] = set()

app = create_app(Settings.load())
INTERNAL = build_openapi(app)


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operations[method])
        for path, operations in document["paths"].items()
        for method in HTTP_METHODS
        if method in operations
    ]


BASELINE_OPS = {(p, m): op for p, m, op in _operations(BASELINE)}
IMPLEMENTED = [(p, m, op) for p, m, op in _operations(INTERNAL) if (p, m) not in WHITELIST]


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        return dict(document["components"]["schemas"][ref.rsplit("/", 1)[-1]])
    return schema


def _type(schema: dict[str, Any]) -> str | None:
    if "anyOf" in schema:
        kinds = {_type(s) for s in schema["anyOf"]} - {"null"}
        return kinds.pop() if len(kinds) == 1 else "union"
    kind = schema.get("type")
    if kind == "integer":
        return "number"
    if "$ref" in schema or "allOf" in schema:
        return "object"
    return kind


def _shape(document: dict[str, Any], schema: dict[str, Any], depth: int = 0) -> Any:
    schema = _resolve(document, schema)
    if depth > 4:
        return _type(schema)
    if schema.get("type") == "array" or "items" in schema:
        return [_shape(document, schema.get("items", {}), depth + 1)]
    properties = schema.get("properties")
    if properties:
        return {name: _shape(document, prop, depth + 1) for name, prop in properties.items()}
    return _type(schema)


def _parameters(operation: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return sorted(
        (p["in"], p["name"], bool(p.get("required", False)))
        for p in operation.get("parameters", [])
        if {k: p.get(k) for k in GLOBAL_HEADER} != GLOBAL_HEADER
    )


def _ref_name(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    ref = schema.get("$ref") or schema.get("items", {}).get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _success_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    for status, response in operation.get("responses", {}).items():
        if status.startswith("2"):
            content = response.get("content", {}).get("application/json")
            if content:
                schema: dict[str, Any] | None = content.get("schema")
                return schema
    return None


def test_docs_json_is_baseline_bytes() -> None:
    response = TestClient(app).get("/docs-json")

    assert response.content == BASELINE_PATH.read_bytes()


@pytest.mark.parametrize(
    ("path", "method", "operation"), IMPLEMENTED, ids=[f"{m} {p}" for p, m, _ in IMPLEMENTED]
)
class TestImplementedOperation:
    def test_exists_in_baseline(self, path: str, method: str, operation: dict[str, Any]) -> None:
        assert (path, method) in BASELINE_OPS

    def test_identity(self, path: str, method: str, operation: dict[str, Any]) -> None:
        expected = BASELINE_OPS[(path, method)]

        assert operation.get("operationId") == expected.get("operationId")
        assert operation.get("tags") == expected.get("tags")

    def test_parameters(self, path: str, method: str, operation: dict[str, Any]) -> None:
        assert _parameters(operation) == _parameters(BASELINE_OPS[(path, method)])

    def test_request_body(self, path: str, method: str, operation: dict[str, Any]) -> None:
        expected = BASELINE_OPS[(path, method)].get("requestBody")
        actual = operation.get("requestBody")
        if expected is None:
            assert actual is None
            return
        assert actual is not None
        assert set(actual["content"]) == set(expected["content"])
        for content_type, media in expected["content"].items():
            expected_schema = media.get("schema")
            actual_schema = actual["content"][content_type].get("schema")
            assert _ref_name(actual_schema) == _ref_name(expected_schema)
            assert _shape(INTERNAL, actual_schema or {}) == _shape(BASELINE, expected_schema or {})

    def test_response_schema(self, path: str, method: str, operation: dict[str, Any]) -> None:
        expected = _success_schema(BASELINE_OPS[(path, method)])
        if expected is None:
            return
        actual = _success_schema(operation)
        assert actual is not None
        if _ref_name(expected) is not None:
            assert _ref_name(actual) == _ref_name(expected)
        assert _shape(INTERNAL, actual) == _shape(BASELINE, expected)

    def test_security(self, path: str, method: str, operation: dict[str, Any]) -> None:
        route = next(
            r
            for r in app.routes
            if isinstance(r, ApiRoute)
            and r.path_format == path
            and method.upper() in (r.methods or ())
        )
        declared_bearer = route.auth in {Auth.JWT, Auth.OPTIONAL_JWT, Auth.REFRESH_JWT}
        baseline_bearer = any(
            "bearer" in s for s in BASELINE_OPS[(path, method)].get("security", [])
        )

        assert declared_bearer == baseline_bearer


def test_implemented_operations_keep_baseline_order() -> None:
    baseline_order = [key for key in BASELINE_OPS if key in {(p, m) for p, m, _ in IMPLEMENTED}]
    implemented_order = [(p, m) for p, m, _ in IMPLEMENTED]

    assert implemented_order == baseline_order


def _reachable(dto: str, meta: metadata.ValidationMetadata, seen: set[str]) -> set[str]:
    if dto in seen or dto not in meta.classes:
        return seen
    seen.add(dto)
    for prop in meta.classes[dto].properties.values():
        if prop.type_target:
            _reachable(prop.type_target, meta, seen)
    return seen


def test_route_dtos_fully_supported_by_validation_engine() -> None:
    meta = metadata.load()
    roots = {model.__name__ for model in [*BODY_MODELS.values(), *QUERY_MODELS.values()]}
    unsupported: list[str] = []
    for root in roots:
        assert root in meta.classes, f"{root} 不在 validation.json 中"
        for dto in _reachable(root, meta, set()):
            for prop in meta.classes[dto].properties.values():
                unsupported.extend(
                    f"{dto}.{prop.name} @Transform {ref.source}"
                    for ref in prop.transforms
                    if ref.key not in functions.TRANSFORMS
                )
                for rule in prop.rules:
                    if rule.type == metadata.CONDITIONAL_VALIDATION:
                        if rule.condition.key not in functions.CONDITIONS:
                            unsupported.append(
                                f"{dto}.{prop.name} @ValidateIf {rule.condition.source}"
                            )
                    elif (
                        rule.type in {metadata.CUSTOM_VALIDATION, metadata.IS_DEFINED}
                        and constraints.resolve(rule.key, rule.default_message) is None
                    ):
                        unsupported.append(f"{dto}.{prop.name} {rule.key}")

    assert unsupported == []


def render_contract_doc() -> str:
    implemented = {(p, m) for p, m, _ in IMPLEMENTED}
    rows = [
        f"| {m.upper()} | `{p}` | {op.get('operationId', '')} | {', '.join(op.get('tags', []))} |"
        for (p, m), op in BASELINE_OPS.items()
        if (p, m) not in implemented
    ]
    whitelist = "\n".join(f"- `{m.upper()} {p}`" for p, m in sorted(WHITELIST)) or "- 无"
    return (
        "# 与 nestjs-template 的契约差异\n\n"
        "本文件由 `tests/contract/test_openapi_contract.py` 生成"
        "（`UPDATE_CONTRACT_DOC=1 make test-contract`），与实际不符时测试失败。\n\n"
        f"## 基线中未实现的接口（{len(rows)} 个）\n\n"
        "请求这些接口返回 404 信封 `Cannot <METHOD> <path>`。\n\n"
        "| 方法 | 路径 | operationId | tag |\n|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## 白名单（实现与基线不同）\n\n"
        + whitelist
        + "\n"
    )


def test_contract_doc_is_up_to_date() -> None:
    rendered = render_contract_doc()
    if os.environ.get("UPDATE_CONTRACT_DOC") == "1":
        CONTRACT_DOC.parent.mkdir(exist_ok=True)
        CONTRACT_DOC.write_text(rendered, encoding="utf-8")
    assert CONTRACT_DOC.read_text(encoding="utf-8") == rendered


def test_source_records_pinned_commit() -> None:
    source = dict(
        line.split("=", 1)
        for line in (ROOT / "openapi" / "SOURCE").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )

    assert source["commit"] == "02fdb4def2df9b5866352d17ec949683d28cc887"
    assert re.fullmatch(r"[0-9a-f]{40}", source["validation_script"])
    assert source["environment"] == "NODE_ENV=development FILE_DRIVER=local"


def test_validation_json_has_no_unsupported_features() -> None:
    raw = json.loads((ROOT / "openapi" / "validation.json").read_text(encoding="utf-8"))
    pipe = raw["pipe"]
    unsupported = [
        f"{cls_name}.{prop_name}"
        for cls_name, cls in raw["classes"].items()
        for prop_name, prop in cls["properties"].items()
        for rule in prop["validations"]
        if rule["messageFunction"]
        or rule["groups"]
        or rule["always"] is not None
        or rule["validateIf"]
    ]

    assert pipe["enableImplicitConversion"] is False
    assert pipe["forbidNonWhitelisted"] is False
    assert unsupported == []
