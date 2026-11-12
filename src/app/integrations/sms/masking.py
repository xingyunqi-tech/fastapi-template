def mask_phone(phone: str) -> str:
    """nestjs maskPhone：保留前 3 位与后 4 位。"""
    if not phone:
        return "[invalid]"
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"
