"""模块代码生成器（对应 nestjs `generate:resource:relational`）：生成最小可用的 CRUD 模块骨架。"""

import keyword
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Final

import typer
from jinja2 import Environment, FileSystemLoader, StrictUndefined

lazy from app.infrastructure.database.models import Base

TEMPLATE_DIR: Final = Path(__file__).parent / "templates" / "resource"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
_WORD_RE: Final = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")
_NAME_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_MODULE_FILES: Final = (
    "__init__.py",
    "models.py",
    "schemas.py",
    "repository.py",
    "service.py",
    "router.py",
)

generate_cli = typer.Typer(no_args_is_help=True)


def pluralize(word: str) -> str:
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


@dataclass(frozen=True, slots=True)
class ResourceNames:
    """`Product`、`product-category` 等写法统一拆词后派生的各类名字。"""

    class_name: str
    class_plural: str
    camel: str
    snake: str
    package: str
    url: str
    table: str
    tag: str

    @classmethod
    def parse(cls, name: str, plural: str | None = None) -> ResourceNames:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"模块名只能包含字母、数字、- 与 _，且以字母开头：{name!r}")
        words = [word.lower() for word in _WORD_RE.findall(name)]
        plural_words = (
            [word.lower() for word in _WORD_RE.findall(plural)]
            if plural
            else [*words[:-1], pluralize(words[-1])]
        )
        snake = "_".join(words)
        package = "_".join(plural_words)
        if package == snake:
            raise ValueError(f"复数形式与单数相同，请用 --plural 指定：{name!r}")
        for identifier in (snake, package):
            if keyword.iskeyword(identifier) or not identifier.isidentifier():
                raise ValueError(f"{identifier!r} 不是合法的 Python 标识符")
        class_name = "".join(word.capitalize() for word in words)
        class_plural = "".join(word.capitalize() for word in plural_words)
        return cls(
            class_name=class_name,
            class_plural=class_plural,
            camel=class_name[0].lower() + class_name[1:],
            snake=snake,
            package=package,
            url="-".join(plural_words),
            table=snake,
            tag=" ".join(plural_words).capitalize(),
        )


def check_table_available(names: ResourceNames) -> None:
    """表名已被其它模块的 ORM 模型占用时抛 ValueError。"""
    table = Base.metadata.tables.get(names.table)
    if table is None:
        return
    owners = {
        mapper.class_.__module__ for mapper in Base.registry.mappers if mapper.local_table is table
    }
    if owners != {f"app.modules.{names.package}.models"}:
        raise ValueError(
            f"表名 {names.table!r} 已被 {', '.join(sorted(owners)) or '其它表定义'} 使用"
        )


@dataclass(frozen=True, slots=True)
class GeneratedResource:
    names: ResourceNames
    module_dir: Path
    test_file: Path

    @property
    def files(self) -> list[Path]:
        return [*(self.module_dir / name for name in _MODULE_FILES), self.test_file]


def generate_resource(
    name: str, root: Path, *, plural: str | None = None, force: bool = False
) -> GeneratedResource:
    """在 `root/src/app/modules/<复数>/` 与 `root/tests/integration/` 下按模板写入模块与测试。"""
    names = ResourceNames.parse(name, plural)
    check_table_available(names)
    module_dir = root / "src" / "app" / "modules" / names.package
    test_file = root / "tests" / "integration" / f"test_{names.package}.py"
    if not force and (module_dir.exists() or test_file.exists()):
        raise FileExistsError(f"{module_dir} 或 {test_file} 已存在，使用 --force 覆盖")
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # noqa: S701
    )
    context = asdict(names)
    module_dir.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)
    for file_name in _MODULE_FILES:
        template = environment.get_template(f"{file_name}.jinja")
        (module_dir / file_name).write_text(template.render(context), encoding="utf-8")
    test_file.write_text(environment.get_template("test.py.jinja").render(context), "utf-8")
    return GeneratedResource(names, module_dir, test_file)


def format_files(files: list[Path], cwd: Path) -> None:
    """对应 nestjs postgenerate（eslint --fix、prettier）；未安装 ruff 时跳过。"""
    ruff = shutil.which("ruff") or shutil.which("ruff", path=str(Path(sys.executable).parent))
    if ruff is None:
        return
    paths = [str(path) for path in files]
    subprocess.run([ruff, "format", "--quiet", *paths], cwd=cwd, check=False)  # noqa: S603
    subprocess.run([ruff, "check", "--fix", "--quiet", *paths], cwd=cwd, check=False)  # noqa: S603
    subprocess.run([ruff, "format", "--quiet", *paths], cwd=cwd, check=False)  # noqa: S603


@generate_cli.command("resource")
def resource(
    name: Annotated[str, typer.Argument(help="资源名（单数），如 Product、product-category")],
    plural: Annotated[str | None, typer.Option(help="复数形式，默认按英文规则推导")] = None,
    root: Annotated[Path, typer.Option(help="项目根目录")] = PROJECT_ROOT,
    force: Annotated[bool, typer.Option(help="覆盖已存在的文件")] = False,
) -> None:
    """生成 CRUD 模块骨架：models、repository、service、router、schemas 与集成测试。"""
    try:
        generated = generate_resource(name, root, plural=plural, force=force)
    except (ValueError, FileExistsError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    format_files(generated.files, root)
    names = generated.names
    for path in generated.files:
        typer.echo(f"created {path.relative_to(root)}")
    typer.echo(
        "\n后续步骤：\n"
        f"  1. src/app/factory.py：from app.modules.{names.package} import router as "
        f"{names.package}，并在 create_app 中 include_routes(app, {names.package}.router, "
        "api_prefix)\n"
        f"  2. src/app/infrastructure/database/models.py：导入 {names.class_name}Model\n"
        f'  3. uv run alembic revision --autogenerate -m "add {names.table}"，'
        "检查生成的迁移后 make migrate\n"
        f"  4. uv run pytest tests/integration/test_{names.package}.py"
    )
