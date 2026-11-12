"""与 nestjs-i18n（AcceptLanguageResolver）一致的语言解析与翻译。"""

import json
import math
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import cache, cmp_to_key
from pathlib import Path
from typing import Any

I18N_DIR = Path(__file__).resolve().parent

_current_language: ContextVar[str | None] = ContextVar("current_language", default=None)


@dataclass(frozen=True, slots=True)
class _Tag:
    code: str
    script: str | None
    region: str | None
    quality: float


def _parse_tag(text: str, quality: float) -> _Tag:
    bits = text.split("-")
    has_script = len(bits) == 3
    return _Tag(
        code=bits[0],
        script=bits[1] if has_script else None,
        region=bits[2] if has_script else (bits[1] if len(bits) > 1 and bits[1] else None),
        quality=quality,
    )


_FLOAT_PREFIX = re.compile(
    r"[\t\n\v\f\r \u00a0\ufeff]*"
    r"([+-]?(?:Infinity|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?))",
    re.ASCII,
)


def _parse_float(text: str | None) -> float:
    """JS parseFloat：解析前缀中的数字，无数字时为 NaN。"""
    match = _FLOAT_PREFIX.match(text or "")
    return float(match.group(1)) if match else math.nan


def parse_accept_language(header: str) -> list[_Tag]:
    tags: list[_Tag] = []
    for part in header.split(","):
        item = part.strip()
        if not item:
            continue
        bits = item.split(";")
        quality = 1.0
        if len(bits) > 1 and bits[1]:
            pieces = bits[1].split("=")
            quality = _parse_float(pieces[1] if len(pieces) > 1 else None)
        tags.append(_parse_tag(bits[0], quality))

    def compare(a: _Tag, b: _Tag) -> int:
        diff = b.quality - a.quality
        if math.isnan(diff) or diff == 0:
            return 0
        return -1 if diff < 0 else 1

    return sorted(tags, key=cmp_to_key(compare))


def pick(supported: list[str], header: str, *, loose: bool = False) -> str | None:
    if not supported or not header:
        return None
    candidates = [_parse_tag(s, 1.0) for s in supported]
    for tag in parse_accept_language(header):
        code = tag.code.lower()
        region = tag.region.lower() if tag.region else None
        script = tag.script.lower() if tag.script else None
        for index, option in enumerate(candidates):
            option_script = option.script.lower() if option.script else None
            option_region = option.region.lower() if option.region else None
            if (
                code == option.code.lower()
                and (loose or not script or script == option_script)
                and (loose or not region or region == option_region)
            ):
                return supported[index]
    return None


class I18n:
    def __init__(self, directory: Path, fallback_language: str) -> None:
        self.fallback_language = fallback_language
        self.translations: dict[str, dict[str, Any]] = {}
        for lang_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
            self.translations[lang_dir.name] = {
                f.stem: json.loads(f.read_text(encoding="utf-8"))
                for f in sorted(lang_dir.glob("*.json"))
            }

    @property
    def supported_languages(self) -> list[str]:
        return list(self.translations)

    def resolve(self, accept_language: str | None) -> str:
        if accept_language:
            supported = self.supported_languages
            picked = pick(supported, accept_language) or pick(
                supported, accept_language, loose=True
            )
            if picked:
                return picked
        return self.fallback_language

    def translate(self, key: str, lang: str | None = None) -> str | None:
        """`key` 形如 `common.notFound`；当前语言缺失时回落到默认语言，均缺失返回 None。"""
        lang = lang or current_language() or self.fallback_language
        for candidate in dict.fromkeys([lang, self.fallback_language]):
            value = self._lookup(candidate, key)
            if value is not None:
                return value
        return None

    def _lookup(self, lang: str, key: str) -> str | None:
        node: Any = self.translations.get(lang)
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, str) and node else None


@cache
def get_i18n(fallback_language: str = "en") -> I18n:
    return I18n(I18N_DIR, fallback_language)


def current_language() -> str | None:
    return _current_language.get()


def set_language(lang: str) -> Token[str | None]:
    return _current_language.set(lang)


def reset_language(token: Token[str | None]) -> None:
    _current_language.reset(token)
