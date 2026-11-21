"""约定 §6 `Accept-Language` 解析与翻译；`errors` 按 errorKey、`rules` 按校验 rule 取文本。"""

import json
import re
from contextvars import ContextVar, Token
from functools import cache
from pathlib import Path
from typing import Any, Final

I18N_DIR: Final = Path(__file__).resolve().parent
DEFAULT_LOCALE: Final = "zh"
_QVALUE: Final = re.compile(r"0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?")

_current_language: ContextVar[str | None] = ContextVar("current_language", default=None)


def parse_accept_language(header: str) -> list[str]:
    """按 q 值降序返回主语言子标签（小写）；q 非法或为 0 的项丢弃，同 q 保持原顺序。"""
    weighted: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        tag = tag.strip()
        if not tag:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                value = value.strip()
                quality = float(value) if _QVALUE.fullmatch(value) else 0.0
        if quality > 0:
            weighted.append((-quality, index, tag.split("-", 1)[0].lower()))
    return [tag for _, _, tag in sorted(weighted)]


class I18n:
    def __init__(self, directory: Path, default_locale: str = DEFAULT_LOCALE) -> None:
        self.default_locale = default_locale
        self.translations: dict[str, dict[str, dict[str, Any]]] = {
            lang_dir.name: {
                f.stem: json.loads(f.read_text(encoding="utf-8"))
                for f in sorted(lang_dir.glob("*.json"))
            }
            for lang_dir in sorted(p for p in directory.iterdir() if (p / "errors.json").is_file())
        }

    @property
    def supported_languages(self) -> list[str]:
        return list(self.translations)

    def resolve(self, accept_language: str | None) -> str:
        if accept_language:
            for tag in parse_accept_language(accept_language):
                if tag == "*":
                    break
                if tag in self.translations:
                    return tag
        return self.default_locale

    def translate(self, key: str, lang: str | None = None) -> str | None:
        """`key` 为 `<文件名>.<键>`，如 `errors.common.notFound`；缺失时回落到默认语言。"""
        namespace, _, name = key.partition(".")
        lang = lang or current_language() or self.default_locale
        for candidate in dict.fromkeys([lang, self.default_locale]):
            value = self.translations.get(candidate, {}).get(namespace, {}).get(name)
            if isinstance(value, str) and value:
                return value
        return None

    def error_message(self, error_key: str, lang: str | None = None) -> str:
        return self.translate(f"errors.{error_key}", lang) or error_key

    def rule_message(
        self, rule: str, params: dict[str, Any] | None = None, lang: str | None = None
    ) -> str:
        template = self.translate(f"rules.{rule}", lang) or self.translate("rules.format", lang)
        text = template or rule
        for name, value in (params or {}).items():
            text = text.replace(f"{{{name}}}", str(value))
        return text


@cache
def get_i18n(default_locale: str = DEFAULT_LOCALE) -> I18n:
    return I18n(I18N_DIR, default_locale)


def current_language() -> str | None:
    return _current_language.get()


def set_language(language: str) -> Token[str | None]:
    return _current_language.set(language)


def reset_language(token: Token[str | None]) -> None:
    _current_language.reset(token)
