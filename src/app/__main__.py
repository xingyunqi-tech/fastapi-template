from granian import Granian
from granian.constants import Interfaces

from app.config import get_settings
from app.infrastructure.logging import configure_logging
from app.infrastructure.metrics import prepare_multiprocess


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    if settings.metrics.enabled:
        prepare_multiprocess(settings.app.web_concurrency)
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
