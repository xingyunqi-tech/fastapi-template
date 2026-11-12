import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
lazy from fastapi import Request

from app.common.exceptions import (
    BusinessException,
    HttpError,
    TooManyRequests,
    forbidden,
    not_found_error,
    validation_error,
)
from app.common.params import Body, IdParam, parse_query_string
from app.common.routing import ApiRouter, Auth
from app.config import Settings
from app.factory import create_app
from tests.conftest import use_production


class AuthEmailLoginDto(BaseModel):
    email: str
    password: str


class FakeUniqueViolation(Exception):
    sqlstate = "23505"

    def __init__(self, constraint: str | None) -> None:
        super().__init__(constraint)
        self.constraint_name = constraint


class WrappedDbError(Exception):
    def __init__(self, orig: BaseException) -> None:
        super().__init__("wrapped")
        self.orig = orig


router = ApiRouter(tags=["Test"])


@router.endpoint("GET", "/ok", operation_id="ok", auth=Auth.PUBLIC)
async def ok() -> dict[str, Any]:
    return {"value": 1, "items": [1, 2]}


@router.endpoint("POST", "/upload", operation_id="upload", auth=Auth.PUBLIC, status_code=201)
async def upload() -> dict[str, Any]:
    return {"file": {"id": "x"}}


@router.endpoint("POST", "/void", operation_id="void", auth=Auth.PUBLIC)
async def void() -> None:
    return None


@router.endpoint("GET", "/null", operation_id="nullData", auth=Auth.PUBLIC)
async def null_data() -> Any:
    return None


@router.endpoint("GET", "/sync", operation_id="sync", auth=Auth.PUBLIC)
def sync_endpoint() -> dict[str, Any]:
    return {"thread": threading.current_thread() is not threading.main_thread()}


@router.endpoint("GET", "/numbers", operation_id="numbers", auth=Auth.PUBLIC)
async def numbers() -> dict[str, Any]:
    return {
        "10": 1,
        "2": 2,
        "integral": 10.0,
        "negativeZero": -0.0,
        "large": 1.2345678901234568e20,
        "small": 0.000001,
        "nan": float("nan"),
        "at": datetime(2024, 1, 1, 8, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8))),
    }


@router.endpoint("GET", "/files/{name}", operation_id="file", auth=Auth.PUBLIC)
async def file_name(name: str) -> dict[str, str]:
    return {"name": name}


@router.endpoint("GET", "/query", operation_id="query", auth=Auth.PUBLIC)
async def query(request: Request) -> dict[str, Any]:
    return parse_query_string(request.scope["query_string"].decode("latin-1"))


@router.endpoint("GET", "/envelope", operation_id="envelope", auth=Auth.PUBLIC)
async def already_envelope() -> dict[str, Any]:
    return {"code": 201, "message": "custom", "data": [1]}


@router.endpoint("GET", "/raw", operation_id="raw", auth=Auth.PUBLIC, wrap=False)
async def raw() -> dict[str, Any]:
    return {"plain": True}


@router.endpoint("POST", "/login", operation_id="login", auth=Auth.PUBLIC)
async def login(body: Annotated[AuthEmailLoginDto, Body(AuthEmailLoginDto)]) -> dict[str, Any]:
    return body.model_dump()


@router.endpoint("GET", "/items/{id}", operation_id="item", auth=Auth.PUBLIC)
async def item(id: IdParam) -> dict[str, int]:
    return {"id": id}


def _raised_in_except() -> BaseException:
    try:
        try:
            raise FakeUniqueViolation("UQ_favorite_user_target")
        except FakeUniqueViolation:
            raise BusinessException("sessionClosed") from None
    except BusinessException as exc:
        return exc


ERRORS: dict[str, BaseException] = {
    "session": not_found_error("sessionNotFound"),
    "forbidden": forbidden("Missing permissions: user:read, user:list"),
    "field": validation_error("email", "notFound"),
    "nested": HttpError(
        422,
        {
            "status": 422,
            "errors": {"phone": "rateLimited", "retryAfter": 42, "x": {"a": "notFound"}},
        },
    ),
    "business": BusinessException("sessionClosed", sessionId="s1", remaining=3),
    "throttle": TooManyRequests({"Retry-After": "60"}),
    "plain-string": HttpError(400, "badRequest"),
    "default-not-found": HttpError.build(404),
    "unique-email": WrappedDbError(FakeUniqueViolation("UQ_e12875dfb3b1d92d7d7c5377e22")),
    "unique-favorite": FakeUniqueViolation("UQ_favorite_user_target"),
    "unique-order": FakeUniqueViolation("UQ_order_orderNo"),
    "unique-unknown": FakeUniqueViolation("some_new_key"),
    "unknown": RuntimeError("boom"),
    "raised-in-except": _raised_in_except(),
}


@router.endpoint("GET", "/errors/{name}", operation_id="errors", auth=Auth.PUBLIC)
async def raise_error(name: str) -> None:
    raise ERRORS[name]


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings.load(), extra_routers=[(router, "/api/v1/test")])
    return TestClient(app, raise_server_exceptions=False)


def body(response: Any) -> Any:
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    return response.json()


class TestSuccessEnvelope:
    def test_wraps_payload(self, client: TestClient) -> None:
        response = client.get("/api/v1/test/ok")

        assert response.status_code == 200
        assert body(response) == {
            "code": 200,
            "message": "success",
            "data": {"value": 1, "items": [1, 2]},
        }
        assert (
            response.content == b'{"code":200,"message":"success","data":{"value":1,"items":[1,2]}}'
        )

    def test_post_returns_200(self, client: TestClient) -> None:
        assert client.post("/api/v1/test/void").status_code == 200

    def test_upload_status_201_with_body_code_200(self, client: TestClient) -> None:
        response = client.post("/api/v1/test/upload")

        assert response.status_code == 201
        assert body(response)["code"] == 200

    def test_void_omits_data(self, client: TestClient) -> None:
        assert body(client.post("/api/v1/test/void")) == {"code": 200, "message": "success"}

    def test_existing_envelope_passes_through(self, client: TestClient) -> None:
        assert body(client.get("/api/v1/test/envelope")) == {
            "code": 201,
            "message": "custom",
            "data": [1],
        }

    def test_none_result_is_null_data(self, client: TestClient) -> None:
        assert body(client.get("/api/v1/test/null")) == {
            "code": 200,
            "message": "success",
            "data": None,
        }

    def test_sync_endpoint_runs_in_threadpool(self, client: TestClient) -> None:
        assert body(client.get("/api/v1/test/sync"))["data"] == {"thread": True}

    def test_json_stringify_semantics(self, client: TestClient) -> None:
        response = client.get("/api/v1/test/numbers")

        assert response.content == (
            b'{"code":200,"message":"success","data":{"2":2,"10":1,"integral":10,'
            b'"negativeZero":0,"large":123456789012345680000,"small":0.000001,"nan":null,'
            b'"at":"2024-01-01T00:00:00.123Z"}}'
        )

    def test_wrap_disabled(self, client: TestClient) -> None:
        assert body(client.get("/api/v1/test/raw")) == {"plain": True}


class TestErrorShapes:
    @pytest.mark.parametrize(
        ("name", "lang", "status", "message", "data"),
        [
            ("session", "en", 404, "Session not found", None),
            ("session", "zh", 404, "会话不存在", None),
            ("forbidden", "zh", 403, "Missing permissions: user:read, user:list", None),
            ("field", "en", 422, "Validation failed", {"email": "Not found"}),
            ("field", "zh", 422, "数据验证失败", {"email": "资源不存在"}),
            (
                "nested",
                "en",
                422,
                "Validation failed",
                {
                    "phone": "Requests are too frequent, please try again later",
                    "retryAfter": 42,
                    "x": {"a": "notFound"},
                },
            ),
            (
                "business",
                "en",
                409,
                "This session has ended",
                {"sessionId": "s1", "remaining": 3, "errorKey": "sessionClosed"},
            ),
            ("throttle", "zh", 429, "请求过于频繁，请稍后再试", None),
            ("plain-string", "en", 400, "Bad request", None),
            ("default-not-found", "zh", 404, "资源不存在", None),
            ("unique-email", "en", 422, "Validation failed", {"email": "Email already exists"}),
            ("unique-favorite", "en", 409, "Already in favorites", None),
            ("unique-order", "en", 500, "Internal server error", None),
            ("unique-unknown", "en", 409, "Conflict", None),
            ("unknown", "zh", 500, "服务器内部错误", None),
            (
                "raised-in-except",
                "en",
                409,
                "This session has ended",
                {"errorKey": "sessionClosed"},
            ),
        ],
    )
    def test_error_body(
        self,
        client: TestClient,
        name: str,
        lang: str,
        status: int,
        message: str,
        data: Any,
    ) -> None:
        response = client.get(f"/api/v1/test/errors/{name}", headers={"Accept-Language": lang})

        assert response.status_code == status
        assert body(response) == {"code": status, "message": message, "data": data}

    def test_throttle_keeps_retry_after(self, client: TestClient) -> None:
        assert client.get("/api/v1/test/errors/throttle").headers["retry-after"] == "60"

    def test_unknown_error_keeps_outer_headers(self, client: TestClient) -> None:
        response = client.get("/api/v1/test/errors/unknown")

        assert response.headers["x-request-id"]
        assert response.headers["x-content-type-options"] == "nosniff"


class TestValidation:
    def test_reports_all_constraints_per_field(self, client: TestClient) -> None:
        response = client.post("/api/v1/test/login", json={})

        assert response.status_code == 422
        assert body(response) == {
            "code": 422,
            "message": "Validation failed",
            "data": {
                "email": "email should not be empty, email must be an email",
                "password": (
                    "Password must be a valid MD5 hash (32 hexadecimal characters), "
                    "password should not be empty"
                ),
            },
        }

    def test_message_translated(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login", json={"email": "x"}, headers={"Accept-Language": "zh-CN"}
        )

        assert body(response)["message"] == "数据验证失败"

    def test_applies_transforms_and_strips_unknown_fields(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login",
            json={
                "email": " Admin@Example.COM ",
                "password": "E10ADC3949BA59ABBE56E057F20F883E",
                "extra": 1,
            },
        )

        assert body(response)["data"] == {
            "email": "admin@example.com",
            "password": "e10adc3949ba59abbe56e057f20f883e",
        }

    @pytest.mark.parametrize("payload", [b"{bad", b"null", b'"x"', b"1"])
    def test_invalid_json_is_400(self, client: TestClient, payload: bytes) -> None:
        response = client.post(
            "/api/v1/test/login", content=payload, headers={"content-type": "application/json"}
        )

        assert response.status_code == 400
        assert body(response)["data"] is None

    def test_body_over_256kb_is_413(self, client: TestClient) -> None:
        payload = b'{"email":"' + b"a" * 262144 + b'"}'
        response = client.post(
            "/api/v1/test/login", content=payload, headers={"content-type": "application/json"}
        )

        assert response.status_code == 413
        assert body(response) == {"code": 413, "message": "request entity too large", "data": None}

    def test_non_json_body_validated_as_empty_object(self, client: TestClient) -> None:
        response = client.post("/api/v1/test/login", content=b"email=a%40b.com")

        assert response.status_code == 422
        assert set(body(response)["data"]) == {"email", "password"}

    def test_array_body_validated_as_instance(self, client: TestClient) -> None:
        response = client.post("/api/v1/test/login", json=[1])

        assert response.status_code == 422
        assert set(body(response)["data"]) == {"email", "password"}


class TestBodyParser:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("POST", "/api/v1/nope"), ("GET", "/"), ("PATCH", "/api/health")],
    )
    def test_invalid_json_rejected_before_routing(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(
            method, path, content=b"x", headers={"content-type": "application/json"}
        )

        assert response.status_code == 400
        assert body(response)["data"] is None

    def test_oversized_body_rejected_before_routing(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/nope",
            content=b"[" + b"1," * 140000 + b"1]",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413

    def test_whitespace_only_body_is_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login", content=b"   ", headers={"content-type": "application/json"}
        )

        assert response.status_code == 400

    def test_empty_body_is_empty_object(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login", content=b"", headers={"content-type": "application/json"}
        )

        assert response.status_code == 422

    def test_utf8_bom_is_ignored(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login",
            content=b'\xef\xbb\xbf{"email":"a"}',
            headers={"content-type": "application/json; charset=utf-8"},
        )

        assert response.status_code == 422
        assert body(response)["data"]["email"] == "email must be an email"

    def test_proto_keys_stripped(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/test/login",
            json={
                "email": "a@b.com",
                "password": "e10adc3949ba59abbe56e057f20f883e",
                "constructor": {"x": 1},
                "toString": "x",
            },
        )

        assert response.status_code == 200


class TestPaths:
    def test_percent_encoded_route_segment_is_not_decoded(self, client: TestClient) -> None:
        response = client.get("/api/%68ealth")

        assert response.status_code == 404
        assert body(response)["message"] == "Cannot GET /api/%68ealth"

    @pytest.mark.parametrize(
        ("raw", "decoded"),
        [("a%20b", "a b"), ("1%2F2", "1/2"), ("%E4%B8%AD", "中"), ("a+b", "a+b")],
    )
    def test_params_are_uri_decoded(self, client: TestClient, raw: str, decoded: str) -> None:
        assert body(client.get(f"/api/v1/test/files/{raw}"))["data"] == {"name": decoded}

    @pytest.mark.parametrize("raw", ["%FF", "%E4%B8", "%G1", "abc%"])
    def test_undecodable_param_is_400(self, client: TestClient, raw: str) -> None:
        response = client.get(f"/api/v1/test/files/{raw}")

        assert response.status_code == 400
        assert body(response) == {
            "code": 400,
            "message": f"Failed to decode param '{raw}'",
            "data": None,
        }


class TestQueryString:
    def test_node_querystring_semantics(self, client: TestClient) -> None:
        data = body(client.get("/api/v1/test/query?a=1&a=2&b=x+y&c=%E4%B8%AD&d&e=%ZZ&&f="))["data"]

        assert data == {"a": ["1", "2"], "b": "x y", "c": "中", "d": "", "e": "%ZZ", "f": ""}

    def test_max_keys(self) -> None:
        parsed = parse_query_string("&".join(f"k{i}=1" for i in range(1200)))

        assert len(parsed) == 1000


class TestIdParam:
    @pytest.mark.parametrize("raw", ["abc", "0", "-1", "1.5", "2147483648", chr(0xFF11)])
    @pytest.mark.parametrize(
        ("lang", "message", "detail"),
        [("en", "Bad request", "Invalid id"), ("zh", "请求参数错误", "ID 格式不正确")],
    )
    def test_invalid_id_is_400(
        self, client: TestClient, raw: str, lang: str, message: str, detail: str
    ) -> None:
        response = client.get(f"/api/v1/test/items/{raw}", headers={"Accept-Language": lang})

        assert response.status_code == 400
        assert body(response) == {"code": 400, "message": message, "data": {"id": detail}}

    @pytest.mark.parametrize(
        ("raw", "expected"), [("1", 1), ("007", 7), ("2147483647", 2147483647)]
    )
    def test_valid_id(self, client: TestClient, raw: str, expected: int) -> None:
        assert body(client.get(f"/api/v1/test/items/{raw}"))["data"] == {"id": expected}


class TestRouting:
    def test_unknown_route_includes_query_string(self, client: TestClient) -> None:
        response = client.get("/api/v1/nope?a=1%20b&c")

        assert response.status_code == 404
        assert body(response) == {
            "code": 404,
            "message": "Cannot GET /api/v1/nope?a=1%20b&c",
            "data": None,
        }

    def test_method_mismatch_is_404_without_allow(self, client: TestClient) -> None:
        response = client.patch("/api/health?x=1")

        assert response.status_code == 404
        assert "allow" not in response.headers
        assert body(response)["message"] == "Cannot PATCH /api/health?x=1"

    @pytest.mark.parametrize("path", ["/api/health/", "/API/Health", "/Api/v1/Test/OK/"])
    def test_trailing_slash_and_case_insensitive(self, client: TestClient, path: str) -> None:
        assert client.get(path).status_code == 200

    def test_double_trailing_slash_not_matched(self, client: TestClient) -> None:
        assert client.get("/api/health//").status_code == 404

    def test_head_on_get_route(self, client: TestClient) -> None:
        response = client.head("/api/health")

        assert response.status_code == 200
        assert response.content == b""


class TestRequestId:
    def test_echoes_valid_id(self, client: TestClient) -> None:
        assert client.get("/", headers={"X-Request-Id": "abc.1:2-3"}).headers["x-request-id"] == (
            "abc.1:2-3"
        )

    @pytest.mark.parametrize("incoming", [None, "bad id!", "x" * 129, ""])
    def test_generates_uuid(self, client: TestClient, incoming: str | None) -> None:
        headers = {"X-Request-Id": incoming} if incoming is not None else {}
        generated = client.get("/", headers=headers).headers["x-request-id"]

        assert str(uuid.UUID(generated)) == generated


class TestCors:
    def test_development_reflects_origin(self, client: TestClient) -> None:
        response = client.get("/", headers={"Origin": "http://a.com"})

        assert response.headers["access-control-allow-origin"] == "http://a.com"
        assert response.headers["access-control-allow-credentials"] == "true"
        assert response.headers["vary"] == "Origin"
        assert response.headers["access-control-expose-headers"] == (
            "X-Request-Id,Retry-After,X-RateLimit-Limit,X-RateLimit-Remaining,X-RateLimit-Reset"
        )

    def test_no_origin_header(self, client: TestClient) -> None:
        response = client.get("/")

        assert "access-control-allow-origin" not in response.headers
        assert response.headers["vary"] == "Origin"

    def test_preflight(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/auth/me",
            headers={
                "Origin": "http://a.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-foo,content-type",
            },
        )

        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["access-control-allow-methods"] == "GET,HEAD,PUT,PATCH,POST,DELETE"
        assert response.headers["access-control-allow-headers"] == "x-foo,content-type"
        assert response.headers["vary"] == "Origin, Access-Control-Request-Headers"
        assert response.headers["x-request-id"]

    def test_production_whitelist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_production(monkeypatch)
        monkeypatch.setenv("FRONTEND_DOMAIN", "https://a.com, https://b.com")
        client = TestClient(create_app(Settings.load()))

        allowed = client.options("/", headers={"Origin": "https://b.com"})
        denied = client.get("/", headers={"Origin": "https://evil.com"})

        assert allowed.headers["access-control-allow-origin"] == "https://b.com"
        assert allowed.headers["access-control-allow-headers"] == (
            "Content-Type,Authorization,Accept-Language"
        )
        assert (
            allowed.headers["access-control-allow-methods"] == "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        )
        assert "access-control-allow-origin" not in denied.headers


class TestSecurityHeaders:
    def test_matches_nestjs_helmet(self, client: TestClient) -> None:
        headers = client.get("/").headers

        assert headers["content-security-policy"].startswith("default-src 'self';base-uri 'self';")
        assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        assert headers["cross-origin-resource-policy"] == "cross-origin"
        assert headers["x-frame-options"] == "SAMEORIGIN"
        assert headers["x-xss-protection"] == "0"

    def test_docs_page_allows_cdn_script(self, client: TestClient) -> None:
        csp = client.get("/docs").headers["content-security-policy"]

        assert "https://cdn.jsdelivr.net" in csp


def test_simple_health_reports_current_memory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psutil  # type: ignore[import-untyped]

    from app.modules.home import router as home

    class Info:
        rss = 300 * home.MB
        uss = 120 * home.MB

    monkeypatch.setattr(psutil.Process, "memory_full_info", lambda _: Info())
    memory = client.get("/api/health").json()["data"]["memory"]
    assert memory == {"heapUsed": 120, "heapTotal": 120, "rss": 300}
