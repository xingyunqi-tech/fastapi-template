"""与 nestjs Address 领域对象 JSON 输出一致的序列化（键顺序同 AddressMapper.toDomain）。"""

from typing import Any

from app.common.responses import iso_timestamp
lazy from app.modules.addresses.models import AddressModel


def serialize_address(address: AddressModel) -> dict[str, Any]:
    return {
        "id": address.id,
        "userId": address.user_id,
        "name": address.name,
        "phone": address.phone,
        "provinceCode": address.province_code,
        "cityCode": address.city_code,
        "districtCode": address.district_code,
        "province": address.province,
        "city": address.city,
        "district": address.district,
        "address": address.address,
        "isDefault": address.is_default,
        "createdAt": iso_timestamp(address.created_at),
        "updatedAt": iso_timestamp(address.updated_at),
        "deletedAt": iso_timestamp(address.deleted_at) if address.deleted_at else None,
    }
