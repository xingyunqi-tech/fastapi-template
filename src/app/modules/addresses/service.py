"""收货地址：区划代码校验与名称回填、每用户数量上限、唯一默认地址。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
lazy from collections.abc import Mapping, Sequence

from app.common.exceptions import HttpError, not_found_error
lazy from app.modules.addresses.models import AddressModel

if TYPE_CHECKING:
    from app.modules.addresses.repository import AddressRepository, Sort
    from app.modules.regions.service import RegionsService

MAX_ADDRESSES_PER_USER: Final = 20
_CODE_KEYS: Final = ("provinceCode", "cityCode", "districtCode")
_NAME_KEYS: Final = ("province", "city", "district")


@dataclass(frozen=True, slots=True)
class RegionData:
    province_code: str | None
    city_code: str | None
    district_code: str | None
    province: str
    city: str
    district: str

    def changes(self) -> dict[str, Any]:
        return {
            "provinceCode": self.province_code,
            "cityCode": self.city_code,
            "districtCode": self.district_code,
            "province": self.province,
            "city": self.city,
            "district": self.district,
        }


def _bad_request(error: str, message: str) -> HttpError:
    return HttpError(400, {"error": error, "message": message})


def _address_not_found() -> HttpError:
    return not_found_error("addressNotFound")


class AddressesService:
    def __init__(self, repository: AddressRepository, regions: RegionsService) -> None:
        self.repository = repository
        self.regions = regions

    async def _region_data(self, values: Mapping[str, Any]) -> RegionData:
        """三个区划代码须同时提供或都不提供；提供时按代码回填名称，否则名称必填。"""
        codes = [values.get(key) for key in _CODE_KEYS]
        province, city, district = (values.get(key) for key in _NAME_KEYS)
        has_all = all(codes)
        if any(key in values for key in _CODE_KEYS) and not has_all:
            raise _bad_request(
                "incompleteRegionCodes",
                "Must provide all three region codes (province, city, district) or none at all",
            )
        if not has_all:
            if not province or not city or not district:
                raise _bad_request(
                    "missingRegionNames",
                    "Province, city, and district names are required "
                    "when region codes are not provided",
                )
            return RegionData(None, None, None, province, city, district)
        province_code, city_code, district_code = (str(code) for code in codes)
        regions = await self.regions.validate_region_combination(
            province_code, city_code, district_code
        )
        if regions is None:
            raise _bad_request(
                "invalidRegionCombination", "Invalid province/city/district combination"
            )
        return RegionData(
            province_code,
            city_code,
            district_code,
            regions[0]["name"],
            regions[1]["name"],
            regions[2]["name"],
        )

    async def create(self, user_id: int, body: Mapping[str, Any]) -> AddressModel:
        region = await self._region_data(body)
        created = await self.repository.create_within_limit(
            {
                "user_id": user_id,
                "name": body.get("name"),
                "phone": body.get("phone"),
                "province_code": region.province_code,
                "city_code": region.city_code,
                "district_code": region.district_code,
                "province": region.province,
                "city": region.city,
                "district": region.district,
                "address": body.get("address"),
                "is_default": False,
            },
            MAX_ADDRESSES_PER_USER,
        )
        if created is None:
            raise HttpError(
                403,
                {
                    "error": "maxAddressesReached",
                    "message": f"Maximum {MAX_ADDRESSES_PER_USER} addresses allowed per user",
                },
            )
        address, previous_count = created
        is_default = body.get("isDefault")
        if is_default if is_default is not None else previous_count == 0:
            await self.repository.mark_default(address.id, user_id)
            await self.repository.refresh(address)
        return address

    async def find_page(
        self,
        user_id: int,
        *,
        filters: Mapping[str, Any] | None,
        sort: Sequence[Sort] | None,
        page: float,
        limit: float,
    ) -> tuple[list[AddressModel], int]:
        data = await self.repository.find_many(
            user_id, filters=filters, sort=sort, page=page, limit=limit
        )
        return data, await self.repository.count(user_id, filters)

    async def find_by_id(self, address_id: int, user_id: int) -> AddressModel:
        address = await self.repository.find_by_id_and_user(address_id, user_id)
        if address is None:
            raise _address_not_found()
        return address

    async def find_default(self, user_id: int) -> AddressModel | None:
        return await self.repository.find_default(user_id)

    async def update(self, address_id: int, user_id: int, body: Mapping[str, Any]) -> AddressModel:
        """body 只含请求中出现的字段（含显式 null）。"""
        address = await self.find_by_id(address_id, user_id)
        changes = {key: value for key, value in body.items() if key != "isDefault"}
        is_default = body.get("isDefault")
        if is_default is False:
            changes["isDefault"] = False
        if any(key in body for key in _CODE_KEYS):
            current: dict[str, Any] = {
                "provinceCode": address.province_code,
                "cityCode": address.city_code,
                "districtCode": address.district_code,
                "province": address.province,
                "city": address.city,
                "district": address.district,
            }
            merged = {
                key: body[key] if body.get(key) is not None else current[key]
                for key in (*_CODE_KEYS, *_NAME_KEYS)
            }
            region = await self._region_data(
                {key: value for key, value in merged.items() if value is not None}
            )
            changes.update(region.changes())
        updated = await self.repository.update(address_id, changes)
        if updated is None:
            raise _address_not_found()
        if is_default:
            await self.repository.mark_default(address_id, user_id)
            await self.repository.refresh(updated)
        return updated

    async def set_default(self, address_id: int, user_id: int) -> AddressModel:
        address = await self.find_by_id(address_id, user_id)
        await self.repository.mark_default(address_id, user_id)
        await self.repository.refresh(address)
        return address

    async def remove(self, address_id: int, user_id: int) -> None:
        address = await self.find_by_id(address_id, user_id)
        was_default = address.is_default
        await self.repository.soft_delete(address_id)
        if was_default:
            remaining = await self.repository.find_many(user_id, page=1, limit=1)
            if remaining:
                await self.repository.mark_default(remaining[0].id, user_id)
