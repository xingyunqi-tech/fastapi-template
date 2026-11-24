"""约定 §2 响应信封模型。"""

from functools import cache
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

SUCCESS_CODE = 0
SUCCESS_MESSAGE = "ok"


def type_name(annotation: Any) -> str:
    """OpenAPI 组件名：`list[User]` → `UserList`，`User | None` → `NullableUser`。"""
    if annotation is None or annotation is NoneType:
        return "Null"
    origin = get_origin(annotation)
    if origin is list:
        (item,) = get_args(annotation) or (Any,)
        return f"{type_name(item)}List"
    if origin is dict or annotation is dict:
        return "Object"
    if origin in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not NoneType]
        joined = "Or".join(type_name(member) for member in members)
        return f"Nullable{joined}" if len(members) < len(get_args(annotation)) else joined
    name = getattr(annotation, "__name__", None)
    return name if isinstance(name, str) and name != "Any" else "Any"


def _all_required(schema: dict[str, Any]) -> None:
    """响应中总会出现的字段在 OpenAPI 中标为必填。"""
    schema["required"] = list(schema.get("properties", {}))


class ApiResponse[T](BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_required)

    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: T


_ENVELOPE_NAMES: dict[str, Any] = {}


@cache
def envelope_model(annotation: Any) -> type[BaseModel]:
    """`ApiResponse[annotation]` 的具名子类，组件名 `<类型名>Response`；不同类型同名时启动失败。"""
    name = f"{type_name(annotation)}Response"
    registered = _ENVELOPE_NAMES.setdefault(name, annotation)
    if registered != annotation:
        raise TypeError(f"响应组件名 {name} 冲突：{registered!r} 与 {annotation!r}，请改用具名模型")
    model = ApiResponse[annotation]
    return type(name, (model,), {"__module__": __name__})


def success(data: Any) -> dict[str, Any]:
    return {"code": SUCCESS_CODE, "message": SUCCESS_MESSAGE, "data": data}


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias=True,
        validate_by_name=True,
        json_schema_extra=_all_required,
    )

    code: int
    message: str
    error_key: str = Field(alias="errorKey")
    data: Any = None
    request_id: str = Field(alias="requestId")


class FieldErrorItem(BaseModel):
    field: str
    rule: str
    message: str
    params: dict[str, Any] | None = None


class ValidationErrorData(BaseModel):
    errors: list[FieldErrorItem]


class ValidationErrorResponse(ErrorResponse):
    """`common.validationFailed` 时 `data` 为字段错误，其余 422 errorKey 的 `data` 为 null。"""

    data: ValidationErrorData | None
