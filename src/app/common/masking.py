def mask_phone(phone: str) -> str:
    """约定 §8：`+86138****0000`，即末 4 位之前的 4 位替换为 `****`。"""
    if len(phone) < 9:
        return "***"
    return f"{phone[:-8]}****{phone[-4:]}"


def mask_email(email: str) -> str:
    """约定 §8：`a***@example.com`。"""
    local, at, domain = email.partition("@")
    if not at:
        return "***"
    return f"{local[:1]}***@{domain}"
