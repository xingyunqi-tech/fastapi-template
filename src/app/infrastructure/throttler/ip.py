"""@nestjs/throttler `normalizeIp`：IPv6 按子网前缀归并，回环与 IPv4 映射地址特殊处理。"""

import ipaddress
from typing import Final

DEFAULT_IPV6_SUBNET_PREFIX: Final = 64


def normalize_ip(ip: str, ipv6_subnet_prefix: int = DEFAULT_IPV6_SUBNET_PREFIX) -> str:
    if not ip:
        return ip
    bare = ip.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return ip
    if isinstance(address, ipaddress.IPv4Address):
        return bare
    if address == ipaddress.IPv6Address("::1"):
        return "::1"
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    prefix = min(max(ipv6_subnet_prefix, 0), 128)
    network = ipaddress.IPv6Network((address, prefix), strict=False)
    return f"{network.network_address}/{prefix}"
