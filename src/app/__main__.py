from granian import Granian
from granian.constants import Interfaces

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    Granian(
        "app.main:app",
        interface=Interfaces.ASGI,
        address="0.0.0.0",  # noqa: S104
        port=settings.app.port,
        workers=settings.app.web_concurrency,
    ).serve()


if __name__ == "__main__":
    main()
