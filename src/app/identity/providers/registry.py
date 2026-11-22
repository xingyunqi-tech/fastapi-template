"""已启用的 provider：凭据配齐即启用，按约定 §11.1 的顺序列出。"""

from app.identity.providers import ProviderClient, ProviderId
from app.identity.providers.alipay import AlipayClient
from app.identity.providers.wechat import WechatAppClient, WechatMiniappClient
lazy from app.common.context import AppContext


def miniapp_client(context: AppContext) -> WechatMiniappClient | None:
    if not context.settings.wechat.miniapp_enabled:
        return None
    return WechatMiniappClient(context.settings.wechat, context.cache, context.http_transport)


def enabled_providers(context: AppContext) -> dict[ProviderId, ProviderClient]:
    settings = context.settings
    clients: dict[ProviderId, ProviderClient] = {}
    miniapp = miniapp_client(context)
    if miniapp is not None:
        clients[ProviderId.WECHAT_MINIAPP] = miniapp
    if settings.wechat.app_enabled:
        clients[ProviderId.WECHAT_APP] = WechatAppClient(settings.wechat, context.http_transport)
    if settings.alipay.enabled:
        clients[ProviderId.ALIPAY] = AlipayClient(settings.alipay, context.http_transport)
    return clients


def provider_client(context: AppContext, provider: str) -> ProviderClient | None:
    if provider not in ProviderId.__members__.values():
        return None
    return enabled_providers(context).get(ProviderId(provider))
