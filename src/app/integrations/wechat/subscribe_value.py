"""订阅消息字段取值格式化（nestjs subscribe-value.util）。"""

from typing import Final

FIELD_MAX_LENGTH: Final = (
    ("character_string", 32),
    ("car_number", 8),
    ("thing", 20),
    ("number", 32),
    ("letter", 32),
    ("symbol", 5),
    ("phrase", 5),
    ("name", 10),
)


def format_subscribe_value(key: str, value: str) -> str:
    """按模板字段 key 的前缀截断取值，超长会导致微信返回 47003。"""
    for prefix, max_length in FIELD_MAX_LENGTH:
        if key.startswith(prefix):
            return value[:max_length]
    return value
