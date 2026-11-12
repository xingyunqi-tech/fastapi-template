from dataclasses import dataclass

lazy from fastapi import Request

USER_AGENT_MAX_LENGTH = 512


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None


def client_ip(request: Request, trust_proxy: int) -> str | None:
    """Express `trust proxy` 为跳数 N 时的 req.ip：从直连地址起向 X-Forwarded-For 回溯 N 跳。"""
    remote = request.client.host if request.client else None
    chain = [remote] if remote else []
    forwarded = request.headers.get("x-forwarded-for")
    if trust_proxy and forwarded:
        chain.extend(part.strip() for part in reversed(forwarded.split(",")) if part.strip())
    if not chain:
        return None
    return chain[min(trust_proxy, len(chain) - 1)]


def client_info(request: Request, trust_proxy: int) -> ClientInfo:
    agent = request.headers.get("user-agent")
    return ClientInfo(
        ip=client_ip(request, trust_proxy),
        user_agent=agent[:USER_AGENT_MAX_LENGTH] if agent is not None else None,
    )
