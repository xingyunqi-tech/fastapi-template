from typing import Any

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import positive_data_acceptance

from tests.api.conftest import ROOT, Server, current_token

schema = schemathesis.openapi.from_path(ROOT / "openapi" / "openapi.json").exclude(
    operation_id=["deleteMe", "logout", "revokeOtherSessions"]
)


@schema.auth()
class AdminToken:
    def get(self, case: schemathesis.Case[Any], ctx: schemathesis.AuthContext) -> str:
        return current_token()

    def set(self, case: schemathesis.Case[Any], data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers["Authorization"] = f"Bearer {data}"


@schema.parametrize()
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_responses_conform_to_openapi(case: schemathesis.Case[Any], server: Server) -> None:
    case.call_and_validate(base_url=server.base_url, excluded_checks=[positive_data_acceptance])
