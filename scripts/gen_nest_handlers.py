# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""由 nestjs controller 源码与基线生成限流键用的 operationId → `Controller-handler` 映射。

用法：gen_nest_handlers.py <nestjs src 目录>            写入 nest_handlers.json
      gen_nest_handlers.py <nestjs src 目录> --check    与现有文件比较，不一致时退出码为 1
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "openapi" / "nest_handlers.json"
BASELINE = ROOT / "openapi" / "nestjs-baseline.json"

DRIVER_DEPENDENT = {"uploadFile"}
EXTRA = {
    "getMetrics": "MetricsController-index",
    "readPrivateFile": "FilesController-readPrivate",
    "downloadFile": "FilesLocalController-download",
}

_CLASS = re.compile(r"^export class (\w+)")
_OPERATION = re.compile(r"operationId:\s*['\"](\w+)['\"]")
_METHOD = re.compile(r"^\s+(?:(?:public|private|protected|override|async)\s+)*(\w+)\s*\(")
_GENERATED = re.compile(r"^(\w+Controller)_(\w+)_v\d+$")


def handlers_in(source: str) -> dict[str, str]:
    found: dict[str, str] = {}
    lines = source.splitlines()
    controller: str | None = None
    for index, line in enumerate(lines):
        if match := _CLASS.match(line):
            controller = match.group(1)
            continue
        operation = _OPERATION.search(line)
        if operation is None or controller is None:
            continue
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if stripped.startswith(("@", "}", ")", "//", "*")) or ":" in stripped.split("(")[0]:
                continue
            if method := _METHOD.match(following):
                found[operation.group(1)] = f"{controller}-{method.group(1)}"
                break
    return found


def collect(src: Path) -> dict[str, str]:
    handlers: dict[str, str] = {}
    for path in sorted(src.rglob("*.controller.ts")):
        for operation, handler in handlers_in(path.read_text(encoding="utf-8")).items():
            if operation in handlers and handlers[operation] != handler:
                raise SystemExit(
                    f"duplicate operationId {operation}: {handlers[operation]}, {handler}"
                )
            handlers[operation] = handler
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for item in baseline["paths"].values():
        for operation in item.values():
            match = _GENERATED.match(str(operation.get("operationId", "")))
            if match:
                handlers.setdefault(match.group(0), f"{match.group(1)}-{match.group(2)}")
    for operation in DRIVER_DEPENDENT:
        handlers.pop(operation, None)
    return {**handlers, **EXTRA}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    rendered = json.dumps(collect(Path(argv[1])), indent=2, ensure_ascii=False) + "\n"
    if "--check" in argv:
        current = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else None
        return 0 if current == json.loads(rendered) else 1
    OUTPUT.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
