import ipaddress
from dataclasses import dataclass

lazy from fastapi import Request

lazy from app.config.app import Network

USER_AGENT_MAX_LENGTH = 512


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(value.strip().split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _trusted(value: str, networks: tuple[Network, ...]) -> bool:
    address = _address(value)
    return address is not None and any(address in network for network in networks)


def client_ip(request: Request, trusted_proxies: int | tuple[Network, ...]) -> str | None:
    """约定 §7：层数 N 时取 `X-Forwarded-For` 右起第 N 个；CIDR 列表时取右起第一个不可信地址。"""
    remote = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    hops = [part.strip() for part in reversed((forwarded or "").split(",")) if part.strip()]
    if isinstance(trusted_proxies, int):
        if not trusted_proxies or not hops:
            return remote
        return hops[min(trusted_proxies, len(hops)) - 1]
    if remote is None or not _trusted(remote, trusted_proxies):
        return remote
    for hop in hops:
        if not _trusted(hop, trusted_proxies):
            return hop
    return hops[-1] if hops else remote


def _valid_ip(value: str | None) -> str | None:
    if value is None:
        return None
    address = _address(value)
    return None if address is None else str(address)


def client_info(request: Request, trusted_proxies: int | tuple[Network, ...]) -> ClientInfo:
    """`ip` 不是合法 IP 地址时为 None。"""
    agent = request.headers.get("user-agent")
    return ClientInfo(
        ip=_valid_ip(client_ip(request, trusted_proxies)),
        user_agent=agent[:USER_AGENT_MAX_LENGTH] if agent is not None else None,
    )
