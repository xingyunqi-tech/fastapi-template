"""由 nestjs 基线的 components.schemas 生成 Pydantic 模型（类名即 nestjs DTO 名）。

用法：gen_schemas.py            输出到 src/app/contract/schemas.py
      gen_schemas.py --check    与现有文件比较，不一致时退出码为 1
"""

import json
import keyword
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "openapi" / "nestjs-baseline.json"
OUTPUT = ROOT / "src" / "app" / "contract" / "schemas.py"

ROOTS = [
    "Address",
    "AgreeConsentDto",
    "AppProfile",
    "AuthAlipayLoginDto",
    "AuthChangePasswordDto",
    "AuthConfirmEmailDto",
    "AuthDeleteAccountDto",
    "AuthEmailLoginDto",
    "AuthForgotPasswordDto",
    "AuthPhoneLoginDto",
    "AuthPhoneRegisterDto",
    "AuthPhoneResetPasswordDto",
    "AuthPhoneSmsLoginDto",
    "AuthRegisterLoginDto",
    "AuthResetPasswordDto",
    "AuthSendCodeDto",
    "AuthUpdateDto",
    "AuthWechatLoginDto",
    "AuthWechatPhoneLoginDto",
    "CheckFavoriteDto",
    "CheckFavoriteResponseDto",
    "Config",
    "ConsentStatusDto",
    "CreateAddressDto",
    "CreateFavoriteDto",
    "CreateFeedbackDto",
    "CreateUserDto",
    "Favorite",
    "Feedback",
    "InfinityPaginationUserResponseDto",
    "LoginResponseDto",
    "Notification",
    "NotificationSetting",
    "PaginationAddressResponseDto",
    "PaginationFavoriteItemDtoResponseDto",
    "PaginationFeedbackResponseDto",
    "PaginationNotificationResponseDto",
    "RefreshResponseDto",
    "Region",
    "RegionTreeDto",
    "SessionResponseDto",
    "UpdateAddressDto",
    "UpdateAppProfileDto",
    "UpdateNotificationSettingsDto",
    "UpdateUserDto",
    "UpsertConfigDto",
    "User",
]

HEADER = '''"""由 scripts/gen_schemas.py 依据 nestjs 基线生成，请勿手改。"""

{typing_import}from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
'''


def _snake(name: str) -> str:
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()
    return f"{snake}_" if keyword.iskeyword(snake) or snake in {"list", "type"} else snake


def _ref(schema: dict[str, Any]) -> str | None:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    all_of = schema.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1 and "$ref" in all_of[0]:
        return str(all_of[0]["$ref"]).rsplit("/", 1)[-1]
    return None


def _annotation(schema: dict[str, Any]) -> str:
    ref = _ref(schema)
    if ref:
        return ref
    kind = schema.get("type")
    if kind == "array":
        return f"list[{_annotation(schema.get('items', {}))}]"
    if kind == "string":
        return "str"
    if kind in {"number", "integer"}:
        return "int | float"
    if kind == "boolean":
        return "bool"
    if kind == "object" and not schema.get("properties"):
        return "dict[str, Any]"
    return "Any"


def _references(schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    ref = _ref(schema)
    if ref:
        found.add(ref)
    for value in schema.values():
        if isinstance(value, dict):
            found |= _references(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found |= _references(item)
    return found


def collect(schemas: dict[str, Any], roots: list[str]) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in ordered or name in visiting:
            return
        visiting.add(name)
        for dependency in sorted(_references(schemas[name])):
            visit(dependency)
        visiting.discard(name)
        ordered.append(name)

    for root in roots:
        visit(root)
    return ordered


def render_enum(name: str, values: list[str]) -> str:
    members = "".join(f'    {_member(value)} = "{value}"\n' for value in values)
    return f"class {name}(StrEnum):\n{members}".rstrip("\n")


def _member(value: str) -> str:
    member = re.sub(r"\W", "_", value).upper()
    return f"_{member}" if member[:1].isdigit() else member


def render_model(name: str, schema: dict[str, Any]) -> str:
    if schema.get("type") == "string" and isinstance(schema.get("enum"), list):
        return render_enum(name, [str(value) for value in schema["enum"]])
    required = set(schema.get("required", []))
    lines = [f"class {name}(ContractModel):"]
    properties: dict[str, Any] = schema.get("properties", {})
    if not properties:
        lines.append("    pass")
    for prop, definition in properties.items():
        annotation = _annotation(definition)
        if annotation == "Any":
            nullable_annotation = "Any"
        else:
            nullable = definition.get("nullable") or prop not in required
            nullable_annotation = f"{annotation} | None" if nullable else annotation
        default = "" if prop in required else "default=None, "
        lines.append(f'    {_snake(prop)}: {nullable_annotation} = Field({default}alias="{prop}")')
    return "\n".join(lines)


def render() -> str:
    schemas = json.loads(BASELINE.read_text(encoding="utf-8"))["components"]["schemas"]
    names = collect(schemas, ROOTS)
    body = "\n\n\n".join(render_model(name, schemas[name]) for name in names)
    imports = []
    if "(StrEnum):" in body:
        imports.append("from enum import StrEnum\n")
    if re.search(r"\bAny\b", body):
        imports.append("from typing import Any\n")
    typing_import = "".join(imports) + "\n" if imports else ""
    return f"{HEADER.format(typing_import=typing_import)}\n\n{body}\n"


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        return 0 if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == rendered else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
