"""按 validation.json 复刻 nestjs ValidationPipe（class-transformer + class-validator）。"""

from dataclasses import dataclass, field
from typing import Any

from app.common.validation import constraints, functions
from app.common.validation.js import (
    ARRAY_PROTOTYPE_METHODS,
    UNDEFINED,
    JsBuiltinFunction,
    JsDate,
    JsValue,
    is_number,
    js_replace,
    to_number,
    to_string,
    truthy,
)
from app.common.validation.metadata import (
    CONDITIONAL_VALIDATION,
    CUSTOM_VALIDATION,
    IS_DEFINED,
    NESTED_VALIDATION,
    WHITELIST,
    ClassMeta,
    Rule,
    ValidationMetadata,
)

UNKNOWN_VALUE_MESSAGE = "an unknown value was passed to the validate function"
OBJECT_EXPECTED_MESSAGE = "Validation failed (object is expected)"
PRIMITIVE_TARGETS = {"String", "Number", "Boolean", "Date"}
PROTO_KEYS = frozenset({"__proto__", "prototype", "constructor"})
OBJECT_PROTOTYPE_FUNCTIONS = frozenset(
    {
        "constructor",
        "__defineGetter__",
        "__defineSetter__",
        "__lookupGetter__",
        "__lookupSetter__",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "toLocaleString",
        "toString",
        "valueOf",
    }
)


def strip_proto_keys(value: JsValue) -> None:
    """Nest stripProtoKeys：递归删除 __proto__、prototype、constructor 键。"""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, dict):
            for key in PROTO_KEYS & current.keys():
                del current[key]
            stack.extend(current.values())


class Instance(dict[str, Any]):
    """class-transformer 生成的类实例；`cls` 对应 JS 的 constructor.name。"""

    def __init__(self, cls: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cls = cls


class ArrayInstance(Instance):
    """constructor 被改写为 DTO 类的数组：索引键可被白名单删除，原型方法与 length 仍可读取。"""

    def __init__(self, cls: str, items: list[Any]) -> None:
        super().__init__(cls, {str(i): item for i, item in enumerate(items)})
        self.length = len(items)

    def read(self, key: str) -> JsValue:
        if key in self:
            return self[key]
        if key == "length":
            return self.length
        if key in ARRAY_PROTOTYPE_METHODS:
            return JsBuiltinFunction(key)
        return UNDEFINED


def _read(obj: JsValue, name: str) -> JsValue:
    if isinstance(obj, ArrayInstance):
        return obj.read(name)
    if isinstance(obj, dict):
        return obj.get(name, UNDEFINED)
    return UNDEFINED


class UnsupportedFunctionError(LookupError):
    pass


class ValidationFailedError(Exception):
    def __init__(self, errors: dict[str, Any], status: int) -> None:
        super().__init__(errors)
        self.errors = errors
        self.status = status


@dataclass(slots=True)
class _Error:
    property: str
    constraints: dict[str, str] = field(default_factory=dict)
    children: list[_Error] = field(default_factory=list)


def _constraint_to_string(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(to_string(_constraint_value(item)) for item in value)
    return to_string(_constraint_value(value))


def _constraint_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "regex" in value and "flags" in value:
            return f"/{value['regex']}/{value['flags']}"
        if "source" in value and "function" in value:
            return value["source"]
    return value


def _class_name(value: JsValue) -> str:
    if isinstance(value, Instance):
        return value.cls
    if isinstance(value, list):
        return "Array"
    return "Object"


class Validator:
    def __init__(self, metadata: ValidationMetadata) -> None:
        self.metadata = metadata

    def plain_to_instance(self, cls: str, value: JsValue) -> JsValue:
        return self._transform(value, cls)

    def _transform(self, value: JsValue, target: str | None) -> JsValue:
        if isinstance(value, list):
            return [self._transform(item, target) for item in value]
        if target in PRIMITIVE_TARGETS:
            if value is None or value is UNDEFINED:
                return value
            if target == "String":
                return to_string(value)
            if target == "Number":
                return to_number(value)
            if target == "Date":
                return JsDate(value)
            return truthy(value)
        if not isinstance(value, dict):
            return value
        meta = self.metadata.classes.get(target) if target else None
        result: dict[str, Any]
        if target is None:
            result = {}
        else:
            result = Instance(target, dict(meta.initial_values) if meta else {})
        for key in list(value.keys()):
            if key == "__proto__" or key in OBJECT_PROTOTYPE_FUNCTIONS:
                continue
            prop = meta.properties.get(key) if meta else None
            sub_target = prop.type_target if prop else None
            final = self._transform(value[key], sub_target)
            if prop and prop.transforms:
                context = functions.TransformContext(
                    obj=value, key=key, type_target=sub_target, to_instance=self.plain_to_instance
                )
                for ref in prop.transforms:
                    fn = functions.TRANSFORMS.get(ref.key)
                    if fn is None:
                        raise UnsupportedFunctionError(f"{target}.{key} @Transform: {ref.source}")
                    final = fn(final, context)
            result[key] = final
        return result

    def validate(self, entity: JsValue) -> dict[str, Any]:
        errors: list[_Error] = []
        self._execute(entity, errors)
        return _generate(_strip(errors))

    def _execute(self, obj: JsValue, errors: list[_Error]) -> None:
        meta = self.metadata.classes.get(_class_name(obj))
        grouped = meta.rules_by_property() if meta else {}
        if not grouped and self.metadata.pipe.forbid_unknown_values:
            errors.append(_Error("undefined", {"unknownValue": UNKNOWN_VALUE_MESSAGE}))
            return
        if self.metadata.pipe.whitelist and isinstance(obj, dict):
            for key in [k for k in obj if k not in grouped]:
                del obj[key]
        for name, rules in grouped.items():
            value = _read(obj, name)
            defined = [r for r in rules if r.type == IS_DEFINED]
            rest = [r for r in rules if r.type not in {IS_DEFINED, WHITELIST}]
            self._perform(obj, value, name, defined, rest, errors)

    def _perform(
        self,
        obj: JsValue,
        value: JsValue,
        name: str,
        defined: list[Rule],
        rules: list[Rule],
        errors: list[_Error],
    ) -> None:
        error = _Error(name)
        errors.append(error)
        conditions = [
            self._condition(obj, value, r) for r in rules if r.type == CONDITIONAL_VALIDATION
        ]
        if not all(conditions):
            return
        self._custom(obj, value, name, defined, error)
        self._custom(obj, value, name, [r for r in rules if r.type == CUSTOM_VALIDATION], error)
        self._nested(value, name, [r for r in rules if r.type == NESTED_VALIDATION], error)

    def _condition(self, obj: JsValue, value: JsValue, rule: Rule) -> bool:
        ref = rule.condition
        fn = functions.CONDITIONS.get(ref.key)
        if fn is None:
            raise UnsupportedFunctionError(f"@ValidateIf: {ref.source}")
        return fn(obj if isinstance(obj, dict) else {}, value)

    def _custom(
        self, obj: JsValue, value: JsValue, name: str, rules: list[Rule], error: _Error
    ) -> None:
        for rule in rules:
            constraint = constraints.resolve(rule.key, rule.default_message)
            if constraint is None:
                raise UnsupportedFunctionError(f"constraint {rule.key}: {rule.default_message}")
            if rule.each and isinstance(value, list):
                results = [constraint.validate(item, rule.constraints) for item in value]
                valid = all(results)
            else:
                valid = constraint.validate(value, rule.constraints)
            if not valid:
                message = self._message(obj, value, name, rule, constraint)
                error.constraints[rule.key] = message

    def _nested(self, value: JsValue, name: str, rules: list[Rule], error: _Error) -> None:
        if value is UNDEFINED:
            return
        for rule in rules:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    self._perform(value, item, str(index), [], rules, error.children)
            elif isinstance(value, dict | JsBuiltinFunction):
                self._execute(value, error.children)
            else:
                error.constraints[rule.key] = self._message(None, value, name, rule, None)

    def _message(
        self,
        obj: JsValue,
        value: JsValue,
        name: str,
        rule: Rule,
        constraint: constraints.Constraint | None,
    ) -> str:
        if rule.message:
            message = rule.message
        elif constraint and constraint.message:
            message = constraint.message(
                value, rule.constraints, "each value in " if rule.each else ""
            )
        else:
            message = rule.default_message or ""
        for index, item in enumerate(rule.constraints):
            message = js_replace(message, f"$constraint{index + 1}", _constraint_to_string(item))
        if isinstance(value, str | bool) or is_number(value):
            message = js_replace(message, "$value", to_string(value))
        message = js_replace(message, "$property", rule.property_name)
        return js_replace(message, "$target", "Function" if obj is None else _class_name(obj))


def _strip(errors: list[_Error]) -> list[_Error]:
    kept: list[_Error] = []
    for error in errors:
        error.children = _strip(error.children)
        if not error.constraints and not error.children:
            continue
        kept.append(error)
    return kept


def _generate(errors: list[_Error]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for error in errors:
        if error.children:
            result[error.property] = _generate(error.children)
        else:
            result[error.property] = ", ".join(error.constraints.values())
    return result


def run_pipe(validator: Validator, dto: str, value: JsValue, param_type: str = "body") -> JsValue:
    """ValidationPipe.transform：校验失败抛 ValidationFailedError，成功返回转换后的实例。"""
    meta: ClassMeta = validator.metadata.get(dto)
    original = value
    if value is None or value is UNDEFINED:
        value = {}
    primitive = isinstance(value, str | bool) or is_number(value)
    strip_proto_keys(value)
    entity = validator.plain_to_instance(dto, value)
    replaced = not isinstance(entity, Instance)
    if replaced:
        entity = ArrayInstance(dto, entity) if isinstance(entity, list) else Instance(dto)
    errors = validator.validate(entity)
    status = validator.metadata.pipe.error_http_status_code
    if errors:
        raise ValidationFailedError(errors, status)
    if replaced and (primitive or isinstance(value, list)) and meta.rules_by_property():
        raise ValidationFailedError({param_type: OBJECT_EXPECTED_MESSAGE}, status)
    if primitive:
        return original
    if isinstance(entity, ArrayInstance):
        return [entity.get(str(i)) for i in range(entity.length)]
    return entity
