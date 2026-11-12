import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DEFAULT_VALIDATION_PATH = Path(__file__).resolve().parents[4] / "openapi" / "validation.json"

CUSTOM_VALIDATION = "customValidation"
NESTED_VALIDATION = "nestedValidation"
CONDITIONAL_VALIDATION = "conditionalValidation"
IS_DEFINED = "isDefined"
WHITELIST = "whitelistValidation"


def normalize_source(source: str) -> str:
    return re.sub(r"\s+", " ", source).strip()


@dataclass(frozen=True, slots=True)
class FunctionRef:
    name: str | None
    source: str

    @property
    def key(self) -> str:
        return normalize_source(self.source)


@dataclass(frozen=True, slots=True)
class Rule:
    property_name: str
    type: str
    key: str
    name: str | None
    constraints: list[Any]
    message: str | None
    default_message: str | None
    each: bool

    @property
    def condition(self) -> FunctionRef:
        ref = self.constraints[0]
        return FunctionRef(ref.get("function"), ref["source"])


@dataclass(frozen=True, slots=True)
class Property:
    name: str
    design_type: str | None
    transforms: list[FunctionRef]
    type_target: str | None
    rules: list[Rule]


@dataclass(frozen=True, slots=True)
class ClassMeta:
    name: str
    source: str | None
    initial_values: dict[str, Any]
    properties: dict[str, Property]

    def rules_by_property(self) -> dict[str, list[Rule]]:
        return {name: prop.rules for name, prop in self.properties.items() if prop.rules}


@dataclass(frozen=True, slots=True)
class PipeOptions:
    whitelist: bool
    forbid_non_whitelisted: bool
    forbid_unknown_values: bool
    transform: bool
    error_http_status_code: int


@dataclass(frozen=True, slots=True)
class ValidationMetadata:
    pipe: PipeOptions
    classes: dict[str, ClassMeta]

    def get(self, name: str) -> ClassMeta:
        try:
            return self.classes[name]
        except KeyError:
            raise KeyError(f"{name} 不在 validation.json 中") from None


def _rule(prop: str, raw: dict[str, Any]) -> Rule:
    return Rule(
        property_name=prop,
        type=raw["type"],
        key=raw["key"],
        name=raw["name"],
        constraints=raw["constraints"],
        message=raw["message"],
        default_message=raw["defaultMessage"],
        each=raw["each"],
    )


def _property(name: str, raw: dict[str, Any]) -> Property:
    type_meta = raw["type"]
    target = type_meta["target"] if type_meta else None
    return Property(
        name=name,
        design_type=raw["designType"],
        transforms=[FunctionRef(t["function"], t["source"]) for t in raw["transforms"]],
        type_target=target if isinstance(target, str) else None,
        rules=[_rule(name, r) for r in raw["validations"]],
    )


def parse(raw: dict[str, Any]) -> ValidationMetadata:
    pipe = raw["pipe"]
    return ValidationMetadata(
        pipe=PipeOptions(
            whitelist=pipe["whitelist"],
            forbid_non_whitelisted=pipe["forbidNonWhitelisted"],
            forbid_unknown_values=pipe["forbidUnknownValues"],
            transform=pipe["transform"],
            error_http_status_code=pipe["errorHttpStatusCode"],
        ),
        classes={
            name: ClassMeta(
                name=name,
                source=cls["source"],
                initial_values=cls["initialValues"],
                properties={p: _property(p, prop) for p, prop in cls["properties"].items()},
            )
            for name, cls in raw["classes"].items()
        },
    )


@cache
def load(path: Path = DEFAULT_VALIDATION_PATH) -> ValidationMetadata:
    return parse(json.loads(path.read_text(encoding="utf-8")))
