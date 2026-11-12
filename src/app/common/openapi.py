"""FastAPI 内部 OpenAPI（只用于契约测试与 nestjs 基线比对，不对外暴露）。"""

from typing import Any

from fastapi.openapi.utils import get_openapi
lazy from fastapi import FastAPI
lazy from fastapi.dependencies.models import Dependant
lazy from pydantic import BaseModel

from app.common.params import BODY_MODELS, QUERY_MODELS
from app.common.routing import ApiRoute

REF_TEMPLATE = "#/components/schemas/{model}"
_DROPPED_SCHEMAS = {"HTTPValidationError", "ValidationError"}


def _walk(dependant: Dependant) -> list[Dependant]:
    found = [dependant]
    for child in dependant.dependencies:
        found.extend(_walk(child))
    return found


def _models(route: ApiRoute, registry: dict[Any, type[BaseModel]]) -> list[type[BaseModel]]:
    return [registry[d.call] for d in _walk(route.dependant) if d.call in registry]


def _add_schema(schemas: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(by_alias=True, ref_template=REF_TEMPLATE, mode="validation")
    for name, definition in schema.pop("$defs", {}).items():
        schemas.setdefault(name, definition)
    schemas.setdefault(model.__name__, schema)
    return schema


def _query_parameters(model: type[BaseModel], schemas: dict[str, Any]) -> list[dict[str, Any]]:
    schema = _add_schema(schemas, model)
    required = set(schema.get("required", []))
    return [
        {"name": name, "in": "query", "required": name in required, "schema": prop}
        for name, prop in schema.get("properties", {}).items()
    ]


def build_openapi(app: FastAPI) -> dict[str, Any]:
    document = get_openapi(
        title=app.title,
        version="1.0",
        routes=app.routes,
        separate_input_output_schemas=False,
    )
    schemas: dict[str, Any] = document.setdefault("components", {}).setdefault("schemas", {})
    for route in app.routes:
        if not isinstance(route, ApiRoute) or not route.include_in_schema:
            continue
        operations = document["paths"].get(route.path_format, {})
        operations.pop("head", None)
        for method in route.methods or ():
            operation = operations.get(method.lower())
            if operation is None:
                continue
            operation.pop("x-api", None)
            operation.get("responses", {}).pop("422", None)
            for model in _models(route, BODY_MODELS):
                _add_schema(schemas, model)
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": REF_TEMPLATE.format(model=model.__name__)}
                        }
                    },
                }
            for model in _models(route, QUERY_MODELS):
                operation.setdefault("parameters", []).extend(_query_parameters(model, schemas))
    for name in _DROPPED_SCHEMAS:
        schemas.pop(name, None)
    return document
