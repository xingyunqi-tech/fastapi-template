import json
from pathlib import Path
from typing import Any

import pytest

from app.common.validation import metadata
from app.common.validation.engine import (
    UnsupportedFunctionError,
    ValidationFailedError,
    Validator,
    run_pipe,
)
from app.common.validation.js import parse_json, stringify

FIXTURE = Path(__file__).parent / "fixtures" / "validation-cases.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator() -> Validator:
    return Validator(metadata.load())


def _run(validator: Validator, case: dict[str, Any]) -> dict[str, Any]:
    payload = parse_json(json.dumps(case["payload"]))
    try:
        value = run_pipe(validator, case["dto"], payload)
    except ValidationFailedError as exc:
        return {"ok": False, "status": exc.status, "errors": exc.errors}
    except UnsupportedFunctionError:
        raise
    except Exception:
        return {"ok": False, "status": 500, "errors": None}
    return {"ok": True, "value": json.loads(stringify(value))}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_matches_nestjs_validation_pipe(validator: Validator, case: dict[str, Any]) -> None:
    assert _run(validator, case) == case["expected"]
