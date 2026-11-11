import importlib.util
import sys
from pathlib import Path
lazy from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_commit_msg.py"


@pytest.fixture(scope="module")
def module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_commit_msg", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "header",
    [
        "feat: 新增登录",
        "fix(auth): 刷新令牌并发时返回 401",
        "chore(deps)!: 升级 FastAPI",
        "Merge branch 'master' into dev",
        "docs: " + "长" * 66,
        "fix: a",
        "feat!: x",
        "feat(用户): 新增字段",
        "feat(auth,users): 调整",
        "amend! feat: x",
        "Initial commit",
    ],
)
def test_accepts(module: ModuleType, header: str) -> None:
    assert module.check(header) is None


@pytest.mark.parametrize(
    "header",
    [
        "update readme",
        "Feat: 大写类型",
        "feature: 未知类型",
        "fix: 结尾句点.",
        "fix(Auth): 大写 scope",
        "docs: " + "长" * 67,
        "fix:缺少空格",
        "feat: 结尾空格 ",
    ],
)
def test_rejects(module: ModuleType, header: str) -> None:
    assert module.check(header) is not None


def test_main_skips_comment_lines(module: ModuleType, tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("# comment\n\nfeat: ok\n\nbody\n", encoding="utf-8")

    assert module.main([sys.argv[0], str(message)]) == 0

    message.write_text("bad header\n", encoding="utf-8")
    assert module.main([sys.argv[0], str(message)]) == 1


def test_rejects_long_body_line(module: ModuleType) -> None:
    assert module.check("feat: ok", ["x" * 100]) is None
    assert module.check("feat: ok", ["x" * 101]) is not None
