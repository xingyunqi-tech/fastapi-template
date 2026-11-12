# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""由 openapi/validation.json 生成校验用例，或把 nestjs 计算的期望结果合并进用例。

用法：
  gen_validation_cases.py VALIDATION_JSON                          输出用例到 stdout
  gen_validation_cases.py VALIDATION_JSON --merge CASES EXPECTED   输出合并后的 fixture
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

STRINGS: list[Any] = [
    "abc",
    "",
    "  padded  ",
    "13800138000",
    "2024-01-01",
    "2024-02-30",
    "2024-01-01T10:00:00Z",
    "2024-01",
    "A",
    "12.34",
    "<b>bold</b> text",
    "e10adc3949ba59abbe56e057f20f883e",
    "E10ADC3949BA59ABBE56E057F20F883E",
    "test@example.com",
    " Test@Example.COM ",
    "not-an-email@",
    "3f0c8a52-7d0e-4b59-9f8e-2a1d6c4b7e10",
    "3F0C8A52-7D0E-4B59-9F8E-2A1D6C4B7E10",
    "https://example.com/a?b=1",
    "localhost:3000",
    "110101199001011234",
    "123456789012",
    "x" * 300,
    "true",
    "false",
    "1",
    "-5",
    "0",
    "asc",
    "DESC",
    '{"a":1}',
    "[1,2]",
    "1,2, 3",
    chr(0xA0) + "空白" + chr(0x3000),
]
NUMBERS: list[Any] = [1, 0, -1, 1.5, 2.345, 100, 101, 1e21, "5", "abc", 2147483648]
BOOLEANS: list[Any] = [True, False, "true", 1]
ARRAYS: list[Any] = [[], ["a"], ["a", "a"], [1, 2], [{}], "a", [None]]
OBJECTS: list[Any] = [{}, {"x": 1}, "x", [], [{}]]
MIXED: list[Any] = [None, 123, True, [], {}]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("/".join(parts).encode()).hexdigest()[:8], 16)


def _candidates(prop: dict[str, Any], classes: dict[str, Any]) -> list[Any]:
    pool: list[Any] = []
    for rule in prop["validations"]:
        if rule["key"] == "isEnum":
            pool.extend(list(rule["constraints"][0].values())[:3])
            pool.append("zzz")
        elif rule["key"] == "isIn":
            pool.extend(rule["constraints"][0][:3])
            pool.append("zzz")
        elif rule["key"] in {"min", "max", "maxLength", "minLength", "arrayMaxSize"}:
            bound = rule["constraints"][0]
            if isinstance(bound, int | float):
                pool.extend([bound - 1, bound, bound + 1])
    target = (prop["type"] or {}).get("target")
    if isinstance(target, str) and target in classes:
        nested = classes[target]["properties"]
        first = next(iter(nested), "x")
        pool.extend([{}, [{}], {first: "x"}, [{first: 1}], "x"])
    design = prop["designType"]
    if design == "String":
        pool.extend(STRINGS)
    elif design == "Number":
        pool.extend([*NUMBERS, "1", "", "20"])
    elif design == "Boolean":
        pool.extend(BOOLEANS)
    elif design == "Array":
        pool.extend(ARRAYS)
    elif design in {"Object", None}:
        pool.extend(OBJECTS + STRINGS[:6])
    else:
        pool.extend(OBJECTS)
    pool.extend(MIXED)
    return pool


def build_cases(validation: dict[str, Any]) -> list[dict[str, Any]]:
    classes = validation["classes"]
    cases: list[dict[str, Any]] = []
    for name, cls in classes.items():
        props = cls["properties"]
        if not props:
            continue

        def add(label: str, payload: Any, dto: str = name) -> None:
            cases.append({"id": f"{dto}/{label}", "dto": dto, "payload": payload})

        add("empty", {})
        add("nulls", dict.fromkeys(props))
        add("blank", dict.fromkeys(props, ""))
        add("array", [1])
        add("null-body", None)
        pools = {p: _candidates(prop, classes) for p, prop in props.items()}
        for variant in range(14):
            payload = {p: pool[(_seed(name, p) + variant) % len(pool)] for p, pool in pools.items()}
            if variant % 3 == 0:
                payload["unknownField"] = "drop me"
            add(f"v{variant}", payload)
        for p, pool in pools.items():
            for index, value in enumerate(pool[:4]):
                add(f"{p}-{index}", {p: value})
    return cases


def merge(cases: list[dict[str, Any]], expected: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**case, "expected": expected[case["id"]]} for case in cases]


def main(argv: list[str]) -> int:
    validation = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if len(argv) == 5 and argv[2] == "--merge":
        cases = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        expected = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        output: Any = merge(cases, expected)
    else:
        output = build_cases(validation)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
