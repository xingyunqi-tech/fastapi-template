"""`app openapi export`：按代码导出 OpenAPI 3.1 到 openapi/openapi.json。"""

import os
from dataclasses import replace
from typing import Annotated, Final
lazy from pathlib import Path

import typer

from app.common.openapi import OPENAPI_PATH, render_openapi
from app.config import FileDriver, Settings
lazy from app.factory import create_app

EXPORT_TITLE: Final = "fastapi-temp"
EXPORT_DATABASE_URL: Final = "postgresql://export@localhost/export?sslmode=disable"

openapi_cli = typer.Typer(no_args_is_help=True)


def export_settings() -> Settings:
    """导出结果不随本地环境变化：标题固定，文件驱动取 local，未配置数据库时用占位地址（不连接）。"""
    os.environ.setdefault("DATABASE_URL", EXPORT_DATABASE_URL)
    settings = Settings.load()
    return replace(
        settings,
        app=settings.app.model_copy(update={"name": EXPORT_TITLE}),
        file=settings.file.model_copy(update={"driver": FileDriver.LOCAL}),
    )


def render() -> str:
    return render_openapi(create_app(export_settings()))


@openapi_cli.command("export")
def export(
    output: Annotated[Path, typer.Option(help="输出文件")] = OPENAPI_PATH,
    check: Annotated[bool, typer.Option("--check", help="只比对，不一致时退出码 1")] = False,
) -> None:
    content = render()
    if check:
        current = output.read_text(encoding="utf-8") if output.exists() else None
        if current != content:
            typer.echo(f"{output} 与代码不一致，执行 make openapi 重新导出", err=True)
            raise typer.Exit(1)
        typer.echo(f"{output} 与代码一致")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    typer.echo(f"已写入 {output}")
