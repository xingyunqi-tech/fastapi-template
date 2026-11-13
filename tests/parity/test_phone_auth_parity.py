"""手机号 / 短信 / 微信 / 支付宝登录对照；验证码从两边各自的 Redis 读取（两边均开启短信 mock）。"""

import itertools
import json
import os
import subprocess
import time
from typing import Any

import pytest
import redis

from tests.parity.conftest import Pair, Parity
from tests.parity.test_auth_parity import PASSWORD, assert_same, authed, md5, normalize

PYTHON_REDIS_URL = os.environ.get("PARITY_PYTHON_REDIS_URL", "redis://localhost:6379/0")
NESTJS_REDIS_URL = os.environ.get("PARITY_NESTJS_REDIS_URL")
NESTJS_REDIS_CONTAINER = os.environ.get("PARITY_NESTJS_REDIS_CONTAINER", "parity-nestjs-redis-1")

SEND = "/api/v1/auth/phone/send-code"
_PHONES = itertools.count(int(time.time() * 1000) % 10**8)


def new_phone() -> str:
    return f"139{next(_PHONES) % 10**8:08d}"


def _nestjs_get(key: str) -> str | None:
    if NESTJS_REDIS_URL:
        return _get(NESTJS_REDIS_URL, key)
    result = subprocess.run(  # noqa: S603
        ["docker", "exec", NESTJS_REDIS_CONTAINER, "redis-cli", "--raw", "GET", key],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() or None


def _get(url: str, key: str) -> str | None:
    value = redis.Redis.from_url(url).get(key)
    return value.decode() if isinstance(value, bytes) else value


def _python_get(key: str) -> str | None:
    return _get(PYTHON_REDIS_URL, key)


def codes(phone: str, type_: str) -> tuple[str, str]:
    key = f"cache::sms:code:{type_}:{phone}"
    try:
        python, nestjs = _python_get(key), _nestjs_get(key)
    except (redis.RedisError, OSError, subprocess.CalledProcessError) as error:
        pytest.skip(f"无法读取验证码：{error}")
    assert python is not None
    assert nestjs is not None
    return json.loads(python)["value"]["code"], json.loads(nestjs)["value"]["code"]


def per_side(parity: Parity, url: str, bodies: tuple[dict[str, Any], dict[str, Any]]) -> Pair:
    python_client, nestjs_client = parity.clients
    return Pair(python_client.post(url, json=bodies[0]), nestjs_client.post(url, json=bodies[1]))


def send(parity: Parity, phone: str, type_: str) -> None:
    pair = parity.request("POST", SEND, json={"phone": phone, "type": type_})
    assert assert_same(pair) == {"code": 200, "message": "success"}


def register(parity: Parity, phone: str, **extra: Any) -> Pair:
    send(parity, phone, "register")
    python_code, nestjs_code = codes(phone, "register")
    body = {"phone": phone, "password": PASSWORD, **extra}
    return per_side(
        parity,
        "/api/v1/auth/phone/register",
        ({**body, "code": python_code}, {**body, "code": nestjs_code}),
    )


def assert_same_login(pair: Pair) -> None:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert normalize(python.json()) == normalize(nestjs.json())


@pytest.mark.parametrize("lang", ["en", "zh-CN"])
@pytest.mark.parametrize(
    ("url", "body"),
    [
        (SEND, {}),
        (SEND, {"phone": "123", "type": "nope"}),
        (SEND, {"phone": "13800000000", "type": "change_phone"}),
        (SEND, {"phone": "13000000001"}),
        (SEND, {"phone": "13000000001", "type": "reset_password"}),
        (SEND, {"phone": "13000000001", "type": "verify_identity"}),
        ("/api/v1/auth/phone/login", {}),
        ("/api/v1/auth/phone/login", {"phone": "13000000001", "password": PASSWORD}),
        ("/api/v1/auth/phone/login", {"phone": "13000000001", "password": "short"}),
        ("/api/v1/auth/phone/sms-login", {}),
        ("/api/v1/auth/phone/sms-login", {"phone": "13000000001", "code": "123456"}),
        ("/api/v1/auth/phone/sms-login", {"phone": "13000000001", "code": "12"}),
        ("/api/v1/auth/phone/register", {}),
        (
            "/api/v1/auth/phone/register",
            {"phone": "13000000001", "code": "123456", "password": PASSWORD, "nickname": 1},
        ),
        (
            "/api/v1/auth/phone/register",
            {"phone": "13000000001", "code": "123456", "password": PASSWORD},
        ),
        ("/api/v1/auth/phone/reset-password", {}),
        (
            "/api/v1/auth/phone/reset-password",
            {"phone": "13000000001", "code": "12ab", "password": PASSWORD},
        ),
        (
            "/api/v1/auth/phone/reset-password",
            {"phone": "13000000001", "code": "123456", "password": PASSWORD},
        ),
        ("/api/v1/auth/wechat/login", {}),
        ("/api/v1/auth/wechat/login", {"code": "c", "type": "web"}),
        ("/api/v1/auth/wechat/login", {"code": "c", "type": "mini_app"}),
        ("/api/v1/auth/wechat/login", {"code": "c", "type": "app", "nickname": "n"}),
        ("/api/v1/auth/wechat/phone-login", {}),
        ("/api/v1/auth/wechat/phone-login", {"loginCode": "a", "phoneCode": "b"}),
        ("/api/v1/auth/alipay/login", {}),
        ("/api/v1/auth/alipay/login", {"authCode": "x"}),
    ],
)
def test_errors(parity: Parity, url: str, body: dict[str, Any], lang: str) -> None:
    assert_same(parity.request("POST", url, json=body, headers={"Accept-Language": lang}))


def test_register_and_login_flows(parity: Parity) -> None:
    phone = new_phone()
    registered = register(parity, phone, nickname="  Phone User ")
    assert_same_login(registered)
    assert registered.python.json()["data"]["user"]["phone"] == phone

    send(parity, phone, "login")
    again = parity.request("POST", SEND, json={"phone": phone})
    python, nestjs = again.both()
    assert python.status_code == nestjs.status_code == 422
    assert {k: v for k, v in python.json()["data"].items() if k != "retryAfter"} == {
        k: v for k, v in nestjs.json()["data"].items() if k != "retryAfter"
    }
    assert isinstance(python.json()["data"]["retryAfter"], int)
    assert isinstance(nestjs.json()["data"]["retryAfter"], int)
    assert python.json()["message"] == nestjs.json()["message"]

    python_code, nestjs_code = codes(phone, "login")
    wrong = {"phone": phone, "code": "0000000"}
    assert_same(parity.request("POST", "/api/v1/auth/phone/sms-login", json=wrong))
    sms_login = per_side(
        parity,
        "/api/v1/auth/phone/sms-login",
        ({"phone": phone, "code": python_code}, {"phone": phone, "code": nestjs_code}),
    )
    assert_same_login(sms_login)

    for body in (
        {"phone": phone, "password": md5("wrong")},
        {"phone": phone, "password": PASSWORD.upper()},
    ):
        assert_same_login(parity.request("POST", "/api/v1/auth/phone/login", json=body))


def test_reset_password_flow(parity: Parity) -> None:
    phone = new_phone()
    registered = register(parity, phone)
    tokens = tuple(response.json()["data"]["token"] for response in registered.both())
    send(parity, phone, "reset_password")
    python_code, nestjs_code = codes(phone, "reset_password")
    new_password = md5("NewSecret01")
    reset = per_side(
        parity,
        "/api/v1/auth/phone/reset-password",
        (
            {"phone": phone, "code": python_code, "password": new_password},
            {"phone": phone, "code": nestjs_code, "password": new_password},
        ),
    )
    assert assert_same(reset) == {"code": 200, "message": "success"}
    assert_same(authed(parity, "GET", "/api/v1/auth/me", (tokens[0], tokens[1])))
    for password in (PASSWORD, new_password):
        assert_same_login(
            parity.request(
                "POST", "/api/v1/auth/phone/login", json={"phone": phone, "password": password}
            )
        )


def test_register_existing_phone(parity: Parity) -> None:
    phone = new_phone()
    register(parity, phone)
    assert_same(parity.request("POST", SEND, json={"phone": phone, "type": "register"}))
    for lang in ("en", "zh"):
        assert_same(
            parity.request(
                "POST",
                "/api/v1/auth/phone/register",
                json={"phone": phone, "code": "123456", "password": PASSWORD},
                headers={"Accept-Language": lang},
            )
        )


def test_identity_check_for_password_user(parity: Parity) -> None:
    phone = new_phone()
    registered = register(parity, phone)
    tokens = tuple(response.json()["data"]["token"] for response in registered.both())
    for body in ({}, {"smsCode": "123456"}, {"oldPassword": md5("wrong")}):
        assert_same(authed(parity, "DELETE", "/api/v1/auth/me", (tokens[0], tokens[1]), json=body))
    send(parity, phone, "verify_identity")
    assert_same(parity.request("POST", SEND, json={"phone": phone, "type": "verify_identity"}))
