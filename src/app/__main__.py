from granian import Granian
from granian.constants import Interfaces

from app.config import get_settings
from app.infrastructure.logging import configure_logging


def main() -> None:
    """`WEB_CONCURRENCY` 与 `OTEL_METRICS_EXPORTER` 的组合在加载配置时校验。"""
    settings = get_settings()
    configure_logging(settings)
    Granian(
        "app.main:app",
        interface=Interfaces.ASGI,
        address="0.0.0.0",  # noqa: S104
        port=settings.app.port,
        workers=settings.app.web_concurrency,
        respawn_failed_workers=True,
    ).serve()


if __name__ == "__main__":
    main()
