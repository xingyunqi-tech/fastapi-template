"""commit-msg 钩子：conventional commits，类型集合与 nestjs-template commitlint 一致。"""

import re
import sys
from pathlib import Path

TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
HEADER_MAX_LENGTH = 72
LINE_MAX_LENGTH = 100
HEADER_RE = re.compile(
    rf"^(?:{'|'.join(TYPES)})(?:\((?P<scope>[^()A-Z\s][^()A-Z]*)\))?!?: \S(?:.*[^.\s])?$"
)
SKIP_RE = re.compile(r"^(Merge |Revert \"|fixup! |squash! |amend! |Initial commit$)")


def check(header: str, body: list[str] | None = None) -> str | None:
    if SKIP_RE.match(header):
        return None
    if len(header) > HEADER_MAX_LENGTH:
        return f"标题超过 {HEADER_MAX_LENGTH} 个字符（当前 {len(header)}）"
    if not HEADER_RE.match(header):
        return f"标题须为 `<type>(<scope>): <subject>`，type 取 {', '.join(TYPES)}，结尾不加句点"
    for line in body or []:
        if len(line) > LINE_MAX_LENGTH:
            return f"正文每行不超过 {LINE_MAX_LENGTH} 个字符：{line[:30]}…"
    return None


def main(argv: list[str]) -> int:
    lines = [
        line
        for line in Path(argv[1]).read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    content = [line for line in lines if line.strip()]
    header = content[0] if content else ""
    error = check(header, content[1:])
    if error:
        print(f"提交信息不合规：{error}\n  {header}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
